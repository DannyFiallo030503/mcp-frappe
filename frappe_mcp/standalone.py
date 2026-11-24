"""Standalone stdio MCP server that talks to Frappe through its public HTTP API."""

from __future__ import annotations

import argparse
import ast
import copy
import difflib
import functools
import hashlib
import html
import inspect
import json
import logging
import math
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import quote, urlparse

import anyio
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from frappe_mcp import __version__

logging.getLogger("httpx").setLevel(logging.WARNING)
_log = logging.getLogger(__name__)


_INSTRUCTIONS = """\
Frappe MCP works on one Frappe site through its HTTP API, as the configured user.

- Start with get_site_info: the user, app versions, and which apps have a checkout under \
FRAPPE_APPS_PATH (file writes need one).
- Find DocTypes with list_doctypes(search=...) and read get_doctype_schema before changing \
anything; use its fieldnames.
- Your own app's DocTypes: create_doctype, update_doctype, delete_doctype, \
promote_custom_doctype. frappe/erpnext DocTypes: customize_doctype, \
create_custom_field, set_field_property, set_role_permissions, export_form_customizations \
for native Customize Form files, and export_customizations for fixtures. Workflows, Notifications, \
Report Builder reports, Workspaces: the upsert_* tools.
- Run diff_doctype before and after editing a standard DocType; list_app_drift shows what \
differs between the site and the app files.
- Keep limit small and page with offset; count_documents gives totals. Find reports with \
list_reports and their filters with get_report_filters.
- Every write is two calls: without confirm for a preview, then confirm=true with the token \
the preview returned (expected_modified or expected_preview). Show the preview to the user \
first. A stale token means: preview again.
- A PermissionError is final: tell the user which access is missing; never work around it.
- Route by get_site_info: v15 uses this server; v16+ may use native MCP for data and this one for \
development; unknown stays here. It cannot configure another Frappe MCP server.
"""

server = FastMCP("frappe", instructions=_INSTRUCTIONS)
# Tool names per toolset; `_tool()` fills it and `_apply_toolsets()` removes unselected ones.
_TOOLSETS: dict[str, set[str]] = {
    name: set() for name in ("read", "data", "customize", "developer", "bench")
}
_WRITE_LOCK = threading.Lock()


def _tool(
    toolset: str,
    title: str,
    *,
    destructive: bool = False,
    idempotent: bool = False,
    read_only: bool | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register an MCP tool in a toolset, with annotations hosts use to gate approval.

    Tools of the read toolset are read-only; `read_only=True` marks one in another toolset.
    """
    read_only = toolset == "read" if read_only is None else read_only
    # The display name is the tool's `title`; repeating it in the annotations (the pre-2025-06-18
    # place for it) would only lengthen the tool list.
    annotations = ToolAnnotations(
        readOnlyHint=read_only,
        destructiveHint=None if read_only else destructive,
        idempotentHint=None if read_only else idempotent,
        openWorldHint=False,
    )

    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        _TOOLSETS[toolset].add(fn.__name__)

        # FastMCP invokes sync handlers on its event loop. Run their blocking HTTP, Bench, and
        # filesystem work in AnyIO's worker pool so one slow Frappe reload can't stall reads.
        # Serialize writes across workers to protect file changes and the shared change journal.
        # FastMCP reads the schema through `__wrapped__`; module-level functions keep their
        # native sync behavior for internal callers and tests.
        def invoke_sync(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
            try:
                if read_only:
                    result = fn(*args, **kwargs)
                else:
                    with _WRITE_LOCK:
                        result = fn(*args, **kwargs)
                        if kwargs.get("confirm") is True and fn.__name__ not in _SELF_JOURNALED:
                            result = _journal_call(fn.__name__, toolset, kwargs, result)
            except httpx.TransportError as error:
                raise RuntimeError(_unreachable_message(error)) from error
            return result

        @functools.wraps(fn)
        async def call(*args: Any, **kwargs: Any) -> Any:
            if inspect.iscoroutinefunction(fn):
                if not read_only:
                    await anyio.to_thread.run_sync(_WRITE_LOCK.acquire, abandon_on_cancel=False)
                try:
                    result = await fn(*args, **kwargs)
                    if (
                        not read_only
                        and kwargs.get("confirm") is True
                        and fn.__name__ not in _SELF_JOURNALED
                    ):
                        result = await anyio.to_thread.run_sync(
                            functools.partial(_journal_call, fn.__name__, toolset, kwargs, result),
                            abandon_on_cancel=False,
                        )
                    return result
                except httpx.TransportError as error:
                    raise RuntimeError(_unreachable_message(error)) from error
                finally:
                    if not read_only:
                        _WRITE_LOCK.release()
            return await anyio.to_thread.run_sync(
                functools.partial(invoke_sync, args, kwargs), abandon_on_cancel=False
            )

        # No output schema: Frappe returns open-ended JSON, so the generated one says nothing
        # and only adds to the tool list every session pays for (results stay text blocks).
        server.tool(
            title=title,
            description=inspect.cleandoc(fn.__doc__ or ""),
            annotations=annotations,
            structured_output=False,
        )(call)
        _trim_schema(server._tool_manager._tools[fn.__name__].parameters)
        return fn

    return register


def _trim_schema(schema: Any) -> None:
    """Remove what adds characters but no meaning to a generated JSON schema, in place.

    Pydantic's `title` keywords repeat the argument names ("Doctype" for `doctype`); keys
    under `properties` and `$defs` are argument and definition names, so a property called
    `title` is kept. Also drops `additionalProperties: true` and `items: {}` (the defaults)
    and the null branch of optional arguments.
    """
    if isinstance(schema, list):
        for item in schema:
            _trim_schema(item)
        return
    if not isinstance(schema, dict):
        return
    # `X | None = None` becomes just X: the argument is optional because it isn't required,
    # and the server still accepts an explicit null.
    branches = schema.get("anyOf")
    if (
        isinstance(branches, list)
        and {"type": "null"} in branches
        and schema.get("default", ...) is None
    ):
        rest = [branch for branch in branches if branch != {"type": "null"}]
        del schema["default"]
        if len(rest) == 1 and isinstance(rest[0], dict):
            del schema["anyOf"]
            schema.update({key: value for key, value in rest[0].items() if key not in schema})
        else:
            schema["anyOf"] = rest
    schema.pop("title", None)
    if schema.get("additionalProperties") is True:
        del schema["additionalProperties"]
    if schema.get("items") == {}:
        del schema["items"]
    for key, value in schema.items():
        if key in ("properties", "$defs") and isinstance(value, dict):
            for child in value.values():
                _trim_schema(child)
        elif key not in ("default", "enum", "const", "examples"):
            _trim_schema(value)


def _tool_chars(tool: Any) -> int:
    """Characters one tool adds to the tool list, as a host receives it."""
    return len(json.dumps(tool.model_dump(mode="json", exclude_none=True)))


def _tool_list_cost() -> dict[str, tuple[int, int]]:
    """Return {toolset: (tool count, characters)} for every registered toolset."""
    sizes = {tool.name: _tool_chars(tool) for tool in anyio.run(server.list_tools)}
    return {
        toolset: (len(names), sum(sizes.get(name, 0) for name in names))
        for toolset, names in _TOOLSETS.items()
    }


_session: httpx.Client | None = None
_session_lock = threading.RLock()
_session_active: dict[int, int] = {}
_retired_sessions: dict[int, httpx.Client] = {}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,139}$")
_RESERVED_FIELDS = {
    "doctype",
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "idx",
    "parent",
    "parenttype",
    "parentfield",
}
# Columns every record has; reads and filters may use them even though writes may not.
_STANDARD_COLUMNS = frozenset(
    {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx"}
)
_CHILD_COLUMNS = frozenset({"parent", "parenttype", "parentfield"})
_TABLE_FIELDTYPES = {"Table", "Table MultiSelect"}
# Frappe's `display_fieldtypes` (frappe/model/__init__.py): layout and display-only fields
# that hold no value.
_LAYOUT_FIELDTYPES = frozenset(
    {
        "Section Break",
        "Column Break",
        "Tab Break",
        "Attachment Gallery",
        "HTML",
        "Button",
        "Image",
        "Fold",
        "Heading",
    }
)
_GETDOCTYPE = "/api/method/frappe.desk.form.load.getdoctype"
# DocTypes whose records shape other DocTypes' metadata; writing one clears the whole cache.
_SCHEMA_DOCTYPES = frozenset(
    {"DocType", "DocField", "DocPerm", "Custom Field", "Property Setter", "Custom DocPerm"}
)
# The record tools (create_document, update_document, delete_document) keep to record data.
# Schema and configuration records have their own tools, which run checks the record tools
# don't; each entry maps an action ("create", "update", "delete", or "*" for all) to the tool
# to use. An action left out stays allowed: no tool deletes these records, and a delete writes
# no code.
_CUSTOMIZE_TOOLS = (
    "customize_doctype, create_custom_field, set_field_property, or set_role_permissions"
)
_RECORD_TOOL_REDIRECTS: dict[str, dict[str, str]] = {
    "DocType": {
        "create": "use create_doctype",
        "update": (
            "use update_doctype for your own app's DocTypes, or create_custom_field and "
            "set_field_property for Frappe and ERPNext DocTypes"
        ),
        "delete": "use delete_doctype",
    },
    "DocField": {
        "*": (
            "DocField rows belong to their DocType; use update_doctype for your own app's "
            "DocTypes, or create_custom_field and set_field_property for Frappe and ERPNext "
            "DocTypes"
        )
    },
    "DocPerm": {"*": "use set_role_permissions"},
    "Custom DocPerm": {"create": "use set_role_permissions", "update": "use set_role_permissions"},
    "Custom Field": {
        "create": "use create_custom_field",
        "update": "use set_field_property, which saves the change as Customize Form does",
    },
    "Property Setter": {"create": "use set_field_property", "update": "use set_field_property"},
    "Customize Form": {"*": f"use {_CUSTOMIZE_TOOLS}"},
    "Workflow": {"create": "use upsert_workflow", "update": "use upsert_workflow"},
    "Notification": {"create": "use upsert_notification", "update": "use upsert_notification"},
    "Report": {
        "create": "use upsert_report (Report Builder reports)",
        "update": "use upsert_report (Report Builder reports)",
    },
    "Workspace": {"create": "use upsert_workspace", "update": "use upsert_workspace"},
}
# DocTypes whose records hold code Frappe runs: Python, Jinja, JavaScript, CSS, or a dotted
# method path. The record tools never create, update, or delete them. Checked against Frappe
# v16.55.1 (each also exists in v15, except Automation Flow).
_CODE_DOCTYPES = frozenset(
    {
        "Address Template",  # template: Jinja
        "Assignment Rule",  # assign/unassign/close conditions: Python; description: Jinja
        "Automation Flow",  # condition: Python (v16 automation engine)
        "Client Script",  # script: JavaScript
        "Custom HTML Block",  # html, script, style
        "Email Template",  # subject and response: Jinja
        "Letter Head",  # content and footer: Jinja; header/footer scripts: JavaScript
        "Navbar Settings",  # Navbar Item action and condition: JavaScript
        "Print Format",  # html, raw_commands: Jinja; css
        "Print Format Field Template",  # template: Jinja
        "Print Style",  # css
        "Scheduled Job Type",  # method: a dotted path Frappe calls
        "Server Script",  # script: Python
        "System Console",  # console: Python
        "Web Form",  # client_script: JavaScript; breadcrumbs: Python expression
        "Web Page",  # context_script: Python; main_section: Jinja; javascript
        "Web Template",  # template: Jinja
        "Webhook",  # condition: Python; request_url and webhook_json: Jinja
        "Website Script",  # javascript
        "Website Theme",  # SCSS and JavaScript
    }
)
# `Code` fields that hold plain data in common record DocTypes: Frappe and ERPNext only parse
# them, never run them. Every other `Code` field is refused by the record tools.
_PLAIN_CODE_FIELDS: dict[tuple[str, str], str] = {
    ("Translation", "source_text"): "the text to translate; looked up by frappe._, not run",
    ("Translation", "translated_text"): "the translation frappe._ returns; not run",
    **{
        (doctype, "item_tax_rate"): (
            "ERPNext's JSON map of tax account to rate, read with json.loads; copied with item rows"
        )
        for doctype in (
            "Purchase Invoice Item",
            "Purchase Order Item",
            "Purchase Receipt Item",
            "Quotation Item",
            "Sales Order Item",
            "Supplier Quotation Item",
        )
    },
    ("Purchase Order", "customer_contact_email"): (
        "an e-mail address (Code with Email options) fetched from the customer contact"
    ),
}
# Fields that aren't `Code` but that Frappe renders as Jinja, in DocTypes that are otherwise
# record data.
_TEMPLATE_FIELDS: dict[str, frozenset[str]] = {
    "Auto Repeat": frozenset({"subject", "message"}),  # rendered with the new document
}
_COMPACT_DOCTYPE_KEYS = (
    "name",
    "module",
    "istable",
    "issingle",
    "is_submittable",
    "is_tree",
    "autoname",
    "title_field",
    "custom",
)
_COMPACT_FIELD_KEYS = (
    "fieldname",
    "label",
    "fieldtype",
    "options",
    "reqd",
    "read_only",
    "hidden",
    "default",
    "unique",
    "permlevel",
)
# A subset of the operators Frappe's get_filter accepts; tree operators are left out for now.
_FILTER_OPERATORS = (
    "=",
    "!=",
    "<",
    ">",
    "<=",
    ">=",
    "like",
    "not like",
    "in",
    "not in",
    "between",
    "is",
    "timespan",
)
_ORDER_TERM = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)(?:\s+(asc|desc))?\s*", re.I)
_ORDER_MAX_TERMS = 3
_STALE_DOCUMENT = "Document changed since preview; request a fresh preview"


# Argument types shared by tools. The descriptions become part of each tool's MCP schema, which
# hosts send with every request, so keep them short; details belong in docs/tool-reference.md.
Filters = Annotated[
    dict[str, Any] | list[list[Any]] | None,
    Field(
        description=(
            "{field: value}, {field: [op, value]}, or [[field, op, value], ...]; "
            "[child_doctype, field, op, value] for a child table's field. "
            f"Operators: {', '.join(_FILTER_OPERATORS)}. "
            'Example: [["status", "=", "Open"]]'
        )
    ),
]
OrFilters = Annotated[
    dict[str, Any] | list[list[Any]] | None,
    Field(description="Same shapes as filters; a record must match at least one."),
]
Fields = Annotated[
    list[str] | None,
    Field(
        description=(
            "Fieldnames from get_doctype_schema or standard columns (name, owner, creation, "
            "modified, modified_by, docstatus, idx); omit for the default."
        )
    ),
]
OrderBy = Annotated[
    str | None,
    Field(
        description=(
            f"'<field> [asc|desc]', up to {_ORDER_MAX_TERMS} comma-separated, e.g. 'modified desc'."
        )
    ),
]
ExpectedModified = Annotated[
    str | None,
    Field(description="With confirm=true: the preview's `modified`. A mismatch: preview again."),
]
ExpectedPreview = Annotated[
    str | None,
    Field(description="With confirm=true: the preview's `preview_id`. A mismatch: preview again."),
]
Confirm = Annotated[
    bool,
    Field(description="false: preview only. true: apply, after the user saw the preview."),
]
Detail = Annotated[
    Literal["compact", "full"],
    Field(description="full returns every metadata property and is much larger."),
]
ReportFilters = Annotated[
    dict[str, Any] | None,
    Field(description="{filter_fieldname: value}; see get_report_filters."),
]
_DOCTYPE_PROPERTIES = {
    "editable_grid",
    "quick_entry",
    "track_changes",
    "track_seen",
    "track_views",
    "beta",
    "autoname",
    "description",
    "max_attachments",
    "hide_toolbar",
    "allow_copy",
    "allow_rename",
    "allow_import",
    "allow_events_in_timeline",
    "allow_auto_repeat",
    "view_settings",
    "title_field",
    "search_fields",
    "form_builder",
    "image_field",
    "timeline_field",
    "default_print_format",
    "sort_field",
    "sort_order",
    "document_type",
    "icon",
    "color",
    "show_preview_popup",
    "show_name_in_global_search",
    "restrict_to_domain",
    "read_only",
    "in_create",
    "web_view",
    "has_web_view",
    "allow_guest_to_view",
    "route",
    "is_published_field",
    "advanced",
    "engine",
    "nsm_parent_field",
    "documentation",
    "actions",
    "links",
    "subject_field",
    "sender_field",
    "email_append_to",
    "is_virtual",
    "default_email_template",
    "website_search_field",
    "naming_rule",
    "states",
    "is_calendar_and_gantt",
    "sender_name_field",
    "default_view",
    "force_re_route_to_default_view",
    "translated_doctype",
    "make_attachments_public",
    "queue_in_background",
    "index_web_pages_for_search",
    "show_title_field_in_link",
    "grid_page_length",
    "row_format",
    "protect_attached_files",
    "rows_threshold_for_grid_search",
    "recipient_account_field",
}
# Table-valued DocType settings; update_doctype leaves them to their own rows.
_DOCTYPE_TABLE_PROPERTIES = frozenset({"actions", "links", "states"})
# DocField `fieldtype` options (frappe/core/doctype/docfield/docfield.json, v16.33.1).
_FIELDTYPES = frozenset(
    {
        "Autocomplete",
        "Attach",
        "Attach Image",
        "Attachment Gallery",
        "Barcode",
        "Button",
        "Check",
        "Code",
        "Color",
        "Column Break",
        "Currency",
        "Data",
        "Date",
        "Datetime",
        "Duration",
        "Dynamic Link",
        "Float",
        "Fold",
        "Geolocation",
        "Heading",
        "HTML",
        "HTML Editor",
        "Icon",
        "Image",
        "Int",
        "JSON",
        "Link",
        "Long Text",
        "Markdown Editor",
        "Password",
        "Percent",
        "Phone",
        "Read Only",
        "Rating",
        "Section Break",
        "Select",
        "Signature",
        "Small Text",
        "Tab Break",
        "Table",
        "Table MultiSelect",
        "Text",
        "Text Editor",
        "Time",
    }
)
# DocField properties update_doctype may set (docfield.json, v16.33.1). Left out on purpose:
# `collapsible_depends_on` (an expression Frappe evaluates; only the three that
# set_field_property accepts are allowed), `ignore_xss_filter`, and Frappe's bookkeeping
# `oldfieldname` / `oldfieldtype`. `fieldname` is set only when a field is added.
_DOCFIELD_PROPERTIES = frozenset(
    {
        "label",
        "fieldtype",
        "reqd",
        "precision",
        "length",
        "search_index",
        "in_list_view",
        "in_standard_filter",
        "in_global_search",
        "in_preview",
        "allow_in_quick_entry",
        "bold",
        "translatable",
        "collapsible",
        "options",
        "default",
        "fetch_from",
        "fetch_if_empty",
        "depends_on",
        "mandatory_depends_on",
        "read_only_depends_on",
        "hidden",
        "read_only",
        "unique",
        "set_only_once",
        "allow_bulk_edit",
        "permlevel",
        "ignore_user_permissions",
        "allow_on_submit",
        "report_hide",
        "remember_last_selected_value",
        "in_filter",
        "no_copy",
        "print_hide",
        "print_hide_if_no_value",
        "print_width",
        "width",
        "columns",
        "description",
        "alignment",
        "hide_days",
        "hide_seconds",
        "hide_border",
        "non_negative",
        "max_height",
        "show_dashboard",
        "is_virtual",
        "documentation_url",
        "sort_options",
        "link_filters",
        "not_nullable",
        "show_on_timeline",
        "placeholder",
        "make_attachment_public",
        "sticky",
        "mask",
        "button_color",
        "show_description_on_click",
    }
)
_OPTIONS_REQUIRED_FIELDTYPES = frozenset({"Link", "Select", *_TABLE_FIELDTYPES})
# HTML options are markup the form renders and Button options name a server method, so
# update_doctype does not write either.
_CODE_OPTIONS_FIELDTYPES = frozenset({"HTML", "Button"})
# Customize Form's `ALLOWED_FIELDTYPE_CHANGE` (customize_form.py, v16.33.1): type changes
# Frappe treats as safe for a column that holds data.
_FIELDTYPE_CHANGE_GROUPS = (
    frozenset({"Currency", "Float", "Percent"}),
    frozenset({"Small Text", "Data"}),
    frozenset({"Text", "Data"}),
    frozenset({"Text", "Text Editor", "Code", "Signature", "HTML Editor"}),
    frozenset({"Data", "Select"}),
    frozenset({"Text", "Small Text", "Long Text"}),
    frozenset({"Text", "Data", "Barcode"}),
    frozenset({"Code", "Geolocation"}),
    frozenset({"Table", "Table MultiSelect"}),
)
_NEW_FIELDNAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CUSTOM_FIELD_RESERVED_FIELDNAMES = frozenset(
    {
        "name",
        "parent",
        "creation",
        "modified",
        "modified_by",
        "parentfield",
        "parenttype",
        "file_list",
        "flags",
        "docstatus",
    }
)


def _frappe_customize_fieldname(label: Any) -> str:
    """Mirror Custom Field.set_fieldname used by Customize Form when adding a field."""
    if not isinstance(label, str) or not label:
        raise ValueError("Frappe Customize Form needs a label to derive the Custom Field name")
    clean = "".join(
        char
        for char in label.replace(" ", "_")
        if char.isdigit() or char.isalpha() or char == "_"
    )
    fieldname = f"custom_{clean}".lower()
    if fieldname in _CUSTOM_FIELD_RESERVED_FIELDNAMES:
        fieldname += "1"
    return fieldname
_UPDATE_DOCTYPE_MAX_OPERATIONS = 50
_DOCTYPE_LINKS_LIMIT = 100
_ROLE_PERMISSION_FLAGS = {
    "read",
    "write",
    "create",
    "delete",
    "submit",
    "cancel",
    "amend",
    "report",
    "export",
    "import",
    "share",
    "print",
    "email",
    "select",
}
_PERMISSION_MANAGER_EXCLUDED_DOCTYPES = {
    "DocType",
    "Patch Log",
    "Module Def",
    "Transaction Log",
}
# Customize Form's own types for these properties (`docfield_properties` in
# frappe/custom/doctype/customize_form/customize_form.py, v16.33.1).
_PROPERTY_TYPES = {
    "hidden": "Check",
    "read_only": "Check",
    "reqd": "Check",
    "in_list_view": "Check",
    "in_standard_filter": "Check",
    "collapsible": "Check",
    "bold": "Check",
    "label": "Data",
    "width": "Data",
    "depends_on": "Data",
    "mandatory_depends_on": "Data",
    "read_only_depends_on": "Data",
    "options": "Text",
    "description": "Text",
    "default": "Text",
}
_CUSTOM_FIELD_FIXED_KEYS = ("doctype", "dt", "module")
_HOOK_BEGIN = "# BEGIN MCP FRAPPE MANAGED FIXTURES"
_HOOK_END = "# END MCP FRAPPE MANAGED FIXTURES"
# Fixture DocTypes the managed hooks.py block lists, in the order it lists them.
_MANAGED_FIXTURES = ("Client Script", "Workflow", "Workflow State", "Workflow Action Master")
_EXPORT_MAX_DOCTYPES = 50
_EXPORT_MAX_FILES = 200
_EXPORT_ROW_LIMIT = 1000
# Keys Frappe's `export_json` (`bench export-fixtures`) leaves out of fixture rows.
_FIXTURE_DROPPED_KEYS = frozenset({"modified_by", "creation", "owner", "idx", "lft", "rgt"})
_PROTECTED_APPS = {"frappe", "erpnext"}
# Keys that differ between a DocType record and its exported JSON for reasons other than drift.
_DOCTYPE_SYSTEM_KEYS = frozenset(
    {
        "name",
        "doctype",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "docstatus",
        "idx",
        "migration_hash",
        "field_order",
    }
)
_CHILD_SYSTEM_KEYS = frozenset(
    {
        "name",
        "doctype",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "docstatus",
        "idx",
        "parent",
        "parenttype",
        "parentfield",
    }
)
_APP_FILE_MAX_BYTES = 5_000_000
_DRIFT_MAX_ITEMS = 100
_DRIFT_MAX_TEXT = 500
_DRIFT_PAGE_LIMIT = 200
_DRIFT_SITE_LIMIT = 5000
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _is_local_target(url: str) -> bool:
    hostname = urlparse(url).hostname or ""
    return hostname in _LOCAL_HOSTS or hostname.endswith(".localhost")


def _insecure_http_allowed() -> bool:
    return os.environ.get("FRAPPE_ALLOW_INSECURE_HTTP") == "YES"


def _timeout() -> float:
    raw = os.environ.get("FRAPPE_TIMEOUT", "30")
    try:
        timeout = float(raw)
    except ValueError:
        timeout = math.nan
    if not math.isfinite(timeout) or timeout <= 0:
        raise RuntimeError(f"FRAPPE_TIMEOUT must be a positive number of seconds, got {raw!r}")
    return timeout


_SITE_LABEL_MAX = 64


def _site_label() -> str | None:
    """Return FRAPPE_SITE_LABEL, a name for the site that is shown but never used for routing."""
    raw = os.environ.get("FRAPPE_SITE_LABEL") or ""
    if not raw:
        return None
    if len(raw) > _SITE_LABEL_MAX or not _SAFE_NAME.fullmatch(raw):
        raise RuntimeError(
            f"FRAPPE_SITE_LABEL must be 1-{_SITE_LABEL_MAX} characters: letters, digits, "
            "spaces, '_', '.', or '-', starting with a letter or digit"
        )
    return raw


def _remote_target() -> bool:
    """True when FRAPPE_URL is set and isn't a loopback or .localhost target."""
    url = os.environ.get("FRAPPE_URL", "")
    return bool(url) and not _is_local_target(url)


# A site that is down fails in seconds; reads keep the full FRAPPE_TIMEOUT.
_CONNECT_TIMEOUT = 5.0


def _connect_timeout(timeout: float) -> float:
    return min(timeout, _CONNECT_TIMEOUT)


_DNS_ERROR_TEXT = (
    "name or service not known",
    "name resolution",
    "nodename nor servname",
    "getaddrinfo failed",
    "no address associated",
)


def _error_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain and len(chain) < 10:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _transport_failure(error: httpx.TransportError) -> str:
    """Name what went wrong on the network, without the error text (it may echo the URL)."""
    try:
        timeout = _timeout()
    except RuntimeError:
        timeout = None
    if isinstance(error, httpx.ConnectTimeout):
        seconds = _connect_timeout(timeout) if timeout else _CONNECT_TIMEOUT
        return f"no connection within {seconds:g} s"
    if isinstance(error, httpx.TimeoutException):
        return f"no answer within {timeout:g} s" if timeout else "the request timed out"
    chain = _error_chain(error)
    text = " ".join(str(item) for item in chain).lower()
    if any(isinstance(item, socket.gaierror) for item in chain) or any(
        marker in text for marker in _DNS_ERROR_TEXT
    ):
        return "the host name could not be resolved"
    if any(isinstance(item, ConnectionRefusedError) for item in chain) or (
        "connection refused" in text or "errno 111" in text or "errno 61" in text
    ):
        return "connection refused"
    if isinstance(error, httpx.RemoteProtocolError):
        return "the connection was closed before Frappe answered"
    if isinstance(error, httpx.ConnectError):
        return "could not connect"
    return f"network error ({type(error).__name__})"


def _unreachable_message(error: httpx.TransportError, *, suggest_check: bool = True) -> str:
    """Say which site could not be reached, how it failed, and what to check next."""
    url = os.environ.get("FRAPPE_URL", "")
    site = _display_url(url) if url else "the site (FRAPPE_URL is not set)"
    message = f"Cannot reach the Frappe site at {site}: {_transport_failure(error)}."
    port_hint = _local_port_hint(url)
    if port_hint:
        message += f" Port check: {port_hint}"
    if suggest_check:
        return f"{message} Is the site running? Run `mcp-frappe check` to test the connection."
    return f"{message} Is the site running?"


def _local_port_hint(url: str) -> str | None:
    """Explain common Aspire/Docker port mistakes without probing other services."""
    if not _is_local_target(url):
        return None
    try:
        port = urlparse(url).port
    except ValueError:
        return None
    if port == 8000:
        return (
            "If Frappe runs in Aspire or Docker, 8000 may be the container's internal HTTP "
            "port; use the host-published `http` endpoint shown by `aspire describe frappe`."
        )
    if port in {9000, 9088}:
        return (
            f"Port {port} is commonly used for Frappe Socket.IO, but host-published ports "
            "vary. Check `aspire describe frappe`; if this is its `socketio` endpoint, use "
            "the host-published `http` endpoint for the REST API."
        )
    return None


def _meta_ttl() -> float:
    raw = os.environ.get("FRAPPE_META_TTL", "300")
    try:
        ttl = float(raw)
    except ValueError:
        ttl = math.nan
    if not math.isfinite(ttl) or ttl < 0:
        raise RuntimeError(
            f"FRAPPE_META_TTL must be zero or a positive number of seconds, got {raw!r}"
        )
    return ttl


def _password_auth() -> bool:
    return not (os.environ.get("FRAPPE_API_KEY") or os.environ.get("FRAPPE_API_SECRET"))


def _client() -> httpx.Client:
    global _session
    with _session_lock:
        if _session is not None:
            return _session

        base_url = os.environ.get("FRAPPE_URL", "").rstrip("/")
        if not base_url:
            raise RuntimeError("Set FRAPPE_URL to the target site's base URL")
        timeout = _timeout()
        headers = {"Accept": "application/json"}
        site = os.environ.get("FRAPPE_SITE")
        if site:
            headers["Host"] = site
        key, secret = os.environ.get("FRAPPE_API_KEY"), os.environ.get("FRAPPE_API_SECRET")
        username = os.environ.get("FRAPPE_USERNAME", "Administrator")
        password = os.environ.get("FRAPPE_PASSWORD")
        if key or secret:
            if not key or not secret:
                raise RuntimeError("Set both FRAPPE_API_KEY and FRAPPE_API_SECRET")
            headers["Authorization"] = f"token {key}:{secret}"
        elif not password:
            raise RuntimeError("Set an API key/secret or FRAPPE_PASSWORD")
        if (
            urlparse(base_url).scheme != "https"
            and not _is_local_target(base_url)
            and not _insecure_http_allowed()
        ):
            raise RuntimeError(
                "FRAPPE_URL must use HTTPS unless it is a loopback or .localhost target; "
                "set FRAPPE_ALLOW_INSECURE_HTTP=YES only for a trusted development network"
            )

        client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=_connect_timeout(timeout)),
            follow_redirects=False,
        )
        if _password_auth():
            try:
                _check(client.post("/api/method/login", data={"usr": username, "pwd": password}))
            except BaseException:
                client.close()
                raise
        _session = client
        return client


def _acquire_client() -> httpx.Client:
    with _session_lock:
        client = _client()
        key = id(client)
        _session_active[key] = _session_active.get(key, 0) + 1
        return client


def _release_client(client: httpx.Client) -> None:
    key = id(client)
    with _session_lock:
        active = _session_active.get(key, 0)
        if active > 1:
            _session_active[key] = active - 1
            return
        _session_active.pop(key, None)
        retired = _retired_sessions.pop(key, None)
        if retired is not None:
            retired.close()


def _reset_session(client: httpx.Client | None = None) -> None:
    global _session
    with _session_lock:
        if client is not None and _session is not client:
            return
        previous = _session
        _session = None
        if previous is None:
            return
        key = id(previous)
        if _session_active.get(key, 0):
            _retired_sessions[key] = previous
        else:
            previous.close()


def _session_expired(client: httpx.Client) -> bool:
    try:
        user = _check(client.get("/api/method/frappe.auth.get_logged_user"))
    except RuntimeError:
        return True
    return user == "Guest"


class FrappeError(RuntimeError):
    """An error response from Frappe, with its message decoded to plain text.

    `status` is the HTTP status (None for an application error inside a 200 response),
    `exc_type` names the server-side exception class, and `message` is the readable text.
    Frappe's traceback (`exc`) is never kept.
    """

    def __init__(
        self, message: str, exc_type: str | None = None, *, status: int | None = None
    ) -> None:
        super().__init__(message)
        self.exc_type = exc_type
        self.status = status
        self.message = message


_ERROR_MAX_CHARS = 2000
_ERROR_HINTS = {
    "PermissionError": (
        "The connected user lacks this permission. Report the missing access to the user; "
        "do not retry the change another way."
    ),
    "DoesNotExistError": (
        "The record or DocType does not exist; check the name with list_doctypes, "
        "search_records, or list_documents."
    ),
    "MandatoryError": (
        "Required fields are empty; map the labels to fieldnames with get_doctype_schema "
        "and add them to the values."
    ),
    "LinkValidationError": (
        "A Link field names a record that does not exist; find a valid one with "
        "search_records on the linked DocType."
    ),
    "DuplicateEntryError": (
        "A record with this name or unique value already exists; read it instead, or pick "
        "another value."
    ),
    "TimestampMismatchError": (
        "The record changed since the preview; call the tool again without confirm for a "
        "fresh preview."
    ),
    "ValidationError": "Frappe rejected the values; fix what the message names and preview again.",
}
_HTML_TAG = re.compile(r"<[^>]*>")
_MISSING_VALUE = re.compile(r"Value missing for(?: [^:\n]+)?: ([^\n]+)")
# The last traceback line Frappe sends as `exception`: `frappe.exceptions.SomeError: text`.
_EXCEPTION_PREFIX = re.compile(r"^[A-Za-z_][\w.]*(?:Error|Exception)\b:?\s*")


def _plain_text(value: Any) -> str:
    """Turn one Frappe message (HTML allowed) into collapsed plain text."""
    text = re.sub(r"<br\s*/?>", "\n", str(value), flags=re.I)
    text = html.unescape(_HTML_TAG.sub("", text))
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _server_messages(raw: Any) -> list[str]:
    """Decode `_server_messages`: a JSON list of JSON objects, each with an HTML `message`."""
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        items = raw
    if not isinstance(items, list):
        items = [items]
    messages = []
    for item in items:
        if isinstance(item, str):
            try:
                item = json.loads(item)
            except ValueError:
                pass
        if isinstance(item, dict):
            item = item.get("message")
        text = _plain_text(item) if item else ""
        if text and text not in messages:
            messages.append(text)
    return messages


def _error_text(payload: Any) -> str:
    """Pick readable text from an error payload; never the traceback in `exc`."""
    if not isinstance(payload, dict):
        return ""
    messages = _server_messages(payload.get("_server_messages") or [])
    if messages:
        return "\n".join(messages)
    for key in ("_error_message", "exception", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            text = _plain_text(value)
            return _EXCEPTION_PREFIX.sub("", text) if key == "exception" else text
    return ""


def _frappe_error(status: int | None, payload: Any) -> FrappeError:
    exc_type = payload.get("exc_type") if isinstance(payload, dict) else None
    exc_type = exc_type if isinstance(exc_type, str) and exc_type else None
    text = _error_text(payload) or (
        "request failed" if status is not None else "Frappe returned an application error"
    )
    head = "Frappe" if status is None else f"Frappe HTTP {status}"
    hint = _ERROR_HINTS.get(exc_type or "")
    if exc_type == "MandatoryError":
        missing = _MISSING_VALUE.findall(text)
        if missing:
            hint = f"Missing: {', '.join(missing)}. {hint}"
    suffix = f"\nHint: {hint}" if hint else ""
    body = f"{head}{f' {exc_type}' if exc_type else ''}: {text}"
    room = _ERROR_MAX_CHARS - len(suffix)
    if len(body) > room:
        body = body[: room - 15].rstrip() + " ...[truncated]"
    return FrappeError(body + suffix, exc_type, status=status)


def _check(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.is_error:
        raise _frappe_error(response.status_code, payload)
    if isinstance(payload, dict) and payload.get("exc"):
        raise _frappe_error(None, payload)
    if isinstance(payload, dict):
        if "data" in payload:
            return payload["data"]
        if "message" in payload:
            return payload["message"]
    return payload


def _request(method: str, path: str, **kwargs: Any) -> Any:
    client = _acquire_client()
    try:
        response = client.request(method, path, **kwargs)
        if response.status_code in (401, 403) and _password_auth() and _session_expired(client):
            # The expired sid made Frappe handle this request as Guest and reject it before it ran,
            # so retrying it once after a fresh login is safe even for a POST. Other in-flight
            # requests can finish on the retired client before its connection pool is closed.
            _reset_session(client)
            retry_client = _acquire_client()
            try:
                response = retry_client.request(method, path, **kwargs)
            finally:
                _release_client(retry_client)
        return _check(response)
    finally:
        _release_client(client)


def _resource(doctype: str, name: str | None = None) -> str:
    path = f"/api/resource/{quote(doctype, safe='')}"
    return path if name is None else f"{path}/{quote(name, safe='')}"


def _filters(filters: dict[str, Any] | list[list[Any]] | None) -> str:
    return json.dumps(filters or {}, separators=(",", ":"))


# DocType name -> (monotonic time fetched, bundle). One process serves one Frappe user, so
# per-user parts of the metadata (masked fields) stay correct.
_meta_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _meta_bundle(doctype: str) -> dict[str, Any]:
    """Return `{"doctype": meta, "children": {child_doctype: meta}, "workflow": name | None}`.

    The bundle comes from Frappe's form loader; `workflow` names the DocType's active Workflow.

    `getdoctype` returns `frappe.get_meta`, which already holds Custom Fields, Property
    Setters, and Frappe's field order, for any logged-in user; record permissions are still
    checked when records are read or written. Form assets (`__js`, print formats, templates)
    are dropped. Bundles are cached for `FRAPPE_META_TTL` seconds.
    """
    if not isinstance(doctype, str) or not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")
    ttl = _meta_ttl()
    cached = _meta_cache.get(doctype)
    if ttl and cached and time.monotonic() - cached[0] < ttl:
        return copy.deepcopy(cached[1])
    fetched_at = time.monotonic()
    payload = _request("GET", _GETDOCTYPE, params={"doctype": doctype})
    docs = payload.get("docs") if isinstance(payload, dict) else None
    if not isinstance(docs, list) or not docs or any(not isinstance(doc, dict) for doc in docs):
        raise RuntimeError("Unexpected getdoctype response")
    # FormMeta.load_workflows puts the active Workflow first in `__workflow_docs`.
    workflow = next(
        (
            row.get("name")
            for row in docs[0].get("__workflow_docs") or []
            if isinstance(row, dict) and row.get("doctype") == "Workflow"
        ),
        None,
    )
    main, *children = (
        {key: value for key, value in doc.items() if not key.startswith("__")} for doc in docs
    )
    bundle = {
        "doctype": main,
        "children": {child.get("name"): child for child in children},
        "workflow": workflow,
    }
    if ttl:
        _meta_cache[doctype] = (fetched_at, bundle)
    return copy.deepcopy(bundle)


def _forget_meta(doctype: str | None = None) -> None:
    """Drop cached metadata for `doctype` and every bundle that holds it as a child table."""
    if doctype is None:
        _meta_cache.clear()
        return
    for key, (_, bundle) in list(_meta_cache.items()):
        if key == doctype or doctype in bundle["children"]:
            _meta_cache.pop(key, None)


def _schema_fieldnames(doctype: str) -> frozenset[str]:
    """Return fieldnames from the connected site's DocType metadata, never a release template."""
    try:
        fields = _meta_bundle(doctype)["doctype"].get("fields") or []
        return frozenset(
            row["fieldname"]
            for row in fields
            if isinstance(row, dict) and isinstance(row.get("fieldname"), str)
        )
    finally:
        _forget_meta(doctype)


def _site_fieldtypes() -> frozenset[str]:
    """Read the fieldtype choices that this Frappe site actually exposes for DocField."""
    try:
        fields = _meta_bundle("DocField")["doctype"].get("fields") or []
        row = next(
            (
                item
                for item in fields
                if isinstance(item, dict) and item.get("fieldname") == "fieldtype"
            ),
            None,
        )
        options = row.get("options") if row else None
        if isinstance(options, str):
            supported = frozenset(item.strip() for item in options.splitlines() if item.strip())
            if supported:
                return supported
    finally:
        _forget_meta("DocField")
    raise RuntimeError("Frappe did not expose DocField fieldtype choices; refusing to guess")


def _forget_meta_after_write(doctype: str) -> None:
    # Saving a Workflow can add its state field to the DocType as a Custom Field.
    if doctype in _SCHEMA_DOCTYPES or doctype == "Workflow":
        _forget_meta()


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _schema_fields(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["fieldname"]: row for row in schema.get("fields", []) if row.get("fieldname")}


def _assert_readable_fields(
    schema: dict[str, Any], fields: Any, *, child_table: bool = False
) -> None:
    """Allow DocType fields plus the standard columns, and parent columns on child tables."""
    readable = set(_schema_fields(schema)) | _STANDARD_COLUMNS
    if child_table or schema.get("istable"):
        readable |= _CHILD_COLUMNS
    for field in fields:
        if not isinstance(field, str) or field not in readable:
            raise ValueError(f"Unknown field for {schema.get('name')}: {field}")


def _assert_writable_fields(
    schema: dict[str, Any], values: dict[str, Any], *, allow_name: bool = False
) -> None:
    """Allow only DocType fields that are not reserved columns or read-only."""
    if not isinstance(values, dict) or not values:
        raise ValueError("values must be a non-empty object")
    fields = _schema_fields(schema)
    for key in values:
        if key == "name" and allow_name:
            continue
        if key in _RESERVED_FIELDS or key not in fields:
            raise ValueError(f"Field cannot be written: {key}")
        if fields[key].get("read_only"):
            raise ValueError(f"Read-only field cannot be written: {key}")


def _assert_record_doctype(doctype: Any, action: Literal["create", "update", "delete"]) -> None:
    """Refuse schema, configuration, and code DocTypes in the record tools, naming the tool."""
    if not isinstance(doctype, str):
        return  # the name check that follows rejects it
    tool = f"{action}_document"
    if doctype in _CODE_DOCTYPES:
        raise ValueError(
            f"{doctype} records hold code that Frappe runs (Python, Jinja, JavaScript, CSS, or "
            f"a method path); {tool} never writes or deletes them. Change them in Desk or in "
            "your app's files."
        )
    redirects = _RECORD_TOOL_REDIRECTS.get(doctype, {})
    hint = redirects.get(action) or redirects.get("*")
    if hint:
        raise ValueError(f"{tool} does not {action} {doctype} records: {hint}.")


def _assert_not_code(doctype: str, fieldname: str, row: dict[str, Any], where: str = "") -> None:
    if fieldname in _TEMPLATE_FIELDS.get(doctype, ()):
        raise ValueError(
            f"{doctype}.{fieldname}{where} is rendered as a Jinja template; the record tools "
            "don't write templates. Set it in Desk."
        )
    if row.get("fieldtype") == "Code" and (doctype, fieldname) not in _PLAIN_CODE_FIELDS:
        language = f" ({row['options']})" if row.get("options") else ""
        raise ValueError(
            f"{doctype}.{fieldname} is a Code field{language}{where}, which Frappe may run as "
            "code; the record tools don't write Code fields. Set it in Desk or in your app's "
            "files."
        )


def _assert_no_code_fields(bundle: dict[str, Any], values: dict[str, Any]) -> None:
    """Refuse values for `Code` and template fields, child-table rows included.

    The field types come from `getdoctype`, so Custom Fields and Property Setters count.
    """
    schema = bundle["doctype"]
    doctype = str(schema.get("name") or "")
    fields = _schema_fields(schema)
    for fieldname, value in values.items():
        row = fields.get(fieldname)
        if row is None:
            continue
        _assert_not_code(doctype, fieldname, row)
        if row.get("fieldtype") not in _TABLE_FIELDTYPES or not isinstance(value, list):
            continue
        child = bundle["children"].get(row.get("options"))
        if child is None:
            continue
        child_fields = _schema_fields(child)
        for index, item in enumerate(value, 1):
            if not isinstance(item, dict):
                continue
            for child_field in item:
                child_row = child_fields.get(child_field)
                if child_row is not None:
                    _assert_not_code(
                        str(child.get("name") or row.get("options")),
                        child_field,
                        child_row,
                        f", in {doctype}.{fieldname} row {index}",
                    )


def _assert_operator(operator: Any) -> None:
    if not isinstance(operator, str) or operator.strip().lower() not in _FILTER_OPERATORS:
        raise ValueError(
            f"Unsupported filter operator {operator!r}; use one of: {', '.join(_FILTER_OPERATORS)}"
        )


def _assert_filters(bundle: dict[str, Any], filters: dict[str, Any] | list[Any] | None) -> None:
    """Check every Frappe filter shape against a `_meta_bundle` DocType and its child tables.

    Accepted: `{field: value}`, `{field: [operator, value]}`, and a list of
    `[field, operator, value]` or `[doctype, field, operator, value]` conditions, where
    `doctype` is this DocType or the `options` of one of its Table fields.
    """
    if not filters:
        return
    schema, children = bundle["doctype"], bundle["children"]
    if isinstance(filters, dict):
        for field, value in filters.items():
            _assert_readable_fields(schema, [field])
            if isinstance(value, dict) or (isinstance(value, list | tuple) and len(value) != 2):
                raise ValueError(f"Filter for {field} must be a value or [operator, value]")
            if isinstance(value, list | tuple):
                _assert_operator(value[0])
        return
    if not isinstance(filters, list | tuple):
        raise ValueError("filters must be an object or a list of conditions")

    doctype = schema.get("name")
    for condition in filters:
        if not isinstance(condition, list | tuple) or len(condition) not in (3, 4):
            raise ValueError(
                "Each list filter must be [field, operator, value] "
                "or [doctype, field, operator, value]"
            )
        if len(condition) == 3:
            field, operator, _ = condition
            _assert_readable_fields(schema, [field])
        else:
            target, field, operator, _ = condition
            if target == doctype:
                _assert_readable_fields(schema, [field])
            elif isinstance(target, str) and target in children:
                _assert_readable_fields(children[target], [field], child_table=True)
            else:
                choices = ", ".join(sorted(children)) or "none"
                raise ValueError(
                    f"{target} is not a child table of {doctype}; child tables: {choices}"
                )
        _assert_operator(operator)


def _assert_value_column(
    schema: dict[str, Any], fieldname: Any, *, child_table: bool = False
) -> dict[str, Any] | None:
    """Check a report or sort column is a stored field or standard column; return its DocField."""
    _assert_readable_fields(schema, [fieldname], child_table=child_table)
    row = _schema_fields(schema).get(fieldname)
    fieldtype = (row or {}).get("fieldtype")
    if fieldtype in _LAYOUT_FIELDTYPES or fieldtype in _TABLE_FIELDTYPES:
        raise ValueError(
            f"{fieldname} is a {fieldtype} field of {schema.get('name')}, not a column"
        )
    return row


def _order_terms(schema: dict[str, Any], order_by: Any) -> list[tuple[str, str]]:
    """Parse `field [asc|desc], ...` into checked `(fieldname, direction)` pairs.

    Each field must be a stored field or standard column of the DocType, so no SQL passes;
    callers rebuild the sort string from the pairs.
    """
    rule = f"order_by must be '<fieldname> [asc|desc]', up to {_ORDER_MAX_TERMS} joined by commas"
    if not isinstance(order_by, str) or not order_by.strip():
        raise ValueError(rule)
    terms = order_by.split(",")
    if len(terms) > _ORDER_MAX_TERMS:
        raise ValueError(rule)
    pairs = []
    for term in terms:
        match = _ORDER_TERM.fullmatch(term)
        if not match:
            raise ValueError(f"{rule}; got {term.strip()!r}")
        _assert_value_column(schema, match[1])
        pairs.append((match[1], (match[2] or "asc").lower()))
    return pairs


def _expected_modified(doctype: str, name: str, expected: str | None) -> dict[str, Any]:
    doc = _request("GET", _resource(doctype, name))
    if not expected or str(doc.get("modified")) != expected:
        raise ValueError(_STALE_DOCUMENT)
    return doc


def _app_root(app: str) -> tuple[Path, Path]:
    """Resolve `FRAPPE_APPS_PATH/<app>` and its Python package, refusing escapes."""
    root_value = os.environ.get("FRAPPE_APPS_PATH")
    if not root_value:
        raise RuntimeError("Set FRAPPE_APPS_PATH to the local Bench apps directory for exports")
    if not isinstance(app, str) or not _SAFE_NAME.fullmatch(app):
        raise ValueError("Invalid app or module name")
    root = Path(root_value).expanduser().resolve(strict=True)
    try:
        app_path = (root / app).resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError("Selected app does not exist below FRAPPE_APPS_PATH") from error
    if not app_path.is_relative_to(root):
        raise ValueError("App or module path escapes FRAPPE_APPS_PATH")
    try:
        package_path = (app_path / app).resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError("Frappe Python package directory does not exist below the app") from error
    if not package_path.is_relative_to(app_path):
        raise ValueError("App package path escapes FRAPPE_APPS_PATH")
    if not app_path.is_dir() or not package_path.is_dir():
        raise ValueError("App module must exist and be writable")
    return app_path, package_path


def _module_dir(package_path: Path, module: str) -> Path | None:
    """Resolve the folder Frappe uses for a module (`scrub(module)`); None when it is missing."""
    if not isinstance(module, str) or not _SAFE_NAME.fullmatch(module):
        raise ValueError("Invalid app or module name")
    try:
        module_path = (package_path / _scrub(module)).resolve(strict=True)
    except FileNotFoundError:
        return None
    if not module_path.is_relative_to(package_path):
        raise ValueError("App or module path escapes FRAPPE_APPS_PATH")
    if not module_path.is_dir():
        raise ValueError("App module must exist and be writable")
    return module_path


def _app_path(app: str, module: str) -> tuple[Path, Path]:
    if not isinstance(module, str) or not _SAFE_NAME.fullmatch(module):
        raise ValueError("Invalid app or module name")
    app_path, package_path = _app_root(app)
    module_path = _module_dir(package_path, module)
    if module_path is None:
        raise ValueError("Selected module directory does not exist")
    if not os.access(module_path, os.W_OK):
        raise ValueError("App module must exist and be writable")
    return app_path, module_path


def _app_file(base: Path, *parts: str) -> Path | None:
    """Resolve a file below a resolved app folder for reading; None when it does not exist."""
    try:
        path = base.joinpath(*parts).resolve(strict=True)
    except FileNotFoundError:
        return None
    if not path.is_relative_to(base):
        raise ValueError(f"{base.joinpath(*parts)} escapes the app module folder")
    if not path.is_file():
        raise ValueError(f"{path} is not a file")
    if path.stat().st_size > _APP_FILE_MAX_BYTES:
        raise ValueError(f"{path} is larger than {_APP_FILE_MAX_BYTES} bytes")
    return path


def _read_app_json(path: Path) -> tuple[Any, str]:
    """Return a file's JSON and the MD5 of its bytes, which is what `bench migrate` compares."""
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise ValueError(f"{path} is not valid JSON") from error
    return value, hashlib.md5(raw, usedforsecurity=False).hexdigest()


def _assert_app_module(app: str, module: str) -> tuple[Path, Path]:
    app_path, module_path = _app_path(app, module)
    _assert_remote_module(app, module)
    return app_path, module_path


def _assert_remote_module(app: str | None, module: str) -> dict[str, Any]:
    """Check the Module Def exists on the site and, when `app` is given, belongs to it."""
    if not _SAFE_NAME.fullmatch(module) or (app is not None and not _SAFE_NAME.fullmatch(app)):
        raise ValueError("Invalid app or module name")
    rows = _request(
        "GET",
        _resource("Module Def"),
        params={
            "fields": '["name","app_name","custom"]',
            "filters": _filters({"name": module}),
            "limit_page_length": 2,
        },
    )
    if len(rows) != 1:
        raise ValueError(f"Unknown module on the target site: {module}")
    if app is not None and rows[0].get("app_name") != app:
        raise ValueError(f"Module {module} does not belong to app {app} on the target site")
    return rows[0]


def _assert_optional_module(app: str | None, module: str | None) -> None:
    """Validate the optional app/module pair of a site customization; no local files needed."""
    if module is None:
        if app is not None:
            raise ValueError("app is only checked together with module; pass module too")
        return
    _assert_remote_module(app, module)


def _assert_writable_app(app: str) -> None:
    """Keep generated source changes out of Frappe and ERPNext core checkouts."""
    if app.casefold() in _PROTECTED_APPS:
        raise ValueError(f"Refusing to write generated files into protected app: {app}")


def _doctype_exists(name: str) -> bool:
    rows = _request(
        "GET",
        _resource("DocType"),
        params={
            "fields": '["name"]',
            "filters": _filters({"name": name}),
            "limit_page_length": 1,
        },
    )
    return bool(rows)


def _wait_for_doctype(name: str, generated_files: list[Path], timeout: float = 60.0) -> bool:
    """Wait for Frappe's reloader and the in-flight request to settle before reconciling."""
    deadline = time.monotonic() + timeout
    online_since: float | None = None
    files_changed_at = time.monotonic()
    last_snapshot: tuple[tuple[str, int, int] | None, ...] | None = None
    while time.monotonic() < deadline:
        try:
            exists = _doctype_exists(name)
        except httpx.TransportError:
            online_since = None
            time.sleep(0.5)
            continue
        if exists:
            return True
        now = time.monotonic()
        if online_since is None:
            online_since = now
        snapshot = tuple(
            (str(path), path.stat().st_size, path.stat().st_mtime_ns) if path.is_file() else None
            for path in generated_files
        )
        if snapshot != last_snapshot:
            last_snapshot = snapshot
            files_changed_at = now
        # The Frappe dev worker can take several seconds to finish scaffolding
        # after Werkzeug has already begun reloading. Do not remove files or
        # replay the POST until both the site and its generated files are quiet.
        if now - online_since >= 10 and now - files_changed_at >= 10:
            return False
        time.sleep(0.5)
    return False


def _remove_partial_doctype_tree(doctype_path: Path, generated_files: list[Path]) -> None:
    """Remove a newly-created, uncommitted DocType scaffold after the reloader settles."""
    if doctype_path.exists():
        expected = {path.name for path in generated_files} | {"__init__.py"}
        for path in doctype_path.rglob("*"):
            if path.is_dir():
                if path.name != "__pycache__":
                    raise RuntimeError(
                        "Refusing to remove unexpected files from the DocType directory"
                    )
                continue
            if path.name in expected or (
                path.parent.name == "__pycache__" and path.suffix == ".pyc"
            ):
                continue
            raise RuntimeError("Refusing to remove unexpected files from the DocType directory")
        shutil.rmtree(doctype_path)


def _app_publisher(app_path: Path) -> str:
    hooks = app_path / app_path.name / "hooks.py"
    if not hooks.is_file():
        raise ValueError(
            "Target app hooks.py is missing; cannot prepare native DocType scaffolding"
        )
    tree = ast.parse(hooks.read_text(encoding="utf-8"), filename=str(hooks))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "app_publisher" for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str):
                return value
            if isinstance(value, (list, tuple)) and value and isinstance(value[0], str):
                return value[0]
    raise ValueError(
        "Target app hooks.py must define a literal app_publisher for native scaffolding"
    )


def _prepare_native_doctype_scaffolding(
    app_path: Path,
    module_path: Path,
    doctype_path: Path,
    name: str,
    *,
    is_child_table: bool,
    is_tree: bool,
) -> None:
    """Stage Frappe's own boilerplates before the metadata request triggers file watchers."""
    frappe_source = app_path.parent / "frappe" / "frappe"
    templates = frappe_source / "core" / "doctype" / "doctype" / "boilerplate"
    required = [templates / "controller._py"]
    if not is_child_table:
        required.extend([templates / "test_controller._py", templates / "controller.js"])
    if any(not path.is_file() for path in required):
        raise ValueError(
            "FRAPPE_APPS_PATH must include the installed Frappe source "
            "to generate native DocType files"
        )

    for directory in (module_path, module_path / "doctype", doctype_path):
        directory.mkdir(parents=True, exist_ok=True)
        init_file = directory / "__init__.py"
        if not init_file.exists():
            _atomic_write(init_file, "")

    class_name = name.replace(" ", "").replace("-", "")
    base_class = "NestedSet" if is_tree else "Document"
    base_class_import = (
        "from frappe.utils.nestedset import NestedSet"
        if is_tree
        else "from frappe.model.document import Document"
    )
    values = {
        "year": time.localtime().tm_year,
        "app_publisher": _app_publisher(app_path),
        "classname": class_name,
        "doctype": name,
        "base_class_import": base_class_import,
        "base_class": base_class,
        "custom_controller": "pass",
    }
    controller_file = _scrub(name)
    files = {
        doctype_path / f"{controller_file}.py": templates / "controller._py",
    }
    if not is_child_table:
        files[doctype_path / f"test_{controller_file}.py"] = templates / "test_controller._py"
        files[doctype_path / f"{controller_file}.js"] = templates / "controller.js"
    for destination, template in files.items():
        if destination.exists():
            raise ValueError("Refusing to overwrite an existing DocType scaffold")
        _atomic_write(destination, template.read_text(encoding="utf-8").format(**values))


def _wait_for_server_ready(timeout: float = 45.0, settle_seconds: float = 5.0) -> None:
    """Wait until the target site is reachable and stable after generated Python files."""
    deadline = time.monotonic() + timeout
    online_since: float | None = None
    while time.monotonic() < deadline:
        try:
            _request("GET", "/api/method/frappe.auth.get_logged_user")
        except (httpx.TransportError, RuntimeError):
            online_since = None
        else:
            now = time.monotonic()
            if online_since is None:
                online_since = now
            elif now - online_since >= settle_seconds:
                return
        time.sleep(0.5)
    raise RuntimeError("Frappe did not return to a stable state after preparing DocType files")


def _wait_for_document_deleted(doctype: str, name: str, timeout: float = 45.0) -> bool:
    """Reconcile a delete whose response was cut off by a Frappe worker reload."""
    deadline = time.monotonic() + timeout
    online_since: float | None = None
    absent_since: float | None = None
    while time.monotonic() < deadline:
        try:
            rows = _request(
                "GET",
                _resource(doctype),
                params={
                    "fields": '["name"]',
                    "filters": _filters({"name": name}),
                    "limit_page_length": 1,
                },
            )
        except (httpx.TransportError, RuntimeError):
            online_since = None
            absent_since = None
            time.sleep(0.5)
            continue
        now = time.monotonic()
        if online_since is None:
            online_since = now
        if rows:
            absent_since = None
        elif absent_since is None:
            absent_since = now
        elif now - absent_since >= 1.5 and now - online_since >= 1.5:
            return True
        time.sleep(0.5)
    return False


def _create_doctype_request(
    name: str, payload: dict[str, Any], generated_files: list[Path]
) -> bool:
    """Create a DocType and reconcile an uncertain response without blind replay."""
    try:
        _request("POST", _resource("DocType"), json={"data": payload})
        return False
    except httpx.TransportError as error:
        if _wait_for_doctype(name, generated_files):
            return True
        raise RuntimeError(
            "Frappe restarted during DocType creation and no DocType record was found "
            "after the request settled. The MCP removed only its temporary scaffold; retry is safe."
        ) from error


def _build_doctype_payload(
    module: str,
    name: str,
    fields: list[dict[str, Any]],
    *,
    is_submittable: bool = False,
    is_child_table: bool = False,
    is_single: bool = False,
    is_tree: bool = False,
    is_custom: bool = False,
    properties: dict[str, Any] | None = None,
    permissions: list[dict[str, Any]] | None = None,
    supported_fieldtypes: frozenset[str] | None = None,
    supported_field_properties: frozenset[str] | None = None,
    supported_doctype_properties: frozenset[str] | None = None,
) -> dict[str, Any]:
    if not _SAFE_NAME.fullmatch(name) or not fields:
        raise ValueError("A valid DocType name and at least one field are required")
    if is_child_table and is_single:
        raise ValueError("A DocType cannot be both a child table and a Single DocType")
    if is_custom and properties and properties.get("is_virtual"):
        raise ValueError("Frappe does not allow custom Virtual DocTypes")
    if any(not isinstance(field, dict) for field in fields):
        raise ValueError("Each DocType field must be an object")
    fieldnames = [field.get("fieldname") for field in fields if field.get("fieldname")]
    if len(fieldnames) != len(set(fieldnames)):
        raise ValueError("DocType fieldnames must be unique")
    if properties is not None and not isinstance(properties, dict):
        raise ValueError("properties must be an object")
    properties = properties or {}
    if supported_doctype_properties is None:
        supported_doctype_properties = _DOCTYPE_PROPERTIES
    if supported_fieldtypes is None:
        supported_fieldtypes = _FIELDTYPES
    if supported_field_properties is None:
        supported_field_properties = _DOCFIELD_PROPERTIES
    unsupported = set(properties) - supported_doctype_properties
    if unsupported:
        raise ValueError(f"Unsupported DocType properties: {', '.join(sorted(unsupported))}")
    if permissions is not None and (
        not isinstance(permissions, list)
        or any(not isinstance(permission, dict) for permission in permissions)
    ):
        raise ValueError("permissions must be a list of objects")

    for index, field in enumerate(fields):
        fieldtype = field.get("fieldtype")
        if fieldtype not in supported_fieldtypes:
            raise ValueError(f"fields[{index}]: unsupported fieldtype {fieldtype!r}")
        unsupported_fields = set(field) - supported_field_properties - {"fieldname"}
        if unsupported_fields:
            raise ValueError(
                f"fields[{index}]: unsupported DocField properties: "
                f"{', '.join(sorted(unsupported_fields))}"
            )

    payload = {
        "doctype": "DocType",
        "name": name,
        "module": module,
        "custom": int(is_custom),
        "istable": int(is_child_table),
        "issingle": int(is_single),
        "is_submittable": int(is_submittable),
        "is_tree": int(is_tree),
        "fields": fields,
        "permissions": permissions or [],
    }
    payload.update(properties)
    return payload


def _atomic_write(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    temp_dir = Path(tempfile.gettempdir())
    if os.stat(temp_dir).st_dev != os.stat(path.parent).st_dev:
        temp_dir = path.parent
    fd, temp_name = tempfile.mkstemp(prefix=".mcp-frappe-", dir=temp_dir)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _fixture_plan(app_path: Path, entries: dict[str, dict[str, list[str]]]) -> tuple[Path, str]:
    """Plan the managed `hooks.py` block that keeps exported records in the app's `fixtures`.

    `entries` maps a fixture DocType to `{filter_field: values}`; each becomes an `in` filter,
    merged with the values earlier exports put there. Only `_MANAGED_FIXTURES` need a hook
    entry: Custom Fields, Property Setters, and permissions go to the module's native `custom/`
    file, which `bench migrate` reads without one. Entries for other DocTypes left by older
    exports are dropped from the block.
    """
    hooks = app_path / app_path.name / "hooks.py"
    if not hooks.is_file() or not os.access(hooks, os.W_OK):
        raise ValueError("Target app hooks.py is missing or not writable")
    source = hooks.read_text(encoding="utf-8")
    begin, end = source.find(_HOOK_BEGIN), source.find(_HOOK_END)
    existing: list[dict[str, Any]] = []
    if begin >= 0 or end >= 0:
        if begin < 0 or end < begin or source[end + len(_HOOK_END) :].strip():
            raise ValueError("Managed fixture block is invalid; inspect hooks.py manually")
        block = ast.parse(source[begin + len(_HOOK_BEGIN) : end])
        assignment = next(
            (
                node
                for node in block.body
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "mcp_frappe_exported_fixtures"
                    for target in node.targets
                )
            ),
            None,
        )
        if assignment:
            existing = ast.literal_eval(assignment.value)
        source = source[:begin].rstrip()

    managed = []
    for doctype in _MANAGED_FIXTURES:
        previous = next(
            (row for row in existing if isinstance(row, dict) and row.get("dt") == doctype),
            None,
        )
        if previous is None and doctype not in entries:
            continue
        filters = [list(condition) for condition in previous.get("filters", [])] if previous else []
        for key, incoming in entries.get(doctype, {}).items():
            current = next(
                (condition for condition in filters if len(condition) == 3 and condition[0] == key),
                None,
            )
            if current and current[1] == "in":
                current[2] = sorted(set(current[2]) | set(incoming))
            else:
                filters.append([key, "in", sorted(incoming)])
        managed.append({"dt": doctype, "filters": filters})

    block_text = (
        f"\n\n{_HOOK_BEGIN}\n"
        f"mcp_frappe_exported_fixtures = {managed!r}\n"
        "fixtures = list(globals().get('fixtures', [])) + mcp_frappe_exported_fixtures\n"
        f"{_HOOK_END}\n"
    )
    return hooks, source + block_text


def _scrub(value: str) -> str:
    """Frappe's `frappe.scrub`, which names a DocType's customization and fixture files."""
    return value.replace(" ", "_").replace("-", "_").lower()


def _frappe_json(value: Any, *, ensure_ascii: bool = True) -> str:
    """Serialize like `frappe.as_json`, so exported files diff cleanly with Frappe's own."""
    return json.dumps(
        value, indent=1, sort_keys=True, separators=(",", ": "), ensure_ascii=ensure_ascii
    )


def _export_rows(
    doctype: str, filters: list[list[Any]], order_by: str = "name asc", **params: Any
) -> list[dict[str, Any]]:
    """Read every column of the matching rows, refusing to silently truncate a large export."""
    rows = _request(
        "GET",
        _resource(doctype),
        params={
            "fields": '["*"]',
            "filters": _filters(filters),
            "order_by": order_by,
            "limit_page_length": _EXPORT_ROW_LIMIT + 1,
            **params,
        },
    )
    if len(rows) > _EXPORT_ROW_LIMIT:
        raise ValueError(f"More than {_EXPORT_ROW_LIMIT} {doctype} rows; export fewer DocTypes")
    return rows


def _customized_doctypes(doctypes: list[str]) -> list[str]:
    """Return the DocTypes and, like Frappe's export, every table DocType they use."""
    ordered: list[str] = []
    pending = list(doctypes)
    while pending:
        doctype = pending.pop(0)
        if doctype in ordered:
            continue
        if len(ordered) >= _EXPORT_MAX_FILES:
            raise ValueError("Export covers too many DocTypes; select fewer")
        ordered.append(doctype)
        children = list(_meta_bundle(doctype)["children"])
        pending[:0] = [child for child in children if child not in ordered]
    return ordered


def _native_customization(
    doctype: str,
    module: str,
    *,
    with_permissions: bool,
    apply_module_filter: bool,
    sync_on_migrate: bool,
) -> dict[str, Any] | None:
    """Build what `frappe.modules.utils.export_customizations` writes for one DocType.

    Returns None when there are no Custom Fields, Property Setters, or (when asked)
    Custom DocPerms, because Frappe skips the file in that case too.
    """
    module_filter = [["module", "=", module]] if apply_module_filter else []
    custom = {
        "custom_fields": _export_rows("Custom Field", [["dt", "=", doctype], *module_filter]),
        "property_setters": _export_rows(
            "Property Setter", [["doc_type", "=", doctype], *module_filter]
        ),
        "custom_perms": [],
        # DocType Link is a child table, so Frappe only lists it under its parent DocType.
        "links": _export_rows("DocType Link", [["parent", "=", doctype]], parent="DocType"),
        "doctype": doctype,
        "sync_on_migrate": int(sync_on_migrate),
    }
    if with_permissions:
        custom["custom_perms"] = _export_rows("Custom DocPerm", [["parent", "=", doctype]])
    if custom["custom_fields"] or custom["property_setters"] or custom["custom_perms"]:
        return custom
    return None


def _fixture_row(row: dict[str, Any], doctype: str) -> dict[str, Any]:
    """Shape a record like Frappe's `export_json`: no audit keys, child rows without their own."""
    exported: dict[str, Any] = {}
    for key, value in row.items():
        if key in _FIXTURE_DROPPED_KEYS or key.startswith("_"):
            continue
        if isinstance(value, list):
            value = [
                {
                    child_key: child_value
                    for child_key, child_value in child.items()
                    if child_key not in _FIXTURE_DROPPED_KEYS
                    and child_key not in _CHILD_SYSTEM_KEYS
                    and not child_key.startswith("_")
                }
                for child in value
                if isinstance(child, dict)
            ]
        exported[key] = value
    exported["doctype"] = doctype
    return exported


def _fixture_file(
    app_path: Path,
    doctype: str,
    exported: list[dict[str, Any]],
    replaces: Callable[[dict[str, Any]], bool],
) -> tuple[Path, str]:
    """Plan `<app>/<app>/fixtures/<scrub(doctype)>.json`, the file `bench migrate` imports.

    `exported` replaces rows with the same name and the rows `replaces` matches (records this
    export covers that the site no longer has); every other row in the file stays.
    """
    path = app_path / app_path.name / "fixtures" / f"{_scrub(doctype)}.json"
    kept: list[dict[str, Any]] = []
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path} is not valid JSON; fix it before exporting") from error
        if not isinstance(current, list) or any(not isinstance(row, dict) for row in current):
            raise ValueError(f"{path} is not a list of fixture rows; fix it before exporting")
        names = {row.get("name") for row in exported}
        kept = [row for row in current if row.get("name") not in names and not replaces(row)]
    return path, _frappe_json(kept + exported, ensure_ascii=False)


def _client_script_fixture(
    app_path: Path, doctypes: list[str], module: str
) -> tuple[Path, str] | None:
    """Plan `<app>/<app>/fixtures/client_script.json`, keeping rows outside this export."""
    rows = _export_rows(
        "Client Script",
        [["dt", "in", doctypes], ["module", "=", module]],
        order_by="idx asc, creation asc",
    )
    if not rows:
        return None
    return _fixture_file(
        app_path,
        "Client Script",
        [_fixture_row(row, "Client Script") for row in rows],
        lambda row: row.get("dt") in doctypes and row.get("module") == module,
    )


def _workflow_fixtures(
    app_path: Path, doctypes: list[str]
) -> tuple[list[str], list[tuple[Path, str]], dict[str, dict[str, list[str]]]]:
    """Plan fixture files for the Workflows of `doctypes` and the masters they use.

    A Workflow has no module, so Frappe has no module folder to write it to; it travels as a
    fixture, with the Workflow State and Workflow Action Master records it names. Returns the
    Workflow names, the files, and the `_fixture_plan` entries filtering each by name.
    """
    names = [
        row["name"]
        for row in _export_rows(
            "Workflow", [["document_type", "in", doctypes]], order_by="name asc"
        )
    ]
    if not names:
        return [], [], {}
    workflows = [
        _fixture_row(_request("GET", _resource("Workflow", name)), "Workflow") for name in names
    ]
    states = sorted({row.get("state") for flow in workflows for row in flow.get("states", [])})
    actions = sorted(
        {row.get("action") for flow in workflows for row in flow.get("transitions", [])}
    )
    files = [
        _fixture_file(
            app_path, "Workflow", workflows, lambda row: row.get("document_type") in doctypes
        )
    ]
    entries: dict[str, dict[str, list[str]]] = {"Workflow": {"name": names}}
    for doctype, wanted in (("Workflow State", states), ("Workflow Action Master", actions)):
        if not wanted:
            continue
        rows = _export_rows(doctype, [["name", "in", wanted]])
        files.append(
            _fixture_file(
                app_path, doctype, [_fixture_row(row, doctype) for row in rows], lambda row: False
            )
        )
        entries[doctype] = {"name": wanted}
    return names, files, entries


def _created_dirs(app_path: Path, path: Path) -> list[str]:
    """Return the app-relative directories that writing `path` creates, outermost first."""
    missing = []
    parent = path.parent
    while not parent.exists() and parent != app_path:
        missing.append(parent)
        parent = parent.parent
    return [str(directory.relative_to(app_path)) for directory in reversed(missing)]


def _write_app_files(
    app_path: Path, payloads: list[tuple[Path, str]], preview_id: str
) -> tuple[str, Callable[[], None]]:
    """Back up the files `payloads` overwrite, write a rollback manifest, then write them.

    Returns the `rollback_id` rollback_export takes, and a function that undoes the writes at
    once (restoring files, removing created ones, the backups, and the manifest).
    """
    backup_root = _safe_app_file(app_path, app_path / ".mcp-frappe-backups")
    backup_root.mkdir(parents=True, exist_ok=True)
    rollback_id = f"export-{preview_id[:32]}.json"
    manifest: list[dict[str, Any]] = []
    previous: list[tuple[Path, str | None, str | None]] = []
    app_root = app_path.resolve()
    for path, content in payloads:
        path = _safe_app_file(app_path, path)
        backup_name = old = None
        if path.exists():
            old = path.read_text(encoding="utf-8")
            backup_name = f"{uuid.uuid4().hex}.bak"
            _atomic_write(backup_root / backup_name, old, mode=0o600)
        manifest.append(
            {
                "path": str(path.relative_to(app_root)),
                "backup": backup_name,
                "exported_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "created_dirs": _created_dirs(app_root, path),
            }
        )
        previous.append((path, old, backup_name))
    manifest_path = backup_root / rollback_id
    _atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )
    for path, content in payloads:
        _atomic_write(_safe_app_file(app_path, path), content)

    def undo() -> None:
        for path, old, backup_name in previous:
            if old is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, old)
            if backup_name:
                (backup_root / backup_name).unlink(missing_ok=True)
        created = {app_root / directory for item in manifest for directory in item["created_dirs"]}
        for directory in sorted(created, key=lambda path: len(path.parts), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
        manifest_path.unlink(missing_ok=True)
        if backup_root.is_dir() and not any(backup_root.iterdir()):
            backup_root.rmdir()

    return rollback_id, undo


def _customization_export_snapshot(
    module_path: Path,
    doctype: str,
    *,
    with_permissions: bool,
    seen: set[str] | None = None,
) -> tuple[list[tuple[str, Path]], list[dict[str, Any]]]:
    """Fingerprint Frappe's export inputs without serializing or writing them."""
    seen = seen or set()
    if doctype in seen:
        return [], []
    if len(seen) >= 50:
        raise ValueError("Customize Form export exceeds 50 DocTypes")
    seen.add(doctype)
    schema = _meta_bundle(doctype)["doctype"]
    targets = [(doctype, module_path / "custom" / f"{_scrub(doctype)}.json")]
    snapshot: list[dict[str, Any]] = [{"doctype": doctype, "modified": schema.get("modified")}]

    def record_state(resource_doctype: str, filters: dict[str, Any]) -> list[dict[str, Any]]:
        result = _request(
            "GET",
            _resource(resource_doctype),
            params={
                "fields": '["name","modified"]',
                "filters": _filters(filters),
                "limit_page_length": 1000,
                "order_by": "name asc",
            },
        )
        if not isinstance(result, list):
            raise RuntimeError(f"Unexpected {resource_doctype} response")
        if len(result) >= 1000:
            raise ValueError(f"Too many {resource_doctype} records to export safely")
        return [{"name": row.get("name"), "modified": row.get("modified")} for row in result]

    resources = [
        ("Custom Field", {"dt": doctype}),
        ("Property Setter", {"doc_type": doctype}),
    ]
    if with_permissions:
        resources.append(("Custom DocPerm", {"parent": doctype}))
    for resource_doctype, filters in resources:
        snapshot.append(
            {
                "resource": resource_doctype,
                "records": record_state(resource_doctype, filters),
            }
        )

    links = schema.get("links", [])
    if not isinstance(links, list) or len(links) > 1000:
        raise ValueError("Too many or malformed DocType Link metadata records to preview")
    snapshot.append({"resource": "DocType Link", "records": links})

    for field in schema.get("fields", []):
        child_doctype = field.get("options")
        if field.get("fieldtype") in {"Table", "Table MultiSelect"} and isinstance(
            child_doctype, str
        ):
            child_targets, child_snapshot = _customization_export_snapshot(
                module_path,
                child_doctype,
                with_permissions=with_permissions,
                seen=seen,
            )
            targets.extend(child_targets)
            snapshot.extend(child_snapshot)
    return targets, snapshot


def _assert_system_manager() -> None:
    """Match Customize Form's System Manager access before calling its exporter."""
    user = _request("GET", "/api/method/frappe.auth.get_logged_user")
    roles = _request(
        "GET",
        "/api/method/frappe.core.doctype.user.user.get_roles",
        params={"uid": user},
    )
    if not isinstance(roles, list) or "System Manager" not in roles:
        raise PermissionError("Customize Form export requires the System Manager role")


def _safe_app_file(app_path: Path, path: Path) -> Path:
    resolved = path.resolve(strict=False)
    if not resolved.is_relative_to(app_path.resolve()) or resolved == app_path.resolve():
        raise ValueError("Export path escapes the selected app")
    return resolved


def _file_preview(path: Path, content: str) -> dict[str, Any]:
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    diff = "".join(
        difflib.unified_diff(
            current.splitlines(True),
            content.splitlines(True),
            fromfile=str(path) if current else "/dev/null",
            tofile=str(path),
        )
    )
    return {
        "path": str(path),
        "exists": path.is_file(),
        "changed": current != content,
        "old_sha256": hashlib.sha256(current.encode()).hexdigest() if current else None,
        "new_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "diff": diff[:12000],
        "diff_truncated": len(diff) > 12000,
    }


@_tool("customize", "Export form customizations")
def export_form_customizations(
    app: str,
    module: str,
    doctype: str,
    sync_on_migrate: bool = True,
    with_permissions: bool = False,
    confirm: Confirm = False,
    expected_preview: ExpectedPreview = None,
) -> dict[str, Any]:
    """Preview, then run Frappe's own Customize Form export for one DocType on the site host.

    The preview lists the expected paths; Frappe has no dry run, so there is no file diff.
    """
    _assert_writable_app(app)
    app_path, module_path = _assert_app_module(app, module)
    if not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")

    targets, source_snapshot = _customization_export_snapshot(
        module_path,
        doctype,
        with_permissions=with_permissions,
    )
    if len(targets) > 50:
        raise ValueError("Customize Form export exceeds 50 files")
    input_state = {
        "app": app,
        "module": module,
        "doctype": doctype,
        "sync_on_migrate": sync_on_migrate,
        "with_permissions": with_permissions,
        "source": source_snapshot,
    }
    file_previews = []
    previous_contents: dict[Path, str | None] = {}
    for target_doctype, path in targets:
        path = _safe_app_file(app_path, path)
        current = path.read_text(encoding="utf-8") if path.is_file() else None
        previous_contents[path] = current
        file_previews.append(
            {
                "doctype": target_doctype,
                "path": str(path),
                "exists": current is not None,
                "current_sha256": (
                    hashlib.sha256(current.encode()).hexdigest() if current else None
                ),
            }
        )
    input_state["destination"] = [
        {key: item[key] for key in ("doctype", "path", "current_sha256")} for item in file_previews
    ]
    preview_id = hashlib.sha256(
        json.dumps(input_state, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if not confirm:
        return {
            "preview": True,
            "action": "export_form_customizations",
            "app": app,
            "module": module,
            "doctype": doctype,
            "sync_on_migrate": sync_on_migrate,
            "with_permissions": with_permissions,
            "files": file_previews,
            "content_diff_available": False,
            "preview_id": preview_id,
            "requires_confirmation": True,
        }
    if expected_preview != preview_id:
        raise ValueError("Customize Form preview is missing or stale")
    _assert_system_manager()

    native_result = _request(
        "POST",
        "/api/method/frappe.modules.utils.export_customizations",
        data={
            "module": module,
            "doctype": doctype,
            "sync_on_migrate": int(sync_on_migrate),
            "with_permissions": int(with_permissions),
        },
    )

    manifest: list[dict[str, Any]] = []
    backup_root = _safe_app_file(app_path, app_path / ".mcp-frappe-backups")
    for path, previous in previous_contents.items():
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        if content == previous:
            continue
        backup_name = None
        if previous is not None:
            backup_name = f"{uuid.uuid4().hex}.bak"
            _atomic_write(backup_root / backup_name, previous, mode=0o600)
        manifest.append(
            {
                "path": str(path.relative_to(app_path.resolve())),
                "backup": backup_name,
                "exported_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            }
        )
    rollback_id = None
    if manifest:
        backup_root.mkdir(parents=True, exist_ok=True)
        rollback_id = f"export-{preview_id[:32]}.json"
        _atomic_write(
            backup_root / rollback_id,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            mode=0o600,
        )
    local_files = [
        str(path)
        for path, previous in previous_contents.items()
        if path.is_file() and path.read_text(encoding="utf-8") != previous
    ]
    native_path = native_result if isinstance(native_result, str) else None
    native_path_matches_local = bool(
        native_path
        and Path(native_path).resolve(strict=False)
        in {path.resolve(strict=False) for path in previous_contents}
    )
    has_exportable_records = any(
        item.get("resource") in {"Custom Field", "Property Setter", "Custom DocPerm"}
        and item.get("records")
        for item in source_snapshot
    )
    return {
        "exported": bool(native_result or local_files or has_exportable_records),
        "app": app,
        "module": module,
        "doctype": doctype,
        "sync_on_migrate": sync_on_migrate,
        "files": local_files or ([native_path] if native_path else []),
        "native_result": native_result,
        "shared_app_tree_detected": bool(local_files or native_path_matches_local),
        "changed_local_files": local_files,
        "rollback_id": rollback_id,
    }


@_tool("read", "List installed apps")
def list_installed_apps() -> list[dict[str, Any]]:
    """List the site's apps and their modules (from Module Def)."""
    modules = _request(
        "GET",
        _resource("Module Def"),
        params={
            "fields": '["name","app_name"]',
            "limit_page_length": 1000,
            "order_by": "app_name asc,name asc",
        },
    )
    grouped: dict[str, list[str]] = {}
    for row in modules:
        grouped.setdefault(row.get("app_name") or "unknown", []).append(row["name"])
    return [{"app": app, "modules": names} for app, names in sorted(grouped.items())]


def _page(rows: Any, offset: int, limit: int) -> dict[str, Any]:
    """Wrap a `limit + 1` row read as one page; the extra row only sets `has_more`."""
    rows = rows if isinstance(rows, list) else []
    return {"rows": rows[:limit], "offset": offset, "limit": limit, "has_more": len(rows) > limit}


def _assert_offset(offset: Any) -> None:
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset must be zero or a positive integer")


_DEVELOPER_MODE_NOTE = (
    "Frappe exposes developer mode to Desk only (in the page's boot data), not through an API, "
    "so it is not reported here; tools that need it say so when Frappe refuses a change."
)


def _local_apps(apps: list[str]) -> dict[str, Any]:
    """Say which site apps have a checkout under FRAPPE_APPS_PATH, without returning paths."""
    root = os.environ.get("FRAPPE_APPS_PATH")
    if not root:
        return {"configured": False}
    try:
        Path(root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return {"configured": True, "exists": False}
    found, missing = [], []
    for app in apps:
        try:
            _app_root(app)
        except (OSError, RuntimeError, ValueError):
            missing.append(app)
        else:
            found.append(app)
    return {"configured": True, "site_apps_found": found, "site_apps_missing": missing}


@_tool("read", "Get site info")
def get_site_info() -> dict[str, Any]:
    """Say who is connected and what the site runs.

    Returns `user`, `apps` with versions, `frappe_major_version`, `mcp_guidance`, `site_label`,
    and `apps_path`
    (which apps have a checkout under FRAPPE_APPS_PATH; never a path). Developer mode can't be read
    through the API.
    """
    label = _site_label()
    user = _request("GET", "/api/method/frappe.auth.get_logged_user")
    versions = _request("GET", "/api/method/frappe.utils.change_log.get_versions")
    versions = versions if isinstance(versions, dict) else {}
    apps = []
    for app, info in sorted(versions.items())[:_DRIFT_MAX_ITEMS]:
        info = info if isinstance(info, dict) else {}
        apps.append(
            {
                "app": str(app)[:_DRIFT_MAX_TEXT],
                "title": str(info.get("title") or "")[:_DRIFT_MAX_TEXT],
                "version": str(info.get("version") or "")[:_DRIFT_MAX_TEXT],
            }
        )
    major = str((versions.get("frappe") or {}).get("version") or "").split(".")[0]
    guidance = (
        "Frappe v15: use standalone mcp-frappe for data and development tools."
        if major.isdigit() and int(major) == 15
        else (
            "Frappe v16+: if enabled on the site and configured in the MCP host, native Frappe "
            "MCP may handle data; use standalone mcp-frappe for customize and developer tools "
            "with read,customize,developer; add bench if FRAPPE_BENCH_PATH is configured."
        )
        if major.isdigit() and int(major) >= 16
        else (
            "Frappe version is unknown: use standalone mcp-frappe and consult the compatibility "
            "matrix; do not infer native MCP availability."
        )
    )
    return {
        "site_label": label,
        "user": user,
        "apps": apps,
        "frappe_major_version": int(major) if major.isdigit() else None,
        "mcp_guidance": guidance,
        "apps_path": _local_apps([row["app"] for row in apps]),
        "notes": [_DEVELOPER_MODE_NOTE],
    }


@_tool("read", "List DocTypes")
def list_doctypes(
    search: str | None = None,
    app: str | None = None,
    module: str | None = None,
    include_child_tables: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List DocTypes a page at a time.

    `search` is a case-insensitive part of the name; `app` keeps that app's modules. Rows have name,
    module, custom, issingle, istable, is_submittable. Returns {rows, offset, limit, has_more}; the
    next page starts at offset + limit.
    """
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    _assert_offset(offset)
    if search is not None and (
        not isinstance(search, str) or not _SAFE_NAME.fullmatch(search.strip())
    ):
        raise ValueError(
            "search must be 1-140 letters, digits, spaces, or _ . - (part of a DocType name)"
        )
    filters: list[list[Any]] = []
    if app:
        modules = _request(
            "GET",
            _resource("Module Def"),
            params={
                "fields": '["name"]',
                "filters": _filters({"app_name": app}),
                "limit_page_length": 1000,
            },
        )
        module_names = [row["name"] for row in modules]
        if not module_names:
            return _page([], offset, limit)
        filters = [["module", "in", module_names]]
    if module:
        filters.append(["module", "=", module])
    if not include_child_tables:
        filters.append(["istable", "=", 0])
    if search is not None:
        filters.append(["name", "like", f"%{search.strip()}%"])
    rows = _request(
        "GET",
        _resource("DocType"),
        params={
            "fields": json.dumps(
                ["name", "module", "custom", "issingle", "istable", "is_submittable"]
            ),
            "filters": _filters(filters),
            "limit_start": offset,
            "limit_page_length": limit + 1,
            "order_by": "module asc,name asc",
        },
    )
    return _page(rows, offset, limit)


def _compact_fields(meta: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {key: row[key] for key in _COMPACT_FIELD_KEYS if row.get(key)}
        for row in meta.get("fields", [])
        if row.get("fieldname") and row.get("fieldtype") not in _LAYOUT_FIELDTYPES
    ]


@_tool("read", "Get DocType schema")
def get_doctype_schema(doctype: str, detail: Detail = "compact") -> dict[str, Any]:
    """Read a DocType's schema as its form sees it, Custom Fields and Property Setters included.

    compact: the DocType flags and data fields (no layout fields; false or empty values left out),
    `child_tables` with their fields, and the active `workflow`. full: every property as {"doctype",
    "children", "workflow"}; use it only when editing needs it.
    """
    if detail not in ("compact", "full"):
        raise ValueError('detail must be "compact" or "full"')
    bundle = _meta_bundle(doctype)
    if detail == "full":
        return bundle
    meta = bundle["doctype"]
    compact = {key: meta[key] for key in _COMPACT_DOCTYPE_KEYS if meta.get(key)}
    compact["fields"] = _compact_fields(meta)
    if bundle.get("workflow"):
        compact["workflow"] = bundle["workflow"]
    if bundle["children"]:
        compact["child_tables"] = {
            name: _compact_fields(child) for name, child in bundle["children"].items()
        }
    return compact


@_tool("read", "List documents")
def list_documents(
    doctype: str,
    filters: Filters = None,
    fields: Fields = None,
    limit: int = 20,
    offset: int = 0,
    order_by: OrderBy = None,
    or_filters: OrFilters = None,
) -> dict[str, Any]:
    """List records the user may read, a page at a time (default order `modified desc`).

    Returns {rows, offset, limit, has_more}; while has_more, the next page starts at offset + limit.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    _assert_offset(offset)
    bundle = _meta_bundle(doctype)
    requested = fields or ["name"]
    if not isinstance(requested, list) or len(requested) > 40:
        raise ValueError("fields must be a list of no more than 40 field names")
    _assert_readable_fields(bundle["doctype"], requested)
    _assert_filters(bundle, filters)
    _assert_filters(bundle, or_filters)
    sort = "modified desc"
    if order_by is not None:
        sort = ", ".join(
            f"{field} {direction}" for field, direction in _order_terms(bundle["doctype"], order_by)
        )
    params = {
        "fields": json.dumps(requested),
        "filters": _filters(filters),
        "limit_start": offset,
        "limit_page_length": limit + 1,
        "order_by": sort,
    }
    if or_filters:
        params["or_filters"] = _filters(or_filters)
    return _page(_request("GET", _resource(doctype), params=params), offset, limit)


def _get_count(doctype: str, filters: dict[str, Any] | list[list[Any]] | None) -> Any:
    return _request(
        "GET",
        "/api/method/frappe.client.get_count",
        params={"doctype": doctype, "filters": _filters(filters)},
    )


@_tool("read", "Count documents")
def count_documents(doctype: str, filters: Filters = None) -> dict[str, Any]:
    """Count the records the user may read."""
    _assert_filters(_meta_bundle(doctype), filters)
    count = _get_count(doctype, filters)
    if not isinstance(count, int) or isinstance(count, bool):
        raise RuntimeError("Unexpected get_count response")
    return {"doctype": doctype, "count": count}


@_tool("read", "Search records")
def search_records(
    doctype: str,
    text: str,
    filters: Filters = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Find records the way a Link field's dropdown does, with the user's permissions.

    Matches the name and the DocType's search fields. `text` may be empty; `limit` is 1-50. Returns
    {doctype, results: [{value, label?, description?}]}, where `value` is the record name.
    """
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    if not isinstance(text, str) or len(text) > 140:
        raise ValueError("text must be a string of at most 140 characters")
    _assert_filters(_meta_bundle(doctype), filters)
    params: dict[str, Any] = {"doctype": doctype, "txt": text, "page_length": limit}
    if filters:
        params["filters"] = _filters(filters)
    rows = _request("GET", "/api/method/frappe.desk.search.search_link", params=params)
    results = []
    for row in (rows if isinstance(rows, list) else [])[:limit]:
        if not isinstance(row, dict):
            continue
        results.append(
            {
                key: str(row[key])[:_DRIFT_MAX_TEXT]
                for key in ("value", "label", "description")
                if not _is_empty(row.get(key))
            }
        )
    return {"doctype": doctype, "results": results}


@_tool("read", "Get document")
def get_document(
    doctype: str, name: str, fields: Fields = None, child_row_limit: int = 100
) -> dict[str, Any]:
    """Read one document.

    Without `fields`, returns the data fields that have a value; `doctype`, `name`, `modified`, and
    `docstatus` always come back. Child tables return at most `child_row_limit` rows; cut ones are
    listed in `_truncated_child_tables`.
    """
    if not 1 <= child_row_limit <= 200:
        raise ValueError("child_row_limit must be between 1 and 200")
    bundle = _meta_bundle(doctype)
    schema = bundle["doctype"]
    by_name = _schema_fields(schema)
    compact = not fields
    if compact:
        requested = {
            fieldname
            for fieldname, row in by_name.items()
            if row.get("fieldtype") not in _LAYOUT_FIELDTYPES
        }
    else:
        requested = set(fields)
        _assert_readable_fields(schema, requested)
    requested.add("name")
    doc = _request("GET", _resource(doctype, name))
    result = {
        key: value
        for key, value in doc.items()
        if key in {"doctype", "name", "modified", "docstatus"}
        or (key in requested and not (compact and _is_empty(value)))
    }
    truncated = []
    for field in sorted(requested):
        row = by_name.get(field)
        rows = result.get(field)
        if not row or row.get("fieldtype") not in _TABLE_FIELDTYPES or not isinstance(rows, list):
            continue
        if len(rows) > child_row_limit:
            rows = rows[:child_row_limit]
            truncated.append(field)
        child = bundle["children"].get(row.get("options"))
        if compact and child is not None:
            kept = {
                fieldname
                for fieldname, child_row in _schema_fields(child).items()
                if child_row.get("fieldtype") not in _LAYOUT_FIELDTYPES
            } | {"name", "idx"}
            rows = [
                {
                    key: value
                    for key, value in child_doc.items()
                    if key in kept and not _is_empty(value)
                }
                if isinstance(child_doc, dict)
                else child_doc
                for child_doc in rows
            ]
        result[field] = rows
    if truncated:
        result["_truncated_child_tables"] = sorted(truncated)
    return result


@_tool("data", "Create document")
def create_document(
    doctype: str, values: dict[str, Any], confirm: Confirm = False
) -> dict[str, Any]:
    """Preview or create a record.

    `values` may set `name` only when the DocType's autoname is Prompt. Record data only: schema,
    configuration, and code DocTypes, and `Code` fields, are refused with the tool to use instead.
    """
    _assert_record_doctype(doctype, "create")
    bundle = _meta_bundle(doctype)
    prompt_named = str(bundle["doctype"].get("autoname") or "").lower() == "prompt"
    _assert_new_record(bundle, values, prompt_named)
    if not confirm:
        return {
            "preview": True,
            "action": "create_document",
            "doctype": doctype,
            "fields": sorted(values),
            "requires_confirmation": True,
        }
    try:
        created = _request(
            "POST", _resource(doctype), json={"data": _new_record_data(doctype, values)}
        )
    finally:
        _forget_meta_after_write(doctype)
    if not isinstance(created, dict):
        return created
    return _journal(
        created,
        "create_document",
        {"doctype": doctype, "names": [created.get("name")]},
        undo={
            "op": "delete_documents",
            "doctype": doctype,
            "modified": {str(created.get("name")): created.get("modified")},
        },
        record_data=True,
    )


def _assert_new_record(bundle: dict[str, Any], values: Any, prompt_named: bool) -> None:
    """Check one new record's values: writable fields, child rows, no code."""
    schema = bundle["doctype"]
    doctype = schema.get("name")
    if isinstance(values, dict) and "name" in values and not prompt_named:
        raise ValueError(
            f"{doctype} names its records itself (autoname is not Prompt); leave out name"
        )
    _assert_writable_fields(schema, values, allow_name=prompt_named)
    _assert_child_rows(bundle, values)
    _assert_no_code_fields(bundle, values)


def _assert_child_rows(bundle: dict[str, Any], values: dict[str, Any]) -> None:
    """Check each child-table row's keys against the child DocType's fields.

    Read-only child fields stay allowed: mapped drafts set them (an item's link back to its
    order is read-only in Desk but written by the mapper).
    """
    fields = _schema_fields(bundle["doctype"])
    for fieldname, value in values.items():
        row = fields.get(fieldname)
        if row is None or row.get("fieldtype") not in _TABLE_FIELDTYPES:
            continue
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError(f"{fieldname} is a child table; give a list of row objects")
        child = bundle["children"].get(row.get("options"))
        if child is None:
            continue
        child_fields = _schema_fields(child)
        for index, item in enumerate(value, 1):
            where = f"{fieldname} row {index}"
            if not item:
                raise ValueError(f"{where} is empty")
            for key in item:
                if key == "doctype":
                    if item[key] != row.get("options"):
                        raise ValueError(f"{where}: doctype must be {row.get('options')}")
                elif key in _RESERVED_FIELDS or key not in child_fields:
                    raise ValueError(f"{where}: field cannot be written: {key}")


def _new_record_data(doctype: str, values: dict[str, Any]) -> dict[str, Any]:
    data = dict(values)
    if "name" in data:
        # Desk sends a prompted name as __newname; Document.set_new_name uses it only when
        # autoname is "prompt", the condition _assert_new_record checks.
        data["__newname"] = data.pop("name")
    return {"doctype": doctype, **data}


_BATCH_MAX_ROWS = 100
# frappe.client.insert_many (v15 and v16): POST only, at most 200 docs, each through
# frappe.get_doc(doc).insert(). Frappe commits a request only when it ends without an
# exception, so one failing row rolls back the whole batch.
_INSERT_MANY = "/api/method/frappe.client.insert_many"


@_tool("data", "Create documents")
def create_documents(
    doctype: str,
    rows: list[dict[str, Any]],
    expected_preview: ExpectedPreview = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or create 1-100 records of one DocType, all or nothing, for test or seed data.

    Each row is checked like create_document's `values`. The preview returns `count`, `fields`,
    per-row `problems`, and `preview_id` (none while there are problems). Confirm with the same
    rows. Returns the created `names`.
    """
    _assert_record_doctype(doctype, "create")
    if not isinstance(rows, list) or not 1 <= len(rows) <= _BATCH_MAX_ROWS:
        raise ValueError(f"rows must be a list of 1 to {_BATCH_MAX_ROWS} objects")
    bundle = _meta_bundle(doctype)
    prompt_named = str(bundle["doctype"].get("autoname") or "").lower() == "prompt"
    problems: list[dict[str, Any]] = []
    names: dict[Any, int] = {}
    for index, row in enumerate(rows, 1):
        try:
            _assert_new_record(bundle, row, prompt_named)
        except ValueError as error:
            problems.append({"row": index, "problem": str(error)})
            continue
        if "name" in row:
            if row["name"] in names:
                problems.append({"row": index, "problem": f"same name as row {names[row['name']]}"})
            names.setdefault(row["name"], index)
    fields = sorted({key for row in rows if isinstance(row, dict) for key in row})
    preview_id = (
        None
        if problems
        else hashlib.sha256(
            json.dumps({"doctype": doctype, "rows": rows}, sort_keys=True, default=str).encode()
        ).hexdigest()
    )
    if not confirm:
        return {
            "preview": True,
            "action": "create_documents",
            "doctype": doctype,
            "count": len(rows),
            "fields": fields,
            "problems": problems,
            "preview_id": preview_id,
            "requires_confirmation": True,
        }
    if problems:
        raise ValueError(f"{len(problems)} rows have problems; fix them and preview again")
    if not expected_preview or expected_preview != preview_id:
        raise ValueError("The rows changed since the preview; request a fresh preview")
    try:
        created = _request(
            "POST",
            _INSERT_MANY,
            json={"docs": [_new_record_data(doctype, row) for row in rows]},
        )
    except FrappeError as error:
        raise FrappeError(
            f"Nothing was created; Frappe rolled back the whole batch: {error}",
            error.exc_type,
            status=error.status,
        ) from error
    finally:
        _forget_meta_after_write(doctype)
    created_names = [str(name) for name in created] if isinstance(created, list) else []
    result = {"created": len(created_names), "doctype": doctype, "names": created_names}
    return _journal(
        result,
        "create_documents",
        {"doctype": doctype, "names": created_names},
        undo=lambda: {
            "op": "delete_documents",
            "doctype": doctype,
            "modified": _current_modified(doctype, created_names),
        },
        record_data=True,
    )


@_tool("data", "Update document", idempotent=True)
def update_document(
    doctype: str,
    name: str,
    values: dict[str, Any],
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or update a record. Same limits as create_document."""
    _assert_record_doctype(doctype, "update")
    bundle = _meta_bundle(doctype)
    _assert_writable_fields(bundle["doctype"], values)
    _assert_no_code_fields(bundle, values)
    doc = _request("GET", _resource(doctype, name))
    if not confirm:
        return {
            "preview": True,
            "action": "update_document",
            "doctype": doctype,
            "name": name,
            "modified": doc.get("modified"),
            "fields": sorted(values),
            "requires_confirmation": True,
        }
    before = _expected_modified(doctype, name, expected_modified)
    # Callers may not write `modified`, but sending the preview's value closes the gap after
    # the check above: update_doc locks the row, and Document.check_if_latest compares this
    # value with the stored one and raises TimestampMismatchError if they differ.
    try:
        saved = _request(
            "PUT",
            _resource(doctype, name),
            json={"data": {**values, "modified": expected_modified}},
        )
    except FrappeError as error:
        if error.exc_type == "TimestampMismatchError":
            raise ValueError(_STALE_DOCUMENT) from error
        raise
    finally:
        _forget_meta_after_write(doctype)
    if not isinstance(saved, dict):
        return saved
    old, reason = _old_values(bundle, before, values)
    return _journal(
        saved,
        "update_document",
        {"doctype": doctype, "names": [name], "fields": sorted(values)},
        undo=None
        if old is None
        else {
            "op": "update_document",
            "doctype": doctype,
            "name": name,
            "values": old,
            "modified": saved.get("modified"),
        },
        reason=reason,
        record_data=True,
    )


@_tool("data", "Delete document", destructive=True, idempotent=True)
def delete_document(
    doctype: str,
    name: str,
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or delete a record.

    DocTypes go to delete_doctype; records holding code are refused. Also the way to delete Custom
    Fields, Property Setters, Custom DocPerms, Workflows, Notifications, Reports, and Workspaces.
    """
    _assert_record_doctype(doctype, "delete")
    doc = _request("GET", _resource(doctype, name))
    if not confirm:
        return {
            "preview": True,
            "action": "delete_document",
            "doctype": doctype,
            "name": name,
            "modified": doc.get("modified"),
            "requires_confirmation": True,
        }
    _expected_modified(doctype, name, expected_modified)
    try:
        _request("DELETE", _resource(doctype, name))
        reconciled = False
    except httpx.TransportError as error:
        if not _wait_for_document_deleted(doctype, name):
            raise RuntimeError(
                "Frappe disconnected during deletion and the document still exists; "
                "refresh state before retrying."
            ) from error
        reconciled = True
    finally:
        _forget_meta_after_write(doctype)
    result = {"deleted": True, "doctype": doctype, "name": name}
    if reconciled:
        result["reconciled_after_server_reload"] = True
    return result


def _lifecycle_target(
    doctype: str, name: str, action: Literal["submit", "cancel"], docstatus: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read fresh metadata and the document, refusing what submit or cancel can't do here."""
    _forget_meta(doctype)
    bundle = _meta_bundle(doctype)
    if not bundle["doctype"].get("is_submittable"):
        raise ValueError(f"{doctype} is not submittable; its documents have no {action} step")
    if bundle.get("workflow"):
        raise ValueError(
            f"{doctype} has an active Workflow {bundle['workflow']}; the workflow controls "
            f"{action}. A direct {action} would skip its transitions and roles, so it is refused."
        )
    doc = _request("GET", _resource(doctype, name))
    if doc.get("docstatus") != docstatus:
        raise ValueError(
            f"{action.capitalize()} needs docstatus {docstatus}; {doctype} {name} has "
            f"docstatus {doc.get('docstatus')}"
        )
    return bundle, doc


@_tool("data", "Submit document")
def submit_document(
    doctype: str,
    name: str,
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or submit a draft (docstatus 0) of a submittable DocType, to test its controller.

    Refuses DocTypes with an active Workflow. Runs validate, before_submit, and on_submit.
    """
    _, doc = _lifecycle_target(doctype, name, "submit", 0)
    if not confirm:
        return {
            "preview": True,
            "action": "submit_document",
            "doctype": doctype,
            "name": name,
            "docstatus": doc.get("docstatus"),
            "modified": doc.get("modified"),
            "is_submittable": True,
            "workflow": None,
            "warnings": [
                "Submitting runs the controller's validate, before_submit, and on_submit, which "
                "may post ledger or stock entries; a submitted document can only be cancelled."
            ],
            "requires_confirmation": True,
        }
    doc = _expected_modified(doctype, name, expected_modified)
    # frappe.client.submit rebuilds the document from this dict, so Document.check_if_latest
    # compares its `modified` with the stored one and refuses a document changed meanwhile.
    try:
        result = _request("POST", "/api/method/frappe.client.submit", json={"doc": doc})
    except FrappeError as error:
        if error.exc_type == "TimestampMismatchError":
            raise ValueError(_STALE_DOCUMENT) from error
        raise
    result = result if isinstance(result, dict) else {}
    return {
        "submitted": True,
        "doctype": doctype,
        "name": name,
        "docstatus": result.get("docstatus"),
        "modified": result.get("modified"),
    }


@_tool("data", "Cancel document", destructive=True)
def cancel_document(
    doctype: str,
    name: str,
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or cancel a submitted document (docstatus 1).

    Refuses DocTypes with an active Workflow. Runs before_cancel and on_cancel; Frappe refuses while
    submitted documents link to it.
    """
    _, doc = _lifecycle_target(doctype, name, "cancel", 1)
    if not confirm:
        return {
            "preview": True,
            "action": "cancel_document",
            "doctype": doctype,
            "name": name,
            "docstatus": doc.get("docstatus"),
            "modified": doc.get("modified"),
            "warnings": [
                "Cancelling runs the controller's before_cancel and on_cancel, which may reverse "
                "ledger or stock entries. A cancelled document can't be submitted again; it can "
                "only be amended (not available in this server)."
            ],
            "requires_confirmation": True,
        }
    _expected_modified(doctype, name, expected_modified)
    # frappe.client.cancel takes no `modified`, so the check above is the guard.
    result = _request(
        "POST", "/api/method/frappe.client.cancel", json={"doctype": doctype, "name": name}
    )
    result = result if isinstance(result, dict) else {}
    return {
        "cancelled": True,
        "doctype": doctype,
        "name": name,
        "docstatus": result.get("docstatus"),
        "modified": result.get("modified"),
    }


# The change journal: one entry per confirmed write, so a change made through this server can
# be listed and undone. Entries hold names, fieldnames, and the values an undo writes back,
# never credentials; record data only with FRAPPE_MCP_JOURNAL_RECORDS=YES.

_JOURNAL_MAX = 200
_JOURNAL_DIR = ".mcp-frappe-journal"
_JOURNAL_VALUES_MAX = 50_000
_JOURNAL_LIST_MAX = 50
_JOURNAL_NAMES_SHOWN = 10
_journal_memory: list[dict[str, Any]] = []
# True while undo_mcp_change runs an inverse, which marks the entry undone instead.
_journal_paused = False
# Records of these DocTypes describe the site, not business data, so they are always journaled.
_CONFIG_DOCTYPES = _SCHEMA_DOCTYPES | frozenset(_RECORD_TOOL_REDIRECTS)
# Tools that write their own entries, with an inverse; `_tool()` journals every other write.
_SELF_JOURNALED = frozenset(
    {
        "create_document",
        "create_documents",
        "update_document",
        "create_custom_field",
        "set_field_property",
        "create_doctype",
        "update_doctype",
        "undo_mcp_change",
    }
)
# Arguments that identify what a write touched; values and field lists never go in.
_JOURNAL_TARGET_KEYS = (
    "doctype",
    "name",
    "document_type",
    "kind",
    "old",
    "new",
    "app",
    "module",
    "command",
    "method",
    "role",
    "property",
    "fieldname",
)
_NO_UNDO = {
    "delete_document": "a deleted record can't be restored from the journal",
    "delete_doctype": "a deleted DocType can't be restored from the journal",
    "submit_document": "a submitted document has no safe inverse; cancel it instead",
    "cancel_document": "a cancelled document can't be submitted again",
    "export_customizations": "use rollback_export for exported files",
    "export_form_customizations": "restore the exported files from version control",
    "rollback_export": "export again to redo it",
    "promote_custom_doctype": "a promoted DocType's files can't be removed from the journal",
    "run_bench": "Bench commands have no inverse",
    "rename": "rename it back with rename",
    "customize_doctype": "change it back with customize_doctype",
    "set_role_permissions": "set the old permissions again with set_role_permissions",
}
_UNDO_TOOLS = {
    "delete_documents": "delete_document",
    "update_document": "update_document",
    "set_field_property": "set_field_property",
    "delete_doctype": "delete_doctype",
    "update_doctype": "update_doctype",
}


def _journal_records() -> bool:
    return os.environ.get("FRAPPE_MCP_JOURNAL_RECORDS", "").strip().upper() == "YES"


def _journal_file() -> Path | None:
    """The site's journal file under FRAPPE_APPS_PATH, or None to keep it in memory."""
    root_value = os.environ.get("FRAPPE_APPS_PATH")
    if not root_value:
        return None
    try:
        root = Path(root_value).expanduser().resolve(strict=True)
    except OSError as error:
        raise ValueError("FRAPPE_APPS_PATH does not exist; the journal can't be kept") from error
    folder = root / _JOURNAL_DIR
    key = (
        _site_label()
        or os.environ.get("FRAPPE_SITE")
        or urlparse(os.environ.get("FRAPPE_URL") or "").hostname
        or "site"
    )
    key = re.sub(r"[^A-Za-z0-9._-]", "_", key).strip(".")[:100] or "site"
    path = folder / f"{key}.json"
    if folder.is_symlink() or path.is_symlink():
        raise ValueError(f"The journal folder {_JOURNAL_DIR} must not be a symlink")
    return path


def _journal_load() -> list[dict[str, Any]]:
    path = _journal_file()
    if path is None:
        return copy.deepcopy(_journal_memory)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(
            f"The journal file {_JOURNAL_DIR}/{path.name} is unreadable; move it aside"
        ) from error
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError(f"The journal file {_JOURNAL_DIR}/{path.name} has an unknown shape")
    return entries


def _journal_save(entries: list[dict[str, Any]]) -> None:
    entries = entries[-_JOURNAL_MAX:]
    path = _journal_file()
    if path is None:
        _journal_memory[:] = copy.deepcopy(entries)
        return
    path.parent.mkdir(mode=0o700, exist_ok=True)
    _atomic_write(
        path,
        json.dumps({"version": 1, "entries": entries}, indent=1, default=str) + "\n",
        mode=0o600,
    )


def _journal(
    result: Any,
    tool: str,
    target: dict[str, Any],
    *,
    undo: dict[str, Any] | Callable[[], dict[str, Any]] | None = None,
    reason: str | None = None,
    record_data: bool = False,
) -> Any:
    """Record a confirmed write and return `result` with its entry id as `mcp_change`.

    `undo` (or a callable that builds it, since that may cost a request) describes the inverse.
    Writes of record data are skipped unless FRAPPE_MCP_JOURNAL_RECORDS=YES. A journal failure
    never fails the write: the result says so in `journal_error`.
    """
    if _journal_paused or (record_data and not _journal_records()):
        return result
    try:
        if callable(undo):
            undo = undo()
        entry: dict[str, Any] = {
            "id": uuid.uuid4().hex[:12],
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "tool": tool,
            "target": target,
            "undo": undo,
        }
        if undo is None:
            entry["no_undo"] = reason or _NO_UNDO.get(
                tool, "no inverse for this tool; reverse it with the tool that made it"
            )
        entries = _journal_load()
        entries.append(entry)
        _journal_save(entries)
    except Exception as error:  # the write itself succeeded
        detail = str(error) if isinstance(error, ValueError | FrappeError) else type(error).__name__
        _log.warning("Change journal not updated: %s", detail)
        if isinstance(result, dict):
            return {**result, "journal_error": f"change not journaled: {detail}"}
        return result
    if isinstance(result, dict):
        return {**result, "mcp_change": entry["id"]}
    return result


def _journal_call(tool: str, toolset: str, kwargs: dict[str, Any], result: Any) -> Any:
    """Journal a confirmed write of a tool that has no inverse, by the names it touched."""
    doctype = kwargs.get("doctype")
    record_data = doctype not in _CONFIG_DOCTYPES and (
        toolset == "data" or (tool == "rename" and kwargs.get("kind") == "document")
    )
    target = {
        key: value[:_RENAME_NAME_MAX]
        for key in _JOURNAL_TARGET_KEYS
        if isinstance(value := kwargs.get(key), str)
    }
    return _journal(result, tool, target, record_data=record_data)


def _current_rows(doctype: str, names: list[str]) -> dict[str, dict[str, Any]]:
    """Read `name`, `modified`, and `docstatus` of up to 100 records in one request."""
    rows = _request(
        "GET",
        _resource(doctype),
        params={
            "fields": '["name","modified","docstatus"]',
            "filters": _filters([["name", "in", names]]),
            "limit_page_length": len(names),
        },
    )
    return {str(row.get("name")): row for row in rows if isinstance(row, dict)}


def _current_modified(doctype: str, names: list[str]) -> dict[str, Any]:
    rows = _current_rows(doctype, names) if names else {}
    return {name: rows.get(name, {}).get("modified") for name in names}


def _old_values(
    bundle: dict[str, Any], before: dict[str, Any], values: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    """The values an update replaced, as update_document takes them, or why there are none."""
    fields = _schema_fields(bundle["doctype"])
    old: dict[str, Any] = {}
    for key in values:
        row = fields.get(key, {})
        if row.get("fieldtype") == "Password":
            return None, "a Password field changed; Frappe never returns its old value"
        value = before.get(key)
        if row.get("fieldtype") in _TABLE_FIELDTYPES and isinstance(value, list):
            child = _schema_fields(bundle["children"].get(row.get("options")) or {})
            value = [
                {
                    k: v
                    for k, v in item.items()
                    if k in ("name", "idx") or (k in child and k not in _RESERVED_FIELDS)
                }
                for item in value
                if isinstance(item, dict)
            ]
        old[key] = value
    if len(json.dumps(old, default=str)) > _JOURNAL_VALUES_MAX:
        return None, "the old values are too large to journal"
    return old, None


def _inverse_doctype_operations(
    doc: dict[str, Any], plan: dict[str, Any], changes: dict[str, Any]
) -> list[dict[str, Any]]:
    """update_doctype operations that take a DocType back to `doc` from the planned state."""
    operations: list[dict[str, Any]] = [
        {"op": "remove_field", "fieldname": row["fieldname"]} for row in changes["added"]
    ]
    if changes["properties"]:
        operations.append(
            {
                "op": "set_properties",
                "set": {key: delta["from"] for key, delta in changes["properties"].items()},
            }
        )
    for fieldname, delta in changes["changed"].items():
        operations.append(
            {
                "op": "update_field",
                "fieldname": fieldname,
                "set": {key: change["from"] for key, change in delta.items()},
            }
        )
    removed = {row["fieldname"] for row in changes["removed"]}
    moved = set(plan["moved"])
    # Walking the old order, each field goes back after its old predecessor, which is either
    # untouched or already restored by an earlier step.
    previous = None
    for row in doc.get("fields") or []:
        fieldname = row.get("fieldname")
        if fieldname in removed:
            field = {
                key: row[key]
                for key in sorted(_DOCFIELD_PROPERTIES | {"fieldname"})
                if key in row and not _blank(row[key])
            }
            operations.append({"op": "add_field", "field": field, "insert_after": previous})
        elif fieldname in moved:
            operations.append(
                {"op": "move_field", "fieldname": fieldname, "insert_after": previous}
            )
        previous = fieldname
    return operations


def _journal_row(entry: dict[str, Any]) -> dict[str, Any]:
    target = dict(entry.get("target") or {})
    names = target.get("names")
    if isinstance(names, list) and len(names) > _JOURNAL_NAMES_SHOWN:
        target["names"] = names[:_JOURNAL_NAMES_SHOWN]
        target["more_names"] = len(names) - _JOURNAL_NAMES_SHOWN
    row = {
        "id": entry.get("id"),
        "at": entry.get("at"),
        "tool": entry.get("tool"),
        "target": target,
        "undoable": bool(entry.get("undo")) and not entry.get("undone_at"),
    }
    for key in ("no_undo", "undone_at"):
        if entry.get(key):
            row[key] = entry[key]
    return row


@_tool("read", "List MCP changes")
def list_mcp_changes(limit: int = 20) -> dict[str, Any]:
    """List the newest writes made through this server, newest first, from its change journal.

    Rows: `id`, `at`, `tool`, `target`, `undoable`, and `no_undo` or `undone_at`. Record writes are
    journaled only with FRAPPE_MCP_JOURNAL_RECORDS=YES. Undo one with undo_mcp_change.
    """
    if not 1 <= limit <= _JOURNAL_LIST_MAX:
        raise ValueError(f"limit must be between 1 and {_JOURNAL_LIST_MAX}")
    entries = _journal_load()
    return {
        "rows": [_journal_row(entry) for entry in reversed(entries[-limit:])],
        "total": len(entries),
        "storage": "memory" if _journal_file() is None else "file",
        "records_journaled": _journal_records(),
    }


def _changed_since(what: str, current: Any, journaled: Any) -> ValueError:
    return ValueError(
        f"{what} changed after this MCP change (modified {current}, the journal has "
        f"{journaled}); undo is refused so the later change isn't lost"
    )


def _undo_preview(undo: dict[str, Any]) -> dict[str, Any]:
    """Check the inverse still applies and return the preview of the tool that runs it."""
    op = undo["op"]
    if op == "delete_documents":
        doctype, modified = undo["doctype"], undo["modified"]
        _assert_record_doctype(doctype, "delete")
        names = list(modified)
        current = _current_rows(doctype, names)
        for name in names:
            row = current.get(name)
            if row is None:
                raise ValueError(f"{doctype} {name} no longer exists; nothing to undo")
            if str(row.get("modified")) != str(modified[name]):
                raise _changed_since(f"{doctype} {name}", row.get("modified"), modified[name])
            if row.get("docstatus"):
                raise ValueError(
                    f"{doctype} {name} was submitted or cancelled; a submitted document has "
                    "no safe inverse"
                )
        return {"action": "delete_document", "doctype": doctype, "names": names[::-1]}
    if op == "update_document":
        preview = update_document(undo["doctype"], undo["name"], undo["values"])
        current = preview.get("modified")
        what = f"{undo['doctype']} {undo['name']}"
    elif op == "set_field_property":
        preview = set_field_property(
            undo["doctype"], undo["property"], undo["value"], undo["fieldname"]
        )
        if preview.get("name") != undo["setter"]:
            raise ValueError(f"Property Setter {undo['setter']} no longer exists")
        current = preview.get("modified")
        what = f"Property Setter {undo['setter']}"
    elif op == "delete_doctype":
        preview = delete_doctype(undo["doctype"])
        current = preview.get("modified")
        what = f"DocType {undo['doctype']}"
    elif op == "update_doctype":
        preview = update_doctype(undo["doctype"], undo["operations"])
        current = preview.get("modified")
        what = f"DocType {undo['doctype']}"
    else:
        raise ValueError(f"Unknown inverse in the journal: {op!r}")
    if str(current) != str(undo["modified"]):
        raise _changed_since(what, current, undo["modified"])
    return preview


def _undo_apply(entry_id: str, undo: dict[str, Any]) -> Any:
    op = undo["op"]
    if op == "delete_documents":
        doctype, modified = undo["doctype"], undo["modified"]
        deleted: list[str] = []
        for name in reversed(list(modified)):
            try:
                delete_document(doctype, name, expected_modified=modified[name], confirm=True)
            except Exception as error:
                if deleted:
                    _journal_update(entry_id, functools.partial(_forget_deleted, deleted))
                raise RuntimeError(
                    f"Undo stopped after deleting {len(deleted)} of {len(modified)} {doctype} "
                    f"records, at {name}: {error}. The journal entry keeps the rest; preview "
                    "the undo again."
                ) from error
            deleted.append(name)
        return {"deleted": deleted, "doctype": doctype}
    if op == "update_document":
        return update_document(
            undo["doctype"],
            undo["name"],
            undo["values"],
            expected_modified=undo["modified"],
            confirm=True,
        )
    if op == "set_field_property":
        return set_field_property(
            undo["doctype"],
            undo["property"],
            undo["value"],
            undo["fieldname"],
            expected_modified=undo["modified"],
            confirm=True,
        )
    if op == "delete_doctype":
        return delete_doctype(undo["doctype"], expected_modified=undo["modified"], confirm=True)
    return update_doctype(
        undo["doctype"], undo["operations"], expected_modified=undo["modified"], confirm=True
    )


def _forget_deleted(deleted: list[str], entry: dict[str, Any]) -> None:
    """Drop records a stopped undo already deleted from the entry's inverse."""
    modified = entry["undo"]["modified"]
    entry["undo"]["modified"] = {
        name: value for name, value in modified.items() if name not in deleted
    }


def _journal_update(entry_id: str, change: Callable[[dict[str, Any]], Any]) -> None:
    entries = _journal_load()
    for entry in entries:
        if entry.get("id") == entry_id:
            change(entry)
    _journal_save(entries)


@_tool("data", "Undo MCP change", destructive=True)
def undo_mcp_change(
    entry_id: str, expected_preview: ExpectedPreview = None, confirm: Confirm = False
) -> dict[str, Any]:
    """Preview or undo one change from list_mcp_changes, through the tool that reverses it.

    Created records, Custom Fields, Property Setters, and DocTypes are deleted; updates and DocType
    edits are written back. Refused when the target changed since, or when the entry has no safe
    inverse. The preview returns `inverse` and `preview_id`.
    """
    global _journal_paused
    entries = _journal_load()
    entry = next((item for item in entries if item.get("id") == entry_id), None)
    if entry is None:
        raise ValueError(f"No journal entry {entry_id!r}; list_mcp_changes shows the ids")
    if entry.get("undone_at"):
        raise ValueError(f"Change {entry_id} was already undone at {entry['undone_at']}")
    undo = entry.get("undo")
    if not undo:
        raise ValueError(
            f"Change {entry_id} ({entry.get('tool')}) has no safe inverse: "
            f"{entry.get('no_undo') or 'none recorded'}"
        )
    tool = _UNDO_TOOLS.get(undo.get("op"), "")
    if tool not in server._tool_manager._tools:
        raise ValueError(f"Undoing this change needs {tool}, whose toolset is not selected")
    inverse = _undo_preview(undo)
    preview_id = hashlib.sha256(
        json.dumps(
            {"entry": entry_id, "undo": undo, "inverse": inverse}, sort_keys=True, default=str
        ).encode()
    ).hexdigest()
    if not confirm:
        return {
            "preview": True,
            "action": "undo_mcp_change",
            "change": _journal_row(entry),
            "tool": tool,
            "inverse": inverse,
            "preview_id": preview_id,
            "requires_confirmation": True,
        }
    if not expected_preview or expected_preview != preview_id:
        raise ValueError("The change or its target moved since the preview; preview again")
    _journal_paused = True
    try:
        result = _undo_apply(entry_id, undo)
    finally:
        _journal_paused = False
    undone_at = datetime.now().astimezone().isoformat(timespec="seconds")
    _journal_update(entry_id, lambda item: item.update(undone_at=undone_at))
    return {"undone": True, "change": entry_id, "tool": tool, "result": result}


_REPORT_MAX_ROWS = 500
_REPORT_LIST_LIMIT = 100
_REPORT_SCRIPT_CHARS = 8000
_REPORT_FILTER_KEYS = (
    "fieldname",
    "label",
    "fieldtype",
    "options",
    "mandatory",
    "default",
    "wildcard_filter",
)


def _assert_report_name(value: Any, what: str = "report_name") -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 140:
        raise ValueError(f"{what} must be 1-140 characters")
    return value.strip()


@_tool("read", "List reports")
def list_reports(
    search: str | None = None,
    ref_doctype: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List enabled reports a page at a time: name, ref_doctype, report_type, module, is_standard.

    `search` matches part of the name. Needs Desk access; run_report checks each report's roles.
    Returns {rows, offset, limit, has_more}.
    """
    if not 1 <= limit <= _REPORT_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {_REPORT_LIST_LIMIT}")
    _assert_offset(offset)
    filters: list[list[Any]] = [["disabled", "=", 0]]
    if search is not None:
        filters.append(["name", "like", f"%{_assert_report_name(search, 'search')}%"])
    if ref_doctype is not None:
        if not isinstance(ref_doctype, str) or not _SAFE_NAME.fullmatch(ref_doctype):
            raise ValueError("Invalid DocType name")
        filters.append(["ref_doctype", "=", ref_doctype])
    rows = _request(
        "GET",
        _resource("Report"),
        params={
            "fields": json.dumps(["name", "ref_doctype", "report_type", "module", "is_standard"]),
            "filters": _filters(filters),
            "order_by": "name asc",
            "limit_start": offset,
            "limit_page_length": limit + 1,
        },
    )
    return _page(rows, offset, limit)


@_tool("read", "Get report filters")
def get_report_filters(report_name: str) -> dict[str, Any]:
    """Read the filters a report takes, for run_report.

    Returns `filters` (the Report Filter rows of a Query Report) and `script`, the report's
    JavaScript cut to about 8,000 characters; Script Reports declare their filters there.
    """
    report_name = _assert_report_name(report_name)
    data = _request(
        "GET",
        "/api/method/frappe.desk.query_report.get_script",
        params={"report_name": report_name},
    )
    data = data if isinstance(data, dict) else {}
    rows = data.get("filters") if isinstance(data.get("filters"), list) else []
    filters = [
        {key: row.get(key) for key in _REPORT_FILTER_KEYS if not _is_empty(row.get(key))}
        for row in rows
        if isinstance(row, dict)
    ]
    script = data.get("script") if isinstance(data.get("script"), str) else ""
    return {
        "report_name": report_name,
        "filters": filters,
        "script": script[:_REPORT_SCRIPT_CHARS],
        "script_truncated": len(script) > _REPORT_SCRIPT_CHARS,
    }


@_tool("read", "Run report")
def run_report(
    report_name: str,
    filters: ReportFilters = None,
    limit: int = 200,
    offset: int = 0,
    include_chart: bool = False,
) -> dict[str, Any]:
    """Run an enabled report with Frappe's permission checks.

    `result` holds rows `offset` to `offset + limit`; `total_rows` counts them all and `has_more`
    says whether rows remain. `chart` only with include_chart.
    """
    if not 1 <= limit <= _REPORT_MAX_ROWS:
        raise ValueError(f"limit must be between 1 and {_REPORT_MAX_ROWS}")
    _assert_offset(offset)
    result = _request(
        "GET",
        "/api/method/frappe.desk.query_report.run",
        params={
            "report_name": _assert_report_name(report_name),
            "filters": json.dumps(filters or {}),
            "ignore_prepared_report": True,
        },
    )
    result = result if isinstance(result, dict) else {}
    rows = result.get("result") if isinstance(result.get("result"), list) else []
    if not include_chart:
        result.pop("chart", None)
    page = rows[offset : offset + limit]
    result.update(
        {
            "result": page,
            "total_rows": len(rows),
            "offset": offset,
            "limit": limit,
            "returned_rows": len(page),
            "has_more": len(rows) > offset + limit,
            "truncated": len(rows) > offset + limit,
        }
    )
    return result


@_tool("developer", "Create DocType")
def create_doctype(
    app: str,
    module: str,
    name: str,
    fields: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "DocFields in form order: fieldname, fieldtype, and optional properties "
                "(label, options, reqd, in_list_view, ...)."
            )
        ),
    ],
    is_submittable: bool = False,
    is_child_table: bool = False,
    is_single: bool = False,
    is_tree: bool = False,
    is_custom: bool = False,
    properties: dict[str, Any] | None = None,
    permissions: list[dict[str, Any]] | None = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or create a DocType.

    A standard DocType needs developer mode and the app's checkout under FRAPPE_APPS_PATH, where
    Frappe writes its JSON, controller, JavaScript, and test files; `is_custom` keeps it on the site
    only. `properties` takes DocType settings (autoname, track_changes, sort_field, search_fields,
    ...); `permissions` takes DocPerm rows.
    """
    if is_custom:
        _assert_remote_module(app, module)
        app_path = module_path = None
    else:
        _assert_writable_app(app)
        app_path, module_path = _assert_app_module(app, module)
    payload = _build_doctype_payload(
        module,
        name,
        fields,
        is_submittable=is_submittable,
        is_child_table=is_child_table,
        is_single=is_single,
        is_tree=is_tree,
        is_custom=is_custom,
        properties=properties,
        permissions=permissions,
        supported_fieldtypes=_site_fieldtypes(),
        supported_field_properties=_schema_fieldnames("DocField") & _DOCFIELD_PROPERTIES,
        supported_doctype_properties=_schema_fieldnames("DocType") & _DOCTYPE_PROPERTIES,
    )
    if _doctype_exists(name):
        raise ValueError(f"DocType already exists on the target site: {name}")
    if is_custom:
        doctype_path = None
        destination = None
        expected_files: list[Path] = []
    else:
        slug = _scrub(name)
        doctype_path = module_path / "doctype" / slug
        destination = doctype_path / f"{slug}.json"
        python_file = doctype_path / f"{slug}.py"
        javascript_file = doctype_path / f"{slug}.js"
        test_file = doctype_path / f"test_{slug}.py"
        expected_files = [destination, python_file]
        if not is_child_table:
            expected_files.extend([javascript_file, test_file])
        if destination.exists():
            raise ValueError(
                f"{app} already has {name}'s DocType file, but the site has no such DocType "
                "(list_app_drift reports it as missing_on_site): run `bench migrate` to load "
                "it instead of creating it again"
            )
        if any(path.exists() for path in expected_files):
            raise ValueError("Refusing to overwrite an existing DocType file")
        if doctype_path.exists() and any(doctype_path.iterdir()):
            raise ValueError("Refusing to reuse a non-empty DocType directory")
    if not confirm:
        return {
            "preview": True,
            "action": "create_doctype",
            "app": app,
            "module": module,
            "destination": str(destination) if destination is not None else None,
            "fields": len(fields),
            "is_submittable": is_submittable,
            "is_child_table": is_child_table,
            "is_single": is_single,
            "is_tree": is_tree,
            "is_custom": is_custom,
            "properties": properties or {},
            "permissions": permissions or [],
            "expected_native_files": [str(path) for path in expected_files],
            "requires_confirmation": True,
        }
    if is_custom:
        reconciled = _create_doctype_request(name, payload, expected_files)
        _forget_meta(name)
        if not _doctype_exists(name):
            raise RuntimeError("Frappe did not persist the custom DocType record")
        return _journal_new_doctype(
            {
                "created": True,
                "doctype": name,
                "app": app,
                "module": module,
                "is_custom": True,
                "files": {},
                "file_status": {},
                "native_files_complete": None,
                "reconciled_after_server_reload": reconciled,
            }
        )
    # Stage the exact templates shipped with this Frappe checkout before the
    # metadata request. Werkzeug watches Python files and can kill that request
    # mid-transaction when Frappe itself creates the scaffolding.
    try:
        _prepare_native_doctype_scaffolding(
            app_path,
            module_path,
            doctype_path,
            name,
            is_child_table=is_child_table,
            is_tree=is_tree,
        )
        _wait_for_server_ready()
        reconciled = _create_doctype_request(name, payload, expected_files)
        _forget_meta(name)
        if not _doctype_exists(name):
            raise RuntimeError(
                "Frappe did not persist the DocType record. Check the target app before retrying."
            )
    except Exception:
        try:
            persisted = _doctype_exists(name)
        except Exception:
            persisted = True
        if not persisted:
            _remove_partial_doctype_tree(doctype_path, expected_files)
        raise
    file_status = {
        "json": destination.is_file(),
        "python": python_file.is_file(),
        "javascript": javascript_file.is_file() if not is_child_table else None,
        "test": test_file.is_file() if not is_child_table else None,
    }
    return _journal_new_doctype(
        {
            "created": True,
            "doctype": name,
            "app": app,
            "files": {
                key: str(path)
                for key, path in {
                    "json": destination,
                    "python": python_file,
                    "javascript": javascript_file,
                    "test": test_file,
                }.items()
            },
            "file_status": file_status,
            "native_files_complete": all(value is not False for value in file_status.values()),
            "reconciled_after_server_reload": reconciled,
            "app_path": str(app_path),
        }
    )


def _journal_new_doctype(result: dict[str, Any]) -> dict[str, Any]:
    doctype = result["doctype"]
    return _journal(
        result,
        "create_doctype",
        {"doctype": doctype, "app": result.get("app")},
        undo=lambda: {
            "op": "delete_doctype",
            "doctype": doctype,
            "modified": _request("GET", _resource("DocType", doctype)).get("modified"),
        },
    )


_DOCTYPE_OPERATIONS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    # op: (required keys, optional keys)
    "add_field": (frozenset({"field"}), frozenset({"insert_after"})),
    "update_field": (frozenset({"fieldname", "set"}), frozenset()),
    "remove_field": (frozenset({"fieldname"}), frozenset()),
    "move_field": (frozenset({"fieldname", "insert_after"}), frozenset()),
    "set_properties": (frozenset({"set"}), frozenset()),
}


def _module_app(module: Any) -> str:
    if not isinstance(module, str) or not _SAFE_NAME.fullmatch(module):
        raise ValueError("DocType has no valid module")
    rows = _request(
        "GET",
        _resource("Module Def"),
        params={
            "fields": '["name","app_name"]',
            "filters": _filters({"name": module}),
            "limit_page_length": 2,
        },
    )
    if len(rows) != 1:
        raise ValueError(f"Unknown module on the target site: {module}")
    return rows[0].get("app_name") or ""


def _editable_doctype(doctype: str) -> tuple[dict[str, Any], str]:
    """Read a DocType record and its app, refusing standard DocTypes of Frappe and ERPNext."""
    if not isinstance(doctype, str) or not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")
    doc = _request("GET", _resource("DocType", doctype))
    app = _module_app(doc.get("module"))
    if not doc.get("custom") and app.casefold() in _PROTECTED_APPS:
        raise ValueError(
            f"{doctype} is a standard DocType of {app}; change it with customize_doctype "
            "(or create_custom_field and set_field_property) instead"
        )
    return doc, app


def _doctype_files(app: str, module: str, doctype: str) -> dict[str, Any]:
    """Locate the folder Frappe writes for a standard DocType, in the local checkout if any."""
    slug = _scrub(doctype)
    try:
        _, module_path = _app_path(app, module)
    except (RuntimeError, ValueError):
        folder = f"{app}/{app}/{_scrub(module)}/doctype/{slug}"
        return {"folder": folder, "json": f"{folder}/{slug}.json", "local_checkout": False}
    folder_path = module_path / "doctype" / slug
    return {
        "folder": str(folder_path),
        "json": str(folder_path / f"{slug}.json"),
        "local_checkout": True,
        "json_exists": (folder_path / f"{slug}.json").is_file(),
    }


def _blank(value: Any) -> bool:
    return value is None or value == "" or value == 0


def _same(left: Any, right: Any) -> bool:
    """Compare DocField values the way Frappe stores them: unset, "", and 0 are alike."""
    return left == right or (_blank(left) and _blank(right))


def _field_values(values: Any, where: str, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(values, dict) or not values:
        raise ValueError(f"{where} must be a non-empty object")
    if "fieldname" in values and "fieldname" not in allowed:
        raise ValueError(
            f"{where}: a fieldname cannot be changed here; rename it with rename(kind='field')"
        )
    unsupported = sorted(set(values) - allowed)
    if unsupported:
        raise ValueError(f"{where}: unsupported field properties: {', '.join(unsupported)}")
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if value is not None and not isinstance(value, str | int | float):
            raise ValueError(f"{where}: {key} must be a string, number, boolean, or null")
        cleaned[key] = int(value) if isinstance(value, bool) else value
    return cleaned


def _check_field_row(
    row: dict[str, Any],
    where: str,
    values: dict[str, Any],
    supported_fieldtypes: frozenset[str] = _FIELDTYPES,
) -> None:
    fieldtype = row.get("fieldtype")
    if fieldtype not in supported_fieldtypes:
        raise ValueError(f"{where}: unsupported fieldtype {fieldtype!r}")
    if fieldtype in _OPTIONS_REQUIRED_FIELDTYPES and not row.get("options"):
        raise ValueError(f"{where}: {fieldtype} fields need options")
    if (
        fieldtype in _CODE_OPTIONS_FIELDTYPES
        and row.get("options")
        and {"options", "fieldtype"} & set(values)
    ):
        raise ValueError(f"{where}: update_doctype does not set options on {fieldtype} fields")


def _field_index(rows: list[dict[str, Any]], fieldname: Any, where: str) -> int:
    for index, row in enumerate(rows):
        if row.get("fieldname") == fieldname:
            return index
    raise ValueError(f"{where}: no field named {fieldname!r} in the DocType")


def _insert_position(rows: list[dict[str, Any]], insert_after: Any, where: str) -> int:
    """`insert_after: null` means first; otherwise the position after that field."""
    if insert_after is None:
        return 0
    return _field_index(rows, insert_after, f"{where}: insert_after") + 1


def _plan_doctype_update(
    doc: dict[str, Any],
    operations: Any,
    taken: set[str],
    *,
    supported_fieldtypes: frozenset[str] = _FIELDTYPES,
    supported_field_properties: frozenset[str] = _DOCFIELD_PROPERTIES,
    supported_doctype_properties: frozenset[str] = _DOCTYPE_PROPERTIES,
) -> dict[str, Any]:
    """Apply `operations` in order to a copy of a DocType record's fields and settings.

    `taken` holds fieldnames the DocType already has outside its own record (Custom Fields).
    Returns the new `rows`, the DocType `properties` to set, the fields `moved` explicitly,
    and the keys each operation `touched` per field.
    """
    if not isinstance(operations, list) or not (
        1 <= len(operations) <= _UPDATE_DOCTYPE_MAX_OPERATIONS
    ):
        raise ValueError(
            f"operations must be a list of 1 to {_UPDATE_DOCTYPE_MAX_OPERATIONS} operations"
        )
    rows = [dict(row) for row in doc.get("fields") or []]
    properties: dict[str, Any] = {}
    moved: list[str] = []
    touched: dict[str, set[str]] = {}
    for index, operation in enumerate(operations):
        where = f"operations[{index}]"
        if not isinstance(operation, dict) or operation.get("op") not in _DOCTYPE_OPERATIONS:
            raise ValueError(
                f"{where} must be an object whose op is one of: {', '.join(_DOCTYPE_OPERATIONS)}"
            )
        op = operation["op"]
        required, optional = _DOCTYPE_OPERATIONS[op]
        missing = sorted(required - set(operation))
        extra = sorted(set(operation) - required - optional - {"op"})
        if missing or extra:
            raise ValueError(
                f"{where}: {op} takes {', '.join(sorted(required | optional))}"
                + (f"; missing {', '.join(missing)}" if missing else "")
                + (f"; unexpected {', '.join(extra)}" if extra else "")
            )
        if op == "add_field":
            values = _field_values(
                operation["field"], f"{where}.field", supported_field_properties | {"fieldname"}
            )
            fieldname = values.get("fieldname")
            if not isinstance(fieldname, str) or not _NEW_FIELDNAME.fullmatch(fieldname):
                raise ValueError(
                    f"{where}: fieldname must be lowercase letters, digits, and underscores, "
                    "starting with a letter, at most 64 characters"
                )
            if fieldname in taken or any(row.get("fieldname") == fieldname for row in rows):
                raise ValueError(f"{where}: {doc.get('name')} already has a field {fieldname}")
            row = {"doctype": "DocField", **values}
            _check_field_row(row, where, values, supported_fieldtypes)
            position = (
                _insert_position(rows, operation["insert_after"], where)
                if "insert_after" in operation
                else len(rows)
            )
            rows.insert(position, row)
            touched.setdefault(fieldname, set()).update(values)
        elif op == "update_field":
            row = rows[_field_index(rows, operation["fieldname"], where)]
            values = _field_values(operation["set"], f"{where}.set", supported_field_properties)
            row.update(values)
            _check_field_row(row, where, values, supported_fieldtypes)
            touched.setdefault(row["fieldname"], set()).update(values)
        elif op == "remove_field":
            row = rows.pop(_field_index(rows, operation["fieldname"], where))
            touched.pop(row["fieldname"], None)
        elif op == "move_field":
            if operation["insert_after"] == operation["fieldname"]:
                raise ValueError(f"{where}: a field cannot be inserted after itself")
            row = rows.pop(_field_index(rows, operation["fieldname"], where))
            rows.insert(_insert_position(rows, operation["insert_after"], where), row)
            moved.append(row["fieldname"])
        else:
            values = operation["set"]
            if not isinstance(values, dict) or not values:
                raise ValueError(f"{where}.set must be a non-empty object")
            unsupported = sorted(
                set(values) - (supported_doctype_properties - _DOCTYPE_TABLE_PROPERTIES)
            )
            if unsupported:
                raise ValueError(
                    f"{where}: unsupported DocType properties: {', '.join(unsupported)}"
                )
            for key, value in values.items():
                if value is not None and not isinstance(value, str | int | float):
                    raise ValueError(f"{where}: {key} must be a string, number, boolean, or null")
                properties[key] = int(value) if isinstance(value, bool) else value
    return {"rows": rows, "properties": properties, "moved": moved, "touched": touched}


def _doctype_diff(doc: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    old_rows = doc.get("fields") or []
    new_rows = plan["rows"]
    before = {row.get("fieldname"): row for row in old_rows}
    after = {row.get("fieldname"): row for row in new_rows}

    def previous(rows: list[dict[str, Any]], fieldname: str) -> str | None:
        index = next(i for i, row in enumerate(rows) if row.get("fieldname") == fieldname)
        return rows[index - 1].get("fieldname") if index else None

    added = [
        {
            "fieldname": row["fieldname"],
            "fieldtype": row.get("fieldtype"),
            "label": row.get("label"),
            "insert_after": previous(new_rows, row["fieldname"]),
        }
        for row in new_rows
        if row.get("fieldname") not in before
    ]
    removed = [
        {
            "fieldname": name,
            "fieldtype": row.get("fieldtype"),
            "label": row.get("label"),
            **(
                {"options": row.get("options")} if row.get("fieldtype") in _TABLE_FIELDTYPES else {}
            ),
        }
        for name, row in before.items()
        if name not in after
    ]
    moved = [
        {"fieldname": name, "insert_after": previous(new_rows, name)}
        for name in dict.fromkeys(plan["moved"])
        if name in before and name in after and previous(new_rows, name) != previous(old_rows, name)
    ]
    changed: dict[str, dict[str, Any]] = {}
    for name, row in after.items():
        old = before.get(name)
        if old is None:
            continue
        delta = {
            key: {"from": old.get(key), "to": row.get(key)}
            for key in sorted(_DOCFIELD_PROPERTIES)
            if not _same(old.get(key), row.get(key))
        }
        if delta:
            changed[name] = delta
    properties = {
        key: {"from": doc.get(key), "to": value}
        for key, value in plan["properties"].items()
        if not _same(doc.get(key), value)
    }
    return {
        "added": added,
        "removed": removed,
        "moved": moved,
        "changed": changed,
        "properties": properties,
    }


def _check_link_targets(
    doctype: str, before: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
) -> list[str]:
    """Check the target DocType of each new or changed Link and Table field.

    Returns the child DocTypes of Table fields that were added, removed, or changed.
    """
    targets: dict[str, tuple[str, str]] = {}
    children: set[str] = set()
    for row in rows:
        fieldtype = row.get("fieldtype")
        old = before.get(row.get("fieldname"))
        if fieldtype not in {"Link", *_TABLE_FIELDTYPES}:
            continue
        if old and old.get("fieldtype") == fieldtype and old.get("options") == row.get("options"):
            continue
        target = row.get("options")
        if fieldtype == "Link" and target == doctype:
            continue
        if not isinstance(target, str) or not _SAFE_NAME.fullmatch(target):
            raise ValueError(f"{row.get('fieldname')}: options must name a DocType, got {target!r}")
        targets[row["fieldname"]] = (fieldtype, target)
        if fieldtype in _TABLE_FIELDTYPES:
            children.add(target)
    after = {row.get("fieldname"): row for row in rows}
    for name in before.keys() | after.keys():
        old, new = before.get(name), after.get(name)
        if (
            old
            and new
            and (old.get("fieldtype"), old.get("options"))
            == (new.get("fieldtype"), new.get("options"))
        ):
            continue
        children.update(
            row.get("options")
            for row in (old, new)
            if row and row.get("fieldtype") in _TABLE_FIELDTYPES
        )
    if targets:
        names = sorted({target for _, target in targets.values()})
        found = {
            row.get("name"): row
            for row in _request(
                "GET",
                _resource("DocType"),
                params={
                    "fields": '["name","istable"]',
                    "filters": _filters([["name", "in", names]]),
                    "limit_page_length": len(names),
                },
            )
        }
        for fieldname, (fieldtype, target) in targets.items():
            if target not in found:
                raise ValueError(f"{fieldname}: options names an unknown DocType: {target}")
            if fieldtype in _TABLE_FIELDTYPES and not found[target].get("istable"):
                raise ValueError(
                    f"{fieldname}: a {fieldtype} field needs a child table DocType "
                    f"(istable=1); {target} is not one"
                )
    return sorted(child for child in children if child)


def _count_records(doctype: str, filters: list[list[Any]]) -> int | None:
    """Count records through `frappe.client.get_count`; None when Frappe would not say."""
    try:
        count = _get_count(doctype, filters)
    except RuntimeError:
        return None
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def _review_doctype_update(doc: dict[str, Any], diff: dict[str, Any]) -> list[str]:
    """Count the data a change touches, warn about it, and refuse type changes that lose it."""
    doctype = doc["name"]
    countable = not doc.get("issingle") and not doc.get("is_virtual")

    def count(filters: list[list[Any]]) -> int | None:
        return _count_records(doctype, filters) if countable else None

    warnings: list[str] = []
    for item in diff["removed"]:
        fieldname, fieldtype = item["fieldname"], item["fieldtype"]
        if fieldtype in _LAYOUT_FIELDTYPES:
            continue
        if fieldtype in _TABLE_FIELDTYPES:
            warnings.append(
                f"Removing table {fieldname}: its rows stay in the {item.get('options')} "
                "table, but no form or API shows them again."
            )
            continue
        item["records_with_value"] = filled = count([[fieldname, "is", "set"]])
        held = "an unknown number of" if filled is None else str(filled)
        warnings.append(
            f"Removing {fieldname}: {held} records have a value. Frappe keeps the database "
            "column and its values (until `bench trim-tables`), but forms, reports, and the "
            "API stop showing them."
        )
    for fieldname, delta in diff["changed"].items():
        if "fieldtype" in delta:
            old, new = delta["fieldtype"]["from"], delta["fieldtype"]["to"]
            if any(old in group and new in group for group in _FIELDTYPE_CHANGE_GROUPS):
                warnings.append(
                    f"{fieldname} changes from {old} to {new}; Frappe may change the column "
                    "type, which Customize Form allows for these two types."
                )
            elif old in _LAYOUT_FIELDTYPES:
                pass
            elif old in _TABLE_FIELDTYPES or new in _TABLE_FIELDTYPES:
                raise ValueError(
                    f"Changing {fieldname} from {old} to {new} is not supported; "
                    "add a new field instead"
                )
            else:
                filled = count([[fieldname, "is", "set"]])
                if filled != 0:
                    held = "an unknown number of" if filled is None else str(filled)
                    raise ValueError(
                        f"Changing {fieldname} from {old} to {new} can lose data and {held} "
                        "records have a value. Frappe's Customize Form allows only changes "
                        "between related types; clear the values first or add a new field."
                    )
                warnings.append(
                    f"{fieldname} changes from {old} to {new}; no record has a value, so the "
                    "column type change loses nothing."
                )
        if "reqd" in delta and delta["reqd"]["to"]:
            empty = count([[fieldname, "is", "not set"]])
            if empty != 0:
                held = "an unknown number of" if empty is None else str(empty)
                warnings.append(
                    f"{fieldname} becomes mandatory and {held} records leave it empty; saving "
                    "them fails until it is filled."
                )
        if "unique" in delta and delta["unique"]["to"]:
            warnings.append(
                f"{fieldname} becomes unique: Frappe adds a unique index, and the save fails "
                "if existing values repeat."
            )
    return warnings


def _doctype_matches(current: dict[str, Any], plan: dict[str, Any], expected_modified: str) -> bool:
    """Tell whether a re-read DocType holds the planned change, for reload reconciliation."""
    if str(current.get("modified")) == expected_modified:
        return False
    saved = current.get("fields") or []
    if [row.get("fieldname") for row in saved] != [row.get("fieldname") for row in plan["rows"]]:
        return False
    for row in saved:
        wanted = next(want for want in plan["rows"] if want["fieldname"] == row["fieldname"])
        keys = plan["touched"].get(row["fieldname"], set())
        if any(not _same(row.get(key), wanted.get(key)) for key in keys):
            return False
    return all(_same(current.get(key), value) for key, value in plan["properties"].items())


@_tool("developer", "Edit DocType", destructive=True)
def update_doctype(
    doctype: str,
    operations: list[dict[str, Any]],
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or apply ordered `operations` to a DocType of your own app or a site-custom one.

    For Frappe and ERPNext DocTypes use create_custom_field and set_field_property. Saved in one
    request:
    - {"op": "add_field", "field": {...}, "insert_after": fieldname} (omitted: last; null: first)
    - {"op": "update_field", "fieldname": ..., "set": {...}} (fieldnames change with rename)
    - {"op": "remove_field", "fieldname": ...}
    - {"op": "move_field", "fieldname": ..., "insert_after": fieldname or null}
    - {"op": "set_properties", "set": {...}} for DocType settings
    Lossy fieldtype changes are refused. The preview returns `changes`, `warnings` with record
    counts, and `modified`; confirm with the same operations. Standard DocTypes need developer mode.
    """
    doc, app = _editable_doctype(doctype)
    if confirm and (not expected_modified or str(doc.get("modified")) != expected_modified):
        raise ValueError(_STALE_DOCUMENT)
    own = {row.get("fieldname") for row in doc.get("fields") or []}
    custom_fields = {
        row.get("fieldname")
        for row in _meta_bundle(doctype)["doctype"].get("fields", [])
        if row.get("fieldname")
    } - own
    plan = _plan_doctype_update(
        doc,
        operations,
        custom_fields,
        supported_fieldtypes=_site_fieldtypes(),
        supported_field_properties=_schema_fieldnames("DocField") & _DOCFIELD_PROPERTIES,
        supported_doctype_properties=_schema_fieldnames("DocType") & _DOCTYPE_PROPERTIES,
    )
    before = {row.get("fieldname"): row for row in doc.get("fields") or []}
    children = _check_link_targets(doctype, before, plan["rows"])
    changes = _doctype_diff(doc, plan)
    if not any(changes.values()):
        raise ValueError("The operations leave the DocType unchanged")
    warnings = _review_doctype_update(doc, changes)
    if not confirm:
        preview: dict[str, Any] = {
            "preview": True,
            "action": "update_doctype",
            "doctype": doctype,
            "app": app,
            "module": doc.get("module"),
            "custom": bool(doc.get("custom")),
            "modified": doc.get("modified"),
            "changes": changes,
            "warnings": warnings,
            "requires_confirmation": True,
        }
        if not doc.get("custom"):
            preview["app_files"] = _doctype_files(app, doc["module"], doctype)
            preview["requires_developer_mode"] = True
            preview["notes"] = [
                "Frappe saves standard DocTypes only in developer mode, then rewrites the "
                "DocType JSON in the app (and the controller's type annotations when the app "
                "sets export_python_type_annotations). A developer-mode Bench may reload.",
            ]
        return preview
    rows = [{**row, "idx": index} for index, row in enumerate(plan["rows"], start=1)]
    reconciled = False
    try:
        # Sending `modified` makes Frappe compare it with the stored value under the row lock.
        saved = _request(
            "PUT",
            _resource("DocType", doctype),
            json={"data": {**plan["properties"], "fields": rows, "modified": expected_modified}},
        )
    except FrappeError as error:
        if error.exc_type == "TimestampMismatchError":
            raise ValueError(_STALE_DOCUMENT) from error
        if error.exc_type == "CannotCreateStandardDoctypeError":
            raise ValueError(
                f"Frappe refused to save {doctype}: standard DocTypes can only be changed "
                f"with developer mode on ({error})"
            ) from error
        raise
    except httpx.TransportError as error:
        # A developer-mode Bench can reload while Frappe writes the DocType files; find out
        # what was saved instead of sending the change again.
        _wait_for_server_ready()
        saved = _request("GET", _resource("DocType", doctype))
        if not _doctype_matches(saved, plan, expected_modified):
            raise RuntimeError(
                "Frappe disconnected while saving the DocType and the saved DocType does not "
                "hold the change; request a fresh preview before retrying."
            ) from error
        reconciled = True
    finally:
        for name in (doctype, *children):
            _forget_meta(name)
    modified = saved.get("modified") if isinstance(saved, dict) else None
    return _journal(
        {
            "updated": True,
            "doctype": doctype,
            "modified": modified,
            "changes": changes,
            "warnings": warnings,
            "reconciled_after_server_reload": reconciled,
        },
        "update_doctype",
        {"doctype": doctype, "app": app},
        undo=lambda: {
            "op": "update_doctype",
            "doctype": doctype,
            "operations": _inverse_doctype_operations(doc, plan, changes),
            "modified": modified,
        },
    )


def _doctype_links(doctype: str) -> list[dict[str, Any]]:
    """List the Link and Table fields of other DocTypes, Custom Fields included, using it."""
    filters = _filters(
        [["options", "=", doctype], ["fieldtype", "in", ["Link", *sorted(_TABLE_FIELDTYPES)]]]
    )
    standard = _request(
        "GET",
        _resource("DocField"),
        params={
            "fields": '["parent","fieldname","fieldtype"]',
            "filters": filters,
            "parent": "DocType",
            "limit_page_length": _DOCTYPE_LINKS_LIMIT + 1,
        },
    )
    custom = _request(
        "GET",
        _resource("Custom Field"),
        params={
            "fields": '["dt","fieldname","fieldtype"]',
            "filters": filters,
            "limit_page_length": _DOCTYPE_LINKS_LIMIT + 1,
        },
    )
    links = [
        {
            "doctype": row.get("parent"),
            "fieldname": row.get("fieldname"),
            "fieldtype": row.get("fieldtype"),
        }
        for row in standard
        if row.get("parent") != doctype
    ] + [
        {
            "doctype": row.get("dt"),
            "fieldname": row.get("fieldname"),
            "fieldtype": row.get("fieldtype"),
            "custom_field": True,
        }
        for row in custom
        if row.get("dt") != doctype
    ]
    return sorted(links, key=lambda link: (str(link["doctype"]), str(link["fieldname"])))


@_tool("developer", "Delete DocType", destructive=True, idempotent=True)
def delete_doctype(
    doctype: str, expected_modified: ExpectedModified = None, confirm: Confirm = False
) -> dict[str, Any]:
    """Preview or delete a DocType created by mistake.

    Refuses Frappe and ERPNext DocTypes and DocTypes others use through Link or Table fields. Frappe
    deletes the record and, for a standard DocType in developer mode, its app folder; the table, its
    records, and its customizations stay. Standard DocTypes: Administrator only.
    """
    doc, app = _editable_doctype(doctype)
    links = _doctype_links(doctype)
    if links:
        listed = ", ".join(
            f"{link['doctype']}.{link['fieldname']} ({link['fieldtype']})"
            for link in links[:_DOCTYPE_LINKS_LIMIT]
        )
        more = " and more" if len(links) > _DOCTYPE_LINKS_LIMIT else ""
        raise ValueError(
            f"Other DocTypes use {doctype}; remove or repoint these fields first: {listed}{more}"
        )
    custom = bool(doc.get("custom"))
    countable = not doc.get("issingle") and not doc.get("is_virtual")
    records = _count_records(doctype, []) if countable else None
    leftovers = {
        "custom_fields": _count_records("Custom Field", [["dt", "=", doctype]]),
        "property_setters": _count_records("Property Setter", [["doc_type", "=", doctype]]),
    }
    removes = [
        "the DocType record with its fields, permissions, actions, links, and states",
        "Table-type Custom Fields on other DocTypes that point to it",
        "its global search entries",
    ]
    files = None if custom else _doctype_files(app, doc["module"], doctype)
    if files is not None:
        removes.append(
            f"in developer mode, the folder {files['folder']} with its JSON, controller, "
            "JavaScript, and test files"
        )
    keeps = [
        f"the database table `tab{doctype}` and its records",
        "Custom Fields and Property Setters defined on this DocType",
    ]
    if not confirm:
        preview: dict[str, Any] = {
            "preview": True,
            "action": "delete_doctype",
            "doctype": doctype,
            "app": app,
            "module": doc.get("module"),
            "custom": custom,
            "istable": bool(doc.get("istable")),
            "modified": doc.get("modified"),
            "records": records,
            "customizations_left": leftovers,
            "frappe_removes": removes,
            "frappe_keeps": keeps,
            "requires_confirmation": True,
        }
        if files is not None:
            preview["app_files"] = files
            preview["requires_developer_mode"] = True
            preview["requires_administrator"] = True
        return preview
    if not expected_modified or str(doc.get("modified")) != expected_modified:
        raise ValueError(_STALE_DOCUMENT)
    reconciled = False
    folder = Path(files["folder"]) if files and files.get("local_checkout") else None
    with tempfile.TemporaryDirectory(prefix="mcp-frappe-doctype-delete-") as temporary:
        backup = Path(temporary) / "doctype"
        if folder is not None and folder.is_dir():
            shutil.copytree(folder, backup, symlinks=True)
        try:
            _request("DELETE", _resource("DocType", doctype))
        except httpx.TransportError as error:
            # Deleting a standard DocType removes app files, which can reload a dev Bench.
            if not _wait_for_document_deleted("DocType", doctype):
                restored = False
                if folder is not None and backup.is_dir():
                    try:
                        restored = _restore_doctype_files_after_failed_delete(folder, backup)
                    except RuntimeError as recovery_error:
                        retained = _retain_doctype_snapshot(backup)
                        snapshot = f" Snapshot retained at {retained}." if retained else ""
                        raise RuntimeError(f"{recovery_error}.{snapshot}") from error
                detail = "; original app files restored" if restored else ""
                raise RuntimeError(
                    "Frappe disconnected during deletion and the DocType still exists"
                    f"{detail}; request a fresh preview before retrying."
                ) from error
            reconciled = True
        finally:
            _forget_meta(doctype)
    return {
        "deleted": True,
        "doctype": doctype,
        "reconciled_after_server_reload": reconciled,
    }


# Renames through Frappe's own code paths, which update what points at the renamed thing.

_RENAME_DOCUMENT = "/api/method/frappe.model.rename_doc.update_document_title"
_RENAME_CUSTOM_FIELD = (
    "/api/method/frappe.custom.doctype.custom_field.custom_field.rename_fieldname"
)
_RENAME_NAME_MAX = 140
_RENAME_LINK_COUNTS = 50
_RENAME_MENTIONS_MAX = 50
# DocType settings that hold one fieldname; a field rename carries them along.
_FIELD_REFERENCE_PROPERTIES = (
    "title_field",
    "image_field",
    "sort_field",
    "timeline_field",
    "subject_field",
    "sender_field",
    "sender_name_field",
    "recipient_account_field",
    "website_search_field",
    "is_published_field",
    "nsm_parent_field",
)
# DocField properties that name other fields inside an expression.
_FIELD_EXPRESSION_PROPERTIES = (
    "depends_on",
    "mandatory_depends_on",
    "read_only_depends_on",
    "collapsible_depends_on",
    "fetch_from",
    "link_filters",
)
_PATCH_MODULE = re.compile(r"[a-z_][a-z0-9_]{0,199}")
# The only Python this server writes: a fixed template, filled with checked identifiers as
# string literals (repr), that calls Frappe's own `rename_field` from a patch.
_RENAME_FIELD_PATCH = """\
# Generated by mcp-frappe: renames the {doctype} field {old} to {new}.
import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
\tfrappe.reload_doc({module!r}, "doctype", {slug!r})
\trename_field({doctype!r}, {old!r}, {new!r})
"""


def _rename_name(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _RENAME_NAME_MAX:
        raise ValueError(f"{what} must be a non-empty string of at most {_RENAME_NAME_MAX} chars")
    if value != value.strip() or "\n" in value:
        raise ValueError(f"{what} must not have surrounding spaces or line breaks")
    return value


def _mentions(text: Any, name: str) -> bool:
    return isinstance(text, str) and re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text)


def _incoming_links(doctype: str, old: str) -> dict[str, Any]:
    """Count records whose Link fields hold `old` of `doctype`, per field."""
    links = [link for link in _doctype_links(doctype) if link["fieldtype"] == "Link"]
    shown = links[:_RENAME_LINK_COUNTS]
    for link in shown:
        link["records"] = _count_records(link["doctype"], [[link["fieldname"], "=", old]])
    counts = [link["records"] for link in shown]
    total = None if None in counts or len(links) > len(shown) else sum(counts)
    return {"links": shown, "links_truncated": len(links) > len(shown), "linked_records": total}


def _app_mentions(base: Path, pattern: re.Pattern[str], skip: Path | None = None) -> list[Any]:
    """List the app files under `base` that match `pattern`, with line numbers (bounded).

    Symlinks are not followed, and hidden, test, and cache folders are skipped.
    """
    found: list[dict[str, Any]] = []
    seen = 0
    for folder, dirs, files in os.walk(base):
        current = Path(folder)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in _METHOD_SKIP_DIRS
            and not name.startswith(".")
            and (skip is None or current / name != skip)
        )
        for name in sorted(files):
            path = current / name
            if path.suffix not in (".py", ".js", ".json", ".txt", ".html") or path.is_symlink():
                continue
            seen += 1
            if seen > _METHOD_MAX_FILES or len(found) >= _RENAME_MENTIONS_MAX:
                return found
            if path.stat().st_size > _METHOD_FILE_MAX_BYTES:
                continue
            text = path.read_text(errors="replace")
            lines = [n for n, line in enumerate(text.splitlines(), 1) if pattern.search(line)]
            if lines:
                found.append({"file": path.relative_to(base).as_posix(), "lines": lines[:20]})
    return found


def _append_patch(text: str, patch: str) -> str:
    """Add `patch` at the end of patches.txt's [post_model_sync] section (or the file)."""
    if not text.strip():
        return f"[pre_model_sync]\n\n[post_model_sync]\n{patch}\n"
    file_format, _, _ = _parse_patches(text)
    lines = text.splitlines()
    if file_format == "sections":
        start = next(
            (i for i, line in enumerate(lines) if line.strip() == "[post_model_sync]"), None
        )
        if start is None:
            lines += ["", "[post_model_sync]", patch]
        else:
            end = next(
                (
                    i
                    for i in range(start + 1, len(lines))
                    if _PATCH_SECTION_HEADER.match(lines[i].strip())
                ),
                len(lines),
            )
            while end > start + 1 and (
                not lines[end - 1].strip() or lines[end - 1].lstrip().startswith(("#", ";"))
            ):
                end -= 1
            lines.insert(end, patch)
    else:
        lines.append(patch)
    return "\n".join(lines) + "\n"


def _rename_document_plan(doctype: str, old: str, new: str, merge: bool) -> dict[str, Any]:
    if doctype == "DocType":
        raise ValueError("Rename a DocType with kind='doctype'")
    if doctype in _CODE_DOCTYPES or doctype in _SCHEMA_DOCTYPES or doctype == "Customize Form":
        raise ValueError(f"rename does not rename {doctype} records")
    meta = _meta_bundle(doctype)["doctype"]
    if meta.get("issingle") or meta.get("istable"):
        raise ValueError(f"{doctype} records are not renamed on their own")
    if not meta.get("allow_rename"):
        raise ValueError(
            f"{doctype} does not allow renaming (its Allow Rename setting is off); Frappe refuses"
        )
    current = _request("GET", _resource(doctype, old))
    exists = _count_records(doctype, [["name", "=", new]])
    if merge and exists == 0:
        raise ValueError(f"merge needs an existing {doctype} {new}")
    if not merge and exists:
        raise ValueError(f"{doctype} {new} already exists; pass merge=true to merge into it")
    return {
        "modified": current.get("modified"),
        **_incoming_links(doctype, old),
        "frappe_updates": [
            "Link fields that point to it, and Dynamic Links",
            "its child rows, attachments, versions, and assignments",
        ],
        "notes": [
            "Frappe may adjust the new name (naming rules, the DocType's before_rename hook)."
        ],
    }


def _rename_custom_field_plan(doctype: str, old: str, new: str) -> dict[str, Any]:
    meta = _meta_bundle(doctype)["doctype"]
    fields = {row.get("fieldname"): row for row in meta.get("fields") or []}
    rows = _listed(
        "Custom Field",
        ["name", "fieldname", "fieldtype", "is_system_generated", "modified"],
        {"dt": doctype, "fieldname": old},
    )
    if not rows:
        if old in fields:
            raise ValueError(
                f"{doctype}.{old} is a standard field, not a Custom Field; kind='field' renames "
                "fields of your own app's DocTypes"
            )
        raise ValueError(f"{doctype} has no Custom Field {old}")
    row = rows[0]
    if row.get("is_system_generated"):
        raise ValueError("Frappe does not rename system-generated Custom Fields")
    if not _NEW_FIELDNAME.fullmatch(new):
        raise ValueError("new must be lowercase letters, digits, and underscores, at most 64")
    if new in fields:
        raise ValueError(f"{doctype} already has a field {new}")
    countable = not meta.get("issingle") and not meta.get("is_virtual")
    has_value = row.get("fieldtype") not in _NO_VALUE_FIELDTYPES and countable
    setters = _listed(
        "Property Setter", ["name", "field_name", "property", "value"], {"doc_type": doctype}
    )
    stale = [
        f"Property Setter {row['property']} on {old}"
        for row in setters
        if row.get("field_name") == old
    ]
    stale += [
        f"field_order Property Setter of {doctype}"
        for row in setters
        if row.get("property") == "field_order" and old in (_json_list(row.get("value")) or [])
    ]
    stale += [
        f"DocType setting {prop}" for prop in _FIELD_REFERENCE_PROPERTIES if meta.get(prop) == old
    ]
    stale += [
        f"{other}.{prop}"
        for other, other_row in fields.items()
        for prop in _FIELD_EXPRESSION_PROPERTIES
        if other != old and _mentions(other_row.get(prop), old)
    ]
    warnings = []
    if stale:
        warnings.append(
            "Frappe renames the column and updates insert_after of other Custom Fields, but not: "
            + ", ".join(stale[:_RENAME_MENTIONS_MAX])
            + ". Update them afterwards (customize_doctype or set_field_property)."
        )
    return {
        "modified": row.get("modified"),
        "custom_field": row["name"],
        "fieldtype": row.get("fieldtype"),
        "records_with_value": _count_records(doctype, [[old, "is", "set"]]) if has_value else None,
        "warnings": warnings,
        "notes": [
            f"The Custom Field record keeps its name {row['name']}; only its fieldname changes.",
            "Export the customization again for apps that ship it as a fixture.",
        ],
    }


def _unused_patch_name(app: str, name: str) -> str:
    """`name`, or `name_2`, `name_3`, ... : the first that the site's Patch Log hasn't recorded.

    `bench migrate` skips a patch already in Patch Log, so a field renamed, undone, and renamed
    again would otherwise never get its values copied.
    """
    prefix = f"{app}.patches.{name}"
    rows = _request(
        "GET",
        _resource("Patch Log"),
        params={
            "fields": '["patch"]',
            "filters": json.dumps([["patch", "like", f"{prefix}%"]]),
            "limit_page_length": _PATCH_LOG_PAGE,
        },
    )
    rows = rows if isinstance(rows, list) else []
    ran = {row.get("patch") for row in rows if isinstance(row, dict)}
    candidate, number = name, 1
    while f"{app}.patches.{candidate}" in ran:
        number += 1
        candidate = f"{name}_{number}"
    return candidate


def _rename_field_plan(doctype: str, old: str, new: str) -> dict[str, Any]:
    doc, app = _editable_doctype(doctype)
    if doc.get("custom"):
        raise ValueError(
            f"{doctype} is site-custom: there is no app to hold the patch that moves the data. "
            "Promote it with promote_custom_doctype first, or add a new field instead."
        )
    if not _PATCH_MODULE.fullmatch(app):
        raise ValueError(f"App name {app!r} is not a Python package name")
    app_path, package_path = _app_root(app)
    rows = [dict(row) for row in doc.get("fields") or []]
    row = rows[_field_index(rows, old, "old")]
    if not _NEW_FIELDNAME.fullmatch(new):
        raise ValueError("new must be lowercase letters, digits, and underscores, at most 64")
    taken = {row.get("fieldname") for row in _meta_bundle(doctype)["doctype"].get("fields", [])}
    if new in taken or any(other.get("fieldname") == new for other in rows):
        raise ValueError(f"{doctype} already has a field {new}")
    row["fieldname"] = new
    properties = {prop: new for prop in _FIELD_REFERENCE_PROPERTIES if doc.get(prop) == old}
    search = doc.get("search_fields")
    if _mentions(search, old):
        properties["search_fields"] = ",".join(
            new if part.strip() == old else part.strip() for part in search.split(",")
        )
    slug, module_slug = _scrub(doctype), _scrub(doc["module"])
    patch_name = f"rename_{slug}_{old}_to_{new}"
    if not _PATCH_MODULE.fullmatch(patch_name) or not _PATCH_MODULE.fullmatch(module_slug):
        raise ValueError(f"{doctype} or its module is not a Python identifier once scrubbed")
    patch_name = _unused_patch_name(app, patch_name)
    patch = f"{app}.patches.{patch_name}"
    patches_dir = package_path / "patches"
    patch_path = _safe_app_file(app_path, patches_dir / f"{patch_name}.py")
    if patch_path.exists():
        raise ValueError(f"{patch_path} already exists")
    patches_txt = _app_file(package_path, "patches.txt")
    text = patches_txt.read_text(encoding="utf-8") if patches_txt else ""
    if patch in {entry["patch"] for entry in _parse_patches(text)[1]}:
        raise ValueError(f"patches.txt already lists {patch}")
    content = _RENAME_FIELD_PATCH.format(
        doctype=doctype, old=old, new=new, module=module_slug, slug=slug
    )
    payloads = [(patch_path, content)]
    init = _safe_app_file(app_path, patches_dir / "__init__.py")
    if not init.exists():
        payloads.append((init, ""))
    patches_path = _safe_app_file(app_path, package_path / "patches.txt")
    payloads.append((patches_path, _append_patch(text, patch)))
    countable = not doc.get("issingle") and not doc.get("is_virtual")
    has_value = row.get("fieldtype") not in _NO_VALUE_FIELDTYPES and countable
    expressions = [
        f"{other.get('fieldname')}.{prop}"
        for other in rows
        for prop in _FIELD_EXPRESSION_PROPERTIES
        if other is not row and _mentions(other.get(prop), old)
    ]
    folder = _module_dir(package_path, doc["module"])
    pattern = re.compile(rf"(?<![\w]){re.escape(old)}(?![\w])")
    code = (
        [
            {**item, "file": f"{module_slug}/doctype/{slug}/{item['file']}"}
            for item in _app_mentions(folder / "doctype" / slug, pattern)
            if item["file"] != f"{slug}.json"
        ]
        if folder is not None and (folder / "doctype" / slug).is_dir()
        else []
    )
    warnings = [
        f"Until the patch runs, {doctype}.{new} reads empty on this site: the values stay in "
        f"the {old} column. {_pending_patches_hint()} and copies them.",
    ]
    if expressions:
        warnings.append(
            "These expressions still name the old field; update them: " + ", ".join(expressions)
        )
    if code:
        warnings.append("App code in the DocType folder names the old field (see code_mentions).")
    return {
        "app": app,
        "modified": doc.get("modified"),
        "records_with_value": _count_records(doctype, [[old, "is", "set"]]) if has_value else None,
        "doctype_settings": properties,
        "patch": patch,
        "files": [_file_preview(path, content) for path, content in payloads],
        "code_mentions": code,
        "warnings": warnings,
        "requires_developer_mode": True,
        "_doc": doc,
        "_rows": rows,
        "_app_path": app_path,
        "_payloads": payloads,
    }


def _rename_doctype_plan(old: str, new: str, merge: bool) -> dict[str, Any]:
    if merge:
        raise ValueError("Frappe does not merge DocTypes")
    doc, app = _editable_doctype(old)
    if not _SAFE_NAME.fullmatch(new):
        raise ValueError("Invalid new DocType name")
    if _doctype_exists(new):
        raise ValueError(f"DocType {new} already exists")
    custom = bool(doc.get("custom"))
    countable = not doc.get("issingle") and not doc.get("is_virtual")
    plan: dict[str, Any] = {
        "app": app,
        "module": doc.get("module"),
        "custom": custom,
        "modified": doc.get("modified"),
        "records": _count_records(old, []) if countable else None,
        "links": _doctype_links(old)[:_RENAME_LINK_COUNTS],
        "customizations": {
            "custom_fields": _count_records("Custom Field", [["dt", "=", old]]),
            "property_setters": _count_records("Property Setter", [["doc_type", "=", old]]),
        },
        "frappe_updates": [
            "its table, records, child rows, and customizations",
            "Link and Table fields of other DocTypes that use it",
        ],
    }
    if custom:
        return plan
    before, after = _doctype_files(app, doc["module"], old), _doctype_files(app, doc["module"], new)
    plan["app_files"] = {
        "from": before["folder"],
        "to": after["folder"],
        "local_checkout": before.get("local_checkout", False),
    }
    plan["requires_developer_mode"] = True
    plan["requires_administrator"] = True
    warnings = [
        "Frappe renames the folder, the file names, and the class names in its controller; it "
        "does not change other app code. Other DocTypes' JSON files still name the old DocType "
        "until they are saved again; list_app_drift shows them."
    ]
    try:
        _, package_path = _app_root(app)
    except (RuntimeError, ValueError):
        package_path = None
    if package_path is not None:
        folder = _module_dir(package_path, doc["module"])
        skip = folder / "doctype" / _scrub(old) if folder is not None else None
        pattern = re.compile(
            rf"(?<![\w]){re.escape(old)}(?![\w])|(?<![\w]){re.escape(_scrub(old))}(?![\w])"
        )
        plan["code_mentions"] = _app_mentions(package_path, pattern, skip)
    plan["warnings"] = warnings
    return plan


@_tool("developer", "Rename", destructive=True)
def rename(
    kind: Literal["document", "custom_field", "field", "doctype"],
    doctype: str,
    old: str,
    new: str,
    merge: bool = False,
    expected_preview: ExpectedPreview = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or rename through Frappe's own code, which updates what points at it.

    - document: record `old` of `doctype` to `new`; merge=true merges into an existing `new`.
    - custom_field: a Custom Field's fieldname on `doctype`; Frappe renames the column.
    - field: a field of your app's standard DocType. Also writes a patch from a fixed template
      that calls rename_field, listed in patches.txt; the data moves when migrate runs it.
    - doctype: your app's DocType `old` to `new` (pass doctype="DocType").
    The preview shows the counts and files involved; confirm with its `preview_id`.
    """
    if kind not in ("document", "custom_field", "field", "doctype"):
        raise ValueError("kind must be document, custom_field, field, or doctype")
    if not isinstance(doctype, str) or not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")
    old, new = _rename_name(old, "old"), _rename_name(new, "new")
    if old == new:
        raise ValueError("old and new are the same")
    if kind == "doctype" and doctype != "DocType":
        raise ValueError("kind='doctype' takes doctype='DocType' and the DocType names")
    if kind == "document":
        plan = _rename_document_plan(doctype, old, new, merge)
    elif merge:
        raise ValueError("merge applies to kind='document' only")
    elif kind == "custom_field":
        plan = _rename_custom_field_plan(doctype, old, new)
    elif kind == "field":
        plan = _rename_field_plan(doctype, old, new)
    else:
        plan = _rename_doctype_plan(old, new, merge)
    shown = {key: value for key, value in plan.items() if not key.startswith("_")}
    summary = {"kind": kind, "doctype": doctype, "old": old, "new": new, "merge": merge}
    preview_id = hashlib.sha256(
        json.dumps({**summary, **shown}, sort_keys=True, default=str).encode()
    ).hexdigest()
    if not confirm:
        return {
            "preview": True,
            "action": "rename",
            **summary,
            **shown,
            "preview_id": preview_id,
            "requires_confirmation": True,
        }
    if expected_preview != preview_id:
        raise ValueError("The preview is missing or stale; request a fresh preview")
    if kind == "document":
        name = _request(
            "POST",
            _RENAME_DOCUMENT,
            json={"doctype": doctype, "docname": old, "name": new, "merge": merge},
        )
        return {"renamed": True, **summary, "name": name}
    if kind == "custom_field":
        try:
            _request(
                "POST",
                _RENAME_CUSTOM_FIELD,
                json={"custom_field": plan["custom_field"], "fieldname": new},
            )
        finally:
            _forget_meta(doctype)
        return {"renamed": True, **summary, "warnings": plan["warnings"]}
    if kind == "field":
        return _apply_field_rename(doctype, old, new, plan, summary, preview_id)
    return _apply_doctype_rename(old, new, summary, plan)


def _field_rename_recovery_message(
    old: str, new: str, app: str, module: str, rollback_id: str, *, confirmed: bool
) -> str:
    """Explain how to inspect an uncertain remote save without repeating the write."""
    state = (
        "The readback did not confirm the save."
        if confirmed
        else "The save could not be read back."
    )
    return (
        f"Frappe disconnected while saving the DocType. {state} Patch files were preserved "
        f"(rollback_id={rollback_id}). Do not retry the rename yet. Inspect the DocType schema "
        f"for {old!r} and {new!r}. If the schema is unchanged, preview `rollback_export` with "
        f"app={app!r}, module={module!r}, rollback_id={rollback_id!r}, then confirm it to remove "
        "only these generated files. `rollback_export` needs the `customize` toolset; enable it "
        "in `FRAPPE_MCP_TOOLSETS` and reconnect if it is not listed. If the new field is present "
        "and the old one is absent, keep "
        "the patch. If the state is ambiguous, stop and investigate before running `bench migrate`."
    )


def _apply_field_rename(
    doctype: str,
    old: str,
    new: str,
    plan: dict[str, Any],
    summary: dict[str, Any],
    preview_id: str,
) -> dict[str, Any]:
    """Write patch files, then save the DocType; retain them if remote save is uncertain."""
    rollback_id, undo = _write_app_files(plan["_app_path"], plan["_payloads"], preview_id)
    expected = str(plan["modified"])
    target = {
        "rows": plan["_rows"],
        "touched": {new: {"fieldname"}},
        "properties": plan["doctype_settings"],
    }
    rows = [{**row, "idx": index} for index, row in enumerate(plan["_rows"], start=1)]
    reconciled = False
    try:
        _request(
            "PUT",
            _resource("DocType", doctype),
            json={"data": {**plan["doctype_settings"], "fields": rows, "modified": expected}},
        )
    except FrappeError as error:
        undo()
        if error.exc_type == "TimestampMismatchError":
            raise ValueError(_STALE_DOCUMENT) from error
        raise
    except httpx.TransportError as error:
        try:
            _wait_for_server_ready()
            saved = _request("GET", _resource("DocType", doctype))
        except (httpx.TransportError, FrappeError, RuntimeError, ValueError) as read_error:
            raise RuntimeError(
                _field_rename_recovery_message(
                    old, new, plan["app"], plan["_doc"]["module"], rollback_id, confirmed=False
                )
            ) from read_error
        if not isinstance(saved, dict):
            raise RuntimeError(
                _field_rename_recovery_message(
                    old, new, plan["app"], plan["_doc"]["module"], rollback_id, confirmed=False
                )
            ) from error
        fields = saved.get("fields") or []
        fieldnames = [row.get("fieldname") for row in fields if isinstance(row, dict)]
        if new not in fieldnames or old in fieldnames:
            raise RuntimeError(
                _field_rename_recovery_message(
                    old, new, plan["app"], plan["_doc"]["module"], rollback_id, confirmed=True
                )
            ) from error
        if not _doctype_matches(saved, target, expected):
            plan["warnings"].append(
                "The field rename was confirmed after disconnect, but the saved DocType differs "
                "from the preview; review the DocType and patch before running bench migrate."
            )
        reconciled = True
    finally:
        _forget_meta(doctype)
    return {
        "renamed": True,
        **summary,
        "patch": plan["patch"],
        "files": [str(path) for path, _ in plan["_payloads"]],
        "rollback_id": rollback_id,
        "next_step": _pending_patches_hint(),
        "warnings": plan["warnings"],
        "reconciled_after_server_reload": reconciled,
    }


def _restore_doctype_files_after_failed_rename(
    source: Path, destination: Path, backup: Path, old: str, new: str
) -> dict[str, Path] | None:
    """Quarantine Frappe's moved folder and restore pre-rename app files."""
    if source.exists() and not destination.exists():
        return None
    if not backup.is_dir():
        raise RuntimeError("The original DocType files are unavailable for recovery")
    retained = _retain_doctype_snapshot(backup)
    if retained is None:
        raise RuntimeError("The original DocType files could not be retained for recovery")
    if source.exists():
        raise RuntimeError(
            f"Both original and renamed DocType folders exist; recovery snapshot: {retained}"
        )
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise RuntimeError("Refusing to move an unexpected DocType destination path")
        quarantine = source.parent / f".mcp-frappe-recovery-{retained.name}"
        quarantine.mkdir(mode=0o700)
        moved_files = quarantine / "moved-doctype-folder"
        os.replace(destination, moved_files)
    else:
        moved_files = None
    shutil.copytree(retained / "original", source, symlinks=True)
    recovery = {"snapshot": retained}
    if moved_files:
        recovery["moved_files"] = moved_files
    return recovery


def _restore_doctype_files_after_failed_delete(folder: Path, backup: Path) -> bool:
    """Restore an app folder if Frappe removed it but rolled back the DocType deletion."""
    if folder.is_symlink():
        raise RuntimeError("Refusing to restore DocType files over a symlink")
    if not backup.is_dir():
        return False
    if folder.exists():
        if _doctype_file_trees_match(folder, backup):
            return False
        raise RuntimeError(
            "Frappe left a partially changed DocType folder after the failed deletion"
        )
    shutil.copytree(backup, folder, symlinks=True)
    return True


def _doctype_file_trees_match(left: Path, right: Path) -> bool:
    def entries(root: Path) -> dict[Path, tuple[str, bytes | str | None]]:
        result = {}
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if path.is_symlink():
                result[relative] = ("link", os.readlink(path))
            elif path.is_dir():
                result[relative] = ("directory", None)
            elif path.is_file():
                result[relative] = ("file", path.read_bytes())
            else:
                result[relative] = ("other", None)
        return result

    return entries(left) == entries(right)


def _retain_doctype_snapshot(backup: Path) -> Path | None:
    """Keep a private copy when native Frappe leaves an ambiguous app-file state."""
    if not backup.is_dir():
        return None
    state_root = Path(
        os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
    )
    recovery_root = state_root / "mcp-frappe" / "doctype-recoveries"
    recovery_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(recovery_root, 0o700)
    retained = recovery_root / uuid.uuid4().hex
    shutil.copytree(backup, retained / "original", symlinks=True)
    return retained


def _apply_doctype_rename(
    old: str, new: str, summary: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    reconciled = False
    name: Any = new
    app_files = plan.get("app_files") or {}
    source = Path(app_files["from"]) if app_files.get("local_checkout") else None
    destination = Path(app_files["to"]) if app_files.get("local_checkout") else None
    with tempfile.TemporaryDirectory(prefix="mcp-frappe-doctype-rename-") as temporary:
        backup = Path(temporary) / "doctype"
        if source is not None and source.is_dir() and destination is not None:
            shutil.copytree(source, backup, symlinks=True)
        try:
            name = _request(
                "POST",
                _RENAME_DOCUMENT,
                json={"doctype": "DocType", "docname": old, "name": new, "merge": False},
            )
        except httpx.TransportError as error:
            # Frappe moves app files during a standard DocType rename. A dev Bench may reload
            # before the transaction commits, leaving the DB unchanged and the folder moved.
            try:
                _wait_for_server_ready()
            except Exception as wait_error:
                retained = _retain_doctype_snapshot(backup)
                snapshot = f" Snapshot retained at {retained}." if retained else ""
                raise RuntimeError(
                    "Frappe did not return to a stable state after the DocType rename; "
                    f"inspect both names and app files before retrying.{snapshot}"
                ) from wait_error
            try:
                new_exists = _doctype_exists(new)
                old_exists = _doctype_exists(old)
            except Exception as readback_error:
                retained = _retain_doctype_snapshot(backup)
                snapshot = f" Snapshot retained at {retained}." if retained else ""
                raise RuntimeError(
                    "Frappe returned but the renamed DocType state could not be read; "
                    f"inspect both names and app files before retrying.{snapshot}"
                ) from readback_error
            if new_exists and not old_exists:
                reconciled = True
            elif old_exists and not new_exists:
                recovery = None
                if source is not None and destination is not None and backup.is_dir():
                    try:
                        recovery = _restore_doctype_files_after_failed_rename(
                            source, destination, backup, old, new
                        )
                    except RuntimeError as recovery_error:
                        retained = _retain_doctype_snapshot(backup)
                        snapshot = f" Snapshot retained at {retained}." if retained else ""
                        raise RuntimeError(f"{recovery_error}{snapshot}") from error
                detail = ""
                if recovery:
                    detail = f"; original snapshot retained at {recovery['snapshot']}"
                    if recovery.get("moved_files"):
                        detail += f"; moved files quarantined at {recovery['moved_files']}"
                raise RuntimeError(
                    "Frappe disconnected during the rename and the DocType was not renamed"
                    f"{detail}; request a fresh preview before retrying."
                ) from error
            else:
                retained = _retain_doctype_snapshot(backup)
                snapshot = f" Snapshot retained at {retained}." if retained else ""
                raise RuntimeError(
                    "Frappe disconnected during the rename and its final DocType state is "
                    f"ambiguous; inspect both names and app folders before retrying.{snapshot}"
                ) from error
        finally:
            _forget_meta()
    return {
        "renamed": True,
        **summary,
        "name": name,
        "reconciled_after_server_reload": reconciled,
    }


def _clean_row(row: dict[str, Any], system: frozenset[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in system and not key.startswith("_") and not _blank(value) and value != []
    }


def _normalize_doctype(doc: dict[str, Any]) -> dict[str, Any]:
    """Reduce a DocType record, or the JSON Frappe exports for it, to what both sides hold.

    Frappe's export drops blank values, system columns, and child-row names, and keeps the real
    field order in `field_order` (its `fields` list may keep an older order). So both sides lose
    system keys, `_` columns, and blank values (None, "", 0, []); fields are keyed by fieldname
    and their order is kept separately so a move is still seen.
    """
    properties: dict[str, Any] = {}
    tables: dict[str, list[dict[str, Any]]] = {}
    for key, value in doc.items():
        if key in _DOCTYPE_SYSTEM_KEYS or key.startswith("_"):
            continue
        if isinstance(value, list):
            rows = [_clean_row(row, _CHILD_SYSTEM_KEYS) for row in value if isinstance(row, dict)]
            if rows:
                tables[key] = rows
        elif not _blank(value):
            properties[key] = value
    fields: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(tables.pop("fields", [])):
        fields[row.get("fieldname") or f"{row.get('fieldtype', 'field')}#{position}"] = row
    order = doc.get("field_order")
    listed = (
        list(dict.fromkeys(name for name in order if name in fields))
        if isinstance(order, list)
        else []
    )
    field_order = listed + [name for name in fields if name not in listed]
    return {
        "properties": properties,
        "fields": fields,
        "field_order": field_order,
        "tables": tables,
    }


def _value_changes(site: dict[str, Any], app: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: {"from": site.get(key), "to": app.get(key)}
        for key in sorted(site.keys() | app.keys())
        if site.get(key) != app.get(key)
    }


def _compare_doctypes(site: dict[str, Any], app: dict[str, Any]) -> dict[str, Any]:
    """Diff two `_normalize_doctype` results in `update_doctype`'s shape, from site to app.

    `added` fields are only in the app file, `removed` only on the site, and `from`/`to` hold
    the site and app values: the change `bench migrate` would make to the site.
    """
    site_fields, app_fields = site["fields"], app["fields"]
    site_order, app_order = site["field_order"], app["field_order"]

    def previous(order: list[str], name: str) -> str | None:
        index = order.index(name)
        return order[index - 1] if index else None

    def summary(row: dict[str, Any], name: str) -> dict[str, Any]:
        return {"fieldname": name, "fieldtype": row.get("fieldtype"), "label": row.get("label")}

    added = [
        {**summary(app_fields[name], name), "insert_after": previous(app_order, name)}
        for name in app_order
        if name not in site_fields
    ]
    removed = [summary(site_fields[name], name) for name in site_order if name not in app_fields]
    # Fields outside the longest common order moved; the rest only shifted around them.
    common_site = [name for name in site_order if name in app_fields]
    common_app = [name for name in app_order if name in site_fields]
    kept: set[str] = set()
    matcher = difflib.SequenceMatcher(None, common_site, common_app, autojunk=False)
    for block in matcher.get_matching_blocks():
        kept.update(common_app[block.b : block.b + block.size])
    moved = [
        {"fieldname": name, "insert_after": previous(app_order, name)}
        for name in common_app
        if name not in kept
    ]
    changed = {
        name: delta
        for name in common_app
        if (delta := _value_changes(site_fields[name], app_fields[name]))
    }
    tables: dict[str, dict[str, Any]] = {}
    for name in sorted(site["tables"].keys() | app["tables"].keys()):
        site_rows, app_rows = site["tables"].get(name, []), app["tables"].get(name, [])
        if site_rows == app_rows:
            continue
        only_app = list(app_rows)
        only_site = []
        for row in site_rows:
            if row in only_app:
                only_app.remove(row)
            else:
                only_site.append(row)
        tables[name] = (
            {"only_on_site": only_site, "only_in_app": only_app}
            if only_site or only_app
            else {"reordered": True}
        )
    return {
        "added": added,
        "removed": removed,
        "moved": moved,
        "changed": changed,
        "properties": _value_changes(site["properties"], app["properties"]),
        "tables": tables,
    }


def _bounded(value: Any, state: dict[str, bool]) -> Any:
    """Cap lists, objects, and strings in a drift report; `state["truncated"]` says if any was."""
    if isinstance(value, list | dict):
        items = list(value.items() if isinstance(value, dict) else value)
        if len(items) > _DRIFT_MAX_ITEMS:
            state["truncated"] = True
            items = items[:_DRIFT_MAX_ITEMS]
        if isinstance(value, dict):
            return {key: _bounded(item, state) for key, item in items}
        return [_bounded(item, state) for item in items]
    if isinstance(value, str) and len(value) > _DRIFT_MAX_TEXT:
        state["truncated"] = True
        return value[:_DRIFT_MAX_TEXT] + "..."
    return value


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError:
        return None


def _newer(site_modified: Any, app_modified: Any) -> str | None:
    site_time, app_time = _timestamp(site_modified), _timestamp(app_modified)
    if site_time is None or app_time is None:
        return None
    return "site" if site_time > app_time else "app" if app_time > site_time else "same"


def _doctype_file_drift(site_doc: dict[str, Any], path: Path) -> dict[str, Any]:
    """Compare a DocType record with its app JSON and decide which side is ahead.

    `bench migrate` imports a DocType's JSON whenever its MD5 differs from the site's
    `migration_hash` (or none is stored), whatever `modified` says. So the hash tells whether
    the file changed since the last migrate, and `modified` which side was saved last.
    """
    app_doc, digest = _read_app_json(path)
    if not isinstance(app_doc, dict):
        raise ValueError(f"{path} does not hold a DocType")
    changes = _compare_doctypes(_normalize_doctype(site_doc), _normalize_doctype(app_doc))
    stored = site_doc.get("migration_hash")
    file_changed = bool(stored) and stored != digest
    newer = _newer(site_doc.get("modified"), app_doc.get("modified"))
    # Same hash and same `modified`: the site holds exactly what the last import read from this
    # file, so what still differs is what Frappe filled in on import (`engine`, `sort_field`,
    # ...) that a hand-written JSON leaves out.
    filled_on_import = bool(stored) and stored == digest and newer == "same"
    if not any(changes.values()) or filled_on_import:
        verdict = "in_sync"
    elif newer == "app":
        verdict = "app_ahead"
    elif newer == "site":
        verdict = "diverged" if file_changed else "site_ahead"
    else:
        verdict = "app_ahead" if file_changed else "diverged"
    state = {"truncated": False}
    result = {
        "path": str(path),
        "exists": True,
        "verdict": verdict,
        "site_modified": site_doc.get("modified"),
        "app_modified": app_doc.get("modified"),
        "newer": newer,
        "migrate_imports_file": stored != digest,
        "changes": _bounded(changes, state),
        "truncated": state["truncated"],
    }
    if filled_on_import and any(changes.values()):
        result["filled_on_import"] = True
    return result


_CUSTOMIZATION_KINDS = (
    ("custom_fields", "Custom Field", "dt"),
    ("property_setters", "Property Setter", "doc_type"),
)


def _customization_key(kind: str, row: dict[str, Any]) -> str:
    if kind == "custom_fields":
        return str(row.get("fieldname"))
    return f"{row.get('field_name') or row.get('row_name') or ''}:{row.get('property')}"


def _site_customizations(doctype: str) -> dict[str, list[dict[str, Any]]]:
    return {
        kind: _export_rows(source, [[dt_field, "=", doctype]])
        for kind, source, dt_field in _CUSTOMIZATION_KINDS
    }


def _customization_drift(
    doctype: str,
    site_rows: dict[str, list[dict[str, Any]]],
    app_modules: set[str],
    module: str | None,
    path: Path | None,
) -> dict[str, Any] | None:
    """Compare a `<module>/custom/<doctype>.json` file (or its absence) with the site's rows.

    Site rows tagged with another app's module are left out unless the file holds them too.
    `bench migrate` re-applies every file row and never deletes site rows. Returns None when
    there is no file and the site has nothing of this app's to export.
    """
    data: dict[str, Any] = {}
    if path is not None:
        data, _ = _read_app_json(path)
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a customization file")
    result: dict[str, Any] = {"module": module, "path": str(path) if path else None}
    directions: set[str] = set()
    for kind, _, dt_field in _CUSTOMIZATION_KINDS:
        app_rows = {
            _customization_key(kind, row): row
            for row in data.get(kind) or []
            if isinstance(row, dict) and row.get(dt_field) == doctype
        }
        site = {}
        for row in site_rows[kind]:
            key = _customization_key(kind, row)
            if not row.get("module") or row.get("module") in app_modules or key in app_rows:
                site[key] = row
        changed = {}
        for key in sorted(site.keys() & app_rows.keys()):
            delta = _value_changes(
                _clean_row(site[key], _CHILD_SYSTEM_KEYS),
                _clean_row(app_rows[key], _CHILD_SYSTEM_KEYS),
            )
            if delta:
                changed[key] = delta
                newer = _newer(site[key].get("modified"), app_rows[key].get("modified"))
                directions.update({newer} if newer in ("site", "app") else {"site", "app"})
        only_site = sorted(site.keys() - app_rows.keys())
        only_app = sorted(app_rows.keys() - site.keys())
        if only_site:
            directions.add("site")
        if only_app:
            directions.add("app")
        result[kind] = {"only_on_site": only_site, "only_in_app": only_app, "changed": changed}
    if path is None:
        if not directions:
            return None
        result["verdict"] = "missing_in_app"
    else:
        result["verdict"] = (
            "in_sync"
            if not directions
            else f"{directions.pop()}_ahead"
            if len(directions) == 1
            else "diverged"
        )
    state = {"truncated": False}
    result = _bounded(result, state)
    result["truncated"] = state["truncated"]
    return result


def _app_modules(app: str) -> list[dict[str, Any]]:
    """List the Module Defs the site assigns to `app`."""
    rows = _request(
        "GET",
        _resource("Module Def"),
        params={
            "fields": '["name","app_name","custom"]',
            "filters": _filters({"app_name": app}),
            "order_by": "name asc",
            "limit_page_length": 1000,
        },
    )
    if not rows:
        raise ValueError(f"The target site has no modules of app {app}")
    return rows


_DRIFT_SUMMARIES = {
    "in_sync": "{doctype}: the site and {app} match.",
    "site_ahead": "{doctype}: the site has changes the app files lack; export them to {app}.",
    "app_ahead": "{doctype}: {app}'s files have changes the site lacks; run `bench migrate`.",
    "diverged": (
        "{doctype}: both the site and {app}'s files changed; reconcile them by hand before "
        "migrating or exporting."
    ),
    "missing_in_app": "{doctype}: the site has it but {app} has no file for it.",
    "missing_on_site": "{doctype}: {app} has its file but the site does not; run `bench migrate`.",
    "site_custom": (
        "{doctype} is a site-custom DocType with no app files; use promote_custom_doctype to "
        "ship it with {app}."
    ),
}


@_tool("read", "Diff DocType against app files")
def diff_doctype(doctype: str, app: str | None = None) -> dict[str, Any]:
    """Compare a DocType on the site with its files in the app checkout (read-only).

    `app` defaults to the app owning the DocType's module; another app checks only its
    customizations. `verdict`: in_sync, site_ahead (export it), app_ahead (run bench migrate),
    diverged, missing_in_app, or site_custom (see promote_custom_doctype). `doctype_file.changes`
    goes from the site (`from`) to the app (`to`); `customizations` compares Custom Fields and
    Property Setters.
    """
    if not isinstance(doctype, str) or not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")
    site_doc = _request("GET", _resource("DocType", doctype))
    module = site_doc.get("module")
    owner = _module_app(module)
    app = app or owner
    if not isinstance(app, str) or not _SAFE_NAME.fullmatch(app):
        raise ValueError("Invalid app name")
    modules = [row.get("name") for row in _app_modules(app)]
    _, package_path = _app_root(app)
    slug = _scrub(doctype)
    custom = bool(site_doc.get("custom"))
    warnings: list[str] = []
    doctype_file: dict[str, Any] | None = None
    verdict: str | None = None
    if custom:
        verdict = "site_custom"
    elif app == owner:
        module_path = _module_dir(package_path, module)
        path = _app_file(module_path, "doctype", slug, f"{slug}.json") if module_path else None
        if path is None:
            expected = package_path / _scrub(module) / "doctype" / slug / f"{slug}.json"
            doctype_file = {"path": str(expected), "exists": False, "verdict": "missing_in_app"}
        else:
            doctype_file = _doctype_file_drift(site_doc, path)
            if doctype_file["migrate_imports_file"] and doctype_file["verdict"] in (
                "site_ahead",
                "diverged",
            ):
                warnings.append(
                    "`bench migrate` will re-import the app file and replace the site's "
                    "changes. Save the DocType once with developer mode on (update_doctype or "
                    "Desk) so Frappe writes them to the file first."
                )
        verdict = doctype_file["verdict"]

    site_rows = _site_customizations(doctype)
    customizations = []
    for name in modules:
        module_path = _module_dir(package_path, name)
        path = _app_file(module_path, "custom", f"{slug}.json") if module_path else None
        if path is not None:
            customizations.append(
                _customization_drift(doctype, site_rows, set(modules), name, path)
            )
    if not customizations:
        unexported = _customization_drift(doctype, site_rows, set(modules), None, None)
        customizations = [unexported] if unexported else []
    custom_verdicts = {entry["verdict"] for entry in customizations} - {"in_sync"}
    if verdict is None:
        verdict = (
            "in_sync"
            if not custom_verdicts
            else custom_verdicts.pop()
            if len(custom_verdicts) == 1
            else "diverged"
        )
    summary = _DRIFT_SUMMARIES[verdict].format(doctype=doctype, app=app)
    if doctype_file is not None and custom_verdicts:
        summary += f" Customizations: {', '.join(sorted(custom_verdicts))}."
    return {
        "doctype": doctype,
        "app": app,
        "owner_app": owner,
        "module": module,
        "custom": custom,
        "verdict": verdict,
        "summary": summary,
        "doctype_file": doctype_file,
        "customizations": customizations,
        "warnings": warnings,
    }


def _drift_item(module: str, slug: str, entry: dict[str, Any]) -> dict[str, Any]:
    site, path = entry.get("site"), entry.get("path")
    name = site.get("name") if site else slug
    if site and site.get("custom"):
        verdict = "site_custom"
    elif path is None:
        verdict = "missing_in_app"
    else:
        app_doc, digest = _read_app_json(path)
        if site is None:
            verdict = "missing_on_site"
            if isinstance(app_doc, dict) and isinstance(app_doc.get("name"), str):
                name = app_doc["name"]
        elif (
            isinstance(app_doc, dict)
            and site.get("migration_hash") == digest
            and str(site.get("modified")) == str(app_doc.get("modified"))
        ):
            # Migrate imported this exact file and nobody saved the DocType since.
            verdict = "in_sync"
        else:
            full = _request("GET", _resource("DocType", site["name"]))
            verdict = _doctype_file_drift(full, path)["verdict"]
    return {"doctype": name, "module": module, "verdict": verdict}


@_tool("read", "List app drift")
def list_app_drift(
    app: str, module: str | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """List which DocTypes of an app differ between the site and the checkout (read-only).

    Verdicts as in diff_doctype, plus missing_on_site (run bench migrate). Page with `offset` and
    the returned `next_offset`.
    """
    if not isinstance(limit, int) or not 1 <= limit <= _DRIFT_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {_DRIFT_PAGE_LIMIT}")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be 0 or more")
    if not isinstance(app, str) or not _SAFE_NAME.fullmatch(app):
        raise ValueError("Invalid app name")
    names = [row.get("name") for row in _app_modules(app)]
    if module is not None:
        if module not in names:
            raise ValueError(f"Module {module} does not belong to app {app} on the target site")
        names = [module]
    _, package_path = _app_root(app)
    site_rows = _request(
        "GET",
        _resource("DocType"),
        params={
            "fields": '["name","module","custom","modified","migration_hash"]',
            "filters": _filters([["module", "in", names]]),
            "order_by": "name asc",
            "limit_page_length": _DRIFT_SITE_LIMIT + 1,
        },
    )
    if len(site_rows) > _DRIFT_SITE_LIMIT:
        raise ValueError(f"More than {_DRIFT_SITE_LIMIT} DocTypes; pass a module")
    entries: dict[tuple[str, str], dict[str, Any]] = {}
    for row in site_rows:
        entries[(row.get("module"), _scrub(row.get("name")))] = {"site": row}
    missing_modules = []
    for name in names:
        module_path = _module_dir(package_path, name)
        if module_path is None:
            missing_modules.append(name)
            continue
        folder = module_path / "doctype"
        if not folder.is_dir():
            continue
        for child in sorted(folder.iterdir()):
            if child.name.startswith((".", "__")) or not child.is_dir():
                continue
            path = _app_file(module_path, "doctype", child.name, f"{child.name}.json")
            if path is not None:
                entries.setdefault((name, child.name), {})["path"] = path
    keys = sorted(entries)
    items = [_drift_item(*key, entries[key]) for key in keys[offset : offset + limit]]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1
    result = {
        "app": app,
        "modules": names,
        "modules_missing_locally": missing_modules,
        "total": len(keys),
        "offset": offset,
        "limit": limit,
        "next_offset": offset + limit if offset + limit < len(keys) else None,
        "counts": counts,
        "items": items,
    }
    if counts.get("missing_on_site"):
        result["hint"] = _missing_on_site_hint()
    return result


# Developer diagnostics: pending patches, recent errors, and the app's whitelisted methods.

_PATCH_SECTIONS = ("pre_model_sync", "post_model_sync")
_PATCH_SECTION_HEADER = re.compile(r"\[(?P<header>.+)\]")
_PATCH_LOG_PAGE = 1000
_PATCH_LOG_MAX = 50_000
_PATCH_LIST_MAX = 200


def _parse_patches(text: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Read patches.txt the way `frappe.modules.patch_handler` does, keeping line numbers.

    With a `[section]` before the first entry the file is INI-like (`configparser` with
    `#`/`;` comment lines and the whole line as the key, so `#2` markers stay part of the
    patch); otherwise each non-comment line is a pre-model-sync patch. Nothing is run.
    """
    lines = text.splitlines()
    stripped = (line.strip() for line in lines)
    first = next((line for line in stripped if line and not line.startswith(("#", ";"))), "")
    entries: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not _PATCH_SECTION_HEADER.match(first):
        for number, line in enumerate(lines, 1):
            if line.strip() and not line.lstrip().startswith("#"):
                entries.append({"patch": line.strip(), "section": "pre_model_sync", "line": number})
        return "plain", entries, warnings
    section: str | None = None
    for number, line in enumerate(lines, 1):
        value = line.strip()
        if not value or value.startswith(("#", ";")):
            continue
        header = _PATCH_SECTION_HEADER.match(value)
        if header:
            section = header.group("header")
            if section not in _PATCH_SECTIONS:
                warnings.append(f"Section [{section}] on line {number} is ignored by Frappe")
            continue
        if section in _PATCH_SECTIONS:
            entries.append({"patch": value, "section": section, "line": number})
    return "sections", entries, warnings


def _patch_log() -> list[dict[str, Any]]:
    """Read every Patch Log row (`patch`, `skipped`), a page at a time."""
    rows: list[dict[str, Any]] = []
    while True:
        page = _request(
            "GET",
            _resource("Patch Log"),
            params={
                "fields": '["patch","skipped"]',
                "order_by": "name asc",
                "limit_start": len(rows),
                "limit_page_length": _PATCH_LOG_PAGE,
            },
        )
        page = page if isinstance(page, list) else []
        rows.extend(page)
        if len(rows) > _PATCH_LOG_MAX:
            raise ValueError(f"Patch Log has more than {_PATCH_LOG_MAX} rows; not read")
        if len(page) < _PATCH_LOG_PAGE:
            return rows


@_tool("read", "List pending patches")
def list_pending_patches(app: str) -> dict[str, Any]:
    """List the app's patches.txt entries not yet run on the site, per Patch Log (read-only).

    Returns `pending` in file order, `applied_count`, and `unknown_in_log` (logged, not in file).
    """
    if not isinstance(app, str) or not _SAFE_NAME.fullmatch(app):
        raise ValueError("Invalid app name")
    _, package_path = _app_root(app)
    path = _app_file(package_path, "patches.txt")
    if path is None:
        file_format, entries, warnings = "missing", [], []
    else:
        file_format, entries, warnings = _parse_patches(path.read_text(errors="replace"))
    seen: set[str] = set()
    for entry in entries:
        if entry["patch"] in seen:
            warnings.append(f"{entry['patch']} is listed more than once (line {entry['line']})")
        seen.add(entry["patch"])
    log = _patch_log()
    applied = {row.get("patch") for row in log if not row.get("skipped")}
    skipped = {row.get("patch") for row in log if row.get("skipped")}
    pending = []
    applied_count = 0
    for entry in entries:
        # Patch Log stores a `finally:` patch without the prefix.
        key = entry["patch"].replace("finally:", "")
        if key in applied:
            applied_count += 1
            continue
        pending.append({**entry, "skipped": True} if key in skipped else entry)
    in_file = {entry["patch"].replace("finally:", "") for entry in entries}
    unknown = sorted(
        {
            patch
            for row in log
            if isinstance(patch := row.get("patch"), str)
            and patch.split(maxsplit=1)[0].startswith(f"{app}.")
            and patch not in in_file
        }
    )
    return {
        "app": app,
        "format": file_format,
        "entries": len(entries),
        "applied_count": applied_count,
        "pending_count": len(pending),
        "pending": pending[:_PATCH_LIST_MAX],
        "unknown_in_log_count": len(unknown),
        "unknown_in_log": unknown[:_PATCH_LIST_MAX],
        "warnings": warnings[:20],
        **({"hint": _pending_patches_hint()} if pending else {}),
    }


_ERROR_TRACEBACK_LINES = 40
_ERROR_TRACEBACK_CHARS = 4000
_ERROR_PAGE_LIMIT = 50


def _error_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep an Error Log row's identity and the tail of its traceback."""
    lines = str(row.get("error") or "").rstrip().splitlines()
    tail = "\n".join(lines[-_ERROR_TRACEBACK_LINES:])
    truncated = len(lines) > _ERROR_TRACEBACK_LINES or len(tail) > _ERROR_TRACEBACK_CHARS
    exception = next((line.strip() for line in reversed(lines) if line.strip()), "")
    return {
        **{
            key: row.get(key)
            for key in ("name", "creation", "method", "reference_doctype", "reference_name")
        },
        "exception": exception[:500],
        "traceback": tail[-_ERROR_TRACEBACK_CHARS:],
        "traceback_truncated": truncated,
    }


@_tool("read", "Get recent errors")
def get_recent_errors(
    since: str | None = None,
    method: str | None = None,
    reference_doctype: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Read Error Log newest first: exception and traceback tail (read-only). Returns a page.

    `since`: ISO datetime, site time. Tracebacks can hold record values; show only what's needed.
    """
    if not isinstance(limit, int) or not 1 <= limit <= _ERROR_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {_ERROR_PAGE_LIMIT}")
    _assert_offset(offset)
    filters: list[list[Any]] = []
    if since is not None:
        try:
            moment = datetime.fromisoformat(str(since))
        except ValueError as error:
            raise ValueError("since must be an ISO datetime such as 2026-11-20T09:30") from error
        if moment.tzinfo is not None:
            raise ValueError("since takes the site's local time, without a time zone")
        filters.append(["creation", ">=", moment.strftime("%Y-%m-%d %H:%M:%S")])
    if method is not None:
        if not isinstance(method, str) or not 0 < len(method) <= 140:
            raise ValueError("method must be 1-140 characters")
        filters.append(["method", "like", f"%{method}%"])
    if reference_doctype is not None:
        if not isinstance(reference_doctype, str) or not _SAFE_NAME.fullmatch(reference_doctype):
            raise ValueError("Invalid DocType name")
        filters.append(["reference_doctype", "=", reference_doctype])
    rows = _request(
        "GET",
        _resource("Error Log"),
        params={
            "fields": '["name","creation","method","reference_doctype","reference_name","error"]',
            "filters": _filters(filters),
            "order_by": "creation desc",
            "limit_start": offset,
            "limit_page_length": limit + 1,
        },
    )
    rows = (
        [_error_row(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    )
    return _page(rows, offset, limit)


_METHOD_MAX_FILES = 5000
_METHOD_FILE_MAX_BYTES = 1_000_000
_METHOD_PAGE_LIMIT = 200
_METHOD_SKIP_DIRS = {"tests", "test", "node_modules", "__pycache__"}
_WHITELIST_DEFAULT_METHODS = ["GET", "POST", "PUT", "DELETE"]


def _literal(node: ast.expr) -> Any:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return ast.unparse(node)


def _whitelist_call(
    fn: ast.FunctionDef | ast.AsyncFunctionDef, frappe_names: set[str], bare_names: set[str]
) -> dict[str, Any] | None:
    """Return the decorator's options when `fn` is decorated with `frappe.whitelist`."""
    for decorator in fn.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "whitelist"
            and isinstance(target.value, ast.Name)
            and target.value.id in frappe_names
        ) or (isinstance(target, ast.Name) and target.id in bare_names):
            keywords = decorator.keywords if isinstance(decorator, ast.Call) else []
            options = {keyword.arg: _literal(keyword.value) for keyword in keywords if keyword.arg}
            return {
                "allow_guest": options.get("allow_guest", False),
                "methods": options.get("methods") or _WHITELIST_DEFAULT_METHODS,
            }
    return None


def _method_args(fn: ast.FunctionDef | ast.AsyncFunctionDef, method: bool) -> list[dict[str, Any]]:
    spec = fn.args
    positional = [*spec.posonlyargs, *spec.args]
    defaults = [None] * (len(positional) - len(spec.defaults)) + list(spec.defaults)
    pairs = list(zip(positional, defaults, strict=True))
    if method and pairs and pairs[0][0].arg in {"self", "cls"}:
        pairs = pairs[1:]
    if spec.vararg:
        pairs.append((ast.arg(arg=f"*{spec.vararg.arg}", annotation=spec.vararg.annotation), None))
    pairs.extend(zip(spec.kwonlyargs, spec.kw_defaults, strict=True))
    if spec.kwarg:
        pairs.append((ast.arg(arg=f"**{spec.kwarg.arg}", annotation=spec.kwarg.annotation), None))
    args = []
    for arg, default in pairs:
        row: dict[str, Any] = {"name": arg.arg}
        if arg.annotation is not None:
            row["annotation"] = ast.unparse(arg.annotation)
        if default is not None:
            row["default"] = ast.unparse(default)
        args.append(row)
    return args


def _controller_doctype(path: Path) -> str | None:
    """Name the DocType when `path` is `<module>/doctype/<slug>/<slug>.py`."""
    folder = path.parent
    if folder.parent.name != "doctype" or path.stem != folder.name:
        return None
    try:
        json_path = _app_file(folder, f"{folder.name}.json")
        name = json.loads(json_path.read_bytes()).get("name") if json_path else None
    except (OSError, ValueError, AttributeError):
        name = None
    return name if isinstance(name, str) else folder.name.replace("_", " ").title()


def _file_methods(tree: ast.Module, dotted: str, path: Path, file: str) -> list[dict[str, Any]]:
    frappe_names: set[str] = set()
    bare_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            frappe_names.update(
                alias.asname or "frappe" for alias in node.names if alias.name == "frappe"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "frappe" and not node.level:
            bare_names.update(
                alias.asname or "whitelist" for alias in node.names if alias.name == "whitelist"
            )
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    found: list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str | None]] = []
    for node in tree.body:
        if isinstance(node, functions):
            found.append((node, None))
        elif isinstance(node, ast.ClassDef):
            found.extend((child, node.name) for child in node.body if isinstance(child, functions))
    rows = []
    for fn, class_name in found:
        options = _whitelist_call(fn, frappe_names, bare_names)
        if options is None:
            continue
        row: dict[str, Any] = {
            "method": ".".join(part for part in (dotted, class_name, fn.name) if part),
            "kind": "document" if class_name else "module",
        }
        if class_name:
            row["doctype"] = _controller_doctype(path)
        doc = (ast.get_docstring(fn) or "").split("\n\n")[0]
        row.update(
            {
                "args": _method_args(fn, bool(class_name)),
                "doc": " ".join(doc.split())[:300],
                **options,
                "file": f"{file}:{fn.lineno}",
            }
        )
        rows.append(row)
    return rows


@_tool("read", "List app methods")
def list_app_methods(
    app: str,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
    as_config: bool = False,
) -> dict[str, Any]:
    """List the app checkout's whitelisted methods with their args (read-only; never imported).

    `search` matches part of the dotted path. Returns a page; document methods name the DocType.
    `as_config` returns FRAPPE_MCP_METHODS entries to review and paste instead.
    """
    if not isinstance(limit, int) or not 1 <= limit <= _METHOD_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {_METHOD_PAGE_LIMIT}")
    _assert_offset(offset)
    if not isinstance(app, str) or not _SAFE_NAME.fullmatch(app):
        raise ValueError("Invalid app name")
    app_path, package_path = _app_root(app)
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    scanned = 0
    truncated = False
    for folder, dirnames, filenames in os.walk(package_path):
        # os.walk lists symlinked folders but doesn't descend into them (followlinks=False).
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".") and name not in _METHOD_SKIP_DIRS
        )
        for filename in sorted(filenames):
            if filename.startswith(".") or not filename.endswith(".py"):
                continue
            if scanned >= _METHOD_MAX_FILES:
                truncated = True
                break
            scanned += 1
            path = Path(folder) / filename
            relative = path.relative_to(app_path)
            file = relative.as_posix()
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError):
                skipped.append({"file": file, "reason": "could not be resolved"})
                continue
            if not resolved.is_relative_to(app_path) or not resolved.is_file():
                skipped.append({"file": file, "reason": "escapes the app"})
                continue
            if resolved.stat().st_size > _METHOD_FILE_MAX_BYTES:
                skipped.append(
                    {"file": file, "reason": f"larger than {_METHOD_FILE_MAX_BYTES} bytes"}
                )
                continue
            source = resolved.read_bytes()
            if b"whitelist" not in source:
                continue
            try:
                tree = ast.parse(source, filename=file)
            except SyntaxError as error:
                skipped.append({"file": file, "reason": f"syntax error on line {error.lineno}"})
                continue
            except (ValueError, RecursionError, MemoryError):
                skipped.append({"file": file, "reason": "could not be parsed"})
                continue
            parts = list(relative.with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            rows.extend(_file_methods(tree, ".".join(parts), path, file))
        if truncated:
            break
    if search:
        needle = str(search).lower()
        rows = [row for row in rows if needle in row["method"].lower()]
    extra: dict[str, Any] = {}
    if as_config:
        # Only module functions can be called by their dotted path; controller methods can't.
        rows = [_config_suggestion(row, app_path) for row in rows if row["kind"] == "module"]
        extra["note"] = _CONFIG_SUGGESTION_NOTE
    return {
        **_page(rows[offset : offset + limit + 1], offset, limit),
        "files_scanned": scanned,
        "files_truncated": truncated,
        "skipped": skipped[:50],
        **extra,
    }


# Allowlisted method calls. FRAPPE_MCP_METHODS names a JSON file listing the only methods the
# method tools may call; without it they aren't registered. Nothing here calls a method that
# isn't in that file.
_METHODS_ENV = "FRAPPE_MCP_METHODS"
_METHOD_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_METHOD_PATH_MAX = 200
_METHOD_KINDS = ("mapper", "read", "write")
_METHOD_ENTRY_KEYS = frozenset(
    {"method", "kind", "description", "source_doctype", "target_doctype", "params"}
)
_METHOD_CONFIG_MAX_BYTES = 1_000_000
_METHOD_CONFIG_MAX_ENTRIES = 200
_METHOD_TEXT_MAX = 300
_METHOD_PARAMS_MAX = 50
_METHOD_PARAM_KEYS = frozenset({"type", "required", "enum", "description"})
_METHOD_PARAM_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}
_METHOD_ARG_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_METHOD_ARG_MAX_CHARS = 10_000
# `cmd` is how Frappe's handler names the method to run; `ignore_permissions` would skip
# Frappe's permission checks in methods that accept it.
_FORBIDDEN_METHOD_ARGS = frozenset({"cmd", "ignore_permissions"})
# Methods that run another method or write any record. Listing one would turn the allowlist into
# unrestricted dispatch, so the loader refuses them; the record tools cover frappe.client.
_DISPATCH_METHODS = frozenset(
    {
        "frappe.model.mapper.make_mapped_doc",
        "frappe.model.mapper.map_docs",
        "frappe.handler.run_doc_method",
        "frappe.desk.form.run_method.runserverobj",
        "frappe.model.document.execute_action",
    }
)
_DISPATCH_PREFIXES = ("frappe.client.",)
_MAKE_MAPPED_DOC = "/api/method/frappe.model.mapper.make_mapped_doc"
_METHOD_RESULT_LIMIT = 500
_METHOD_RESULT_TEXT = 2000
_METHOD_RESULT_DEPTH = 8
_METHOD_RESULT_MAX_CHARS = 100_000
_MAPPED_CHILD_ROWS = 200
_METHOD_TOOLS = ("list_allowed_methods", "make_mapped_document", "call_allowed_method")
_CONFIG_SUGGESTION_NOTE = (
    "Suggested FRAPPE_MCP_METHODS entries; kind and params are guesses from the name and "
    "signature. Review each one (a method that changes data must be a write entry), then add "
    "it to the file yourself; the server never writes it."
)
_ANNOTATION_TYPES = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}
_READ_NAME_PREFIXES = ("get_", "list_", "fetch_", "search_", "count_", "is_", "has_")

# method -> entry: the file's keys plus `_args` (argument names from the source, or None when
# unknown or the function takes **kwargs) and `_source` ("<app>/<path>.py:<line>" or None).
_method_config: dict[str, dict[str, Any]] = {}


def _param_type(annotation: str | None) -> str | None:
    """Map a source annotation such as `str | None` or `dict[str, Any]` to a param type."""
    if not annotation:
        return None
    parts = [part.strip() for part in annotation.split("|") if part.strip() != "None"]
    if sorted(parts) == ["float", "int"]:
        return "number"
    if len(parts) != 1:
        return None
    return _ANNOTATION_TYPES.get(parts[0].split("[", 1)[0])


def _config_suggestion(row: dict[str, Any], app_path: Path) -> dict[str, Any]:
    """Turn a `list_app_methods` row of a module function into a config entry to review."""
    name = row["method"].rsplit(".", 1)[-1]
    args = [arg for arg in row["args"] if not arg["name"].startswith("*")]
    verbs = (
        {str(verb).upper() for verb in row["methods"]}
        if isinstance(row["methods"], list)
        else set(_WHITELIST_DEFAULT_METHODS)
    )
    entry: dict[str, Any] = {"method": row["method"]}
    if name.startswith("make_") and args and args[0]["name"] == "source_name":
        entry["kind"] = "mapper"
        source = _controller_doctype(app_path / row["file"].rsplit(":", 1)[0])
        if source:
            entry["source_doctype"] = source
    else:
        read = name.startswith(_READ_NAME_PREFIXES)
        entry["kind"] = "read" if ("GET" in verbs and (read or "POST" not in verbs)) else "write"
    if row["doc"]:
        entry["description"] = row["doc"][:_METHOD_TEXT_MAX]
    if entry["kind"] != "mapper":
        params = {}
        for arg in args:
            if arg["name"] in _FORBIDDEN_METHOD_ARGS:
                continue
            spec: dict[str, Any] = {}
            kind = _param_type(arg.get("annotation"))
            if kind:
                spec["type"] = kind
            if "default" not in arg:
                spec["required"] = True
            params[arg["name"]] = spec
        if params:
            entry["params"] = params
    return entry


def _config_line(text: str, method: str, occurrence: int) -> int | None:
    """Line of the `occurrence`-th `"method": "<method>"` in the config text, if found."""
    pattern = re.compile(r'"method"\s*:\s*"' + re.escape(method) + '"')
    for index, match in enumerate(pattern.finditer(text)):
        if index == occurrence:
            return text.count("\n", 0, match.start()) + 1
    return None


def _config_param(name: Any, spec: Any) -> str | None:
    """Return the problem with one `params` entry, or None."""
    if not isinstance(name, str) or not _METHOD_ARG_NAME.fullmatch(name):
        return f"params name {name!r} is not a Python identifier"
    if name in _FORBIDDEN_METHOD_ARGS:
        return f"params may not include {name}"
    if not isinstance(spec, dict):
        return f"params.{name} must be an object"
    unknown = sorted(set(spec) - _METHOD_PARAM_KEYS)
    if unknown:
        return f"params.{name} has unknown keys: {', '.join(unknown)}"
    if "type" in spec and spec["type"] not in _METHOD_PARAM_TYPES:
        return f"params.{name}.type must be one of {', '.join(_METHOD_PARAM_TYPES)}"
    if "required" in spec and not isinstance(spec["required"], bool):
        return f"params.{name}.required must be true or false"
    enum = spec.get("enum")
    if "enum" in spec and (
        not isinstance(enum, list)
        or not 1 <= len(enum) <= _METHOD_PARAMS_MAX
        or any(not isinstance(item, str | int | float | bool) for item in enum)
    ):
        return f"params.{name}.enum must be a list of 1 to {_METHOD_PARAMS_MAX} scalar values"
    description = spec.get("description")
    if "description" in spec and (
        not isinstance(description, str) or len(description) > _METHOD_TEXT_MAX
    ):
        return f"params.{name}.description must be text up to {_METHOD_TEXT_MAX} characters"
    return None


def _config_entry(item: dict[str, Any]) -> str | None:
    """Return the problem with one config entry's keys and values (not its method), or None."""
    unknown = sorted(set(item) - _METHOD_ENTRY_KEYS)
    if unknown:
        return f"unknown keys: {', '.join(unknown)}"
    kind = item.get("kind")
    if kind not in _METHOD_KINDS:
        return f"kind must be one of {', '.join(_METHOD_KINDS)}"
    description = item.get("description")
    if "description" in item and (
        not isinstance(description, str) or len(description) > _METHOD_TEXT_MAX
    ):
        return f"description must be text up to {_METHOD_TEXT_MAX} characters"
    for key in ("source_doctype", "target_doctype"):
        if key not in item:
            continue
        if not isinstance(item[key], str) or not _SAFE_NAME.fullmatch(item[key]):
            return f"{key} must be a DocType name"
        if kind == "write" or (kind == "read" and key == "source_doctype"):
            return f"{key} does not apply to a {kind} entry"
    if "params" in item:
        params = item["params"]
        if kind == "mapper":
            return "a mapper takes only source_name; remove params"
        if not isinstance(params, dict) or len(params) > _METHOD_PARAMS_MAX:
            return f"params must be an object with at most {_METHOD_PARAMS_MAX} arguments"
        for name, spec in params.items():
            problem = _config_param(name, spec)
            if problem:
                return problem
    return None


def _method_source(method: str) -> dict[str, Any] | None:
    """Find `method` in the app checkout as a whitelisted module function.

    Returns its `list_app_methods` row, `{}` when the checkout doesn't define it that way, or
    None when there is no checkout of the method's app to check against.
    """
    parts = method.split(".")
    if not os.environ.get("FRAPPE_APPS_PATH"):
        return None
    try:
        app_path, package_path = _app_root(parts[0])
    except (RuntimeError, ValueError, OSError):
        return None
    modules = parts[1:-1]
    candidates = (
        [(*modules[:-1], f"{modules[-1]}.py"), (*modules, "__init__.py")]
        if modules
        else [("__init__.py",)]
    )
    for candidate in candidates:
        try:
            path = _app_file(package_path, *candidate)
        except (ValueError, OSError):
            return {}
        if path is None:
            continue
        try:
            tree = ast.parse(path.read_bytes())
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return {}
        file = path.relative_to(app_path).as_posix()
        dotted = ".".join(parts[:-1])
        for row in _file_methods(tree, dotted, path, file):
            if row["method"] == method and row["kind"] == "module":
                return row
        return {}
    return {}


def _source_problem(entry: dict[str, Any], row: dict[str, Any]) -> str | None:
    """Compare an entry with its whitelisted function in the source; record its arguments."""
    methods = row["methods"]
    verbs = {str(verb).upper() for verb in methods} if isinstance(methods, list) else set()
    needed = {"read": "GET", "write": "POST"}.get(entry["kind"])
    if needed and verbs and needed not in verbs:
        return (
            f"{row['file']} whitelists it for {', '.join(sorted(verbs))} only; a "
            f"{entry['kind']} entry calls it with {needed}"
        )
    names = [arg["name"] for arg in row["args"]]
    plain = [name for name in names if not name.startswith("*")]
    if entry["kind"] == "mapper" and not plain:
        return f"{row['file']} takes no argument; a mapper is called with source_name"
    takes_kwargs = any(name.startswith("**") for name in names)
    for param in entry.get("params") or {}:
        if param not in plain and not takes_kwargs:
            return f"{row['file']} has no argument {param}"
    entry["_args"] = None if takes_kwargs else set(plain)
    entry["_source"] = row["file"]
    return None


def _load_method_config(value: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Read and check the FRAPPE_MCP_METHODS file; return (entries by method, warnings).

    Raises RuntimeError naming the file and line of every problem found.
    """
    path = Path(value).expanduser()
    label = path.name or value
    try:
        if path.stat().st_size > _METHOD_CONFIG_MAX_BYTES:
            raise RuntimeError(f"{label} is larger than {_METHOD_CONFIG_MAX_BYTES} bytes")
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise RuntimeError(
            f"{_METHODS_ENV}: cannot read {label} ({type(error).__name__})"
        ) from None
    try:
        items = json.loads(text)
    except ValueError as error:
        line = f" line {error.lineno}" if isinstance(error, json.JSONDecodeError) else ""
        raise RuntimeError(f"{label}{line}: invalid JSON") from None
    if not isinstance(items, list) or not 1 <= len(items) <= _METHOD_CONFIG_MAX_ENTRIES:
        raise RuntimeError(
            f"{label}: the file must hold a JSON list of 1 to {_METHOD_CONFIG_MAX_ENTRIES} entries"
        )
    entries: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    seen: dict[str, int] = {}
    unchecked: dict[str, int] = {}
    for index, item in enumerate(items, 1):
        method = item.get("method") if isinstance(item, dict) else None
        if not isinstance(method, str):
            problems.append(f"{label} entry {index}: needs a method")
            continue
        occurrence = seen.get(method, 0)
        seen[method] = occurrence + 1
        line = _config_line(text, method, occurrence)
        where = f"{label} line {line}" if line else f"{label} entry {index}"
        if "*" in method:
            problems.append(f"{where}: {method}: wildcards are not allowed; list each method")
            continue
        if len(method) > _METHOD_PATH_MAX or not _METHOD_PATH.fullmatch(method):
            problems.append(f"{where}: {method!r} is not a dotted Python path")
            continue
        if method in _DISPATCH_METHODS or method.startswith(_DISPATCH_PREFIXES):
            problems.append(
                f"{where}: {method} runs other methods or writes any record; it can't be "
                "allowlisted (use the record tools)"
            )
            continue
        if occurrence:
            problems.append(f"{where}: {method} is listed twice")
            continue
        problem = _config_entry(item)
        if problem:
            problems.append(f"{where}: {method}: {problem}")
            continue
        entry = {key: copy.deepcopy(item[key]) for key in _METHOD_ENTRY_KEYS if key in item}
        entry.update({"_args": None, "_source": None})
        row = _method_source(method)
        if row is None:
            app = method.split(".", 1)[0]
            unchecked[app] = unchecked.get(app, 0) + 1
        elif not row:
            problems.append(
                f"{where}: {method} is not a whitelisted module function in the "
                f"{method.split('.', 1)[0]} checkout"
            )
            continue
        else:
            problem = _source_problem(entry, row)
            if problem:
                problems.append(f"{where}: {method}: {problem}")
                continue
        entries[method] = entry
    if problems:
        shown = problems[:20]
        if len(problems) > len(shown):
            shown.append(f"... and {len(problems) - len(shown)} more")
        raise RuntimeError(f"{_METHODS_ENV} has problems:\n" + "\n".join(shown))
    apps_path_set = bool(os.environ.get("FRAPPE_APPS_PATH"))
    warnings = [
        f"{label}: {count} {app} entr{'y' if count == 1 else 'ies'} not checked against the "
        "app source ("
        + (
            f"no {app} checkout under FRAPPE_APPS_PATH"
            if apps_path_set
            else "FRAPPE_APPS_PATH is not set"
        )
        + ")"
        for app, count in sorted(unchecked.items())
    ]
    return entries, warnings


def _register_method_tools() -> None:
    """Register the method tools in the data toolset (once)."""
    if _METHOD_TOOLS[0] in server._tool_manager._tools:
        return
    _tool("data", "List allowed methods", read_only=True)(list_allowed_methods)
    _tool("data", "Make mapped document", read_only=True)(make_mapped_document)
    _tool("data", "Call allowed method", destructive=True)(call_allowed_method)


def _unregister_method_tools() -> None:
    for name in _METHOD_TOOLS:
        if name in server._tool_manager._tools:
            server.remove_tool(name)
        _TOOLSETS["data"].discard(name)


def _method_settings() -> list[str]:
    """Load FRAPPE_MCP_METHODS when set and register the method tools; return warnings."""
    _method_config.clear()
    value = (os.environ.get(_METHODS_ENV) or "").strip()
    if not value:
        return []
    entries, warnings = _load_method_config(value)
    _method_config.update(entries)
    _register_method_tools()
    return warnings


def _allowed_entry(method: Any, kinds: tuple[str, ...], tool: str) -> dict[str, Any]:
    """Return the config entry for `method`, refusing unlisted methods and other kinds."""
    entry = _method_config.get(method) if isinstance(method, str) else None
    if entry is None:
        raise ValueError(
            f"{method!r} is not in the FRAPPE_MCP_METHODS allowlist; list_allowed_methods shows "
            "what can be called"
        )
    if entry["kind"] not in kinds:
        other = "make_mapped_document" if entry["kind"] == "mapper" else "call_allowed_method"
        raise ValueError(f"{method} is a {entry['kind']} entry; call it with {other}, not {tool}")
    return entry


def _method_call_args(entry: dict[str, Any], args: Any) -> dict[str, Any]:
    """Check `args` against the entry's params (or, without params, allow scalars only)."""
    method = entry["method"]
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("args must be an object of argument names and values")
    params = entry.get("params")
    known = entry.get("_args")
    checked: dict[str, Any] = {}
    for name, value in args.items():
        if not isinstance(name, str) or not _METHOD_ARG_NAME.fullmatch(name):
            raise ValueError(f"Invalid argument name: {name!r}")
        if name in _FORBIDDEN_METHOD_ARGS:
            raise ValueError(f"The argument {name} is never sent")
        if value is None:
            continue
        if params is not None:
            spec = params.get(name)
            if spec is None:
                allowed = ", ".join(params) or "none"
                raise ValueError(f"{method} takes no argument {name}; allowed: {allowed}")
            kind = spec.get("type")
            if kind is None and not isinstance(value, str | int | float | bool):
                raise ValueError(f"{name} must be a scalar value")
            if kind is not None and (
                not isinstance(value, _METHOD_PARAM_TYPES[kind])
                or (kind in ("integer", "number") and isinstance(value, bool))
            ):
                raise ValueError(f"{name} must be of type {kind}")
            if "enum" in spec and value not in spec["enum"]:
                raise ValueError(f"{name} must be one of {spec['enum']}")
        else:
            if known is not None and name not in known:
                raise ValueError(f"{method} takes no argument {name}")
            if not isinstance(value, str | int | float | bool):
                raise ValueError(
                    f"{name} must be a scalar value; add params to the {method} entry to allow "
                    "objects or arrays"
                )
        if len(json.dumps(value, default=str)) > _METHOD_ARG_MAX_CHARS:
            raise ValueError(f"{name} is longer than {_METHOD_ARG_MAX_CHARS} characters")
        checked[name] = value
    missing = [
        name
        for name, spec in (params or {}).items()
        if spec.get("required") and name not in checked
    ]
    if missing:
        raise ValueError(f"{method} needs {', '.join(missing)}")
    return checked


def _capped(value: Any, limit: int, state: dict[str, bool], depth: int = 0) -> Any:
    """Cap nested lists and objects at `limit` items and strings at a fixed length."""
    if isinstance(value, list | dict):
        if depth >= _METHOD_RESULT_DEPTH:
            state["truncated"] = True
            return "..."
        items = list(value.items() if isinstance(value, dict) else value)
        if len(items) > limit:
            state["truncated"] = True
            items = items[:limit]
        if isinstance(value, dict):
            return {key: _capped(item, limit, state, depth + 1) for key, item in items}
        return [_capped(item, limit, state, depth + 1) for item in items]
    if isinstance(value, str) and len(value) > _METHOD_RESULT_TEXT:
        state["truncated"] = True
        return value[:_METHOD_RESULT_TEXT] + "..."
    return value


def _bounded_result(value: Any, limit: int, offset: int) -> dict[str, Any]:
    """Page a list result like run_report; cap anything else. `truncated` says if cut."""
    state = {"truncated": False}
    out: dict[str, Any] = {}
    if isinstance(value, list):
        page = value[offset : offset + limit]
        out["result"] = [_capped(item, limit, state, 1) for item in page]
        out.update(
            {
                "total_rows": len(value),
                "offset": offset,
                "limit": limit,
                "returned_rows": len(page),
                "has_more": len(value) > offset + limit,
            }
        )
    else:
        out["result"] = _capped(value, limit, state)
    text = json.dumps(out["result"], default=str)
    if len(text) > _METHOD_RESULT_MAX_CHARS:
        state["truncated"] = True
        out["result"] = None
        out["result_text"] = text[:_METHOD_RESULT_MAX_CHARS]
    out["truncated"] = state["truncated"]
    return out


def _draft_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if not key.startswith("__") and key not in _RESERVED_FIELDS and not _is_empty(value)
    }


def _mapped_draft(doc: Any, method: str, expected: str | None) -> dict[str, Any]:
    """Shape an unsaved document as `create_document` input.

    `values` holds the fields create_document accepts (child rows whole, minus local names);
    read-only fields, which Frappe computes again on save, go to `read_only`.
    """
    target = doc.get("doctype") if isinstance(doc, dict) else None
    if not isinstance(target, str) or not _SAFE_NAME.fullmatch(target):
        raise RuntimeError(f"{method} did not return a document")
    if expected and target != expected:
        raise RuntimeError(f"{method} returned a {target}, not the listed {expected}")
    fields = _schema_fields(_meta_bundle(target)["doctype"])
    values: dict[str, Any] = {}
    read_only: dict[str, Any] = {}
    truncated = []
    for key, value in doc.items():
        row = fields.get(key)
        if row is None or key in _RESERVED_FIELDS or _is_empty(value):
            continue
        if row.get("fieldtype") in _TABLE_FIELDTYPES and isinstance(value, list):
            if len(value) > _MAPPED_CHILD_ROWS:
                truncated.append(key)
                value = value[:_MAPPED_CHILD_ROWS]
            value = [_draft_row(item) for item in value if isinstance(item, dict)]
        (read_only if row.get("read_only") else values)[key] = value
    state = {"truncated": False}
    result: dict[str, Any] = {
        "doctype": target,
        "values": values,
        "read_only": _capped(read_only, _MAPPED_CHILD_ROWS, state),
    }
    if truncated:
        result["_truncated_child_tables"] = sorted(truncated)
        result["note"] = (
            f"Child tables over {_MAPPED_CHILD_ROWS} rows were cut; this draft can't be "
            "created as it is"
        )
    return result


def list_allowed_methods() -> dict[str, Any]:
    """List the methods FRAPPE_MCP_METHODS allows: kind (mapper, read, write), description, params.

    No other method can be called.
    """
    rows = []
    for method in sorted(_method_config):
        entry = _method_config[method]
        row = {key: entry[key] for key in sorted(_METHOD_ENTRY_KEYS) if key in entry}
        row["checked_against_source"] = entry.get("_source") is not None
        rows.append(row)
    return {"methods": rows, "count": len(rows)}


def make_mapped_document(method: str, source_name: str) -> dict[str, Any]:
    """Map a record into a new unsaved draft through an allowlisted mapper (nothing is saved).

    Returns `doctype` and `values` for create_document; `read_only` holds fields Frappe
    computes again on save.
    """
    entry = _allowed_entry(method, ("mapper",), "make_mapped_document")
    if not isinstance(source_name, str) or not source_name.strip() or len(source_name) > 140:
        raise ValueError("source_name must be the name of the source record")
    # GET: Frappe rolls back anything a GET request writes, so the mapper only builds a draft.
    doc = _request("GET", _MAKE_MAPPED_DOC, params={"method": method, "source_name": source_name})
    return {
        "method": method,
        "source_doctype": entry.get("source_doctype"),
        "source_name": source_name,
        **_mapped_draft(doc, method, entry.get("target_doctype")),
    }


def call_allowed_method(
    method: str,
    args: dict[str, Any] | None = None,
    limit: int = 100,
    offset: int = 0,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Call an allowlisted read or write method; `args` must fit its params.

    read runs at once (GET); write returns a preview until confirm. A list result is paged
    (`limit`, `offset`, `has_more`); long results are cut (`truncated`).
    """
    entry = _allowed_entry(method, ("read", "write"), "call_allowed_method")
    if not isinstance(limit, int) or not 1 <= limit <= _METHOD_RESULT_LIMIT:
        raise ValueError(f"limit must be between 1 and {_METHOD_RESULT_LIMIT}")
    _assert_offset(offset)
    checked = _method_call_args(entry, args)
    path = f"/api/method/{method}"
    if entry["kind"] == "write":
        if not confirm:
            return {
                "preview": True,
                "action": "call_allowed_method",
                "method": method,
                "kind": "write",
                "description": entry.get("description"),
                "args": checked,
                "requires_confirmation": True,
            }
        value = _request("POST", path, json=checked)
    else:
        # GET carries strings; objects and arrays go as JSON, which Frappe parses back.
        params = {
            name: json.dumps(value) if isinstance(value, dict | list) else value
            for name, value in checked.items()
        }
        value = _request("GET", path, params=params)
    target = entry.get("target_doctype")
    if target and isinstance(value, dict) and value.get("doctype") == target:
        return {"method": method, "kind": entry["kind"], **_mapped_draft(value, method, target)}
    return {"method": method, "kind": entry["kind"], **_bounded_result(value, limit, offset)}


def _promoted(doc: Any, module: str, expected_modified: str) -> bool:
    return (
        isinstance(doc, dict)
        and not doc.get("custom")
        and doc.get("module") == module
        and str(doc.get("modified")) != expected_modified
    )


def _custom_link_targets(doc: dict[str, Any]) -> list[str]:
    """Warn about Link and Table fields that point to site-custom DocTypes."""
    targets: dict[str, list[str]] = {}
    for row in doc.get("fields") or []:
        target = row.get("options")
        if (
            row.get("fieldtype") in {"Link", *_TABLE_FIELDTYPES}
            and isinstance(target, str)
            and target != doc.get("name")
            and _SAFE_NAME.fullmatch(target)
        ):
            targets.setdefault(target, []).append(row.get("fieldname"))
    if not targets:
        return []
    rows = _request(
        "GET",
        _resource("DocType"),
        params={
            "fields": '["name","custom"]',
            "filters": _filters([["name", "in", sorted(targets)], ["custom", "=", 1]]),
            "limit_page_length": len(targets),
        },
    )
    return [
        f"{row.get('name')} (used by {', '.join(targets.get(row.get('name'), []))}) is a "
        "site-custom DocType; promote it too, or sites that install the app will lack it."
        for row in rows
        if row.get("custom")
    ]


@_tool("developer", "Promote custom DocType to app")
def promote_custom_doctype(
    doctype: str,
    app: str,
    module: str,
    confirm: Confirm = False,
    expected_modified: ExpectedModified = None,
) -> dict[str, Any]:
    """Preview or turn a site-custom DocType into a standard DocType of an app module.

    Frappe saves it with custom=0 in developer mode and writes `<module>/doctype/<doctype>/` into
    the app; records stay. Refuses frappe/erpnext, other apps' modules, and an existing folder.
    """
    if not isinstance(doctype, str) or not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")
    _assert_writable_app(app)
    app_path, module_path = _app_path(app, module)
    if _assert_remote_module(app, module).get("custom"):
        raise ValueError(
            f"{module} is a custom module; Frappe exports its DocTypes into the site folder, "
            f"not an app. Pick a module of {app}."
        )
    doc = _request("GET", _resource("DocType", doctype))
    if not doc.get("custom"):
        raise ValueError(
            f"{doctype} is already a standard DocType of module {doc.get('module')}; "
            "nothing to promote"
        )
    if doc.get("default_print_format"):
        raise ValueError(
            f"{doctype} has a default print format, which Frappe refuses on a standard "
            "DocType; clear default_print_format first"
        )
    slug = _scrub(doctype)
    doctype_path = module_path / "doctype" / slug
    if doctype_path.exists() or doctype_path.is_symlink():
        raise ValueError(f"{doctype_path} already exists; refusing to reuse an app's folder")
    istable, is_tree = bool(doc.get("istable")), bool(doc.get("is_tree"))
    files = {
        "json": doctype_path / f"{slug}.json",
        "python": doctype_path / f"{slug}.py",
    }
    if not istable:
        files["javascript"] = doctype_path / f"{slug}.js"
        files["test"] = doctype_path / f"test_{slug}.py"
    if is_tree:
        files["tree_javascript"] = doctype_path / f"{slug}_tree.js"
    if doc.get("has_web_view"):
        files["web_template"] = doctype_path / "templates" / f"{slug}.html"
        files["web_list_template"] = doctype_path / "templates" / f"{slug}_list.html"

    warnings = _custom_link_targets(doc)
    custom_fields = _count_records("Custom Field", [["dt", "=", doctype]])
    property_setters = _count_records("Property Setter", [["doc_type", "=", doctype]])
    if custom_fields or property_setters:
        warnings.append(
            f"{custom_fields or 0} Custom Fields and {property_setters or 0} Property Setters "
            "on this DocType stay site customizations, outside the DocType JSON; export them "
            "with export_customizations to ship them too."
        )
    if doc.get("module") != module:
        warnings.append(f"The DocType moves from module {doc.get('module')} to {module}.")
    if not confirm:
        return {
            "preview": True,
            "action": "promote_custom_doctype",
            "doctype": doctype,
            "app": app,
            "module": module,
            "modified": doc.get("modified"),
            "folder": str(doctype_path),
            "files": [str(path) for path in files.values()],
            "warnings": warnings,
            "requires_developer_mode": True,
            "notes": [
                "Frappe writes these files only with developer mode on, in the site's own "
                "Bench; FRAPPE_APPS_PATH must be that Bench's apps folder.",
                "The MCP writes the controller boilerplates first, so a developer-mode reload "
                "does not cut off the save. A developer-mode Bench may still reload.",
                "Records stay in the DocType's table; nothing is migrated.",
            ],
            "requires_confirmation": True,
        }
    if not expected_modified or str(doc.get("modified")) != expected_modified:
        raise ValueError(_STALE_DOCUMENT)
    generated = list(files.values())
    reconciled = False
    try:
        _prepare_native_doctype_scaffolding(
            app_path, module_path, doctype_path, doctype, is_child_table=istable, is_tree=is_tree
        )
        _wait_for_server_ready()
        try:
            # Sending `modified` makes Frappe compare it with the stored value under the row lock.
            saved = _request(
                "PUT",
                _resource("DocType", doctype),
                json={"data": {"custom": 0, "module": module, "modified": expected_modified}},
            )
        except FrappeError as error:
            if error.exc_type == "TimestampMismatchError":
                raise ValueError(_STALE_DOCUMENT) from error
            if error.exc_type == "CannotCreateStandardDoctypeError":
                raise ValueError(
                    f"Frappe refused to promote {doctype}: standard DocTypes can only be saved "
                    f"with developer mode on ({error})"
                ) from error
            raise
        except httpx.TransportError as error:
            # Frappe writes the DocType JSON during the save, which can reload a dev Bench.
            _wait_for_server_ready()
            saved = _request("GET", _resource("DocType", doctype))
            if not _promoted(saved, module, expected_modified):
                raise RuntimeError(
                    f"Frappe disconnected while promoting {doctype} and it is still a custom "
                    "DocType; request a fresh preview before retrying."
                ) from error
            reconciled = True
    except Exception:
        try:
            persisted = _promoted(
                _request("GET", _resource("DocType", doctype)), module, expected_modified
            )
        except Exception:
            persisted = True
        if not persisted:
            _remove_partial_doctype_tree(doctype_path, generated)
        raise
    finally:
        _forget_meta(doctype)
    file_status = {key: path.is_file() for key, path in files.items()}
    if not file_status["json"]:
        warnings.append(
            "Frappe saved the DocType but no JSON appeared under FRAPPE_APPS_PATH; check that "
            "it is the apps folder of the site's Bench."
        )
    return {
        "promoted": True,
        "doctype": doctype,
        "app": app,
        "module": module,
        "modified": saved.get("modified") if isinstance(saved, dict) else None,
        "folder": str(doctype_path),
        "files": {key: str(path) for key, path in files.items()},
        "file_status": file_status,
        "native_files_complete": all(file_status.values()),
        "warnings": warnings,
        "reconciled_after_server_reload": reconciled,
    }


@_tool("customize", "Set role permissions", destructive=True, idempotent=True)
def set_role_permissions(
    doctype: str,
    role: str,
    permissions: dict[str, bool],
    permlevel: int = 0,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or set a role's permission flags on a DocType through Frappe's native manager."""
    if not _SAFE_NAME.fullmatch(doctype) or not _SAFE_NAME.fullmatch(role):
        raise ValueError("A valid DocType and role are required")
    if doctype in _PERMISSION_MANAGER_EXCLUDED_DOCTYPES:
        raise ValueError("DocType is excluded from role permission management")
    if not isinstance(permlevel, int) or isinstance(permlevel, bool) or not 0 <= permlevel <= 9:
        raise ValueError("permlevel must be an integer from 0 to 9")
    if not isinstance(permissions, dict) or not permissions:
        raise ValueError("permissions must be a non-empty object")
    unsupported = set(permissions) - _ROLE_PERMISSION_FLAGS
    if unsupported:
        raise ValueError(f"Unsupported permission flags: {', '.join(sorted(unsupported))}")
    if any(not isinstance(value, bool) for value in permissions.values()):
        raise ValueError("Permission values must be booleans")

    schema = _meta_bundle(doctype)["doctype"]
    if schema.get("istable") or schema.get("is_virtual"):
        raise ValueError("Child-table and virtual DocTypes cannot use Role Permission Manager")
    role_rows = _request(
        "GET",
        _resource("Role"),
        params={
            "fields": '["name","disabled"]',
            "filters": _filters({"name": role}),
            "limit_page_length": 1,
        },
    )
    if not role_rows or role_rows[0].get("disabled"):
        raise ValueError(f"Unknown or disabled role: {role}")

    preview = {
        "preview": True,
        "action": "set_role_permissions",
        "doctype": doctype,
        "role": role,
        "permlevel": permlevel,
        "permissions": permissions,
        "requires_confirmation": True,
    }
    if not confirm:
        return preview

    permission_rows = _request(
        "GET",
        _resource("Custom DocPerm"),
        params={
            "fields": '["name"]',
            "filters": _filters(
                {"parent": doctype, "role": role, "permlevel": permlevel, "if_owner": 0}
            ),
            "limit_page_length": 1,
        },
    )
    base_url = "/api/method/frappe.core.page.permission_manager.permission_manager"
    try:
        if not permission_rows:
            _request(
                "POST",
                f"{base_url}.add",
                json={
                    "parent": doctype,
                    "role": role,
                    "permlevel": permlevel,
                },
            )
        for flag, value in permissions.items():
            _request(
                "POST",
                f"{base_url}.update",
                json={
                    "doctype": doctype,
                    "role": role,
                    "permlevel": permlevel,
                    "ptype": flag,
                    "value": int(value),
                    "if_owner": 0,
                },
            )
    finally:
        _forget_meta(doctype)
    return {
        "updated": True,
        "doctype": doctype,
        "role": role,
        "permlevel": permlevel,
        "permissions": permissions,
    }


@_tool("customize", "Create custom field")
def create_custom_field(
    doctype: str,
    field: dict[str, Any],
    app: str | None = None,
    module: str | None = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or create a Custom Field on any DocType; no app checkout needed.

    `field` holds fieldname, fieldtype, label, insert_after, ... (not doctype, dt, or module).
    `module` optionally tags it for export and must belong to `app` when both are given. Change it
    later with set_field_property; delete it with delete_document.
    """
    if not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("A valid DocType is required")
    if not isinstance(field, dict) or not field.get("fieldname"):
        raise ValueError("field must be an object with a fieldname")
    fixed = [key for key in _CUSTOM_FIELD_FIXED_KEYS if key in field]
    if fixed:
        raise ValueError(
            f"field may not set {', '.join(fixed)}; pass the DocType and module as arguments"
        )
    _assert_optional_module(app, module)
    schema = _meta_bundle(doctype)["doctype"]
    existing = {row.get("fieldname") for row in schema.get("fields", [])}
    if field["fieldname"] in existing:
        raise ValueError("Field already exists")
    data = {**field, "doctype": "Custom Field", "dt": doctype}
    fieldtype = data.get("fieldtype")
    if fieldtype not in _site_fieldtypes():
        raise ValueError(f"Unsupported fieldtype for this Frappe site: {fieldtype!r}")
    if module is not None:
        data["module"] = module
    if not confirm:
        return {
            "preview": True,
            "action": "create_custom_field",
            "app": app,
            "doctype": doctype,
            "field": data,
            "requires_confirmation": True,
        }
    try:
        created = _request("POST", _resource("Custom Field"), json={"data": data})
    finally:
        _forget_meta(doctype)
    if not isinstance(created, dict):
        return created
    return _journal(
        created,
        "create_custom_field",
        {"doctype": doctype, "fieldname": field["fieldname"], "custom_field": created.get("name")},
        undo={
            "op": "delete_documents",
            "doctype": "Custom Field",
            "modified": {str(created.get("name")): created.get("modified")},
        },
    )


def _property_value(property: str, value: Any, property_type: str) -> str:
    """Normalize a Property Setter value for its type; Check accepts 0/1 and booleans."""
    if property_type == "Check":
        if isinstance(value, bool):
            return "1" if value else "0"
        if value in (0, 1, "0", "1") and not isinstance(value, float):
            return str(int(value))
        raise ValueError(f"{property} is a Check property; value must be 0 or 1 (or a boolean)")
    if not isinstance(value, str):
        raise ValueError(f"{property} is a {property_type} property; value must be a string")
    return value


@_tool("customize", "Set field property", idempotent=True)
def set_field_property(
    doctype: str,
    property: str,
    value: str | int | bool,
    fieldname: str | None = None,
    property_type: str | None = None,
    app: str | None = None,
    module: str | None = None,
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or save a Customize Form value as a Property Setter.

    Sets `property` on `fieldname`, or on the DocType when it is omitted. `property_type` is
    inferred (Check, Data, or Text); Check values are 0/1 or booleans. An existing setter gives a
    preview with `mode: "update"` and `modified`, which confirming needs as `expected_modified`. A
    new DocType-level setter replaces the field-level ones listed in `replaces_field_setters`.
    """
    if property not in _PROPERTY_TYPES:
        raise ValueError(
            "Property is not in the supported Customize Form allowlist: "
            + ", ".join(sorted(_PROPERTY_TYPES))
        )
    inferred = _PROPERTY_TYPES[property]
    if property_type is not None and property_type != inferred:
        raise ValueError(f"property_type for {property} is {inferred}")
    stored = _property_value(property, value, inferred)
    _assert_optional_module(app, module)
    schema = _meta_bundle(doctype)["doctype"]
    if fieldname and fieldname not in {row.get("fieldname") for row in schema.get("fields", [])}:
        raise ValueError("Unknown fieldname")

    rows = _request(
        "GET",
        _resource("Property Setter"),
        params={
            "fields": '["name","field_name","row_name","value","property_type","modified"]',
            "filters": _filters({"doc_type": doctype, "property": property}),
            "limit_page_length": 500,
        },
    )
    matches = [
        row
        for row in rows
        if (row.get("field_name") or None) == (fieldname or None) and not row.get("row_name")
    ]
    if len(matches) > 1:
        names = ", ".join(row["name"] for row in matches)
        raise ValueError(f"Several Property Setters match; resolve them first: {names}")
    existing = matches[0] if matches else None

    preview: dict[str, Any] = {
        "preview": True,
        "action": "set_field_property",
        "mode": "update" if existing else "create",
        "doctype": doctype,
        "doctype_or_field": "DocField" if fieldname else "DocType",
        "fieldname": fieldname,
        "property": property,
        "value": stored,
        "property_type": inferred,
        "module": module,
        "requires_confirmation": True,
    }
    if existing:
        preview.update(
            name=existing["name"],
            current_value=existing.get("value"),
            modified=existing.get("modified"),
        )
    elif not fieldname:
        replaced = sorted(row["field_name"] for row in rows if row.get("field_name"))
        if replaced:
            preview["replaces_field_setters"] = replaced
    if not confirm:
        return preview
    try:
        saved = _save_property_setter(preview, existing, expected_modified)
    finally:
        _forget_meta(doctype)
    if not isinstance(saved, dict):
        return saved
    undo: dict[str, Any] | None
    reason = None
    if existing:
        undo = {
            "op": "set_field_property",
            "doctype": doctype,
            "fieldname": fieldname,
            "property": property,
            "value": existing.get("value") if existing.get("value") is not None else "",
            "setter": existing["name"],
            "modified": saved.get("modified"),
        }
    elif preview.get("replaces_field_setters"):
        undo = None
        reason = (
            "this DocType-level setter replaced field-level ones, which deleting it would not "
            "bring back"
        )
    else:
        undo = {
            "op": "delete_documents",
            "doctype": "Property Setter",
            "modified": {str(saved.get("name")): saved.get("modified")},
        }
    return _journal(
        saved,
        "set_field_property",
        {
            "doctype": doctype,
            "fieldname": fieldname,
            "property": property,
            "property_setter": saved.get("name"),
        },
        undo=undo,
        reason=reason,
    )


def _save_property_setter(
    preview: dict[str, Any], existing: dict[str, Any] | None, expected_modified: str | None
) -> Any:
    """Create the previewed Property Setter, or update the existing one under its token."""
    doctype, fieldname, module = preview["doctype"], preview["fieldname"], preview["module"]
    stored, inferred = preview["value"], preview["property_type"]
    property = preview["property"]
    if not existing:
        if expected_modified is not None:
            raise ValueError("The Property Setter no longer exists; request a fresh preview")
        data = {
            "doctype": "Property Setter",
            "doctype_or_field": preview["doctype_or_field"],
            "doc_type": doctype,
            "field_name": fieldname,
            "property": property,
            "value": stored,
            "property_type": inferred,
            "is_system_generated": 0,
        }
        if module is not None:
            data["module"] = module
        return _request("POST", _resource("Property Setter"), json={"data": data})

    name = existing["name"]
    _expected_modified("Property Setter", name, expected_modified)
    values: dict[str, Any] = {"value": stored, "property_type": inferred}
    if module is not None:
        values["module"] = module
    # Same guard as update_document: Frappe compares `modified` under the row lock.
    try:
        return _request(
            "PUT",
            _resource("Property Setter", name),
            json={"data": {**values, "modified": expected_modified}},
        )
    except FrappeError as error:
        if error.exc_type == "TimestampMismatchError":
            raise ValueError(_STALE_DOCUMENT) from error
        raise


# Customize Form saves: the same `save_customization` Desk runs, so Frappe compares each value
# with the standard one, drops redundant Property Setters, keeps the field order, and validates
# the whole form together.

# The full path: v15 and v16 resolve it to the same function, without the shorthand warning.
_RUN_DOC_METHOD = "/api/method/frappe.handler.run_doc_method"
# Customize Form's `docfield_properties` (customize_form.py, v16.55.1) that update_doctype also
# allows, with Customize Form's property types. `fieldtype` has its own operation; `idx`,
# `is_virtual`, `collapsible_depends_on`, `ignore_xss_filter`, and `in_import_template` are left
# out. v15 lacks alignment, button_color, mask, and set_only_once.
_CUSTOMIZE_FIELD_PROPERTIES = {
    "label": "Data",
    "options": "Text",
    "sort_options": "Check",
    "fetch_from": "Small Text",
    "fetch_if_empty": "Check",
    "show_dashboard": "Check",
    "permlevel": "Int",
    "width": "Data",
    "print_width": "Data",
    "alignment": "Select",
    "non_negative": "Check",
    "reqd": "Check",
    "unique": "Check",
    "ignore_user_permissions": "Check",
    "in_list_view": "Check",
    "in_standard_filter": "Check",
    "in_global_search": "Check",
    "in_preview": "Check",
    "bold": "Check",
    "no_copy": "Check",
    "hidden": "Check",
    "collapsible": "Check",
    "print_hide": "Check",
    "print_hide_if_no_value": "Check",
    "report_hide": "Check",
    "allow_on_submit": "Check",
    "translatable": "Check",
    "mandatory_depends_on": "Data",
    "read_only_depends_on": "Data",
    "depends_on": "Data",
    "description": "Text",
    "default": "Text",
    "precision": "Select",
    "read_only": "Check",
    "length": "Int",
    "columns": "Int",
    "remember_last_selected_value": "Check",
    "allow_bulk_edit": "Check",
    "allow_in_quick_entry": "Check",
    "hide_border": "Check",
    "hide_days": "Check",
    "hide_seconds": "Check",
    "link_filters": "JSON",
    "placeholder": "Data",
    "button_color": "Select",
    "mask": "Check",
    "set_only_once": "Check",
}
# Customize Form's `doctype_properties` (v16.55.1) that update_doctype also allows; `istable`
# is left out. v15 lacks hide_toolbar.
_CUSTOMIZE_DOCTYPE_PROPERTIES = {
    "search_fields": "Data",
    "title_field": "Data",
    "image_field": "Data",
    "sort_field": "Data",
    "sort_order": "Data",
    "default_print_format": "Data",
    "hide_toolbar": "Check",
    "allow_copy": "Check",
    "quick_entry": "Check",
    "queue_in_background": "Check",
    "editable_grid": "Check",
    "max_attachments": "Int",
    "make_attachments_public": "Check",
    "protect_attached_files": "Check",
    "track_changes": "Check",
    "track_views": "Check",
    "allow_auto_repeat": "Check",
    "allow_import": "Check",
    "show_name_in_global_search": "Check",
    "show_preview_popup": "Check",
    "default_email_template": "Data",
    "email_append_to": "Check",
    "subject_field": "Data",
    "sender_field": "Data",
    "naming_rule": "Data",
    "autoname": "Data",
    "show_title_field_in_link": "Check",
    "is_calendar_and_gantt": "Check",
    "default_view": "Select",
    "force_re_route_to_default_view": "Check",
    "translated_doctype": "Check",
    "grid_page_length": "Int",
    "rows_threshold_for_grid_search": "Int",
}
# Customize Form's ALLOWED_OPTIONS_CHANGE, without HTML (its options are markup).
_CUSTOMIZE_OPTIONS_FIELDTYPES = frozenset({"Data", "Read Only"})
# frappe.model.no_value_fields; get_fields_not_allowed_in_list_view starts from it.
_NO_VALUE_FIELDTYPES = frozenset(
    {
        "Section Break",
        "Column Break",
        "Tab Break",
        "Attachment Gallery",
        "HTML",
        "Table",
        "Table MultiSelect",
        "Button",
        "Image",
        "Fold",
        "Heading",
    }
)
# frappe.model.docfield.supports_translation.
_TRANSLATABLE_FIELDTYPES = frozenset({"Data", "Select", "Text", "Small Text", "Text Editor"})
_CUSTOMIZE_OPERATIONS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "set_property": (frozenset({"property", "value"}), frozenset({"fieldname"})),
    "move_field": (frozenset({"fieldname", "insert_after"}), frozenset()),
    "add_custom_field": (frozenset({"field"}), frozenset({"insert_after"})),
    "change_fieldtype": (frozenset({"fieldname", "fieldtype"}), frozenset()),
}
_CUSTOMIZE_LIST_LIMIT = 2000


def _typed_value(where: str, property: str, value: Any, property_type: str) -> Any:
    """Check a Customize Form value against its property type and return it as Frappe holds it."""
    if property_type == "Check":
        if isinstance(value, bool) or value in (0, 1):
            return int(value)
        raise ValueError(f"{where}: {property} is a Check property; value must be 0 or 1")
    if property_type == "Int":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise ValueError(f"{where}: {property} must be an integer")
    if not isinstance(value, str):
        raise ValueError(f"{where}: {property} must be a string")
    return value


def _customize_form(doctype: str) -> dict[str, Any]:
    """Load Customize Form for `doctype` the way Desk does; GET, so Frappe keeps no writes."""
    payload = _request(
        "GET",
        _RUN_DOC_METHOD,
        params={
            "method": "fetch_to_customize",
            "docs": json.dumps(
                {"doctype": "Customize Form", "name": "Customize Form", "doc_type": doctype}
            ),
        },
    )
    docs = payload.get("docs") if isinstance(payload, dict) else None
    form = docs[0] if isinstance(docs, list) and docs else None
    if not isinstance(form, dict) or form.get("doc_type") != doctype:
        raise RuntimeError("Unexpected Customize Form response")
    if not isinstance(form.get("fields"), list):
        raise RuntimeError("Customize Form returned no fields")
    return form


def _listed(doctype: str, fields: list[str], filters: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _request(
        "GET",
        _resource(doctype),
        params={
            "fields": json.dumps(fields),
            "filters": _filters(filters),
            "order_by": "name asc",
            "limit_page_length": _CUSTOMIZE_LIST_LIMIT + 1,
        },
    )
    rows = rows if isinstance(rows, list) else []
    if len(rows) > _CUSTOMIZE_LIST_LIMIT:
        raise ValueError(f"More than {_CUSTOMIZE_LIST_LIMIT} {doctype} rows for this DocType")
    return rows


def _customization_state(doctype: str) -> dict[str, Any]:
    """Read what a Customize Form save compares with: the form, base fields, setters, fields."""
    if not isinstance(doctype, str) or not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")
    meta = _meta_bundle(doctype)["doctype"]
    if meta.get("custom"):
        raise ValueError(
            f"{doctype} is a site-custom DocType; Customize Form refuses it. Change it with "
            "update_doctype."
        )
    if meta.get("issingle"):
        raise ValueError("Customize Form refuses Single DocTypes")
    form = _customize_form(doctype)
    record = _request("GET", _resource("DocType", doctype))
    setters = _listed(
        "Property Setter",
        ["name", "doctype_or_field", "field_name", "row_name", "property", "value", "modified"],
        {"doc_type": doctype},
    )
    custom_fields = _listed(
        "Custom Field",
        ["name", "fieldname", "insert_after", "is_system_generated", "modified"],
        {"dt": doctype},
    )
    return {
        "form": form,
        "record": record,
        "base": {row.get("fieldname"): row for row in record.get("fields") or []},
        "setters": setters,
        "custom": {row.get("fieldname"): row for row in custom_fields},
    }


def _is_custom_row(row: dict[str, Any]) -> bool:
    """Customize Form's test: a Custom Field it saves directly, not through Property Setters."""
    return bool(row.get("is_custom_field")) and not row.get("is_system_generated")


def _plan_customization(
    form: dict[str, Any],
    operations: Any,
    *,
    field_properties: dict[str, str] | None = None,
    doctype_properties: dict[str, str] | None = None,
    supported_fieldtypes: frozenset[str] = _FIELDTYPES,
) -> dict[str, Any]:
    """Apply `operations` to a copy of the Customize Form document.

    Returns the new `form`, the `touched` (fieldname or None, property) pairs, and the
    fieldnames `added`.
    """
    if not isinstance(operations, list) or not (
        1 <= len(operations) <= _UPDATE_DOCTYPE_MAX_OPERATIONS
    ):
        raise ValueError(
            f"operations must be a list of 1 to {_UPDATE_DOCTYPE_MAX_OPERATIONS} operations"
        )
    form = copy.deepcopy(form)
    if field_properties is None:
        field_properties = _CUSTOMIZE_FIELD_PROPERTIES
    if doctype_properties is None:
        doctype_properties = _CUSTOMIZE_DOCTYPE_PROPERTIES
    rows: list[dict[str, Any]] = form["fields"]
    touched: list[tuple[str | None, str]] = []
    added: list[str] = []
    for index, operation in enumerate(operations):
        where = f"operations[{index}]"
        if not isinstance(operation, dict) or operation.get("op") not in _CUSTOMIZE_OPERATIONS:
            raise ValueError(
                f"{where} must be an object whose op is one of: {', '.join(_CUSTOMIZE_OPERATIONS)}"
            )
        op = operation["op"]
        required, optional = _CUSTOMIZE_OPERATIONS[op]
        missing = sorted(required - set(operation))
        extra = sorted(set(operation) - required - optional - {"op"})
        if missing or extra:
            raise ValueError(
                f"{where}: {op} takes {', '.join(sorted(required | optional))}"
                + (f"; missing {', '.join(missing)}" if missing else "")
                + (f"; unexpected {', '.join(extra)}" if extra else "")
            )
        if op == "set_property":
            prop, fieldname = operation["property"], operation.get("fieldname")
            if fieldname is None:
                if prop not in doctype_properties:
                    raise ValueError(
                        f"{where}: {prop!r} is not a DocType setting Customize Form sets here: "
                        + ", ".join(sorted(doctype_properties))
                    )
                form[prop] = _typed_value(where, prop, operation["value"], doctype_properties[prop])
            else:
                if prop not in field_properties:
                    hint = " (use change_fieldtype)" if prop == "fieldtype" else ""
                    raise ValueError(
                        f"{where}: {prop!r} is not a field property Customize Form sets here"
                        f"{hint}: " + ", ".join(sorted(field_properties))
                    )
                row = rows[_field_index(rows, fieldname, where)]
                row[prop] = _typed_value(where, prop, operation["value"], field_properties[prop])
                if prop == "options" and row.get("fieldtype") in _CODE_OPTIONS_FIELDTYPES:
                    raise ValueError(
                        f"{where}: options of {row.get('fieldtype')} fields are code; not set"
                    )
            touched.append((fieldname, prop))
        elif op == "move_field":
            if operation["insert_after"] == operation["fieldname"]:
                raise ValueError(f"{where}: a field cannot be inserted after itself")
            row = rows.pop(_field_index(rows, operation["fieldname"], where))
            rows.insert(_insert_position(rows, operation["insert_after"], where), row)
        elif op == "add_custom_field":
            values = _field_values(
                operation["field"],
                f"{where}.field",
                frozenset(field_properties) | {"fieldname", "fieldtype"},
            )
            fieldname = values.get("fieldname")
            if not isinstance(fieldname, str) or not _NEW_FIELDNAME.fullmatch(fieldname):
                raise ValueError(
                    f"{where}: fieldname must be lowercase letters, digits, and underscores, "
                    "starting with a letter, at most 64 characters"
                )
            if values.get("fieldtype") in _CODE_OPTIONS_FIELDTYPES and values.get("options"):
                raise ValueError(
                    f"{where}: options of {values.get('fieldtype')} fields are code; not set"
                )
            effective_fieldname = _frappe_customize_fieldname(values.get("label"))
            if fieldname != effective_fieldname:
                raise ValueError(
                    f"{where}: Frappe Customize Form derives fieldname "
                    f"{effective_fieldname!r} from label; use that value instead of {fieldname!r}"
                )
            if any(row.get("fieldname") == fieldname for row in rows):
                raise ValueError(f"{where}: {form['doc_type']} already has a field {fieldname}")
            for prop, value in values.items():
                if prop in field_properties:
                    kind = field_properties[prop]
                    values[prop] = _typed_value(where, prop, value, kind)
            row = {
                "doctype": "Customize Form Field",
                "is_custom_field": 1,
                "is_system_generated": 0,
                **values,
            }
            if row.get("fieldtype") in _CODE_OPTIONS_FIELDTYPES and row.get("options"):
                raise ValueError(f"{where}: options of {row['fieldtype']} fields are code; not set")
            _check_field_row(row, where, values, supported_fieldtypes)
            position = (
                _insert_position(rows, operation["insert_after"], where)
                if "insert_after" in operation
                else len(rows)
            )
            rows.insert(position, row)
            added.append(fieldname)
        else:
            row = rows[_field_index(rows, operation["fieldname"], where)]
            old, new = row.get("fieldtype"), operation["fieldtype"]
            if new not in supported_fieldtypes:
                raise ValueError(f"{where}: unsupported fieldtype {new!r}")
            if row.get("fieldname") not in added and not row.get("is_virtual"):
                if not any(old in group and new in group for group in _FIELDTYPE_CHANGE_GROUPS):
                    raise ValueError(
                        f"{where}: Customize Form does not change {old} to {new}; it allows "
                        "only changes within these groups: "
                        + "; ".join(", ".join(sorted(group)) for group in _FIELDTYPE_CHANGE_GROUPS)
                    )
            row["fieldtype"] = new
            touched.append((row["fieldname"], "fieldtype"))
    for fieldname in added:
        row = rows[_field_index(rows, fieldname, "operations")]
        effective_fieldname = _frappe_customize_fieldname(row.get("label"))
        if fieldname != effective_fieldname:
            raise ValueError(
                f"operations: Frappe Customize Form derives fieldname "
                f"{effective_fieldname!r} from the final label; use that value instead of "
                f"{fieldname!r}"
            )
    return {"form": form, "touched": touched, "added": added}


def _setter_key(row: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    return (row.get("field_name") or None, row.get("property"), row.get("row_name") or None)


def _setter_change(
    action: str, fieldname: str | None, prop: str, before: Any, after: Any
) -> dict[str, Any]:
    change: dict[str, Any] = {"action": action, "property": prop}
    if fieldname:
        change["fieldname"] = fieldname
    if action != "create":
        change["from"] = before
    if action != "remove":
        change["to"] = after
    return change


def _review_field_property(
    state: dict[str, Any], row: dict[str, Any], prop: str, value: Any, where: str
) -> None:
    """Refuse what Customize Form's `allow_property_change` would skip for a standard field."""
    base = state["base"].get(row["fieldname"]) or {}
    fieldtype = row.get("fieldtype")
    if prop == "allow_on_submit" and value and not base.get("allow_on_submit"):
        raise ValueError(f"{where}: Frappe does not enable Allow on Submit for standard fields")
    if prop == "reqd" and not value and base.get("reqd") == 1:
        raise ValueError(f"{where}: Frappe does not make a mandatory standard field optional")
    if prop == "read_only" and not value and base.get("read_only") == 1:
        raise ValueError(f"{where}: Frappe does not make a read-only standard field editable")
    if prop == "in_list_view" and value and fieldtype in _NO_VALUE_FIELDTYPES:
        raise ValueError(f"{where}: {fieldtype} fields cannot be shown in the list view")
    if prop == "options" and fieldtype not in _CUSTOMIZE_OPTIONS_FIELDTYPES:
        raise ValueError(
            f"{where}: Customize Form changes options only on Data and Read Only standard "
            f"fields, not {fieldtype}; set_field_property writes a setter without that check"
        )
    if prop == "translatable" and value and fieldtype not in _TRANSLATABLE_FIELDTYPES:
        raise ValueError(f"{where}: {fieldtype} fields cannot be translatable")


def _customization_changes(
    state: dict[str, Any], plan: dict[str, Any]
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Work out the Property Setters and Custom Fields a save of `plan` writes, as Frappe does."""
    doctype = state["form"]["doc_type"]
    old_form, new_form = state["form"], plan["form"]
    old_rows = {row.get("fieldname"): row for row in old_form["fields"]}
    new_rows: list[dict[str, Any]] = new_form["fields"]
    setters = state["setters"]
    property_setters: list[dict[str, Any]] = []
    custom_updates: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    countable = not state["record"].get("issingle") and not state["record"].get("is_virtual")

    seen: set[tuple[str | None, str]] = set()
    for fieldname, prop in plan["touched"]:
        if (fieldname, prop) in seen or fieldname in plan["added"]:
            continue
        seen.add((fieldname, prop))
        if fieldname is None:
            # Customize Form's `make_property_setter` deletes every setter of this property.
            value, current = new_form.get(prop), old_form.get(prop)
            if value == current:
                continue
            base = state["record"].get(prop)
            existing = [row for row in setters if row.get("property") == prop]
            own = next(
                (row for row in existing if not row.get("field_name") and not row.get("row_name")),
                None,
            )
        else:
            row = next(row for row in new_rows if row.get("fieldname") == fieldname)
            value, current = row.get(prop), old_rows[fieldname].get(prop)
            if (value or "") == (current or ""):
                continue
            if _is_custom_row(row):
                custom_updates.setdefault(fieldname, {})[prop] = {"from": current, "to": value}
                continue
            _review_field_property(state, row, prop, value, f"{fieldname}.{prop}")
            base = (state["base"].get(fieldname) or {}).get(prop)
            existing = [
                row
                for row in setters
                if row.get("field_name") == fieldname and row.get("property") == prop
            ]
            own = next((row for row in existing if not row.get("row_name")), None)
        for row in existing:
            if row is not own:
                property_setters.append(
                    _setter_change("remove", row.get("field_name"), prop, row.get("value"), None)
                )
        if base == value:
            if own:
                property_setters.append(
                    _setter_change("remove", fieldname, prop, own.get("value"), None)
                )
        else:
            action = "update" if own else "create"
            before = own.get("value") if own else None
            property_setters.append(_setter_change(action, fieldname, prop, before, value))
        if fieldname is not None:
            warnings += _customization_warnings(doctype, fieldname, prop, current, value, countable)

    order = [row.get("fieldname") for row in new_rows]
    custom_fields: list[dict[str, Any]] = []
    for position, row in enumerate(new_rows):
        fieldname = row.get("fieldname")
        previous = order[position - 1] if position else None
        if fieldname in plan["added"]:
            values = {
                key: row[key]
                for key in ("fieldtype", *_CUSTOMIZE_FIELD_PROPERTIES)
                if row.get(key) not in (None, "")
            }
            custom_fields.append(
                {"action": "create", "fieldname": fieldname, "insert_after": previous, **values}
            )
        elif _is_custom_row(row) and previous:
            stored = (state["custom"].get(fieldname) or {}).get("insert_after")
            if stored != previous:
                custom_updates.setdefault(fieldname, {})["insert_after"] = {
                    "from": stored,
                    "to": previous,
                }
    custom_fields += [
        {"action": "update", "fieldname": fieldname, "set": change}
        for fieldname, change in custom_updates.items()
    ]

    # Customize Form's `set_property_setter_for_field_order`: the standard order needs no setter.
    default_order = [row.get("fieldname") for row in state["record"].get("fields") or []]
    stored_order = next(
        (
            row
            for row in setters
            if row.get("property") == "field_order" and not row.get("field_name")
        ),
        None,
    )
    if order == default_order:
        if stored_order:
            property_setters.append(
                _setter_change("remove", None, "field_order", stored_order.get("value"), None)
            )
    elif not stored_order or _json_list(stored_order.get("value")) != order:
        before = stored_order.get("value") if stored_order else None
        property_setters.append(
            _setter_change(
                "update" if stored_order else "create",
                None,
                "field_order",
                before,
                json.dumps(order),
            )
        )
    return {"property_setters": property_setters, "custom_fields": custom_fields}, warnings


def _customization_warnings(
    doctype: str, fieldname: str, prop: str, current: Any, value: Any, countable: bool
) -> list[str]:
    if prop == "fieldtype":
        return [
            f"{fieldname} changes from {current} to {value}; Frappe may change the column type "
            "and refuses the save if stored values are too long for the new type."
        ]
    if prop == "length" and value and (current or 0) > value:
        return [f"{fieldname} gets shorter; Frappe refuses the save if stored values are longer."]
    if prop == "reqd" and value:
        empty = _count_records(doctype, [[fieldname, "is", "not set"]]) if countable else None
        if empty == 0:
            return []
        held = "an unknown number of" if empty is None else str(empty)
        return [
            f"{fieldname} becomes mandatory and {held} records leave it empty; saving them "
            "fails until it is filled."
        ]
    if prop == "unique" and value:
        return [
            f"{fieldname} becomes unique: Frappe adds a unique index, and the save fails if "
            "existing values repeat."
        ]
    return []


def _json_list(value: Any) -> Any:
    try:
        return json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _customization_preview(doctype: str, operations: Any) -> dict[str, Any]:
    state = _customization_state(doctype)
    form = state["form"]
    form_field_names = frozenset(form)
    # fetch_to_customize omits unset DocType settings from its dict; the connected Customize
    # Form schema is the authoritative allowlist, while the returned form remains our save data.
    form_doctype_properties = _schema_fieldnames("Customize Form")
    # Customize Form renders DocField properties from its own child schema. Current form rows
    # omit unset properties, so using their keys would incorrectly reject valid Desk controls.
    form_field_properties = _schema_fieldnames("Customize Form Field")
    allowed_field_properties = {
        key: value
        for key, value in _CUSTOMIZE_FIELD_PROPERTIES.items()
        if key in form_field_properties
    }
    allowed_doctype_properties = {
        key: value
        for key, value in _CUSTOMIZE_DOCTYPE_PROPERTIES.items()
        if key in form_doctype_properties
    }
    if not form_field_properties or not form_doctype_properties or not form_field_names:
        raise RuntimeError("Frappe did not expose Customize Form schema; refusing to guess")
    plan = _plan_customization(
        form,
        operations,
        field_properties=allowed_field_properties,
        doctype_properties=allowed_doctype_properties,
        supported_fieldtypes=_site_fieldtypes(),
    )
    changes, warnings = _customization_changes(state, plan)
    moved = [row.get("fieldname") for row in plan["form"]["fields"]] != [
        row.get("fieldname") for row in state["form"]["fields"]
    ]
    if (
        not moved
        and not changes["custom_fields"]
        and not any(change["property"] != "field_order" for change in changes["property_setters"])
    ):
        raise ValueError("The operations leave the customization unchanged")
    binding = {
        "doctype": doctype,
        "operations": operations,
        "changes": changes,
        "doctype_modified": str(state["record"].get("modified")),
        "setters": sorted(
            (str(row.get("name")), str(row.get("modified")), str(row.get("value")))
            for row in state["setters"]
        ),
        "custom_fields": sorted(
            (str(row.get("name")), str(row.get("modified")), str(row.get("insert_after")))
            for row in state["custom"].values()
        ),
    }
    preview_id = hashlib.sha256(
        json.dumps(binding, sort_keys=True, default=str).encode()
    ).hexdigest()
    return {
        "state": state,
        "plan": plan,
        "changes": changes,
        "warnings": warnings,
        "preview_id": preview_id,
    }


def _applied_customization(
    state: dict[str, Any], changes: dict[str, list[dict[str, Any]]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read the Property Setters after a save and list what changed; also what didn't apply."""
    doctype = state["form"]["doc_type"]
    after = _listed(
        "Property Setter",
        ["name", "doctype_or_field", "field_name", "row_name", "property", "value", "modified"],
        {"doc_type": doctype},
    )
    old = {_setter_key(row): row.get("value") for row in state["setters"]}
    new = {_setter_key(row): row.get("value") for row in after}
    applied = []
    for key in sorted(set(old) | set(new), key=lambda item: tuple(str(part) for part in item)):
        fieldname, prop, _ = key
        if key not in new:
            applied.append(_setter_change("remove", fieldname, prop, old[key], None))
        elif key not in old:
            applied.append(_setter_change("create", fieldname, prop, None, new[key]))
        elif old[key] != new[key]:
            applied.append(_setter_change("update", fieldname, prop, old[key], new[key]))
    done = {(change.get("fieldname"), change["property"]) for change in applied}
    missing = [
        f"Property Setter {change.get('fieldname') or doctype}.{change['property']}"
        for change in changes["property_setters"]
        if (change.get("fieldname"), change["property"]) not in done
    ]
    fields = {
        row.get("fieldname"): row
        for row in _listed("Custom Field", ["name", "fieldname"], {"dt": doctype})
    }
    for change in changes["custom_fields"]:
        fieldname = change["fieldname"]
        if fieldname not in fields:
            missing.append(f"Custom Field {fieldname}")
        elif change["action"] == "update":
            saved = _request("GET", _resource("Custom Field", fields[fieldname]["name"]))
            missing += [
                f"Custom Field {fieldname}.{prop}"
                for prop, delta in change["set"].items()
                if not _same(saved.get(prop), delta["to"])
            ]
    return applied, missing


@_tool("customize", "Customize DocType", destructive=True)
def customize_doctype(
    doctype: str,
    operations: list[dict[str, Any]],
    expected_preview: ExpectedPreview = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or save ordered `operations` on a standard DocType in one Customize Form save.

    Frappe checks each value against the standard one, removes redundant Property Setters, and
    keeps the field order, as Desk does:
    - {"op": "set_property", "property": ..., "value": ..., "fieldname": ...} (no fieldname:
      DocType setting)
    - {"op": "move_field", "fieldname": ..., "insert_after": fieldname or null}
    - {"op": "add_custom_field", "field": {fieldname, fieldtype, label, ...}, "insert_after": ...}
    - {"op": "change_fieldtype", "fieldname": ..., "fieldtype": ...} (related types only)
    The preview lists `property_setters` and `custom_fields` to create, update, or remove, with
    `warnings`; confirm with its `preview_id`. System Manager only.
    """
    preview = _customization_preview(doctype, operations)
    if not confirm:
        return {
            "preview": True,
            "action": "customize_doctype",
            "doctype": doctype,
            **preview["changes"],
            "warnings": preview["warnings"],
            "preview_id": preview["preview_id"],
            "requires_confirmation": True,
        }
    if expected_preview != preview["preview_id"]:
        raise ValueError("The customization changed since the preview; request a fresh preview")
    try:
        payload = _request(
            "POST",
            _RUN_DOC_METHOD,
            json={
                "method": "save_customization",
                "docs": json.dumps(preview["plan"]["form"], default=str),
            },
        )
    finally:
        _forget_meta(doctype)
    raw = payload.get("_server_messages") if isinstance(payload, dict) else None
    messages = _server_messages(raw) if raw else []
    applied, missing = _applied_customization(preview["state"], preview["changes"])
    warnings = list(preview["warnings"])
    if missing:
        warnings.append(
            "Frappe did not apply every previewed change (see frappe_messages): "
            + ", ".join(missing)
        )
    return {
        "saved": True,
        "doctype": doctype,
        "property_setters": applied,
        "custom_fields": preview["changes"]["custom_fields"],
        "warnings": warnings,
        "frappe_messages": messages,
    }


@_tool("customize", "Export customizations to app")
def export_customizations(
    app: str,
    module: str,
    doctypes: list[str],
    with_permissions: bool = False,
    apply_module_filter: bool = False,
    sync_on_migrate: bool = True,
    with_workflows: bool = False,
    confirm: Confirm = False,
    expected_preview: ExpectedPreview = None,
) -> dict[str, Any]:
    """Preview or export site customizations into an app's files, so `bench migrate` applies them.

    Writes `<module>/custom/<doctype>.json` for 1-50 `doctypes` and their child tables, as Customize
    Form's export does; `with_permissions` adds Custom DocPerms, which replace the site's on
    migrate. `apply_module_filter` keeps only customizations of `module`. Client Scripts, and
    Workflows with `with_workflows`, become fixtures listed in a managed hooks.py block; migrate
    overwrites them.
    """
    _assert_writable_app(app)
    app_path, module_path = _assert_app_module(app, module)
    if not doctypes or len(doctypes) > _EXPORT_MAX_DOCTYPES:
        raise ValueError(f"Select between 1 and {_EXPORT_MAX_DOCTYPES} DocTypes")
    if any(not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) for name in doctypes):
        raise ValueError("Invalid DocType name")
    covered = _customized_doctypes(doctypes)
    payloads: list[tuple[Path, str]] = []
    empty: list[str] = []
    for doctype in covered:
        custom = _native_customization(
            doctype,
            module,
            with_permissions=with_permissions,
            apply_module_filter=apply_module_filter,
            sync_on_migrate=sync_on_migrate,
        )
        if custom is None:
            empty.append(doctype)
            continue
        payloads.append((module_path / "custom" / f"{_scrub(doctype)}.json", _frappe_json(custom)))
    fixture_entries: dict[str, dict[str, list[str]]] = {}
    client_scripts = _client_script_fixture(app_path, covered, module)
    if client_scripts:
        payloads.append(client_scripts)
        fixture_entries["Client Script"] = {"dt": covered, "module": [module]}
    workflows: list[str] = []
    if with_workflows:
        workflows, workflow_files, workflow_entries = _workflow_fixtures(app_path, doctypes)
        payloads.extend(workflow_files)
        fixture_entries.update(workflow_entries)
    if fixture_entries:
        payloads.append(_fixture_plan(app_path, fixture_entries))
    if len(payloads) > _EXPORT_MAX_FILES:
        raise ValueError("Export produced too many files")
    previews = []
    total_size = 0
    for path, content in payloads:
        path = _safe_app_file(app_path, path)
        total_size += len(content.encode("utf-8"))
        if total_size > 5_000_000:
            raise ValueError("Export exceeds the 5 MB total output limit")
        previews.append(_file_preview(path, content))
    summary: dict[str, Any] = {"app": app, "module": module, "doctypes": covered, "empty": empty}
    if with_workflows:
        summary["workflows"] = workflows
    if not payloads:
        if confirm:
            raise ValueError("Nothing to export: these DocTypes have no customizations")
        return {
            "preview": True,
            **summary,
            "files": [],
            "nothing_to_export": True,
            "preview_id": None,
            "requires_confirmation": False,
        }
    preview_id = hashlib.sha256(json.dumps(previews, sort_keys=True).encode()).hexdigest()
    if not confirm:
        return {
            "preview": True,
            **summary,
            "files": previews,
            "preview_id": preview_id,
            "requires_confirmation": True,
        }
    if expected_preview != preview_id:
        raise ValueError("Export preview is missing or stale")
    rollback_id, _ = _write_app_files(app_path, payloads, preview_id)
    return {
        "exported": True,
        **summary,
        "files": [str(path) for path, _ in payloads],
        "rollback_id": rollback_id,
    }


@_tool("customize", "Roll back export", destructive=True)
def rollback_export(
    app: str,
    module: str,
    rollback_id: str,
    expected_preview: ExpectedPreview = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or undo the export with `rollback_id` (from the export's result).

    Restores overwritten files from backups and deletes created files and empty directories. Refuses
    with `conflict` when an exported file changed since.
    """
    app_path, _ = _assert_app_module(app, module)
    if not re.fullmatch(r"export-[a-f0-9]{32}\.json", rollback_id):
        raise ValueError("Invalid rollback ID")
    backup_root = _safe_app_file(app_path, app_path / ".mcp-frappe-backups")
    manifest_path = _safe_app_file(app_path, backup_root / rollback_id)
    if not manifest_path.is_file():
        raise ValueError("Export rollback manifest was not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or len(manifest) > _EXPORT_MAX_FILES:
        raise ValueError("Invalid export rollback manifest")

    changes = []
    conflicts = []
    for item in manifest:
        target = _safe_app_file(app_path, app_path / item["path"])
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        current_hash = (
            hashlib.sha256(current.encode("utf-8")).hexdigest() if current is not None else None
        )
        if current_hash != item.get("exported_sha256"):
            conflicts.append(str(target))
        backup_name = item.get("backup")
        if backup_name is None:
            restored = ""
        else:
            if Path(backup_name).name != backup_name:
                raise ValueError("Invalid backup path in rollback manifest")
            backup = _safe_app_file(app_path, backup_root / backup_name)
            if not backup.is_file():
                raise ValueError("Export backup is missing")
            restored = backup.read_text(encoding="utf-8")
        changes.append(_file_preview(target, restored))
    if conflicts:
        return {
            "conflict": True,
            "message": "Exported files changed after export; review and restore them manually.",
            "files": conflicts,
            "requires_confirmation": False,
        }
    preview_id = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
    if not confirm:
        return {
            "preview": True,
            "app": app,
            "rollback_id": rollback_id,
            "changes": changes,
            "preview_id": preview_id,
            "requires_confirmation": True,
        }
    if expected_preview != preview_id:
        raise ValueError("Rollback preview is missing or stale")
    for item in manifest:
        target = _safe_app_file(app_path, app_path / item["path"])
        backup_name = item.get("backup")
        if backup_name is None:
            target.unlink(missing_ok=True)
        else:
            backup = _safe_app_file(app_path, backup_root / backup_name)
            _atomic_write(target, backup.read_text(encoding="utf-8"))
            backup.unlink()
    created_dirs = {
        _safe_app_file(app_path, app_path / directory)
        for item in manifest
        for directory in item.get("created_dirs") or []
    }
    for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    manifest_path.unlink()
    if backup_root.is_dir() and not any(backup_root.iterdir()):
        backup_root.rmdir()
    return {"rolled_back": True, "app": app, "rollback_id": rollback_id, "files": len(manifest)}


# Configuration records: Workflows and Notifications.

_CONFIG_LIST_LIMIT = 200
_WORKFLOW_MAX_STATES = 50
_WORKFLOW_MAX_TRANSITIONS = 100
_WORKFLOW_STATE_KEYS = ("state", "doc_status", "allow_edit", "update_field", "update_value")
_WORKFLOW_TRANSITION_KEYS = ("state", "action", "next_state", "allowed", "allow_self_approval")
_WORKFLOW_PROPERTIES = ("is_active", "send_email_alert", "workflow_state_field")
_WORKFLOW_STATE_FIELDTYPES = frozenset({"Link", "Data", "Select"})
_NOTIFICATION_EVENTS = (
    "New",
    "Save",
    "Submit",
    "Cancel",
    "Value Change",
    "Days Before",
    "Days After",
)
_NOTIFICATION_DAY_EVENTS = frozenset({"Days Before", "Days After"})
_NOTIFICATION_CHANNELS = ("Email", "System Notification")
_NOTIFICATION_RECIPIENT_KEYS = ("receiver_by_document_field", "receiver_by_role", "cc", "bcc")
_NOTIFICATION_MAX_RECIPIENTS = 20
# Frappe refuses Notifications on these (FORBIDDEN_DOCUMENT_TYPES in notification.py).
_NOTIFICATION_FORBIDDEN_DOCTYPES = frozenset({"Email Queue"})
_NOTIFICATION_SUBJECT_MAX = 140
_TEMPLATE_MAX_LENGTH = 20_000
_TEMPLATE_RULE = "use plain text with {{ doc.<fieldname> }} placeholders"
# Jinja filters a placeholder may use, with the type of each optional literal argument in
# order. The plain ones are Jinja built-ins; int, str, and flt are Frappe's cint, cstr, flt.
_TEMPLATE_FILTERS: dict[str, tuple[type, ...]] = {
    "upper": (),
    "lower": (),
    "title": (),
    "capitalize": (),
    "trim": (),
    "striptags": (),
    "escape": (),
    "e": (),
    "int": (),
    "str": (),
    "flt": (int,),
    "round": (int, str),
    "truncate": (int,),
    "default": (str,),
}
_ROUND_METHODS = frozenset({"common", "ceil", "floor"})
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.S)
_PLACEHOLDER_BODY = re.compile(r"\s*doc\.([A-Za-z_][A-Za-z0-9_]*)\s*((?:\|[^|]*)*)")
_TEMPLATE_FILTER = re.compile(r"\s*([a-z_]+)\s*(?:\(([^()]*)\))?\s*")
_EMAIL_ADDRESS = re.compile(r"[^@\s,;{}<>()\"'%]+@[^@\s,;{}<>()\"'%]+\.[A-Za-z0-9-]+")


def _template_fields(schema: dict[str, Any]) -> set[str]:
    """Fields a placeholder may print: value fields (not layout or tables) and standard columns."""
    return {
        row["fieldname"]
        for row in schema.get("fields", [])
        if row.get("fieldname")
        and row.get("fieldtype") not in _LAYOUT_FIELDTYPES
        and row.get("fieldtype") not in _TABLE_FIELDTYPES
    } | _STANDARD_COLUMNS


def _assert_template_filter(part: str, where: str) -> None:
    match = _TEMPLATE_FILTER.fullmatch(part)
    if not match or match.group(1) not in _TEMPLATE_FILTERS:
        raise ValueError(
            f"{where}: filter {part.strip()!r} is not allowed; allowed filters: "
            f"{', '.join(_TEMPLATE_FILTERS)}"
        )
    name, arguments = match.groups()
    try:
        values = ast.literal_eval(f"({arguments},)") if arguments and arguments.strip() else ()
    except (ValueError, SyntaxError) as error:
        raise ValueError(f"{where}: filter {name} takes only number or string literals") from error
    kinds = _TEMPLATE_FILTERS[name]
    if (
        len(values) > len(kinds)
        or any(type(value) is not kind for value, kind in zip(values, kinds, strict=False))
        or any(isinstance(value, str) and set(value) & set("{}%#") for value in values)
        or (name == "round" and len(values) == 2 and values[1] not in _ROUND_METHODS)
    ):
        expected = ", ".join(kind.__name__ for kind in kinds) or "no arguments"
        raise ValueError(f"{where}: filter {name} takes {expected}")


def _assert_template(text: Any, where: str, fields: set[str]) -> None:
    """Allow plain text with `{{ doc.<field> }}` placeholders and allowlisted filters only.

    Frappe renders Notification subjects and messages as Jinja on the server. Anything beyond
    printing a field of the document (statements, calls, other variables, attribute chains)
    would let the model run template code there, so it is refused.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{where} must be non-empty text")
    if len(text) > _TEMPLATE_MAX_LENGTH:
        raise ValueError(f"{where} is longer than {_TEMPLATE_MAX_LENGTH} characters")
    for marker, what in (("{%", "statements"), ("{#", "comments")):
        if marker in text:
            raise ValueError(f"{where}: Jinja {what} ({marker}) are not allowed; {_TEMPLATE_RULE}")
    for match in _PLACEHOLDER.finditer(text):
        body = _PLACEHOLDER_BODY.fullmatch(match.group(1))
        if not body:
            raise ValueError(
                f"{where}: unsupported placeholder {match.group(0)!r}; {_TEMPLATE_RULE}"
            )
        fieldname, chain = body.groups()
        if fieldname not in fields:
            raise ValueError(f"{where}: unknown field in placeholder: {fieldname}")
        for part in chain.split("|")[1:]:
            _assert_template_filter(part, where)
    if "{{" in _PLACEHOLDER.sub("", text):
        raise ValueError(f"{where}: unclosed {{{{ placeholder; {_TEMPLATE_RULE}")


def _email_list(value: Any, where: str) -> str:
    """Accept plain addresses separated by commas or newlines; Frappe renders `{` as Jinja."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must list email addresses")
    addresses = [part.strip() for part in re.split(r"[,\n]", value) if part.strip()]
    if len(addresses) > _NOTIFICATION_MAX_RECIPIENTS:
        raise ValueError(f"{where} lists more than {_NOTIFICATION_MAX_RECIPIENTS} addresses")
    for address in addresses:
        if not _EMAIL_ADDRESS.fullmatch(address):
            raise ValueError(f"{where}: not a plain email address: {address!r}")
    return "\n".join(addresses)


def _missing_records(doctype: str, names: Any) -> list[str]:
    """Return which of `names` have no `doctype` record, in one list read."""
    wanted = sorted(set(names))
    if not wanted:
        return []
    rows = _request(
        "GET",
        _resource(doctype),
        params={
            "fields": '["name"]',
            "filters": _filters([["name", "in", wanted]]),
            "limit_page_length": len(wanted),
        },
    )
    found = {row.get("name") for row in rows}
    return [name for name in wanted if name not in found]


def _assert_roles(roles: Any) -> None:
    missing = _missing_records("Role", roles)
    if missing:
        raise ValueError(f"Unknown roles on the target site: {', '.join(missing)}")


def _config_record(doctype: str, name: str) -> dict[str, Any] | None:
    """Read a configuration record with its child tables; None when it does not exist."""
    rows = _request(
        "GET",
        _resource(doctype),
        params={
            "fields": '["name"]',
            "filters": _filters({"name": name}),
            "limit_page_length": 1,
        },
    )
    return _request("GET", _resource(doctype, name)) if rows else None


def _record_holds(doc: dict[str, Any], values: dict[str, Any]) -> bool:
    """Tell whether a re-read record holds `values`, child rows compared in order."""
    for key, value in values.items():
        if isinstance(value, list):
            rows = doc.get(key) or []
            if len(rows) != len(value) or any(
                not isinstance(row, dict) or not _record_holds(row, wanted)
                for row, wanted in zip(rows, value, strict=False)
            ):
                return False
        elif key != "doctype" and not _same(doc.get(key), value):
            return False
    return True


def _save_config_record(
    doctype: str,
    values: dict[str, Any],
    name: str,
    expected_modified: ExpectedModified = None,
    *,
    forget: tuple[str, ...] = (),
) -> tuple[dict[str, Any], bool]:
    """Create (no `expected_modified`) or update one configuration record and its child tables.

    Updates send `modified` in the body, so Frappe refuses a record changed since the preview.
    A developer-mode save of a standard record (a Notification, Report, or Workspace) writes
    files and can make the Bench reload mid-request. Then the record is
    re-read, and the save counts only if it holds `values` and, on update, `modified` moved;
    nothing is sent twice. Returns the saved record and whether it was reconciled. The
    metadata cache is cleared for `doctype` and every DocType in `forget`.
    """
    try:
        if expected_modified is None:
            saved = _request(
                "POST", _resource(doctype), json={"data": {"doctype": doctype, **values}}
            )
        else:
            saved = _request(
                "PUT",
                _resource(doctype, name),
                json={"data": {**values, "modified": expected_modified}},
            )
        return saved, False
    except FrappeError as error:
        if error.exc_type == "TimestampMismatchError":
            raise ValueError(_STALE_DOCUMENT) from error
        raise
    except httpx.TransportError as error:
        _wait_for_server_ready()
        current = _config_record(doctype, name)
        if (
            current is None
            or not _record_holds(current, values)
            or (expected_modified is not None and str(current.get("modified")) == expected_modified)
        ):
            raise RuntimeError(
                f"Frappe disconnected while saving {doctype} {name} and the saved record does "
                "not hold the change; request a fresh preview before retrying."
            ) from error
        return current, True
    finally:
        for target in (doctype, *forget):
            _forget_meta(target)


def _workflow_view(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": doc.get("name"),
        "document_type": doc.get("document_type"),
        "is_active": bool(doc.get("is_active")),
        "send_email_alert": bool(doc.get("send_email_alert")),
        "workflow_state_field": doc.get("workflow_state_field"),
        "modified": doc.get("modified"),
        "states": [
            {key: row.get(key) for key in _WORKFLOW_STATE_KEYS if not _blank(row.get(key))}
            for row in doc.get("states") or []
        ],
        "transitions": [
            {
                **{key: row.get(key) for key in _WORKFLOW_TRANSITION_KEYS[:4]},
                "allow_self_approval": bool(row.get("allow_self_approval")),
                **({"has_condition": True} if row.get("condition") else {}),
            }
            for row in doc.get("transitions") or []
        ],
    }


@_tool("read", "Get workflow")
def get_workflow(doctype: str) -> dict[str, Any]:
    """Return a DocType's active Workflow (states, transitions, state field), or `workflow: null`.

    `inactive` names its other Workflows; `has_condition` marks a transition with a Python condition
    set elsewhere.
    """
    _meta_bundle(doctype)
    rows = _request(
        "GET",
        _resource("Workflow"),
        params={
            "fields": '["name","is_active"]',
            "filters": _filters({"document_type": doctype}),
            "order_by": "modified desc",
            "limit_page_length": 50,
        },
    )
    active = next((row["name"] for row in rows if row.get("is_active")), None)
    return {
        "doctype": doctype,
        "workflow": _workflow_view(_request("GET", _resource("Workflow", active)))
        if active
        else None,
        "inactive": [row["name"] for row in rows if row["name"] != active],
    }


def _workflow_states(states: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(states, list) or not 1 <= len(states) <= _WORKFLOW_MAX_STATES:
        raise ValueError(f"states must list 1 to {_WORKFLOW_MAX_STATES} states")
    doctype = schema.get("name")
    value_fields = _template_fields(schema) - _STANDARD_COLUMNS
    rows: list[dict[str, Any]] = []
    for index, state in enumerate(states):
        where = f"states[{index}]"
        if not isinstance(state, dict):
            raise ValueError(f"{where} must be an object")
        if "evaluate_as_expression" in state:
            raise ValueError(
                f"{where}: evaluate_as_expression makes Frappe evaluate update_value as code; "
                "it is not supported"
            )
        unknown = sorted(set(state) - set(_WORKFLOW_STATE_KEYS))
        if unknown:
            raise ValueError(f"{where}: unsupported keys: {', '.join(unknown)}")
        name = state.get("state")
        if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
            raise ValueError(f"{where}: state must be a name such as 'Pending Approval'")
        if any(row["state"] == name for row in rows):
            raise ValueError(f"{where}: state {name!r} is listed twice")
        doc_status = state.get("doc_status", 0)
        if isinstance(doc_status, bool) or str(doc_status) not in ("0", "1", "2"):
            raise ValueError(f"{where}: doc_status must be 0 (draft), 1 (submitted), or 2")
        doc_status = str(doc_status)
        if doc_status != "0" and not schema.get("is_submittable"):
            raise ValueError(
                f"{where}: doc_status {doc_status} needs a submittable DocType; "
                f"{doctype} is not submittable"
            )
        role = state.get("allow_edit")
        if not isinstance(role, str) or not _SAFE_NAME.fullmatch(role):
            raise ValueError(f"{where}: allow_edit must name a Role")
        update_field, update_value = state.get("update_field"), state.get("update_value")
        if update_field is not None and (
            not isinstance(update_field, str) or update_field not in value_fields
        ):
            raise ValueError(f"{where}: update_field {update_field!r} is not a field of {doctype}")
        if update_value is not None:
            if update_field is None:
                raise ValueError(f"{where}: update_value needs update_field")
            if isinstance(update_value, bool) or not isinstance(update_value, str | int | float):
                raise ValueError(f"{where}: update_value must be a string or number")
            update_value = str(update_value)
        rows.append(
            {
                "state": name,
                "doc_status": doc_status,
                "allow_edit": role,
                "update_field": update_field,
                "update_value": update_value,
            }
        )
    if rows[0]["doc_status"] != "0":
        raise ValueError("The first state is where new documents start; give it doc_status 0")
    return rows


def _workflow_transitions(transitions: Any, states: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(transitions, list) or not 1 <= len(transitions) <= _WORKFLOW_MAX_TRANSITIONS:
        raise ValueError(f"transitions must list 1 to {_WORKFLOW_MAX_TRANSITIONS} transitions")
    doc_status = {row["state"]: row["doc_status"] for row in states}
    rows: list[dict[str, Any]] = []
    for index, transition in enumerate(transitions):
        where = f"transitions[{index}]"
        if not isinstance(transition, dict):
            raise ValueError(f"{where} must be an object")
        if "condition" in transition:
            raise ValueError(
                f"{where}: condition is a Python expression Frappe evaluates; transition "
                "conditions are not supported. Use roles and states to control who acts"
            )
        unknown = sorted(set(transition) - set(_WORKFLOW_TRANSITION_KEYS))
        if unknown:
            raise ValueError(f"{where}: unsupported keys: {', '.join(unknown)}")
        for key in ("state", "next_state"):
            if transition.get(key) not in doc_status:
                raise ValueError(
                    f"{where}: {key} {transition.get(key)!r} is not one of the listed states"
                )
        for key, what in (("action", "an action name such as 'Approve'"), ("allowed", "a Role")):
            value = transition.get(key)
            if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
                raise ValueError(f"{where}: {key} must be {what}")
        self_approval = transition.get("allow_self_approval", True)
        if not isinstance(self_approval, bool | int) or self_approval not in (0, 1):
            raise ValueError(f"{where}: allow_self_approval must be true or false")
        source = doc_status[transition["state"]]
        target = doc_status[transition["next_state"]]
        # The same rules as Workflow.validate_docstatus, checked before anything is saved.
        if source == "2":
            raise ValueError(f"{where}: a cancelled state (doc_status 2) cannot change")
        if source == "1" and target == "0":
            raise ValueError(f"{where}: a submitted document cannot go back to draft")
        if source == "0" and target == "2":
            raise ValueError(f"{where}: a document cannot be cancelled before it is submitted")
        row = {
            "state": transition["state"],
            "action": transition["action"],
            "next_state": transition["next_state"],
            "allowed": transition["allowed"],
            "allow_self_approval": int(self_approval),
        }
        if any(_transition_key(other) == _transition_key(row) for other in rows):
            raise ValueError(f"{where}: the same state, action, and role are listed twice")
        rows.append(row)
    return rows


def _transition_key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    return row.get("state"), row.get("action"), row.get("allowed")


def _workflow_paths(
    states: list[dict[str, Any]], transitions: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Return the states unreachable from the first one, and draft states with no way out."""
    exits: dict[str, set[str]] = {}
    for row in transitions:
        if row["next_state"] != row["state"]:
            exits.setdefault(row["state"], set()).add(row["next_state"])
    reached, pending = {states[0]["state"]}, [states[0]["state"]]
    while pending:
        for state in exits.get(pending.pop(), ()):
            if state not in reached:
                reached.add(state)
                pending.append(state)
    unreachable = [row["state"] for row in states if row["state"] not in reached]
    dead_ends = [
        row["state"] for row in states if row["doc_status"] == "0" and not exits.get(row["state"])
    ]
    return unreachable, dead_ends


def _merged_rows(
    current: list[dict[str, Any]], rows: list[dict[str, Any]], key: Callable[[dict], Any]
) -> list[dict[str, Any]]:
    """Give each new row the existing row it replaces, so Frappe updates it in place.

    A child row sent without its other columns would be saved with their defaults, so columns
    this server does not manage (a state's message, a transition's condition set in Desk)
    are carried over.
    """
    existing = {key(row): row for row in current}
    merged = []
    for row in rows:
        old = existing.get(key(row))
        if old is None:
            merged.append(row)
            continue
        kept = {
            name: value
            for name, value in old.items()
            if name == "name" or (name not in _CHILD_SYSTEM_KEYS and not name.startswith("_"))
        }
        merged.append({**kept, **row})
    return merged


def _workflow_diff(current: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    old_states = {row.get("state"): row for row in current.get("states") or []}
    new_states = {row["state"]: row for row in values["states"]}
    changed_states = {}
    for name, row in new_states.items():
        if name in old_states:
            delta = {
                key: {"from": old_states[name].get(key), "to": row.get(key)}
                for key in _WORKFLOW_STATE_KEYS[1:]
                if not _same(old_states[name].get(key), row.get(key))
            }
            if delta:
                changed_states[name] = delta
    old_transitions = {_transition_key(row): row for row in current.get("transitions") or []}
    new_transitions = {_transition_key(row): row for row in values["transitions"]}
    changed_transitions = []
    for key, row in new_transitions.items():
        if key in old_transitions:
            delta = {
                name: {"from": old_transitions[key].get(name), "to": row.get(name)}
                for name in ("next_state", "allow_self_approval")
                if not _same(old_transitions[key].get(name), row.get(name))
            }
            if delta:
                state, action, allowed = key
                changed_transitions.append(
                    {"state": state, "action": action, "allowed": allowed, "changes": delta}
                )
    return {
        "properties": {
            key: {"from": current.get(key), "to": values[key]}
            for key in _WORKFLOW_PROPERTIES
            if not _same(current.get(key), values[key])
        },
        "states": {
            "added": [name for name in new_states if name not in old_states],
            "removed": [name for name in old_states if name not in new_states],
            "changed": changed_states,
            "order_changed": [name for name in old_states if name in new_states]
            != [name for name in new_states if name in old_states],
        },
        "transitions": {
            "added": [row for key, row in new_transitions.items() if key not in old_transitions],
            "removed": [
                {name: row.get(name) for name in _WORKFLOW_TRANSITION_KEYS[:4]}
                for key, row in old_transitions.items()
                if key not in new_transitions
            ],
            "changed": changed_transitions,
        },
    }


@_tool("developer", "Create or update workflow", idempotent=True)
def upsert_workflow(
    doctype: str,
    name: str,
    states: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    is_active: bool = True,
    send_email_alert: bool = False,
    workflow_state_field: str = "workflow_state",
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or create/replace (by `name`) an approval Workflow for `doctype`.

    `doctype` can't be a child table or Single.

    - states, in order (new documents start in the first): {"state", "doc_status": 0|1|2,
      "allow_edit": Role, "update_field"?, "update_value"?}; 1 and 2 need a submittable DocType.
    - transitions: {"state", "action", "next_state", "allowed": Role, "allow_self_approval"?}; both
      states must be listed. Python conditions are refused.
    is_active deactivates the other Workflows; a missing `workflow_state_field` is added as a hidden
    Custom Field. Roles must exist.
    """
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise ValueError("Invalid Workflow name")
    schema = _meta_bundle(doctype)["doctype"]
    if schema.get("istable") or schema.get("issingle"):
        raise ValueError(f"{doctype} is a child table or Single DocType; it cannot have a Workflow")
    if not isinstance(workflow_state_field, str) or not _NEW_FIELDNAME.fullmatch(
        workflow_state_field
    ):
        raise ValueError("workflow_state_field must be a lowercase fieldname")
    state_field = _schema_fields(schema).get(workflow_state_field)
    if state_field and state_field.get("fieldtype") not in _WORKFLOW_STATE_FIELDTYPES:
        raise ValueError(
            f"{workflow_state_field} is a {state_field.get('fieldtype')} field; the state field "
            "must be a Link, Data, or Select field"
        )
    state_rows = _workflow_states(states, schema)
    transition_rows = _workflow_transitions(transitions, state_rows)
    unreachable, dead_ends = _workflow_paths(state_rows, transition_rows)
    if unreachable:
        raise ValueError(
            f"No transition path from {state_rows[0]['state']!r} reaches: {', '.join(unreachable)}"
        )
    _assert_roles(
        [row["allow_edit"] for row in state_rows] + [row["allowed"] for row in transition_rows]
    )
    current = _config_record("Workflow", name)
    if current and current.get("document_type") != doctype:
        raise ValueError(
            f"Workflow {name} belongs to {current.get('document_type')}; choose another name"
        )
    if (
        confirm
        and current
        and (not expected_modified or str(current.get("modified")) != expected_modified)
    ):
        raise ValueError(_STALE_DOCUMENT)
    missing_states = _missing_records("Workflow State", [row["state"] for row in state_rows])
    missing_actions = _missing_records(
        "Workflow Action Master", [row["action"] for row in transition_rows]
    )
    deactivates = []
    if is_active:
        deactivates = [
            row["name"]
            for row in _request(
                "GET",
                _resource("Workflow"),
                params={
                    "fields": '["name"]',
                    "filters": _filters(
                        [
                            ["document_type", "=", doctype],
                            ["is_active", "=", 1],
                            ["name", "!=", name],
                        ]
                    ),
                    "limit_page_length": 50,
                },
            )
        ]
    values: dict[str, Any] = {
        "workflow_name": name,
        "document_type": doctype,
        "is_active": int(is_active),
        "send_email_alert": int(send_email_alert),
        "workflow_state_field": workflow_state_field,
        "states": _merged_rows(
            (current or {}).get("states") or [], state_rows, lambda row: row.get("state")
        ),
        "transitions": _merged_rows(
            (current or {}).get("transitions") or [], transition_rows, _transition_key
        ),
    }
    warnings = [
        f"Draft state {state!r} has no transition out of it; documents there are stuck"
        for state in dead_ends
    ]
    warnings += [
        f"Transition {row['state']} --{row['action']}--> {row['next_state']} keeps the Python "
        "condition set outside this server"
        for row in values["transitions"]
        if row.get("condition")
    ]
    empty = _count_records(
        doctype, [[workflow_state_field, "is", "not set"]] if state_field else []
    )
    if empty:
        warnings.append(
            f"{empty} existing {doctype} records have no {workflow_state_field}; on save Frappe "
            "sets each to the first state with the record's doc_status"
        )
    if not confirm:
        preview: dict[str, Any] = {
            "preview": True,
            "action": "upsert_workflow",
            "workflow": name,
            "doctype": doctype,
            "exists": current is not None,
            "modified": current.get("modified") if current else None,
            "states": state_rows,
            "transitions": transition_rows,
            "creates": {
                "Workflow State": missing_states,
                "Workflow Action Master": missing_actions,
            },
            "state_field": {"fieldname": workflow_state_field, "exists": state_field is not None},
            "deactivates": deactivates,
            "warnings": warnings,
            "requires_confirmation": True,
        }
        if state_field is None:
            preview["state_field"]["custom_field"] = {
                "dt": doctype,
                "fieldname": workflow_state_field,
                "fieldtype": "Link",
                "options": "Workflow State",
                "hidden": 1,
                "allow_on_submit": 1,
                "no_copy": 1,
            }
        if current:
            preview["changes"] = _workflow_diff(current, values)
        return preview
    created: dict[str, list[str]] = {"Workflow State": [], "Workflow Action Master": []}
    for master, field, names in (
        ("Workflow State", "workflow_state_name", missing_states),
        ("Workflow Action Master", "workflow_action_name", missing_actions),
    ):
        for master_name in names:
            _save_config_record(master, {field: master_name}, master_name)
            created[master].append(master_name)
    saved, reconciled = _save_config_record(
        "Workflow",
        values,
        name,
        expected_modified if current else None,
        forget=(doctype,),
    )
    return {
        "saved": True,
        "created": current is None,
        "workflow": name,
        "doctype": doctype,
        "modified": saved.get("modified") if isinstance(saved, dict) else None,
        "masters_created": created,
        "deactivated": deactivates,
        "warnings": warnings,
        "reconciled_after_server_reload": reconciled,
    }


@_tool("read", "List notifications")
def list_notifications(document_type: str | None = None, limit: int = 50) -> dict[str, Any]:
    """List Notification rules: name, DocType, event, channel, enabled, and module."""
    if not 1 <= limit <= _CONFIG_LIST_LIMIT:
        raise ValueError(f"limit must be between 1 and {_CONFIG_LIST_LIMIT}")
    filters: dict[str, Any] = {}
    if document_type is not None:
        if not isinstance(document_type, str) or not _SAFE_NAME.fullmatch(document_type):
            raise ValueError("Invalid DocType name")
        filters["document_type"] = document_type
    rows = _request(
        "GET",
        _resource("Notification"),
        params={
            "fields": '["name","document_type","event","channel","enabled","is_standard","module"]',
            "filters": _filters(filters),
            "order_by": "name asc",
            "limit_page_length": limit + 1,
        },
    )
    return {"notifications": rows[:limit], "truncated": len(rows) > limit}


def _notification_recipients(
    recipients: Any, schema: dict[str, Any], channel: str
) -> list[dict[str, Any]]:
    if not isinstance(recipients, list) or not 1 <= len(recipients) <= _NOTIFICATION_MAX_RECIPIENTS:
        raise ValueError(f"recipients must list 1 to {_NOTIFICATION_MAX_RECIPIENTS} entries")
    doctype = schema.get("name")
    fields = _schema_fields(schema)
    rows = []
    for index, recipient in enumerate(recipients):
        where = f"recipients[{index}]"
        if not isinstance(recipient, dict):
            raise ValueError(f"{where} must be an object")
        if "condition" in recipient:
            raise ValueError(
                f"{where}: condition is a Python expression Frappe evaluates; recipient "
                "conditions are not supported"
            )
        unknown = sorted(set(recipient) - set(_NOTIFICATION_RECIPIENT_KEYS))
        if unknown:
            raise ValueError(f"{where}: unsupported keys: {', '.join(unknown)}")
        row: dict[str, Any] = dict.fromkeys(_NOTIFICATION_RECIPIENT_KEYS)
        field = recipient.get("receiver_by_document_field")
        if field is not None:
            target = fields.get(field) if isinstance(field, str) else None
            holds_address = target is not None and (
                (target.get("fieldtype") == "Data" and target.get("options") == "Email")
                or (target.get("fieldtype") == "Link" and target.get("options") == "User")
            )
            if field != "owner" and not holds_address:
                raise ValueError(
                    f"{where}: receiver_by_document_field must be owner, a Data field with "
                    f"options Email, or a Link to User of {doctype}"
                )
            row["receiver_by_document_field"] = field
        role = recipient.get("receiver_by_role")
        if role is not None:
            if not isinstance(role, str) or not _SAFE_NAME.fullmatch(role):
                raise ValueError(f"{where}: receiver_by_role must name a Role")
            row["receiver_by_role"] = role
        for key in ("cc", "bcc"):
            if recipient.get(key) is not None:
                if channel != "Email":
                    raise ValueError(f"{where}: {key} applies to the Email channel only")
                row[key] = _email_list(recipient[key], f"{where}.{key}")
        if all(value is None for value in row.values()):
            raise ValueError(
                f"{where}: set receiver_by_document_field, receiver_by_role, cc, or bcc"
            )
        rows.append(row)
    return rows


def _record_files(
    app: str, module: str, doctype: str, name: str, suffixes: tuple[str, ...], init: bool
) -> dict[str, Any]:
    """List the files Frappe's `export_to_files` writes for a standard record of `doctype`.

    They go to `<module>/<scrub(doctype)>/<scrub(name)>/`, shown in the local checkout when
    FRAPPE_APPS_PATH has one; `init` adds the `__init__.py` Frappe creates there.
    """
    slug = _scrub(name)
    try:
        _, module_path = _app_path(app, module)
    except (RuntimeError, ValueError):
        folder, local = f"{app}/{app}/{_scrub(module)}/{_scrub(doctype)}/{slug}", False
    else:
        folder, local = str(module_path / _scrub(doctype) / slug), True
    return {
        "folder": folder,
        "files": [f"{folder}/{slug}{suffix}" for suffix in suffixes]
        + ([f"{folder}/__init__.py"] if init else []),
        "local_checkout": local,
    }


def _notification_files(app: str, module: str, name: str) -> dict[str, Any]:
    """List what Frappe's `export_module_json` writes for a standard Notification."""
    return _record_files(app, module, "Notification", name, (".json", ".md", ".py"), True)


def _standard_module(module: Any, what: str) -> str:
    """Check the Module Def of a standard record and return its app, refusing core apps."""
    if module is None:
        raise ValueError(f"A standard {what} needs module")
    if not isinstance(module, str):
        raise ValueError("Invalid app or module name")
    module_row = _assert_remote_module(None, module)
    if module_row.get("custom"):
        raise ValueError(f"{module} is a custom Module Def; Frappe has no app folder for it")
    app = module_row.get("app_name") or ""
    _assert_writable_app(app)
    return app


@_tool("developer", "Create or update notification", idempotent=True)
def upsert_notification(
    name: str,
    document_type: str,
    event: Literal["New", "Save", "Submit", "Cancel", "Value Change", "Days Before", "Days After"],
    channel: Literal["Email", "System Notification"],
    recipients: list[dict[str, str]],
    subject: str,
    message: str,
    value_changed: str | None = None,
    date_changed: str | None = None,
    days_in_advance: int | None = None,
    filters: Filters = None,
    enabled: bool = True,
    is_standard: bool = False,
    module: str | None = None,
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or create/replace (by `name`) a Notification for `document_type`.

    - event: Value Change needs `value_changed`; Days Before/After need `date_changed` (a date
      field) and `days_in_advance`.
    - recipients: 1-20 of {receiver_by_document_field (owner, an Email field, or a Link to User),
      receiver_by_role, cc, bcc}.
    - subject (max 140 characters) and message: text with `{{ doc.<fieldname> }}` placeholders and
      simple filters (upper, round(2), truncate(40), default("-"), ...); other Jinja is refused.
    Python conditions are refused. is_standard needs `module` (not frappe/erpnext); Frappe writes
    the file in developer mode.
    """
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise ValueError("Invalid Notification name")
    if event not in _NOTIFICATION_EVENTS:
        raise ValueError(
            f"Unsupported event {event!r}; use one of: {', '.join(_NOTIFICATION_EVENTS)} "
            "(Custom, Method, and minute-based events are not supported)"
        )
    if channel not in _NOTIFICATION_CHANNELS:
        raise ValueError(
            f"Unsupported channel {channel!r}; use Email or System Notification "
            "(Slack and SMS are not supported)"
        )
    if document_type in _NOTIFICATION_FORBIDDEN_DOCTYPES:
        raise ValueError(f"Frappe does not allow Notifications on {document_type}")
    bundle = _meta_bundle(document_type)
    schema = bundle["doctype"]
    if schema.get("istable") and event not in _NOTIFICATION_DAY_EVENTS:
        raise ValueError(f"{document_type} is a child table; only Days Before/After apply to it")
    if event in ("Submit", "Cancel") and not schema.get("is_submittable"):
        raise ValueError(f"{document_type} is not submittable, so it has no {event} event")
    fields = _schema_fields(schema)
    value_fields = _template_fields(schema) - _STANDARD_COLUMNS
    if event == "Value Change":
        if not isinstance(value_changed, str) or value_changed not in value_fields:
            raise ValueError(f"value_changed must name a field of {document_type}")
    elif value_changed is not None:
        raise ValueError("value_changed applies to the Value Change event only")
    if event in _NOTIFICATION_DAY_EVENTS:
        if not isinstance(date_changed, str) or (fields.get(date_changed) or {}).get(
            "fieldtype"
        ) not in ("Date", "Datetime"):
            raise ValueError(f"date_changed must name a Date or Datetime field of {document_type}")
        if (
            isinstance(days_in_advance, bool)
            or not isinstance(days_in_advance, int)
            or (days_in_advance < 1)
        ):
            raise ValueError("days_in_advance must be a positive whole number of days")
    elif date_changed is not None or days_in_advance is not None:
        raise ValueError("date_changed and days_in_advance apply to Days Before/After only")
    placeholders = _template_fields(schema)
    _assert_template(subject, "subject", placeholders)
    if len(subject) > _NOTIFICATION_SUBJECT_MAX:
        raise ValueError(f"subject is longer than {_NOTIFICATION_SUBJECT_MAX} characters")
    _assert_template(message, "message", placeholders)
    if filters:
        _assert_filters(bundle, filters)
        if isinstance(filters, list) and any(
            len(condition) == 4 and condition[0] != document_type for condition in filters
        ):
            raise ValueError("Notification filters can only test fields of the DocType itself")
    recipient_rows = _notification_recipients(recipients, schema, channel)
    _assert_roles(row["receiver_by_role"] for row in recipient_rows if row["receiver_by_role"])
    app = None
    if is_standard:
        app = _standard_module(module, "Notification")
    elif module is not None:
        raise ValueError("module applies to standard Notifications only; set is_standard")
    current = _config_record("Notification", name)
    if (
        confirm
        and current
        and (not expected_modified or str(current.get("modified")) != expected_modified)
    ):
        raise ValueError(_STALE_DOCUMENT)
    values: dict[str, Any] = {
        "document_type": document_type,
        "event": event,
        "channel": channel,
        "enabled": int(enabled),
        "subject": subject,
        "message": message,
        "value_changed": value_changed,
        "date_changed": date_changed,
        "days_in_advance": days_in_advance or 0,
        "condition_type": "Filters" if filters else "Python",
        "filters": json.dumps(filters) if filters else None,
        "condition": None,
        "is_standard": int(is_standard),
        "module": module,
        "recipients": recipient_rows,
    }
    if channel == "System Notification":
        # The alert's text comes only from notification_message (create_system_notification).
        values["notification_message"] = message
    warnings = []
    if current and current.get("condition"):
        warnings.append("Removes the Python condition set outside this server")
    if current and any(row.get("condition") for row in current.get("recipients") or []):
        warnings.append("Removes recipient conditions set outside this server")
    if current and current.get("is_standard") and not is_standard:
        warnings.append(
            "Makes a standard Notification site-only; its app files stay until removed by hand"
        )
    if not confirm:
        preview: dict[str, Any] = {
            "preview": True,
            "action": "upsert_notification",
            "notification": name,
            "exists": current is not None,
            "modified": current.get("modified") if current else None,
            "record": values,
            "warnings": warnings,
            "requires_confirmation": True,
        }
        if current:
            state = {"truncated": False}
            preview["changes"] = _bounded(
                {
                    key: {"from": current.get(key), "to": value}
                    for key, value in values.items()
                    if key != "recipients" and not _same(current.get(key), value)
                },
                state,
            )
            old_recipients = [
                {key: row.get(key) for key in _NOTIFICATION_RECIPIENT_KEYS}
                for row in current.get("recipients") or []
            ]
            if not _record_holds({"recipients": old_recipients}, {"recipients": recipient_rows}):
                preview["changes"]["recipients"] = {"from": old_recipients, "to": recipient_rows}
            preview["changes_truncated"] = state["truncated"]
        if is_standard:
            preview["app_files"] = _notification_files(app or "", module or "", name)
            preview["requires_developer_mode"] = True
            preview["notes"] = [
                "Frappe saves an enabled standard Notification only in developer mode and then "
                "writes these files; the message is read from the .md file afterwards. The .py "
                "file is Frappe's get_context stub; this server never writes it. A "
                "developer-mode Bench may reload.",
            ]
        return preview
    saved, reconciled = _save_config_record(
        "Notification",
        values if current else {"name": name, **values},
        name,
        expected_modified if current else None,
    )
    result: dict[str, Any] = {
        "saved": True,
        "created": current is None,
        "notification": name,
        "modified": saved.get("modified") if isinstance(saved, dict) else None,
        "warnings": warnings,
        "reconciled_after_server_reload": reconciled,
    }
    if is_standard:
        result["app_files"] = _notification_files(app or "", module or "", name)
    return result


_REPORT_MAX_COLUMNS = 50
_REPORT_MAX_FILTERS = 50
_REPORT_SAMPLE_ROWS = 5
_REPORT_PAGE_LENGTH = 20
_REPORT_AGGREGATES = ("count", "sum", "avg")
_REPORT_GROUP_BY_KEYS = ("field", "aggregate_function", "aggregate_on")
_REPORT_NUMERIC_FIELDTYPES = frozenset({"Int", "Float", "Currency", "Percent", "Duration"})
_AGGREGATE_COLUMN = "_aggregate_column"


def _report_column(bundle: dict[str, Any], column: Any) -> list[str]:
    """Turn `fieldname` or `<child DocType>.<fieldname>` into Frappe's `[fieldname, doctype]`."""
    if not isinstance(column, str) or not column:
        raise ValueError("Each column must be a fieldname or <child DocType>.<fieldname>")
    schema, children = bundle["doctype"], bundle["children"]
    doctype = schema.get("name")
    target, dot, fieldname = column.rpartition(".")
    if not dot:
        _assert_value_column(schema, fieldname)
        return [fieldname, doctype]
    if target not in children:
        choices = ", ".join(sorted(children)) or "none"
        raise ValueError(f"{target} is not a child table of {doctype}; child tables: {choices}")
    _assert_value_column(children[target], fieldname, child_table=True)
    return [fieldname, target]


def _report_filters(bundle: dict[str, Any], filters: Any) -> list[list[Any]]:
    """Validate filters like `list_documents` and store them as `[doctype, field, op, value]`."""
    if not filters:
        return []
    _assert_filters(bundle, filters)
    if len(filters) > _REPORT_MAX_FILTERS:
        raise ValueError(f"A report takes at most {_REPORT_MAX_FILTERS} filters")
    doctype = bundle["doctype"].get("name")
    if isinstance(filters, dict):
        return [
            [doctype, field, *(value if isinstance(value, list | tuple) else ("=", value))]
            for field, value in filters.items()
        ]
    return [list(row) if len(row) == 4 else [doctype, *row] for row in filters]


def _report_order_by(schema: dict[str, Any], order_by: Any) -> str | None:
    """Rebuild `field [asc|desc], ...` as Frappe's quoted sort string; no raw SQL passes."""
    if order_by is None:
        return None
    doctype = schema.get("name")
    terms = _order_terms(schema, order_by)
    return ", ".join(f"`tab{doctype}`.`{field}` {direction}" for field, direction in terms)


def _report_group_by(schema: dict[str, Any], group_by: Any) -> dict[str, str] | None:
    """Build the `group_by` settings Frappe's Report Builder saves (backquoted columns)."""
    if group_by is None:
        return None
    if not isinstance(group_by, dict):
        raise ValueError("group_by must be an object with field and aggregate_function")
    unknown = sorted(set(group_by) - set(_REPORT_GROUP_BY_KEYS))
    if unknown:
        raise ValueError(
            f"Unknown group_by keys: {', '.join(map(str, unknown))}; "
            f"allowed: {', '.join(_REPORT_GROUP_BY_KEYS)}"
        )
    doctype = schema.get("name")
    field = group_by.get("field")
    _assert_value_column(schema, field)
    function = group_by.get("aggregate_function")
    if function not in _REPORT_AGGREGATES:
        raise ValueError(f"aggregate_function must be one of: {', '.join(_REPORT_AGGREGATES)}")
    aggregate_on = group_by.get("aggregate_on")
    settings = {"group_by": f"`tab{doctype}`.`{field}`", "aggregate_function": function}
    if function == "count":
        if aggregate_on is not None:
            raise ValueError("aggregate_on applies to sum and avg only")
        return settings
    if aggregate_on is None:
        raise ValueError(f"{function} needs aggregate_on, a numeric field of {doctype}")
    row = _assert_value_column(schema, aggregate_on)
    if (row or {}).get("fieldtype") not in _REPORT_NUMERIC_FIELDTYPES:
        raise ValueError(f"aggregate_on must name a numeric field of {doctype}")
    settings["aggregate_on"] = f"`tab{doctype}`.`{aggregate_on}`"
    return settings


def _report_sample(doctype: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Run a saved report's settings the way the Report Builder does, for a few rows.

    `query_report.run` returns nothing for Report Builder reports, so this calls the
    permission-checked `frappe.desk.reportview.get_list` with the same columns and filters.
    """
    doctype_columns = [
        f"`tab{dt}`.`{field}`" for field, dt in settings["fields"] if field != _AGGREGATE_COLUMN
    ]
    params: dict[str, Any] = {
        "doctype": doctype,
        "fields": json.dumps(doctype_columns),
        "filters": json.dumps(settings["filters"]),
        "order_by": settings.get("order_by") or f"`tab{doctype}`.`creation` desc",
        "start": 0,
        "page_length": _REPORT_SAMPLE_ROWS + 1,
        "view": "Report",
    }
    group_by = settings.get("group_by")
    if group_by:
        aggregate_on = group_by.get("aggregate_on")
        params.update(
            group_by=group_by["group_by"],
            aggregate_function=group_by["aggregate_function"],
            aggregate_on_field=aggregate_on.split(".")[-1].strip("`") if aggregate_on else "name",
            aggregate_on_doctype=doctype,
        )
    try:
        rows = _request("GET", "/api/method/frappe.desk.reportview.get_list", params=params)
    except (RuntimeError, httpx.HTTPError) as error:
        return {"error": str(error)[:_DRIFT_MAX_TEXT]}
    rows = rows if isinstance(rows, list) else []
    state = {"truncated": False}
    return {
        "rows": _bounded(rows[:_REPORT_SAMPLE_ROWS], state),
        "truncated": len(rows) > _REPORT_SAMPLE_ROWS or state["truncated"],
    }


@_tool("developer", "Create or update report", idempotent=True)
def upsert_report(
    name: str,
    ref_doctype: str,
    columns: list[str],
    filters: Filters = None,
    order_by: OrderBy = None,
    group_by: dict[str, str] | None = None,
    add_totals_row: bool = False,
    is_standard: bool = False,
    module: str | None = None,
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or create/replace (by `name`) a Report Builder report on `ref_doctype`.

    `ref_doctype` can't be a child table or Single.

    - columns: 1-50 fieldnames or standard columns, or `<child DocType>.<fieldname>`.
    - group_by: {"field", "aggregate_function": count|sum|avg, "aggregate_on"}; then `columns` must
      be [field], with no `order_by`.
    is_standard needs `module` (not frappe/erpnext) and Administrator. Confirming returns sample
    rows. Query and Script Reports are never written.
    """
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise ValueError("Invalid Report name")
    bundle = _meta_bundle(ref_doctype)
    schema = bundle["doctype"]
    if schema.get("istable"):
        raise ValueError(f"{ref_doctype} is a child table; build the report on its parent DocType")
    if schema.get("issingle"):
        raise ValueError(f"{ref_doctype} is a Single DocType; it has no list to report on")
    if not isinstance(columns, list) or not 1 <= len(columns) <= _REPORT_MAX_COLUMNS:
        raise ValueError(f"columns must be a list of 1-{_REPORT_MAX_COLUMNS} column names")
    fields = [_report_column(bundle, column) for column in columns]
    seen: set[tuple[str, str]] = set()
    for column, field in zip(columns, fields, strict=True):
        if tuple(field) in seen:
            raise ValueError(f"Duplicate column: {column}")
        seen.add(tuple(field))
    filter_rows = _report_filters(bundle, filters)
    sort = _report_order_by(schema, order_by)
    grouping = _report_group_by(schema, group_by)
    if grouping:
        if columns != [group_by["field"]]:
            raise ValueError(
                f"With group_by, columns must be exactly [{group_by['field']!r}]; Frappe shows "
                "the group field and the aggregate"
            )
        if sort is not None:
            raise ValueError(
                "order_by cannot be combined with group_by; Frappe sorts by the aggregate"
            )
        fields.append([_AGGREGATE_COLUMN, ref_doctype])
        sort = f"{_AGGREGATE_COLUMN} desc"
    app = None
    if is_standard:
        app = _standard_module(module, "report")
    elif module is not None:
        raise ValueError("module applies to standard reports only; set is_standard")
    current = _config_record("Report", name)
    if current:
        report_type = current.get("report_type")
        if report_type != "Report Builder":
            raise ValueError(
                f"{name} is a {report_type}; this tool edits Report Builder reports only, "
                "and the report type cannot be changed"
            )
        if current.get("is_standard") == "Yes" and not is_standard:
            raise ValueError(
                f"{name} is a standard report; Frappe refuses to make it site-only. "
                "Pass is_standard with its module, or use another name"
            )
    if (
        confirm
        and current
        and (not expected_modified or str(current.get("modified")) != expected_modified)
    ):
        raise ValueError(_STALE_DOCUMENT)
    try:
        old_settings = json.loads((current or {}).get("json") or "{}")
    except ValueError:
        old_settings = {}
    if not isinstance(old_settings, dict):
        old_settings = {}
    # Keys this tool does not manage (column widths, chart settings) are kept.
    settings = {
        **old_settings,
        "fields": fields,
        "filters": filter_rows,
        "order_by": sort,
        "add_totals_row": int(add_totals_row),
        "group_by": grouping,
    }
    settings.setdefault("page_length", _REPORT_PAGE_LENGTH)
    record: dict[str, Any] = {
        "ref_doctype": ref_doctype,
        "report_type": "Report Builder",
        # Sent explicitly: left empty, Frappe makes an Administrator's report standard in
        # developer mode and writes it into the DocType's module.
        "is_standard": "Yes" if is_standard else "No",
    }
    if is_standard:
        record["module"] = module
    values = {**record, "json": json.dumps(settings)}
    files = (
        _record_files(app or "", module or "", "Report", name, (".json",), True)
        if is_standard
        else None
    )
    if not confirm:
        preview: dict[str, Any] = {
            "preview": True,
            "action": "upsert_report",
            "report": name,
            "exists": current is not None,
            "modified": current.get("modified") if current else None,
            "record": record,
            "settings": settings,
            "requires_confirmation": True,
        }
        if current:
            state = {"truncated": False}
            changes = {
                key: {"from": current.get(key), "to": value}
                for key, value in record.items()
                if not _same(current.get(key), value)
            }
            changes.update(
                {
                    key: {"from": old_settings.get(key), "to": value}
                    for key, value in settings.items()
                    if not _same(old_settings.get(key), value)
                }
            )
            preview["changes"] = _bounded(changes, state)
            preview["changes_truncated"] = state["truncated"]
        if files:
            preview["app_files"] = files
            preview["requires_developer_mode"] = True
            preview["notes"] = [
                "Frappe saves a standard report only for Administrator in developer mode, then "
                "writes these files. A developer-mode Bench may reload.",
            ]
        return preview
    saved, reconciled = _save_config_record(
        "Report",
        values if current else {"report_name": name, **values},
        name,
        expected_modified if current else None,
    )
    result: dict[str, Any] = {
        "saved": True,
        "created": current is None,
        "report": name,
        "modified": saved.get("modified") if isinstance(saved, dict) else None,
        "reconciled_after_server_reload": reconciled,
        "sample": _report_sample(ref_doctype, settings),
    }
    if files:
        result["app_files"] = files
    return result


_WORKSPACE_TARGET_TYPES = ("DocType", "Report", "Page")
_WORKSPACE_MAX_ITEMS = 50
_WORKSPACE_LABEL_MAX = 140
_WORKSPACE_DOC_VIEWS = ("List", "Report Builder", "Dashboard", "Tree", "New", "Calendar", "Image")
_WORKSPACE_COLOR = re.compile(r"[A-Za-z]{1,20}|#[0-9A-Fa-f]{3,8}")
_WORKSPACE_SHORTCUT_KEYS = ("label", "type", "link_to", "color", "doc_view")
_WORKSPACE_CARD_KEYS = ("label", "links")
_WORKSPACE_LINK_KEYS = ("label", "type", "link_to")
# Columns of existing rows that this tool does not set but carries over on replacement.
_WORKSPACE_SHORTCUT_KEPT = ("icon", "stats_filter", "format", "restrict_to_domain")
_WORKSPACE_LINK_KEPT = ("icon", "description", "dependencies", "only_for", "onboard", "hidden")
_WORKSPACE_BLOCK_KEYS = {
    "shortcut": "shortcut_name",
    "card": "card_name",
    "number_card": "number_card_name",
    "chart": "chart_name",
    "quick_list": "quick_list_name",
    "custom_block": "custom_block_name",
    "onboarding": "onboarding_name",
}
# Blocks backed by records this tool does not manage; custom blocks hold HTML and JavaScript.
_WORKSPACE_UNMANAGED_BLOCKS = ("chart", "quick_list", "custom_block", "onboarding")
_WORKSPACE_TEXT_BLOCKS = ("header", "paragraph")


def _workspace_blocks(content: Any) -> list[dict[str, Any]]:
    try:
        blocks = json.loads(content or "[]")
    except (TypeError, ValueError):
        return []
    return (
        [block for block in blocks if isinstance(block, dict)] if isinstance(blocks, list) else []
    )


def _workspace_layout(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    layout = []
    for block in blocks:
        kind = block.get("type")
        if kind == "spacer":
            continue
        data = block.get("data") if isinstance(block.get("data"), dict) else {}
        layout.append({"block": kind, "name": data.get(_WORKSPACE_BLOCK_KEYS.get(kind, ""))})
    return layout


def _workspace_cards(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group Workspace Link rows into cards: each Card Break row starts one."""
    cards: list[dict[str, Any]] = []
    for row in links:
        if row.get("type") == "Card Break":
            cards.append({"label": row.get("label"), "row": row, "links": []})
        elif cards:
            cards[-1]["links"].append(row)
        else:
            cards.append({"label": None, "row": {}, "links": [row]})
    return cards


@_tool("read", "Get workspace")
def get_workspace(name: str) -> dict[str, Any]:
    """Return a Workspace: module, visibility, shortcuts, link cards, and number cards.

    `layout` lists the blocks in Desk order; `unmanaged_blocks` lists blocks upsert_workspace can't
    rebuild.
    """
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise ValueError("Invalid Workspace name")
    doc = _request("GET", _resource("Workspace", name))
    blocks = _workspace_blocks(doc.get("content"))
    state = {"truncated": False}
    view = {
        "name": doc.get("name"),
        "label": doc.get("label"),
        "title": doc.get("title"),
        "module": doc.get("module"),
        "app": doc.get("app"),
        "public": bool(doc.get("public")),
        "private": bool(doc.get("for_user")),
        "is_hidden": bool(doc.get("is_hidden")),
        "modified": doc.get("modified"),
        "shortcuts": [
            {key: row.get(key) for key in _WORKSPACE_SHORTCUT_KEYS if not _blank(row.get(key))}
            for row in doc.get("shortcuts") or []
        ],
        "link_cards": [
            {
                "label": card["label"],
                "links": [
                    {
                        "label": row.get("label"),
                        "type": row.get("link_type"),
                        "link_to": row.get("link_to"),
                    }
                    for row in card["links"]
                ],
            }
            for card in _workspace_cards(doc.get("links") or [])
        ],
        "number_cards": [row.get("number_card_name") for row in doc.get("number_cards") or []],
        "layout": _workspace_layout(blocks),
        "unmanaged_blocks": sorted(
            {block.get("type") for block in blocks}.intersection(_WORKSPACE_UNMANAGED_BLOCKS)
        ),
    }
    bounded = _bounded(view, state)
    bounded["truncated"] = state["truncated"]
    return bounded


def _workspace_item(item: Any, where: str, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError(f"{where} must be an object")
    unknown = sorted(set(item) - set(keys))
    if unknown:
        raise ValueError(
            f"{where}: Unknown keys: {', '.join(map(str, unknown))}; allowed: {', '.join(keys)}"
        )
    label = item.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > _WORKSPACE_LABEL_MAX:
        raise ValueError(
            f"{where}: label must be non-empty text of at most {_WORKSPACE_LABEL_MAX} characters"
        )
    if "links" in keys:
        return item
    if item.get("type") not in _WORKSPACE_TARGET_TYPES:
        raise ValueError(
            f"{where}: type must be DocType, Report, or Page, got {item.get('type')!r} "
            "(URL and other targets are refused)"
        )
    link_to = item.get("link_to")
    if not isinstance(link_to, str) or not _SAFE_NAME.fullmatch(link_to):
        raise ValueError(f"{where}: link_to must name an existing {item['type']}")
    return item


def _unique_labels(items: list[dict[str, Any]], what: str) -> None:
    seen: set[str] = set()
    for item in items:
        if item["label"] in seen:
            raise ValueError(f"Duplicate {what} label: {item['label']}")
        seen.add(item["label"])


def _records_by_name(doctype: str, names: set[str], fields: list[str]) -> dict[str, Any]:
    """Read the named records of `doctype` in one list request; refuse any that is missing."""
    if not names:
        return {}
    wanted = sorted(names)
    rows = _request(
        "GET",
        _resource(doctype),
        params={
            "fields": json.dumps(["name", *fields]),
            "filters": _filters([["name", "in", wanted]]),
            "limit_page_length": len(wanted),
        },
    )
    found = {row.get("name"): row for row in rows}
    missing = [name for name in wanted if name not in found]
    if missing:
        raise ValueError(f"Unknown {doctype} on the target site: {', '.join(missing)}")
    return found


def _workspace_targets(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    names: dict[str, set[str]] = {kind: set() for kind in _WORKSPACE_TARGET_TYPES}
    for item in items:
        names[item["type"]].add(item["link_to"])
    found = {
        "DocType": _records_by_name("DocType", names["DocType"], ["istable"]),
        "Report": _records_by_name("Report", names["Report"], ["report_type", "ref_doctype"]),
        "Page": _records_by_name("Page", names["Page"], []),
    }
    for item in items:
        if item["type"] == "DocType" and found["DocType"][item["link_to"]].get("istable"):
            raise ValueError(
                f"{item['label']}: {item['link_to']} is a child table; link its parent DocType"
            )
    return found


def _kept(old: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: old[key] for key in keys if old and not _is_empty(old.get(key))}


def _block_id(kind: str, label: str) -> str:
    # Stable ids, so the same layout gives the same content and diffs stay small.
    return hashlib.sha1(f"{kind}\0{label}".encode(), usedforsecurity=False).hexdigest()[:10]


def _workspace_content(number_cards: list[str], shortcuts: list[str], cards: list[str]) -> str:
    """Lay out number cards, then shortcuts, then link cards, as Desk's editor blocks."""
    blocks: list[dict[str, Any]] = []
    for kind, labels, col in (
        ("number_card", number_cards, 4),
        ("shortcut", shortcuts, 3),
        ("card", cards, 4),
    ):
        if not labels:
            continue
        if blocks:
            blocks.append({"id": _block_id("spacer", kind), "type": "spacer", "data": {"col": 12}})
        blocks.extend(
            {
                "id": _block_id(kind, label),
                "type": kind,
                "data": {_WORKSPACE_BLOCK_KEYS[kind]: label, "col": col},
            }
            for label in labels
        )
    return json.dumps(blocks)


def _order_changes(before: list[str], after: list[str]) -> dict[str, list[str]]:
    """Name the items added, removed, and moved (kept but in a new relative order)."""
    kept_before = [label for label in before if label in after]
    kept_after = [label for label in after if label in before]
    return {
        "added": [label for label in after if label not in before],
        "removed": [label for label in before if label not in after],
        "moved": [
            label for index, label in enumerate(kept_after) if kept_before.index(label) != index
        ],
    }


@_tool("developer", "Create or update workspace", idempotent=True)
def upsert_workspace(
    name: str,
    module: str,
    shortcuts: list[dict[str, str]],
    link_cards: list[dict[str, Any]] | None = None,
    number_cards: list[str] | None = None,
    expected_modified: ExpectedModified = None,
    confirm: Confirm = False,
) -> dict[str, Any]:
    """Preview or create/replace a public, standard Workspace that ships with `module`.

    `module` can't be of frappe or erpnext. `name` is the sidebar label; Frappe writes it to
    `<module>/workspace/` in developer mode.
    - shortcuts (0-50): {"label", "type": DocType|Report|Page, "link_to", "color"?, "doc_view"?};
      doc_view (List, Report Builder, Tree, ...) is for DocTypes.
    - link_cards (0-50): {"label", "links": [{"label", "type", "link_to"}]}, 1-50 links each.
    - number_cards: names of existing Number Cards.
    Laid out as number cards, shortcuts, link cards. Targets must exist; URL shortcuts and custom
    HTML are refused.
    """
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise ValueError("Invalid Workspace name")
    link_cards = link_cards or []
    number_cards = number_cards or []
    for what, items in (
        ("shortcuts", shortcuts),
        ("link_cards", link_cards),
        ("number_cards", number_cards),
    ):
        if not isinstance(items, list) or len(items) > _WORKSPACE_MAX_ITEMS:
            raise ValueError(f"{what} must be a list of at most {_WORKSPACE_MAX_ITEMS} entries")
    if not (shortcuts or link_cards or number_cards):
        raise ValueError("Give at least one shortcut, link card, or number card")
    shortcut_items = [
        _workspace_item(item, f"shortcuts[{index}]", _WORKSPACE_SHORTCUT_KEYS)
        for index, item in enumerate(shortcuts)
    ]
    for item in shortcut_items:
        doc_view = item.get("doc_view")
        if doc_view is not None:
            if item["type"] != "DocType":
                raise ValueError(f"{item['label']}: doc_view applies to DocType shortcuts only")
            if doc_view not in _WORKSPACE_DOC_VIEWS:
                raise ValueError(
                    f"{item['label']}: doc_view must be one of: {', '.join(_WORKSPACE_DOC_VIEWS)}"
                )
        color = item.get("color")
        if color is not None and (
            not isinstance(color, str) or not _WORKSPACE_COLOR.fullmatch(color)
        ):
            raise ValueError(f"{item['label']}: color must be a color name or a #hex value")
    _unique_labels(shortcut_items, "shortcut")
    cards = [
        _workspace_item(card, f"link_cards[{index}]", _WORKSPACE_CARD_KEYS)
        for index, card in enumerate(link_cards)
    ]
    _unique_labels(cards, "link card")
    card_links: list[list[dict[str, Any]]] = []
    for index, card in enumerate(cards):
        links = card.get("links")
        if not isinstance(links, list) or not 1 <= len(links) <= _WORKSPACE_MAX_ITEMS:
            raise ValueError(
                f"{card['label']}: links must be a list of 1-{_WORKSPACE_MAX_ITEMS} links"
            )
        card_links.append(
            [
                _workspace_item(
                    link, f"link_cards[{index}].links[{position}]", _WORKSPACE_LINK_KEYS
                )
                for position, link in enumerate(links)
            ]
        )
    if any(not isinstance(card, str) or not _SAFE_NAME.fullmatch(card) for card in number_cards):
        raise ValueError("number_cards must be names of existing Number Card records")
    if len(set(number_cards)) != len(number_cards):
        raise ValueError("Duplicate number card in number_cards")
    app = _standard_module(module, "Workspace")
    found = _workspace_targets(shortcut_items + [link for links in card_links for link in links])
    _records_by_name("Number Card", set(number_cards), [])

    current = _config_record("Workspace", name)
    warnings: list[str] = []
    old_blocks: list[dict[str, Any]] = []
    if current:
        if current.get("for_user") or not current.get("public"):
            raise ValueError(
                f"Workspace {name} is private (for one user); this tool manages public "
                "Workspaces only"
            )
        if current.get("module") != module:
            if current.get("module"):
                _standard_module(current.get("module"), "Workspace")
            warnings.append(
                f"Moves the Workspace from module {current.get('module')!r} to {module!r}; "
                "Frappe deletes the old module's Workspace folder"
            )
        old_blocks = _workspace_blocks(current.get("content"))
        kinds = {block.get("type") for block in old_blocks}
        unmanaged = sorted(kinds.intersection(_WORKSPACE_UNMANAGED_BLOCKS))
        if unmanaged:
            raise ValueError(
                f"Workspace {name} has {', '.join(unmanaged)} blocks that this tool cannot "
                "rebuild; edit it in Desk"
            )
        text = sorted(kinds.intersection(_WORKSPACE_TEXT_BLOCKS))
        if text:
            warnings.append(
                f"Removes the {' and '.join(text)} blocks from the layout; their text is lost"
            )
    if (
        confirm
        and current
        and (not expected_modified or str(current.get("modified")) != expected_modified)
    ):
        raise ValueError(_STALE_DOCUMENT)

    old_shortcuts = {row.get("label"): row for row in (current or {}).get("shortcuts") or []}
    shortcut_rows = [
        {**_kept(old_shortcuts.get(item["label"]), _WORKSPACE_SHORTCUT_KEPT), **item}
        for item in shortcut_items
    ]
    old_cards = {
        card["label"]: card for card in _workspace_cards((current or {}).get("links") or [])
    }
    link_rows: list[dict[str, Any]] = []
    for card, links in zip(cards, card_links, strict=True):
        old_card = old_cards.get(card["label"])
        old_links = {row.get("label"): row for row in (old_card or {}).get("links", [])}
        link_rows.append(
            {
                **_kept((old_card or {}).get("row"), _WORKSPACE_LINK_KEPT),
                "type": "Card Break",
                "label": card["label"],
                "link_count": len(links),
            }
        )
        for link in links:
            target = found[link["type"]].get(link["link_to"]) or {}
            link_rows.append(
                {
                    **_kept(old_links.get(link["label"]), _WORKSPACE_LINK_KEPT),
                    "type": "Link",
                    "label": link["label"],
                    "link_type": link["type"],
                    "link_to": link["link_to"],
                    "is_query_report": int(
                        link["type"] == "Report" and target.get("report_type") != "Report Builder"
                    ),
                }
            )
    number_card_rows = [{"number_card_name": card, "label": card} for card in number_cards]
    content = _workspace_content(
        number_cards, [item["label"] for item in shortcut_items], [card["label"] for card in cards]
    )
    values: dict[str, Any] = {
        "label": name,
        "title": name,
        "module": module,
        "public": 1,
        "for_user": "",
        "type": "Workspace",
        "content": content,
        "shortcuts": shortcut_rows,
        "links": link_rows,
        "number_cards": number_card_rows,
    }
    # Frappe v16 exports a Workspace to its module only when `standard` is set (v15 has no
    # such field and exports every public one).
    if "standard" in _schema_fields(_meta_bundle("Workspace")["doctype"]):
        values["standard"] = 1
    files = _record_files(app, module, "Workspace", name, (".json",), False)
    if not confirm:
        preview: dict[str, Any] = {
            "preview": True,
            "action": "upsert_workspace",
            "workspace": name,
            "exists": current is not None,
            "modified": current.get("modified") if current else None,
            "record": {key: value for key, value in values.items() if key != "content"},
            "content": content,
            "layout": _workspace_layout(json.loads(content)),
            "warnings": warnings,
            "app_files": files,
            "requires_developer_mode": True,
            "notes": [
                "Frappe writes the Workspace to its module only in developer mode (on v16, "
                "only a standard Workspace, which this tool sets), and saving one needs the "
                "Workspace Manager role. A developer-mode Bench may reload.",
            ],
            "requires_confirmation": True,
        }
        if current:
            old_layout = _workspace_layout(old_blocks)

            def placed(kind: str, rows: list[dict[str, Any]], key: str) -> list[str]:
                # Order as laid out; rows missing from the layout follow in table order.
                labels = [block["name"] for block in old_layout if block["block"] == kind]
                return labels + [
                    row.get(key) for row in rows if row.get(key) not in labels and row.get(key)
                ]

            card_changes = _order_changes(
                placed("card", [card["row"] for card in old_cards.values()], "label"),
                [card["label"] for card in cards],
            )
            card_changes["changed"] = [
                card["label"]
                for card, links in zip(cards, card_links, strict=True)
                if card["label"] in old_cards
                and [
                    (row.get("label"), row.get("link_type"), row.get("link_to"))
                    for row in old_cards[card["label"]]["links"]
                ]
                != [(link["label"], link["type"], link["link_to"]) for link in links]
            ]
            preview["changes"] = {
                "shortcuts": _order_changes(
                    placed("shortcut", current.get("shortcuts") or [], "label"),
                    [item["label"] for item in shortcut_items],
                ),
                "link_cards": card_changes,
                "number_cards": _order_changes(
                    placed("number_card", current.get("number_cards") or [], "label"),
                    number_cards,
                ),
            }
        return preview
    try:
        saved, reconciled = _save_config_record(
            "Workspace", values, name, expected_modified if current else None
        )
    except FrappeError as error:
        if error.exc_type == "NameError":
            raise ValueError(
                f"Frappe refused the name {name!r}: a Page, DocType, or Workspace already uses "
                "the same Desk route; choose another name"
            ) from error
        raise
    return {
        "saved": True,
        "created": current is None,
        "workspace": name,
        "modified": saved.get("modified") if isinstance(saved, dict) else None,
        "warnings": warnings,
        "reconciled_after_server_reload": reconciled,
        "app_files": files,
    }


# Opt-in Bench commands: a fixed command list, run on a local Bench only when the user sets
# FRAPPE_BENCH_PATH. The argv is built from validated values; there is no shell.

_BENCH_PATH_ENV = "FRAPPE_BENCH_PATH"
_BENCH_COMMANDS = ("migrate", "reload-doc", "export-fixtures", "clear-cache", "run-tests")
BenchCommand = Literal["migrate", "reload-doc", "export-fixtures", "clear-cache", "run-tests"]
_BENCH_COMMAND_EXAMPLE = '["docker", "compose", "exec", "-T", "backend", "bench"]'
_BENCH_COMMAND_MAX_PARTS = 20
_BENCH_TIMEOUT_DEFAULT = 900
_BENCH_TIMEOUT_RANGE = (10, 7200)
_BENCH_OUTPUT_LINES = 200
_BENCH_LINE_CHARS = 500
_BENCH_OUTPUT_CHARS = 20_000
_BENCH_PREFLIGHT_ITEMS = 50
_BENCH_PREFLIGHT_PATCHES = 20
_BENCH_FIXTURE_FILES = 200
# The child process gets these variables and nothing else: no FRAPPE_* credentials, and no
# `CI`, which makes `run-tests` skip the site's allow_tests check.
_BENCH_ENV_KEEP = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LANGUAGE",
    "TZ",
    "TMPDIR",
    "TEMP",
    "TMP",
    "SHELL",
    "TERM",
    "PYENV_ROOT",
    "NVM_DIR",
    "NVM_BIN",
    "XDG_RUNTIME_DIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "CONTAINER_HOST",
)
_BENCH_WARNINGS = {
    "migrate": (
        "migrate runs the app's patches, which can change data, then syncs every DocType, "
        "fixture, and customization file into the site"
    ),
    "reload-doc": (
        "reload-doc overwrites the site's DocType record with the app's file; changes made on "
        "the site since the file was written are lost"
    ),
    "export-fixtures": "export-fixtures rewrites the app's fixtures/ files from the site",
    "run-tests": "run-tests runs the app's test code, which writes test records to the site",
}
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_TESTS_RAN = re.compile(r"^Ran (\d+) tests? in ")
_TESTS_STATUS = re.compile(r"^(OK|FAILED)\b(?:\s*\((.*)\))?")
_TESTS_DISABLED = "Testing is disabled for the site"
_bench_config: dict[str, Any] = {}


def _bench_command() -> list[str]:
    """Return FRAPPE_BENCH_COMMAND as a list (default `["bench"]`); its program must be found."""
    raw = (os.environ.get("FRAPPE_BENCH_COMMAND") or "").strip()
    parts: Any = ["bench"]
    if raw:
        try:
            parts = json.loads(raw)
        except ValueError:
            parts = None
    if (
        not isinstance(parts, list)
        or not 1 <= len(parts) <= _BENCH_COMMAND_MAX_PARTS
        or any(not isinstance(part, str) or not part or "\x00" in part for part in parts)
    ):
        raise RuntimeError(
            "FRAPPE_BENCH_COMMAND must be a JSON list of strings, for example "
            + _BENCH_COMMAND_EXAMPLE
        )
    if shutil.which(parts[0]) is None:
        raise RuntimeError(f"{parts[0]} is not on PATH; set FRAPPE_BENCH_COMMAND")
    return parts


def _bench_timeout() -> int:
    raw = (os.environ.get("FRAPPE_BENCH_TIMEOUT") or "").strip()
    low, high = _BENCH_TIMEOUT_RANGE
    if not raw:
        return _BENCH_TIMEOUT_DEFAULT
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if not low <= value <= high:
        raise RuntimeError(f"FRAPPE_BENCH_TIMEOUT must be a whole number of seconds, {low}-{high}")
    return value


def _load_bench_config() -> dict[str, Any] | None:
    """Read the FRAPPE_BENCH_* settings; None when FRAPPE_BENCH_PATH is unset.

    Raises RuntimeError for a remote site or any bad setting.
    """
    value = (os.environ.get(_BENCH_PATH_ENV) or "").strip()
    if not value:
        return None
    if not _is_local_target(os.environ.get("FRAPPE_URL", "")):
        raise RuntimeError(
            "FRAPPE_BENCH_PATH works only with a local site: FRAPPE_URL must be a loopback "
            "or .localhost address"
        )
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise RuntimeError("FRAPPE_BENCH_PATH does not exist") from None
    if not path.is_dir():
        raise RuntimeError("FRAPPE_BENCH_PATH must be the Bench folder")
    site = (os.environ.get("FRAPPE_BENCH_SITE") or os.environ.get("FRAPPE_SITE") or "").strip()
    if not site:
        raise RuntimeError("Set FRAPPE_BENCH_SITE (or FRAPPE_SITE) to the Bench site name")
    if not _SAFE_NAME.fullmatch(site) or ".." in site or " " in site:
        raise RuntimeError(f"FRAPPE_BENCH_SITE {site!r} is not a valid site name")
    # A containerized Bench may have no sites/ folder here; then the site isn't checked locally.
    local_sites = (path / "sites").is_dir()
    if local_sites and not (path / "sites" / site / "site_config.json").is_file():
        raise RuntimeError(f"Site {site} has no sites/{site}/site_config.json in FRAPPE_BENCH_PATH")
    return {
        "path": path,
        "site": site,
        "command": _bench_command(),
        "timeout": _bench_timeout(),
        "local_sites": local_sites,
    }


def _register_bench_tool() -> None:
    if "run_bench" not in server._tool_manager._tools:
        _tool("bench", "Run Bench command", destructive=True)(run_bench)


def _bench_settings() -> None:
    """Load the FRAPPE_BENCH_* settings and register run_bench when they are set."""
    _bench_config.clear()
    config = _load_bench_config()
    if config is not None:
        _bench_config.update(config)
        _register_bench_tool()


def _read_site_config(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _bench_allows_tests(config: dict[str, Any]) -> bool | None:
    """Read allow_tests the way frappe.conf does (site over common); None if not readable."""
    if not config.get("local_sites"):
        return None
    sites = config["path"] / "sites"
    merged = {
        **_read_site_config(sites / "common_site_config.json"),
        **_read_site_config(sites / config["site"] / "site_config.json"),
    }
    return bool(merged.get("allow_tests"))


def _missing_on_site_hint() -> str:
    if _bench_config:
        return (
            "Sync a missing_on_site DocType with run_bench(command='reload-doc', app=..., "
            "module=..., doctype=...), or every change with run_bench(command='migrate')"
        )
    return "missing_on_site DocTypes reach the site with `bench migrate`"


def _pending_patches_hint() -> str:
    if _bench_config:
        return "run_bench(command='migrate') runs pending patches"
    return "`bench migrate` runs pending patches"


def _bench_argv_tail(
    command: str,
    app: str | None,
    module: str | None,
    doctype: str | None,
    test_module: str | None,
) -> list[str]:
    """Check the arguments of one command against the site and build its argv after --site."""
    given = [
        name
        for name, value in (
            ("app", app),
            ("module", module),
            ("doctype", doctype),
            ("test_module", test_module),
        )
        if value is not None
    ]
    if app is not None and (not isinstance(app, str) or not _SAFE_NAME.fullmatch(app)):
        raise ValueError("Invalid app name")
    if command == "clear-cache":
        if given:
            raise ValueError("clear-cache takes no arguments")
        return ["clear-cache"]
    if command == "migrate":
        if set(given) - {"app"}:
            raise ValueError("migrate takes only app (to show its drift and patches)")
        if app is not None:
            _app_modules(app)
        return ["migrate"]
    if command == "export-fixtures":
        if app is None:
            raise ValueError("export-fixtures needs app")
        if set(given) - {"app"}:
            raise ValueError("export-fixtures takes only app")
        _assert_writable_app(app)
        _app_modules(app)
        return ["export-fixtures", "--app", app]
    if command == "reload-doc":
        if set(given) != {"app", "module", "doctype"}:
            raise ValueError("reload-doc needs app, module, and doctype, and nothing else")
        if not isinstance(doctype, str) or not _SAFE_NAME.fullmatch(doctype):
            raise ValueError("Invalid DocType name")
        _assert_remote_module(app, module)  # type: ignore[arg-type]
        _, package_path = _app_root(app)  # type: ignore[arg-type]
        module_path = _module_dir(package_path, module)  # type: ignore[arg-type]
        slug = _scrub(doctype)
        path = _app_file(module_path, "doctype", slug, f"{slug}.json") if module_path else None
        if path is None:
            raise ValueError(f"The {app} checkout has no DocType file for {doctype} in {module}")
        doc, _ = _read_app_json(path)
        if not isinstance(doc, dict) or doc.get("name") != doctype:
            raise ValueError(f"{slug}.json does not hold the DocType {doctype}")
        # The same call frappe.reload_doctype makes: scrubbed module, "doctype", scrubbed name.
        return ["reload-doc", _scrub(module), "doctype", slug]  # type: ignore[arg-type]
    if command == "run-tests":
        if app is None:
            raise ValueError("run-tests needs app, plus one of module, doctype, test_module")
        selectors = [name for name in given if name != "app"]
        if len(selectors) != 1:
            raise ValueError("run-tests takes app and exactly one of module, doctype, test_module")
        _app_modules(app)
        if _bench_allows_tests(_bench_config) is False:
            raise ValueError(
                f"Tests are off for {_bench_config['site']}: set allow_tests in its site config "
                f"(bench --site {_bench_config['site']} set-config allow_tests true)"
            )
        if module is not None:
            _assert_remote_module(app, module)
            return ["run-tests", "--app", app, "--module-def", module]
        if doctype is not None:
            if not isinstance(doctype, str) or not _SAFE_NAME.fullmatch(doctype):
                raise ValueError("Invalid DocType name")
            rows = _request(
                "GET",
                _resource("DocType"),
                params={
                    "fields": '["name","module"]',
                    "filters": _filters({"name": doctype}),
                    "limit_page_length": 1,
                },
            )
            if not rows:
                raise ValueError(f"DocType {doctype} does not exist on the site")
            owner = _module_app(rows[0].get("module"))
            if owner != app:
                raise ValueError(f"{doctype} belongs to {owner}, not {app}")
            return ["run-tests", "--app", app, "--doctype", doctype]
        if (
            not isinstance(test_module, str)
            or len(test_module) > _METHOD_PATH_MAX
            or not _METHOD_PATH.fullmatch(test_module)
            or not test_module.startswith(f"{app}.")
        ):
            raise ValueError(f"test_module must be a module of {app}, as a dotted path")
        return ["run-tests", "--app", app, "--module", test_module]
    raise ValueError(f"command must be one of {', '.join(_BENCH_COMMANDS)}")


def _fixture_files(app: str) -> dict[str, Any]:
    """List the app's fixtures/*.json files with their SHA-256, to see what export changes."""
    _, package_path = _app_root(app)
    folder = package_path / "fixtures"
    files = []
    if folder.is_dir():
        for child in sorted(folder.iterdir()):
            if child.suffix != ".json" or child.name.startswith("."):
                continue
            path = _app_file(package_path, "fixtures", child.name)
            if path is None:
                continue
            files.append(
                {
                    "file": f"fixtures/{child.name}",
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
            if len(files) >= _BENCH_FIXTURE_FILES:
                break
    return {"files": files}


def _bench_drift(app: str, module: str | None) -> dict[str, Any]:
    drift = list_app_drift(app, module=module, limit=_DRIFT_PAGE_LIMIT)
    differing = [item for item in drift["items"] if item.get("verdict") != "in_sync"]
    result = {
        "total": drift["total"],
        "counts": drift["counts"],
        "items": differing[:_BENCH_PREFLIGHT_ITEMS],
    }
    if drift.get("next_offset") is not None:
        result["partial"] = f"only the first {_DRIFT_PAGE_LIMIT} DocTypes were compared"
    return result


def _bench_patches(app: str) -> dict[str, Any]:
    patches = list_pending_patches(app)
    return {
        "pending_count": patches["pending_count"],
        "pending": [row.get("patch") for row in patches["pending"][:_BENCH_PREFLIGHT_PATCHES]],
    }


def _bench_state(command: str, app: str | None, module: str | None) -> dict[str, Any]:
    """Read what the command is about to change: drift, pending patches, fixture files.

    A part that can't be read (no checkout, no access) says why instead of failing the call.
    """
    if app is None or command == "clear-cache":
        return {}
    parts: dict[str, Callable[[], dict[str, Any]]] = {}
    if command in ("migrate", "reload-doc"):
        parts["drift"] = lambda: _bench_drift(app, module if command == "reload-doc" else None)
    if command == "migrate":
        parts["pending_patches"] = lambda: _bench_patches(app)
    if command == "export-fixtures":
        parts["fixture_files"] = lambda: _fixture_files(app)
    state: dict[str, Any] = {}
    for name, read in parts.items():
        try:
            state[name] = read()
        except (RuntimeError, ValueError, OSError) as error:
            state[name] = {"unavailable": _redact(str(error)) or type(error).__name__}
    return state


def _bench_env() -> dict[str, str]:
    env = {
        name: value
        for name, value in os.environ.items()
        if name in _BENCH_ENV_KEEP or name.startswith("LC_")
    }
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _bench_output(raw: bytes | str | None) -> dict[str, Any]:
    """Return the output's last lines: plain text, secrets and the Bench folder replaced."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw or ""
    text = _ANSI_ESCAPE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _redact(text.replace(str(_bench_config["path"]), "<bench>"))
    lines = text.splitlines()
    kept = [
        line if len(line) <= _BENCH_LINE_CHARS else line[:_BENCH_LINE_CHARS] + " [cut]"
        for line in lines[-_BENCH_OUTPUT_LINES:]
    ]
    output = "\n".join(kept)
    truncated = len(kept) < len(lines) or kept != lines[-_BENCH_OUTPUT_LINES:]
    if len(output) > _BENCH_OUTPUT_CHARS:
        output = output[-_BENCH_OUTPUT_CHARS:].split("\n", 1)[-1]
        truncated = True
    return {"output": output, "output_lines": len(lines), "output_truncated": truncated}


def _test_summary(output: str) -> dict[str, Any]:
    """Read unittest's summary lines; Frappe v15 exits 0 even when tests fail."""
    if _TESTS_DISABLED in output:
        return {"status": "disabled"}
    summary: dict[str, Any] = {}
    for line in output.splitlines():
        ran = _TESTS_RAN.match(line)
        if ran:
            summary["ran"] = summary.get("ran", 0) + int(ran.group(1))
        status = _TESTS_STATUS.match(line)
        if status and (status.group(1) == "FAILED" or summary.get("status") != "FAILED"):
            summary["status"] = status.group(1)
            if status.group(2):
                summary["detail"] = status.group(2)
    return summary or {"status": "unknown"}


def _file_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    old = {row["file"]: row["sha256"] for row in before.get("files", [])}
    new = {row["file"]: row["sha256"] for row in after.get("files", [])}
    return {
        "added": sorted(set(new) - set(old)),
        "changed": sorted(name for name in set(old) & set(new) if old[name] != new[name]),
        "removed": sorted(set(old) - set(new)),
    }


def run_bench(
    command: BenchCommand,
    app: str | None = None,
    module: str | None = None,
    doctype: str | None = None,
    test_module: str | None = None,
    confirm: Confirm = False,
    expected_preview: ExpectedPreview = None,
) -> dict[str, Any]:
    """Run one Bench command on the configured local site; preview first.

    migrate (app optional, for its drift and patches); reload-doc (app, module, doctype: sync a
    DocType file to the site); export-fixtures (app); clear-cache; run-tests (app and one of
    module, doctype, test_module; needs allow_tests). migrate runs patches that change data.
    Returns exit_code, ok, the output's tail, and drift and patches after the run.
    """
    if not _is_local_target(os.environ.get("FRAPPE_URL", "")):
        raise ValueError("Bench commands work only with a local site")
    if not _bench_config:
        raise ValueError("Bench commands are off: FRAPPE_BENCH_PATH is not set")
    if command not in _BENCH_COMMANDS:
        raise ValueError(f"command must be one of {', '.join(_BENCH_COMMANDS)}")
    tail = _bench_argv_tail(command, app, module, doctype, test_module)
    argv = [*_bench_config["command"], "--site", _bench_config["site"], *tail]
    before = _bench_state(command, app, module)
    preview: dict[str, Any] = {
        "command": command,
        "site": _bench_config["site"],
        "argv": argv,
        "preflight": before,
    }
    if command in _BENCH_WARNINGS:
        preview["warning"] = _BENCH_WARNINGS[command]
    preview_id = hashlib.sha256(
        json.dumps(preview, sort_keys=True, default=str).encode()
    ).hexdigest()
    if not confirm:
        return {
            "preview": True,
            **preview,
            "preview_id": preview_id,
            "requires_confirmation": True,
        }
    if expected_preview != preview_id:
        raise ValueError("Bench preview is missing or stale; preview again")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=_bench_config["path"],
            env=_bench_env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=_bench_config["timeout"],
            check=False,
        )
        exit_code: int | None = completed.returncode
        raw = completed.stdout
        timed_out = False
    except subprocess.TimeoutExpired as error:
        exit_code, raw, timed_out = None, error.output, True
    except OSError as error:
        raise RuntimeError(
            f"Could not start {argv[0]}: {error.strerror or type(error).__name__}"
        ) from None
    result: dict[str, Any] = {
        "command": command,
        "site": _bench_config["site"],
        "argv": argv,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "seconds": round(time.monotonic() - started, 1),
        **_bench_output(raw),
    }
    ok = exit_code == 0
    if command == "run-tests":
        result["tests"] = _test_summary(result["output"])
        ok = ok and result["tests"]["status"] in ("OK", "unknown")
    result["ok"] = ok
    if timed_out:
        result["note"] = (
            f"Stopped after {_bench_config['timeout']} s (FRAPPE_BENCH_TIMEOUT); a command run "
            "inside a container may still be running there"
        )
    # Migrate and reload-doc can restart the web worker; wait for it before reading again.
    try:
        _wait_for_server_ready()
        result["server_ready"] = True
    except RuntimeError:
        result["server_ready"] = False
    _forget_meta()
    after = _bench_state(command, app, module)
    result["after"] = after
    if command == "export-fixtures":
        result["changed_files"] = _file_changes(
            before.get("fixture_files", {}), after.get("fixture_files", {})
        )
    return result


def _bench_check_rows(toolsets: set[str]) -> list[tuple[str, str, str]]:
    """The `check` rows for FRAPPE_BENCH_PATH (registering run_bench); never prints the folder."""
    if not (os.environ.get(_BENCH_PATH_ENV) or "").strip():
        return []
    try:
        _bench_settings()
    except RuntimeError as error:
        return [("FAIL", "bench", str(error))]
    config = _bench_config
    command = config["command"]
    detail = f"site {config['site']}, command {command[0]}"
    if len(command) > 1:
        detail += f" (+{len(command) - 1} arguments)"
    detail += f", timeout {config['timeout']} s"
    tests = _bench_allows_tests(config)
    if tests is not None:
        detail += "; run-tests allowed" if tests else "; run-tests off (no allow_tests)"
    if "bench" not in toolsets:
        detail += "; the bench toolset is off"
    return [("ok", "bench", detail)]


def _read_only_setting() -> str:
    """Return FRAPPE_MCP_READ_ONLY as "", "YES", or "NO"."""
    read_only = (os.environ.get("FRAPPE_MCP_READ_ONLY") or "").strip().upper()
    if read_only not in ("", "YES", "NO"):
        raise RuntimeError("FRAPPE_MCP_READ_ONLY must be YES or NO")
    return read_only


def _read_only_reason() -> str | None:
    """Say why the server is read-only: "FRAPPE_MCP_READ_ONLY=YES", "remote target", or None.

    A target that isn't loopback or .localhost is read-only unless FRAPPE_MCP_READ_ONLY=NO.
    """
    read_only = _read_only_setting()
    if read_only == "YES":
        return "FRAPPE_MCP_READ_ONLY=YES"
    if read_only == "" and _remote_target():
        return "remote target"
    return None


def _selected_toolsets() -> set[str]:
    """Read FRAPPE_MCP_TOOLSETS and FRAPPE_MCP_READ_ONLY.

    Every toolset is on by default for a loopback or .localhost target; any other target gets
    only the read toolset unless FRAPPE_MCP_READ_ONLY=NO.
    """
    raw = os.environ.get("FRAPPE_MCP_TOOLSETS") or ""
    names = {part.strip().lower() for part in raw.split(",") if part.strip()}
    unknown = sorted(names - set(_TOOLSETS))
    if unknown:
        raise RuntimeError(
            f"Unknown toolset in FRAPPE_MCP_TOOLSETS: {', '.join(unknown)}; "
            f"choose from {', '.join(_TOOLSETS)}"
        )
    reason = _read_only_reason()
    if reason is not None:
        writable = sorted(names - {"read"})
        if writable:
            fix = (
                "set FRAPPE_MCP_READ_ONLY=NO to enable writes on it, or remove"
                if reason == "remote target"
                else "remove"
            )
            raise RuntimeError(
                f"Read-only ({reason}) exposes only the read toolset; {fix} "
                f"{', '.join(writable)} from FRAPPE_MCP_TOOLSETS"
            )
        return {"read"}
    return names or set(_TOOLSETS)


def _apply_toolsets(selected: set[str]) -> None:
    """Remove the tools of unselected toolsets, so the host never lists them."""
    for toolset, names in _TOOLSETS.items():
        if toolset not in selected:
            for name in sorted(names):
                server.remove_tool(name)


def _startup_settings() -> set[str]:
    """Validate the settings read at startup and return the selected toolsets."""
    _timeout()
    _meta_ttl()
    _site_label()
    return _selected_toolsets()


def _remote_mode_notice() -> str | None:
    """Describe the remote-target decision for the startup log and `check`, or None."""
    if not _remote_target():
        return None
    target = _display_url(os.environ.get("FRAPPE_URL", ""))
    setting = _read_only_setting()
    if setting == "":
        return (
            f"read-only: {target} is a remote target; set FRAPPE_MCP_READ_ONLY=NO to enable "
            "write tools"
        )
    if setting == "NO":
        return f"write tools are enabled for the remote target {target} (FRAPPE_MCP_READ_ONLY=NO)"
    return None


_INSECURE_HTTP_WARNING = (
    "FRAPPE_ALLOW_INSECURE_HTTP=YES: credentials may be sent over plain HTTP to "
    "non-local hosts; use this only on a trusted development network"
)


def _display_url(url: str) -> str:
    """Return scheme://host[:port] of a URL, dropping any credentials, path, or query."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.hostname:
        return "(not a URL)"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    try:
        port = parsed.port
    except ValueError:
        port = None
    return f"{parsed.scheme}://{host}" + (f":{port}" if port else "")


_REDACT_MIN_CHARS = 8


def _redact(text: str) -> str:
    """Replace every configured secret that appears in text.

    `check` never prints a secret itself; this guards against one echoed in an error
    message. Values shorter than `_REDACT_MIN_CHARS` are left alone, because replacing them
    would mangle ordinary words ("admin" inside "Administrator").
    """
    for name in ("FRAPPE_API_KEY", "FRAPPE_API_SECRET", "FRAPPE_PASSWORD"):
        secret = os.environ.get(name) or ""
        if len(secret) >= _REDACT_MIN_CHARS:
            text = text.replace(secret, "***")
    return text


def _writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def _apps_path_check(site_apps: list[str]) -> tuple[bool, str]:
    """Say whether FRAPPE_APPS_PATH can serve the file-writing tools, without its path."""
    local = _local_apps(site_apps)
    if not local["configured"]:
        return True, "not set; standard DocType creation and file exports are unavailable"
    if local.get("exists") is False:
        return False, "FRAPPE_APPS_PATH does not exist or cannot be read"
    found = local["site_apps_found"]
    read_only = [app for app in found if not _writable(_app_root(app)[1])]
    parts = [f"checkouts found: {', '.join(found) or 'none'}"]
    if local["site_apps_missing"]:
        parts.append(f"no checkout: {', '.join(local['site_apps_missing'])}")
    if read_only:
        parts.append(f"not writable: {', '.join(read_only)}")
    return bool(found) and not read_only, "; ".join(parts)


def _tool_list_summary(selected: set[str]) -> str:
    """Describe what each toolset adds to the host's context, for `check`."""
    cost = {name: value for name, value in _tool_list_cost().items() if value[0]}
    parts = [f"{name} {count} tools ~{chars:,} chars" for name, (count, chars) in cost.items()]
    listed = sum(chars for name, (_, chars) in cost.items() if name in selected)
    summary = f"{', '.join(parts)}; listed ~{listed:,} chars"
    off = [name for name in cost if name not in selected]
    if off:
        summary += f"; off: {', '.join(off)}"
    return summary


def _setup_check() -> list[tuple[str, str, str]]:
    """Run the `check` command's steps; each row is (status, step, detail)."""
    rows: list[tuple[str, str, str]] = []
    problems = []
    for step in (_timeout, _meta_ttl):
        try:
            step()
        except RuntimeError as error:
            problems.append(str(error))
    try:
        label = _site_label()
    except RuntimeError as error:
        problems.append(str(error))
        label = None
    try:
        toolsets = _selected_toolsets()
    except RuntimeError as error:
        problems.append(str(error))
        toolsets = set()
    url = os.environ.get("FRAPPE_URL", "")
    if not url:
        problems.append(
            "Set FRAPPE_URL to the target site's base URL; for local Aspire, use the "
            "Frappe desk URL shown by `aspire describe frappe`"
        )
    target = _display_url(url) if url else "no site URL"
    if label:
        target += f", label {label}"
    if os.environ.get("FRAPPE_SITE"):
        target += f", site {os.environ['FRAPPE_SITE']}"
    if not _password_auth():
        target += ", API key auth"
    else:
        target += f", password login as {os.environ.get('FRAPPE_USERNAME', 'Administrator')}"
    rows.append(("ok" if not problems else "FAIL", "settings", "; ".join([target, *problems])))
    if _insecure_http_allowed():
        rows.append(("warn", "transport", _INSECURE_HTTP_WARNING))
    if problems:
        return rows

    try:
        info = get_site_info()
    except httpx.TransportError as error:
        rows.append(("FAIL", "connection", _unreachable_message(error, suggest_check=False)))
        return rows
    except (RuntimeError, ValueError, OSError, httpx.HTTPError) as error:
        detail = str(error) or type(error).__name__
        port_hint = _local_port_hint(url)
        if port_hint:
            detail += f" Port check: {port_hint}"
        rows.append(("FAIL", "connection", detail))
        return rows
    if info["user"] in (None, "", "Guest"):
        rows.append(("FAIL", "connection", "the site answered as Guest; check the credentials"))
        return rows
    rows.append(("ok", "connection", f"logged in as {info['user']}"))
    rows.append(("info", "MCP version guidance", info["mcp_guidance"]))
    versions = ", ".join(f"{app['app']} {app['version']}" for app in info["apps"])
    rows.append(("ok", "versions", versions or "the site reported no app versions"))
    rows.append(("info", "developer mode", "not reported: Frappe has no API for it"))
    if (os.environ.get(_METHODS_ENV) or "").strip():
        try:
            method_warnings = _method_settings()
        except RuntimeError as error:
            rows.append(("FAIL", "methods", str(error)))
        else:
            detail = f"{len(_method_config)} allowlisted"
            if "data" not in toolsets:
                detail += "; the method tools are off with the data toolset"
            rows.append(("ok", "methods", detail))
            rows.extend(("warn", "methods", warning) for warning in method_warnings)
    rows.extend(_bench_check_rows(toolsets))
    # A toolset whose tools are registered only on request (bench) is listed only when it has some.
    selected = ", ".join(name for name in _TOOLSETS if name in toolsets and _TOOLSETS[name])
    reason = _read_only_reason()
    if reason == "remote target":
        selected += " (read-only: remote target; set FRAPPE_MCP_READ_ONLY=NO to enable writes)"
    elif reason is not None:
        selected += " (read-only mode)"
    rows.append(("ok", "toolsets", selected))
    rows.append(("info", "tool list", _tool_list_summary(toolsets)))
    if reason is None and _remote_target():
        rows.append(("warn", "toolsets", _remote_mode_notice() or ""))
    usable, detail = _apps_path_check([app["app"] for app in info["apps"]])
    rows.append(("ok" if usable else "FAIL", "FRAPPE_APPS_PATH", detail))
    return rows


def _run_setup_check() -> int:
    """Print the `check` report to stdout and return the exit code: 0 when ready, 1 if not."""
    try:
        rows = _setup_check()
    finally:
        _reset_session()
    width = max(len(step) for _, step, _ in rows)
    lines = [f"mcp-frappe {__version__}"]
    lines += [f"  {status:<4}  {step:<{width}}  {detail}" for status, step, detail in rows]
    failed = sum(status == "FAIL" for status, _, _ in rows)
    lines.append("Ready." if not failed else f"{failed} problem(s) found.")
    print(_redact("\n".join(lines)))
    return 1 if failed else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-frappe",
        description=(
            "Standalone MCP server for a Frappe site. With no command it serves MCP over "
            "stdio; settings come from FRAPPE_* environment variables."
        ),
    )
    parser.add_argument("--version", action="version", version=f"mcp-frappe {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.add_parser(
        "check",
        help="check the settings and the connection to the site, then exit",
        description=(
            "Read the FRAPPE_* settings, log in, and print the user, app versions, toolsets, "
            "and whether FRAPPE_APPS_PATH is usable. Exits 0 when ready, 1 otherwise. "
            "Secrets are never printed."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    """Run the `mcp-frappe` command: the stdio server by default, or `check` / `--version`.

    In server mode stdout is the MCP channel, so nothing else is written to it; logs and
    startup errors go to stderr.
    """
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "check":
        raise SystemExit(_run_setup_check())
    try:
        selected = _startup_settings()
        method_warnings = _method_settings()
        _bench_settings()
        _apply_toolsets(selected)
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    for warning in method_warnings:
        _log.warning(warning)
    if _method_config and "data" not in selected:
        _log.warning(
            "FRAPPE_MCP_METHODS is set, but the method tools are in the data toolset, which is off"
        )
    if _bench_config and "bench" not in selected:
        _log.warning("FRAPPE_BENCH_PATH is set, but the bench toolset is off")
    if _insecure_http_allowed():
        _log.warning(_INSECURE_HTTP_WARNING)
    notice = _remote_mode_notice()
    if notice:
        _log.warning(notice)
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
