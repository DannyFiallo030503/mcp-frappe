"""Standalone stdio MCP server that talks to Frappe through its public HTTP API."""

from __future__ import annotations

import ast
import copy
import difflib
import hashlib
import json
import logging
import math
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
from mcp.server.fastmcp import FastMCP

logging.getLogger("httpx").setLevel(logging.WARNING)
_log = logging.getLogger(__name__)


server = FastMCP("frappe")
_session: httpx.Client | None = None
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
    {"name", "doctype", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
     "migration_hash", "field_order"}
)
_CHILD_SYSTEM_KEYS = frozenset(
    {"name", "doctype", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
     "parent", "parenttype", "parentfield"}
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
        base_url=base_url, headers=headers, timeout=timeout, follow_redirects=False
    )
    if _password_auth():
        try:
            _check(client.post("/api/method/login", data={"usr": username, "pwd": password}))
        except BaseException:
            client.close()
            raise
    _session = client
    return _session


def _reset_session() -> None:
    global _session
    if _session is not None:
        _session.close()
    _session = None


def _session_expired(client: httpx.Client) -> bool:
    try:
        user = _check(client.get("/api/method/frappe.auth.get_logged_user"))
    except RuntimeError:
        return True
    return user == "Guest"


class FrappeError(RuntimeError):
    """An error response from Frappe; `exc_type` names the server-side exception class."""

    def __init__(self, message: str, exc_type: str | None = None) -> None:
        super().__init__(message)
        self.exc_type = exc_type


def _check(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    exc_type = payload.get("exc_type") if isinstance(payload, dict) else None
    if response.is_error:
        message = payload.get("_server_messages") if isinstance(payload, dict) else None
        if not message and isinstance(payload, dict):
            message = payload.get("exception") or payload.get("message")
        raise FrappeError(
            f"Frappe HTTP {response.status_code}: {message or 'request failed'}", exc_type
        )
    if isinstance(payload, dict) and payload.get("exc"):
        raise FrappeError("Frappe returned an application error", exc_type)
    if isinstance(payload, dict):
        if "data" in payload:
            return payload["data"]
        if "message" in payload:
            return payload["message"]
    return payload


def _request(method: str, path: str, **kwargs: Any) -> Any:
    client = _client()
    response = client.request(method, path, **kwargs)
    if response.status_code in (401, 403) and _password_auth() and _session_expired(client):
        # The expired sid made Frappe handle this request as Guest and reject it before it ran,
        # so retrying it once after a fresh login is safe even for a POST.
        _reset_session()
        response = _client().request(method, path, **kwargs)
    return _check(response)


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
            if isinstance(value, dict) or (
                isinstance(value, list | tuple) and len(value) != 2
            ):
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


def _wait_for_doctype(
    name: str, generated_files: list[Path], timeout: float = 60.0
) -> bool:
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
    unsupported = set(properties) - _DOCTYPE_PROPERTIES
    if unsupported:
        raise ValueError(f"Unsupported DocType properties: {', '.join(sorted(unsupported))}")
    if permissions is not None and (
        not isinstance(permissions, list)
        or any(not isinstance(permission, dict) for permission in permissions)
    ):
        raise ValueError("permissions must be a list of objects")

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


def _fixture_plan(
    app_path: Path, entries: dict[str, dict[str, list[str]]]
) -> tuple[Path, str]:
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
                (
                    condition
                    for condition in filters
                    if len(condition) == 3 and condition[0] == key
                ),
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


@server.tool()
def list_installed_apps() -> list[dict[str, Any]]:
    """Discover apps and modules represented by the target site's Module Def records."""
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


@server.tool()
def get_site_info() -> dict[str, Any]:
    """Say who is connected and what the site runs: user, app versions, and the local checkout.

    Returns `user`; `apps` as `[{"app", "title", "version"}]` from Frappe's `get_versions`;
    `frappe_major_version`; and `apps_path`, which says whether `FRAPPE_APPS_PATH` is set and
    which of the site's apps have a checkout there (no paths are returned). Developer mode
    is not reported, because Frappe has no API for it; see `notes`.
    """
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
    return {
        "user": user,
        "apps": apps,
        "frappe_major_version": int(major) if major.isdigit() else None,
        "apps_path": _local_apps([row["app"] for row in apps]),
        "notes": [_DEVELOPER_MODE_NOTE],
    }


@server.tool()
def list_doctypes(
    search: str | None = None,
    app: str | None = None,
    module: str | None = None,
    include_child_tables: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """List DocTypes on the site, a page at a time, optionally searched and filtered.

    `search` is a case-insensitive part of the DocType name (letters, digits, spaces, `_`,
    `.`, `-`; `_` matches any one character). `app` keeps DocTypes in that app's modules.
    Each row has `name`, `module`, `custom`, `issingle`, `istable`, and `is_submittable`.
    Returns `{"rows", "offset", "limit", "has_more"}`; pass `offset + limit` as the next
    `offset` while `has_more` is true.
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


@server.tool()
def get_doctype_schema(
    doctype: str, detail: Literal["compact", "full"] = "compact"
) -> dict[str, Any]:
    """Read a DocType's schema as Frappe's form sees it, with Custom Fields and Property Setters.

    Any logged-in user can read it. `compact` (the default) returns the DocType flags (`name`,
    `module`, `istable`, `issingle`, `is_submittable`, `is_tree`, `autoname`, `title_field`,
    `custom`) and its data fields, leaving out layout fields such as Section Break, Column
    Break, Tab Break, HTML, and Button. Each field has only `fieldname`, `label`, `fieldtype`,
    `options`, `reqd`, `read_only`, `hidden`, `default`, `unique`, and `permlevel`, and false or
    empty values are left out. `child_tables` maps each Table field's DocType to its fields in
    the same form, and `workflow` names the active Workflow, if any. `full` returns every
    metadata property as `{"doctype": ..., "children": {child_doctype: ...}, "workflow": ...}`;
    use it only when editing a DocType needs every property.
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


@server.tool()
def list_documents(
    doctype: str,
    filters: dict[str, Any] | list[list[Any]] | None = None,
    fields: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
    order_by: str | None = None,
    or_filters: dict[str, Any] | list[list[Any]] | None = None,
) -> dict[str, Any]:
    """List permission-filtered records a page at a time through the standard Frappe REST API.

    `fields` and `filters` may use the DocType's fields plus the standard columns `name`,
    `owner`, `creation`, `modified`, `modified_by`, `docstatus`, and `idx`. `filters` is
    either an object (`{"status": "Open"}`, `{"grand_total": [">", 100]}`) or a list of
    `[field, operator, value]` / `[child_doctype, field, operator, value]` conditions, where
    `child_doctype` is the DocType of one of this DocType's Table fields. Operators: =, !=,
    <, >, <=, >=, like, not like, in, not in, between, is, timespan. A record must match
    every `filters` condition and, when `or_filters` is given (same shapes), at least one of
    those. `order_by` is `<fieldname> [asc|desc]`, up to 3 joined by commas (default
    `modified desc`). Returns `{"rows", "offset", "limit", "has_more"}`; pass
    `offset + limit` as the next `offset` while `has_more` is true.
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


@server.tool()
def count_documents(
    doctype: str, filters: dict[str, Any] | list[list[Any]] | None = None
) -> dict[str, Any]:
    """Count the records the user may read, with the same `filters` as `list_documents`."""
    _assert_filters(_meta_bundle(doctype), filters)
    count = _get_count(doctype, filters)
    if not isinstance(count, int) or isinstance(count, bool):
        raise RuntimeError("Unexpected get_count response")
    return {"doctype": doctype, "count": count}


@server.tool()
def search_records(
    doctype: str,
    text: str,
    filters: dict[str, Any] | list[list[Any]] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Find records the way a Link field's dropdown does, by name and the DocType's search fields.

    Calls Frappe's `frappe.desk.search.search_link` with the DocType's own search settings
    (never a custom query method) and the user's permissions. `text` may be empty to list
    the first matches; `filters` takes the `list_documents` shapes. `limit` is 1-50. Returns
    `{"doctype", "results": [{"value", "label"?, "description"?}]}`, where `value` is the
    record name.
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


@server.tool()
def get_document(
    doctype: str, name: str, fields: list[str] | None = None, child_row_limit: int = 100
) -> dict[str, Any]:
    """Read one document, selecting fields and bounding any returned child-table rows.

    Without `fields`, returns the DocType's data fields that have a value (`null`, `""`, and
    empty tables are left out; `0` is kept), and child rows keep only their data fields with
    a value plus `name` and `idx`. `fields` may name the DocType's fields and the standard
    columns (`owner`, `creation`, `modified`, `modified_by`, `docstatus`, `idx`). `doctype`,
    `name`, `modified`, and `docstatus` are always returned. Table and Table MultiSelect
    fields return at most `child_row_limit` rows; cut tables are listed in
    `_truncated_child_tables`.
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


@server.tool()
def create_document(doctype: str, values: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
    """Preview or create a document via the native REST resource API.

    `values` may include `name` only when the DocType's autoname is `Prompt` (naming rule
    "Set by user"); otherwise Frappe names the record itself.
    """
    schema = _meta_bundle(doctype)["doctype"]
    prompt_named = str(schema.get("autoname") or "").lower() == "prompt"
    if isinstance(values, dict) and "name" in values and not prompt_named:
        raise ValueError(
            f"{doctype} names its records itself (autoname is not Prompt); leave out name"
        )
    _assert_writable_fields(schema, values, allow_name=prompt_named)
    if not confirm:
        return {
            "preview": True,
            "action": "create_document",
            "doctype": doctype,
            "fields": sorted(values),
            "requires_confirmation": True,
        }
    data = dict(values)
    if "name" in data:
        # Desk sends a prompted name as __newname; Document.set_new_name uses it only when
        # autoname is "prompt", the condition checked above.
        data["__newname"] = data.pop("name")
    try:
        return _request("POST", _resource(doctype), json={"data": {"doctype": doctype, **data}})
    finally:
        _forget_meta_after_write(doctype)


@server.tool()
def update_document(
    doctype: str,
    name: str,
    values: dict[str, Any],
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or update a record; confirmation requires the preview's modified token."""
    _assert_writable_fields(_meta_bundle(doctype)["doctype"], values)
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
    _expected_modified(doctype, name, expected_modified)
    # Callers may not write `modified`, but sending the preview's value closes the gap after
    # the check above: update_doc locks the row, and Document.check_if_latest compares this
    # value with the stored one and raises TimestampMismatchError if they differ.
    try:
        return _request(
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


@server.tool()
def delete_document(
    doctype: str, name: str, expected_modified: str | None = None, confirm: bool = False
) -> dict[str, Any]:
    """Preview or delete a record; confirmation requires the preview's modified token."""
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


@server.tool()
def submit_document(
    doctype: str, name: str, expected_modified: str | None = None, confirm: bool = False
) -> dict[str, Any]:
    """Preview or submit a draft (docstatus 0) of a submittable DocType, to test its controller.

    Refuses DocTypes that are not submittable and DocTypes with an active Workflow (the
    workflow controls submission there). The preview returns `modified`; confirmation must
    pass it back as `expected_modified`. Submitting runs the controller's `validate`,
    `before_submit`, and `on_submit`.
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


@server.tool()
def cancel_document(
    doctype: str, name: str, expected_modified: str | None = None, confirm: bool = False
) -> dict[str, Any]:
    """Preview or cancel a submitted document (docstatus 1) of a submittable DocType.

    Refuses DocTypes with an active Workflow. The preview returns `modified`; confirmation
    must pass it back as `expected_modified`. Cancelling runs the controller's
    `before_cancel` and `on_cancel`, and Frappe refuses it while submitted documents link to
    this one.
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


@server.tool()
def run_report(
    report_name: str, filters: dict[str, Any] | None = None, limit: int = 200
) -> dict[str, Any]:
    """Run an enabled report using Frappe's permission-checked query-report API."""
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    result = _request(
        "GET",
        "/api/method/frappe.desk.query_report.run",
        params={
            "report_name": report_name,
            "filters": json.dumps(filters or {}),
            "ignore_prepared_report": True,
        },
    )
    rows = result.get("result", [])
    result["returned_rows"] = min(limit, len(rows))
    result["truncated"] = len(rows) > limit
    result["result"] = rows[:limit]
    return result


@server.tool()
def create_doctype(
    app: str,
    module: str,
    name: str,
    fields: list[dict[str, Any]],
    is_submittable: bool = False,
    is_child_table: bool = False,
    is_single: bool = False,
    is_tree: bool = False,
    is_custom: bool = False,
    properties: dict[str, Any] | None = None,
    permissions: list[dict[str, Any]] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or create a standard or site-custom DocType through Frappe.

    ``fields`` accepts the native DocField properties and field types. ``properties``
    carries supported DocType-level settings such as autoname, tracking, sorting,
    and search configuration. Standard DocTypes require a local app/module checkout
    and Developer Mode so Frappe can write JSON, Python, JavaScript, and test files.
    Site-custom DocTypes are stored in site metadata and do not generate app files.
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
        return {
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
    return {
        "created": True,
        "doctype": name,
        "app": app,
        "files": {key: str(path) for key, path in {
            "json": destination,
            "python": python_file,
            "javascript": javascript_file,
            "test": test_file,
        }.items()},
        "file_status": file_status,
        "native_files_complete": all(value is not False for value in file_status.values()),
        "reconciled_after_server_reload": reconciled,
        "app_path": str(app_path),
    }


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
            f"{doctype} is a standard DocType of {app}; change it with create_custom_field "
            "and set_field_property instead"
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
            f"{where}: a fieldname cannot be changed; renaming fields is not supported"
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


def _check_field_row(row: dict[str, Any], where: str, values: dict[str, Any]) -> None:
    fieldtype = row.get("fieldtype")
    if fieldtype not in _FIELDTYPES:
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
    doc: dict[str, Any], operations: Any, taken: set[str]
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
                operation["field"], f"{where}.field", _DOCFIELD_PROPERTIES | {"fieldname"}
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
            _check_field_row(row, where, values)
            position = (
                _insert_position(rows, operation["insert_after"], where)
                if "insert_after" in operation
                else len(rows)
            )
            rows.insert(position, row)
            touched.setdefault(fieldname, set()).update(values)
        elif op == "update_field":
            row = rows[_field_index(rows, operation["fieldname"], where)]
            values = _field_values(operation["set"], f"{where}.set", _DOCFIELD_PROPERTIES)
            row.update(values)
            _check_field_row(row, where, values)
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
                set(values) - (_DOCTYPE_PROPERTIES - _DOCTYPE_TABLE_PROPERTIES)
            )
            if unsupported:
                raise ValueError(
                    f"{where}: unsupported DocType properties: {', '.join(unsupported)}"
                )
            for key, value in values.items():
                if value is not None and not isinstance(value, str | int | float):
                    raise ValueError(
                        f"{where}: {key} must be a string, number, boolean, or null"
                    )
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
                {"options": row.get("options")}
                if row.get("fieldtype") in _TABLE_FIELDTYPES
                else {}
            ),
        }
        for name, row in before.items()
        if name not in after
    ]
    moved = [
        {"fieldname": name, "insert_after": previous(new_rows, name)}
        for name in dict.fromkeys(plan["moved"])
        if name in before
        and name in after
        and previous(new_rows, name) != previous(old_rows, name)
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
        if old and new and (old.get("fieldtype"), old.get("options")) == (
            new.get("fieldtype"), new.get("options")
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


def _doctype_matches(
    current: dict[str, Any], plan: dict[str, Any], expected_modified: str
) -> bool:
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


@server.tool()
def update_doctype(
    doctype: str,
    operations: list[dict[str, Any]],
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or apply a list of changes to an existing DocType's fields and settings.

    For DocTypes of your own apps and site-custom DocTypes; for Frappe and ERPNext DocTypes use
    `create_custom_field` and `set_field_property`. Operations run in order on the current
    DocType, and the result is saved in one request, so unchanged fields keep their rows:

    - `{"op": "add_field", "field": {"fieldname": ..., "fieldtype": ..., ...},
      "insert_after": "fieldname"}`; without `insert_after` the field goes last, and
      `null` puts it first.
    - `{"op": "update_field", "fieldname": ..., "set": {"label": ..., "reqd": 1, ...}}`;
      a fieldname cannot be changed.
    - `{"op": "remove_field", "fieldname": ...}`
    - `{"op": "move_field", "fieldname": ..., "insert_after": "fieldname" or null}`
    - `{"op": "set_properties", "set": {"track_changes": 1, ...}}` for DocType settings.

    Fields accept the DocField properties (`label`, `fieldtype`, `options`, `reqd`, `hidden`,
    `read_only`, `default`, `description`, `depends_on`, `in_list_view`, ...). Link and Table
    `options` must name an existing DocType; a Table's must be a child table. Changing a
    fieldtype is refused when it can lose data. The preview returns `changes` (added,
    removed, moved, changed, properties), `warnings` with affected record counts, and
    `modified`; confirm with the same operations and `expected_modified`. Saving a standard
    DocType needs developer mode, and Frappe then rewrites its JSON in the app.
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
    plan = _plan_doctype_update(doc, operations, custom_fields)
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
    return {
        "updated": True,
        "doctype": doctype,
        "modified": saved.get("modified") if isinstance(saved, dict) else None,
        "changes": changes,
        "warnings": warnings,
        "reconciled_after_server_reload": reconciled,
    }


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
        {"doctype": row.get("parent"), "fieldname": row.get("fieldname"),
         "fieldtype": row.get("fieldtype")}
        for row in standard
        if row.get("parent") != doctype
    ] + [
        {"doctype": row.get("dt"), "fieldname": row.get("fieldname"),
         "fieldtype": row.get("fieldtype"), "custom_field": True}
        for row in custom
        if row.get("dt") != doctype
    ]
    return sorted(links, key=lambda link: (str(link["doctype"]), str(link["fieldname"])))


@server.tool()
def delete_doctype(
    doctype: str, expected_modified: str | None = None, confirm: bool = False
) -> dict[str, Any]:
    """Preview or delete a DocType created by mistake during development.

    Refuses Frappe and ERPNext DocTypes, and any DocType another DocType uses through a Link,
    Table, or Table MultiSelect field (the error lists them). The preview says what Frappe
    removes and keeps: it deletes the DocType record and, for a standard DocType in developer
    mode, its folder in the app; it keeps the database table with its records, and the Custom
    Fields and Property Setters defined on the DocType. Confirm with `expected_modified` from
    the preview. Only Administrator can delete a standard DocType.
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
    try:
        _request("DELETE", _resource("DocType", doctype))
    except httpx.TransportError as error:
        # Deleting a standard DocType removes Python files, which can reload a dev Bench.
        if not _wait_for_document_deleted("DocType", doctype):
            raise RuntimeError(
                "Frappe disconnected during deletion and the DocType still exists; "
                "request a fresh preview before retrying."
            ) from error
        reconciled = True
    finally:
        _forget_meta(doctype)
    return {
        "deleted": True,
        "doctype": doctype,
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
    listed = list(dict.fromkeys(name for name in order if name in fields)) if isinstance(
        order, list
    ) else []
    field_order = listed + [name for name in fields if name not in listed]
    return {"properties": properties, "fields": fields, "field_order": field_order,
            "tables": tables}


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
    if not any(changes.values()):
        verdict = "in_sync"
    elif newer == "app":
        verdict = "app_ahead"
    elif newer == "site":
        verdict = "diverged" if file_changed else "site_ahead"
    else:
        verdict = "app_ahead" if file_changed else "diverged"
    state = {"truncated": False}
    return {
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


@server.tool()
def diff_doctype(doctype: str, app: str | None = None) -> dict[str, Any]:
    """Compare a DocType on the site with its files in a local app checkout (read-only).

    Reads `<app>/<app>/<module>/doctype/<doctype>/<doctype>.json` and every
    `<module>/custom/<doctype>.json` of the app under `FRAPPE_APPS_PATH`; nothing is written.
    `app` defaults to the app that owns the DocType's module; pass another app to check only
    its customizations of the DocType.

    `verdict` is one of `in_sync`, `site_ahead` (the site changed after the file; export it,
    e.g. by saving the DocType in developer mode), `app_ahead` (the file is newer; run
    `bench migrate`), `diverged` (both changed), `missing_in_app`, or `site_custom` (no files by
    design; see `promote_custom_doctype`). `doctype_file.changes` lists added, removed, moved,
    and changed fields, properties, and child tables, going from the site (`from`) to the app
    (`to`); `migrate_imports_file` says whether `bench migrate` would re-import the file.
    `customizations` compares Custom Fields and Property Setters with the custom files.
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


@server.tool()
def list_app_drift(
    app: str, module: str | None = None, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """List which DocTypes of an app differ between the site and the local checkout (read-only).

    Pairs every DocType folder under `<app>/<app>/<module>/doctype/` (in `FRAPPE_APPS_PATH`)
    with the site's DocTypes in the same modules, and gives each one a verdict: `in_sync`,
    `site_ahead`, `app_ahead`, `diverged` (as in `diff_doctype`), `missing_on_site` (run
    `bench migrate`), `missing_in_app`, or `site_custom`. Use `diff_doctype` for the details of
    one DocType. Results are sorted by module and folder; page with `offset` and `next_offset`.
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
    return {
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


@server.tool()
def promote_custom_doctype(
    doctype: str,
    app: str,
    module: str,
    confirm: bool = False,
    expected_modified: str | None = None,
) -> dict[str, Any]:
    """Preview or turn a site-custom DocType into a standard DocType of an app module.

    Use it to ship a DocType prototyped on the site (`custom=1`) with your app. Frappe does the
    export: saving the DocType with `custom=0` and the new `module` in developer mode makes it
    write `<module>/doctype/<doctype>/` (JSON, controller, JavaScript, and test files) into the
    app. Refuses Frappe and ERPNext, modules of other apps, custom modules, an existing
    DocType folder, and a DocType with a default print format. The preview lists the files
    and warnings (custom DocTypes it links to, customizations that stay on the site); confirm
    with the preview's `modified` as `expected_modified`. Records are kept as they are.
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


@server.tool()
def set_role_permissions(
    doctype: str,
    role: str,
    permissions: dict[str, bool],
    permlevel: int = 0,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or set DocType role permissions through Frappe's native manager."""
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
            _request("POST", f"{base_url}.add", json={
                "parent": doctype,
                "role": role,
                "permlevel": permlevel,
            })
        for flag, value in permissions.items():
            _request("POST", f"{base_url}.update", json={
                "doctype": doctype,
                "role": role,
                "permlevel": permlevel,
                "ptype": flag,
                "value": int(value),
                "if_owner": 0,
            })
    finally:
        _forget_meta(doctype)
    return {
        "updated": True,
        "doctype": doctype,
        "role": role,
        "permlevel": permlevel,
        "permissions": permissions,
    }


@server.tool()
def create_custom_field(
    doctype: str,
    field: dict[str, Any],
    app: str | None = None,
    module: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or create a Custom Field on a DocType through the standard Frappe REST API.

    `field` holds the Custom Field values (`fieldname`, `fieldtype`, `label`, `insert_after`,
    ...) and may not set `doctype`, `dt`, or `module`. `module` is optional and only tags the
    field for a later export; when given it must exist on the site, and when `app` is also
    given it must belong to that app. No local app checkout is needed. To edit or remove a
    Custom Field later, use `update_document` or `delete_document` on `Custom Field`.
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
        return _request("POST", _resource("Custom Field"), json={"data": data})
    finally:
        _forget_meta(doctype)


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


@server.tool()
def set_field_property(
    doctype: str,
    property: str,
    value: str | int | bool,
    fieldname: str | None = None,
    property_type: str | None = None,
    app: str | None = None,
    module: str | None = None,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or save a Customize Form value as a Property Setter.

    Sets `property` on `fieldname`, or on the DocType itself when `fieldname` is omitted.
    `property_type` is inferred from the property (Check, Data, or Text, as Customize Form
    uses); a value passed in must match. Check values must be 0/1 or booleans. `module` is
    optional, as for `create_custom_field`.

    If a Property Setter for this property already exists, the preview has `mode: "update"`,
    the current value, and its `modified`; confirmation must pass that as `expected_modified`
    and updates the setter in place. Otherwise the preview has `mode: "create"`. A new
    DocType-level setter makes Frappe delete the field-level setters for the same property;
    the preview lists them in `replaces_field_setters`.
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
        return _save_property_setter(preview, existing, expected_modified)
    finally:
        _forget_meta(doctype)


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


@server.tool()
def export_customizations(
    app: str,
    module: str,
    doctypes: list[str],
    with_permissions: bool = False,
    apply_module_filter: bool = False,
    sync_on_migrate: bool = True,
    with_workflows: bool = False,
    confirm: bool = False,
    expected_preview: str | None = None,
) -> dict[str, Any]:
    """Preview or export site customizations into an app's files so `bench migrate` applies them.

    Writes Frappe's native module customization file, the same one Customize Form's "Export
    Customizations" writes: `<app>/<app>/<module>/custom/<doctype>.json` with the DocType's
    Custom Fields, Property Setters, DocType Links, and (with `with_permissions`) Custom
    DocPerms. Child-table DocTypes the DocType uses get their own file. A DocType with nothing
    to export gets no file. Client Scripts for these DocTypes and `module` go to the app-level
    `<app>/<app>/fixtures/client_script.json`, and a managed block in `hooks.py` keeps them in
    the app's `fixtures`; `hooks.py` is untouched when there are none. With `with_workflows`,
    the Workflows of `doctypes` and the Workflow State and Workflow Action Master records they
    use go to `fixtures/workflow.json`, `workflow_state.json`, and `workflow_action_master.json`,
    listed by name in the same managed block.

    DocType JSON is never written: in developer mode Frappe writes standard DocType files itself.

    Args:
        app: App to write into (not frappe or erpnext); must be below FRAPPE_APPS_PATH.
        module: Module of `app` whose `custom/` folder receives the files.
        doctypes: 1-50 DocTypes whose customizations to export; any DocType, standard or not.
        with_permissions: Also export Custom DocPerm rows. On migrate they replace the site's
            Custom DocPerms for that DocType.
        apply_module_filter: Export only Custom Fields and Property Setters whose module is
            `module`, as Frappe's "Apply Module Export Filter" does.
        sync_on_migrate: Have `bench migrate` import the file; Frappe's own default is off.
        with_workflows: Also export the Workflows of `doctypes` as fixtures. `bench migrate`
            imports fixtures on every run and overwrites the site's copies.
        confirm: False returns a preview with diffs; True writes the files.
        expected_preview: The preview's `preview_id`, required when confirm is True.
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
    backup_root = _safe_app_file(app_path, app_path / ".mcp-frappe-backups")
    backup_root.mkdir(parents=True, exist_ok=True)
    rollback_id = f"export-{preview_id[:32]}.json"
    manifest: list[dict[str, Any]] = []
    app_root = app_path.resolve()
    for path, content in payloads:
        path = _safe_app_file(app_path, path)
        backup_name = None
        if path.exists():
            backup_name = f"{uuid.uuid4().hex}.bak"
            _atomic_write(
                backup_root / backup_name,
                path.read_text(encoding="utf-8"),
                mode=0o600,
            )
        manifest.append(
            {
                "path": str(path.relative_to(app_root)),
                "backup": backup_name,
                "exported_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "created_dirs": _created_dirs(app_root, path),
            }
        )
    manifest_path = backup_root / rollback_id
    _atomic_write(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )
    for path, content in payloads:
        _atomic_write(_safe_app_file(app_path, path), content)
    return {
        "exported": True,
        **summary,
        "files": [str(path) for path, _ in payloads],
        "rollback_id": rollback_id,
    }


@server.tool()
def rollback_export(
    app: str,
    module: str,
    rollback_id: str,
    expected_preview: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or restore files from an export manifest without overwriting later edits.

    Restores overwritten files from their backups, deletes files the export created, and
    removes directories it created once they are empty. Refuses with `conflict` when an
    exported file changed after the export.
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
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        current_hash = hashlib.sha256(current.encode("utf-8")).hexdigest() if current else None
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
    expected_modified: str | None = None,
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


@server.tool()
def get_workflow(doctype: str) -> dict[str, Any]:
    """Return a DocType's active Workflow: states, transitions, and its state field.

    `workflow` is null when the DocType has no active Workflow; `inactive` names its other
    Workflows. A transition guarded by a Python condition set outside this server shows
    `has_condition`; this server never writes conditions.
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


def _workflow_transitions(
    transitions: Any, states: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if (
        not isinstance(transitions, list)
        or not 1 <= len(transitions) <= _WORKFLOW_MAX_TRANSITIONS
    ):
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


@server.tool()
def upsert_workflow(
    doctype: str,
    name: str,
    states: list[dict[str, Any]],
    transitions: list[dict[str, Any]],
    is_active: bool = True,
    send_email_alert: bool = False,
    workflow_state_field: str = "workflow_state",
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or create/replace an approval Workflow for a DocType.

    Args:
        doctype: The DocType the Workflow controls (not a child table or Single).
        name: Workflow name; an existing Workflow with this name is replaced.
        states: In order; the first is where new documents start. Each is
            `{"state": "Pending Approval", "doc_status": 0, "allow_edit": "Role",
            "update_field": "fieldname", "update_value": "value"}` (the last two optional).
            `doc_status` is 0 draft, 1 submitted, 2 cancelled; 1 and 2 need a submittable
            DocType. `allow_edit` is the Role that may edit a document in that state.
        transitions: Each is `{"state": ..., "action": "Approve", "next_state": ...,
            "allowed": "Role", "allow_self_approval": true}`; both states must be listed.
            Python `condition`s are refused.
        is_active: Make this the DocType's active Workflow; Frappe deactivates the others.
        send_email_alert: Email the users who can act on the next state.
        workflow_state_field: Field holding the state; Frappe adds a hidden Custom Field
            (a Link to Workflow State) when the DocType has none.
        expected_modified: The preview's `modified`, required to confirm a replacement.
        confirm: False returns a preview; True saves.

    The preview lists the Workflow State and Workflow Action Master records it will create,
    the state field, the Workflows it deactivates, warnings (unreachable states are refused,
    draft states with no way out are warned about), and on replacement a states and
    transitions diff. Roles must exist.
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
            f"No transition path from {state_rows[0]['state']!r} reaches: "
            f"{', '.join(unreachable)}"
        )
    _assert_roles(
        [row["allow_edit"] for row in state_rows] + [row["allowed"] for row in transition_rows]
    )
    current = _config_record("Workflow", name)
    if current and current.get("document_type") != doctype:
        raise ValueError(
            f"Workflow {name} belongs to {current.get('document_type')}; choose another name"
        )
    if confirm and current and (
        not expected_modified or str(current.get("modified")) != expected_modified
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


@server.tool()
def list_notifications(document_type: str | None = None, limit: int = 50) -> dict[str, Any]:
    """List Notification rules: name, DocType, event, channel, enabled, and standard module."""
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


@server.tool()
def upsert_notification(
    name: str,
    document_type: str,
    event: Literal[
        "New", "Save", "Submit", "Cancel", "Value Change", "Days Before", "Days After"
    ],
    channel: Literal["Email", "System Notification"],
    recipients: list[dict[str, str]],
    subject: str,
    message: str,
    value_changed: str | None = None,
    date_changed: str | None = None,
    days_in_advance: int | None = None,
    filters: dict[str, Any] | list[list[Any]] | None = None,
    enabled: bool = True,
    is_standard: bool = False,
    module: str | None = None,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or create/replace a Notification rule (email or in-app alert) for a DocType.

    Args:
        name: Notification name; an existing one with this name is replaced.
        document_type: The DocType whose documents trigger it.
        event: New, Save, Submit, Cancel (Submit and Cancel need a submittable DocType),
            Value Change (with `value_changed`), Days Before / Days After (with
            `date_changed` and `days_in_advance`).
        channel: Email or System Notification (the bell in Desk).
        recipients: 1-20 entries, each with any of `receiver_by_document_field` (owner, a
            Data field with options Email, or a Link to User), `receiver_by_role` (a Role),
            and for Email `cc` / `bcc` (plain addresses, comma separated).
        subject: The email subject or alert title, at most 140 characters.
        message: The email body or alert text (Markdown or HTML).
        value_changed: For Value Change, the field whose change triggers it.
        date_changed: For Days Before/After, a Date or Datetime field.
        days_in_advance: For Days Before/After, a positive number of days.
        filters: Send only when the document matches, e.g. `{"status": "Approved"}` or
            `[["grand_total", ">", 1000]]`; fields of `document_type` only. Python
            conditions are refused.
        enabled: Turn the rule on.
        is_standard: Ship it with an app: Frappe writes it to the module's `notification/`
            folder in developer mode. Needs `module`, of an app other than frappe/erpnext.
        module: The Module Def of a standard Notification.
        expected_modified: The preview's `modified`, required to confirm a replacement.
        confirm: False returns a preview; True saves.

    `subject` and `message` are plain text with `{{ doc.<fieldname> }}` placeholders, where
    the field must exist on the DocType. A placeholder may end with filters from: upper,
    lower, title, capitalize, trim, striptags, escape, int, str, flt(precision),
    round(precision, "common"|"ceil"|"floor"), truncate(length), default("text"). Any other
    Jinja ({% %}, calls, other variables) is refused.
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
        if isinstance(days_in_advance, bool) or not isinstance(days_in_advance, int) or (
            days_in_advance < 1
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
    if confirm and current and (
        not expected_modified or str(current.get("modified")) != expected_modified
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


@server.tool()
def upsert_report(
    name: str,
    ref_doctype: str,
    columns: list[str],
    filters: dict[str, Any] | list[list[Any]] | None = None,
    order_by: str | None = None,
    group_by: dict[str, str] | None = None,
    add_totals_row: bool = False,
    is_standard: bool = False,
    module: str | None = None,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or create/replace a saved Report Builder report on a DocType.

    Args:
        name: Report name; an existing Report Builder report with this name is replaced.
        ref_doctype: The DocType the report lists (not a child table or Single).
        columns: 1-50 columns, in order: fieldnames of `ref_doctype` or standard columns
            (name, owner, creation, modified, modified_by, docstatus, idx), or
            `<child DocType>.<fieldname>` for a field of one of its child tables.
        filters: Saved filters, same shapes and operators as `list_documents`.
        order_by: `<fieldname> [asc|desc]`, up to 3 joined by commas; fields of `ref_doctype`.
        group_by: `{"field": <fieldname>, "aggregate_function": "count"|"sum"|"avg",
            "aggregate_on": <numeric fieldname, for sum/avg>}`. With it, `columns` must be
            exactly `[field]` and `order_by` is not allowed (Frappe sorts by the aggregate).
        add_totals_row: Add Frappe's totals row.
        is_standard: Ship it with an app: Frappe writes it to the module's `report/` folder
            in developer mode (Administrator only). Needs `module`, of an app other than
            frappe/erpnext.
        module: The Module Def of a standard report.
        expected_modified: The preview's `modified`, required to confirm a replacement.
        confirm: False returns a preview; True saves and returns a few sample rows.

    Query Reports and Script Reports run SQL or Python, so this tool never creates or edits
    them.
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
            raise ValueError("order_by cannot be combined with group_by; Frappe sorts by the "
                             "aggregate")
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
    if confirm and current and (
        not expected_modified or str(current.get("modified")) != expected_modified
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
    return [block for block in blocks if isinstance(block, dict)] if isinstance(
        blocks, list
    ) else []


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


@server.tool()
def get_workspace(name: str) -> dict[str, Any]:
    """Return a Workspace: module, visibility, shortcuts, link cards, number cards, and layout.

    `layout` lists the blocks in the order Desk shows them (spacers left out);
    `unmanaged_blocks` names block types (charts, quick lists, custom HTML blocks,
    onboarding) that `upsert_workspace` cannot rebuild.
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
                    {"label": row.get("label"), "type": row.get("link_type"),
                     "link_to": row.get("link_to")}
                    for row in card["links"]
                ],
            }
            for card in _workspace_cards(doc.get("links") or [])
        ],
        "number_cards": [
            row.get("number_card_name") for row in doc.get("number_cards") or []
        ],
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
        raise ValueError(f"{where}: label must be non-empty text of at most "
                         f"{_WORKSPACE_LABEL_MAX} characters")
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


def _workspace_content(
    number_cards: list[str], shortcuts: list[str], cards: list[str]
) -> str:
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
            blocks.append(
                {"id": _block_id("spacer", kind), "type": "spacer", "data": {"col": 12}}
            )
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
            label
            for index, label in enumerate(kept_after)
            if kept_before.index(label) != index
        ],
    }


@server.tool()
def upsert_workspace(
    name: str,
    module: str,
    shortcuts: list[dict[str, str]],
    link_cards: list[dict[str, Any]] | None = None,
    number_cards: list[str] | None = None,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or create/replace a public, standard Workspace that ships with an app module.

    Args:
        name: Workspace name, also its label and title (the sidebar entry).
        module: Its Module Def, of an app other than frappe/erpnext. Frappe writes the
            Workspace to `<module>/workspace/<name>/` in developer mode.
        shortcuts: 0-50 tiles, in order: `{"label", "type": "DocType"|"Report"|"Page",
            "link_to", "color"?, "doc_view"?}`. `link_to` must exist; `doc_view` (List,
            Report Builder, Dashboard, Tree, New, Calendar, Image) applies to DocTypes only.
        link_cards: 0-50 cards, in order: `{"label", "links": [{"label", "type", "link_to"}]}`
            with 1-50 links each, same types and checks as shortcuts.
        number_cards: Names of existing Number Card records, in order.
        expected_modified: The preview's `modified`, required to confirm a replacement.
        confirm: False returns a preview; True saves.

    The tool builds the Workspace layout itself: number cards, then shortcuts, then link
    cards. URL shortcuts, custom HTML blocks, charts, and private Workspaces are refused.
    """
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name):
        raise ValueError("Invalid Workspace name")
    link_cards = link_cards or []
    number_cards = number_cards or []
    for what, items in (("shortcuts", shortcuts), ("link_cards", link_cards),
                        ("number_cards", number_cards)):
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
                _workspace_item(link, f"link_cards[{index}].links[{position}]",
                                _WORKSPACE_LINK_KEYS)
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
    if confirm and current and (
        not expected_modified or str(current.get("modified")) != expected_modified
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
                        link["type"] == "Report"
                        and target.get("report_type") != "Report Builder"
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
                "Frappe writes a public Workspace to its module only in developer mode, and "
                "saving one needs the Workspace Manager role. A developer-mode Bench may reload.",
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


def main() -> None:
    """Run the standalone server using the MCP stdio transport."""
    try:
        _timeout()
        _meta_ttl()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
    if _insecure_http_allowed():
        _log.warning(
            "FRAPPE_ALLOW_INSECURE_HTTP=YES: credentials may be sent over plain HTTP to "
            "non-local hosts; use this only on a trusted development network"
        )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
