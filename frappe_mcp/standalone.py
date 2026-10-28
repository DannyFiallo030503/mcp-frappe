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
_EXPORT_MAX_DOCTYPES = 50
_EXPORT_MAX_FILES = 200
_EXPORT_ROW_LIMIT = 1000
# Keys Frappe's `export_json` (`bench export-fixtures`) leaves out of fixture rows.
_FIXTURE_DROPPED_KEYS = frozenset({"modified_by", "creation", "owner", "idx", "lft", "rgt"})
_PROTECTED_APPS = {"frappe", "erpnext"}
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
    """Return `{"doctype": meta, "children": {child_doctype: meta}}` from Frappe's form loader.

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
    main, *children = (
        {key: value for key, value in doc.items() if not key.startswith("__")} for doc in docs
    )
    bundle = {"doctype": main, "children": {child.get("name"): child for child in children}}
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
    if doctype in _SCHEMA_DOCTYPES:
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


def _expected_modified(doctype: str, name: str, expected: str | None) -> dict[str, Any]:
    doc = _request("GET", _resource(doctype, name))
    if not expected or str(doc.get("modified")) != expected:
        raise ValueError(_STALE_DOCUMENT)
    return doc


def _app_path(app: str, module: str) -> tuple[Path, Path]:
    root_value = os.environ.get("FRAPPE_APPS_PATH")
    if not root_value:
        raise RuntimeError("Set FRAPPE_APPS_PATH to the local Bench apps directory for exports")
    if not _SAFE_NAME.fullmatch(app) or not _SAFE_NAME.fullmatch(module):
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
    try:
        module_path = (package_path / _slug(module)).resolve(strict=True)
    except FileNotFoundError as error:
        raise ValueError("Selected module directory does not exist") from error
    if not module_path.is_relative_to(package_path):
        raise ValueError("App or module path escapes FRAPPE_APPS_PATH")
    if not app_path.is_dir() or not module_path.is_dir() or not os.access(module_path, os.W_OK):
        raise ValueError("App module must exist and be writable")
    return app_path, module_path


def _assert_app_module(app: str, module: str) -> tuple[Path, Path]:
    app_path, module_path = _app_path(app, module)
    _assert_remote_module(app, module)
    return app_path, module_path


def _assert_remote_module(app: str | None, module: str) -> None:
    """Check the Module Def exists on the site and, when `app` is given, belongs to it."""
    if not _SAFE_NAME.fullmatch(module) or (app is not None and not _SAFE_NAME.fullmatch(app)):
        raise ValueError("Invalid app or module name")
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
    if app is not None and rows[0].get("app_name") != app:
        raise ValueError(f"Module {module} does not belong to app {app} on the target site")


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
    controller_slug = _slug(name)
    files = {
        doctype_path / f"{controller_slug}.py": templates / "controller._py",
    }
    if not is_child_table:
        files[doctype_path / f"test_{controller_slug}.py"] = templates / "test_controller._py"
        files[doctype_path / f"{controller_slug}.js"] = templates / "controller.js"
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


def _slug(value: str) -> str:
    return re.sub(r"[^\w]+", "_", value.lower(), flags=re.UNICODE).strip("_")


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


def _fixture_plan(app_path: Path, doctypes: list[str], module: str) -> tuple[Path, str]:
    """Plan the managed `hooks.py` block that keeps exported Client Scripts in `fixtures`.

    Only Client Script needs a hook entry: Custom Fields, Property Setters, and permissions
    go to the module's native `custom/` file, which `bench migrate` reads without one. Entries
    for other DocTypes left by older exports are dropped from the block.
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

    previous = next(
        (row for row in existing if isinstance(row, dict) and row.get("dt") == "Client Script"),
        None,
    )
    filters = list(previous.get("filters", [])) if previous else []
    for key, incoming in (("dt", doctypes), ("module", [module])):
        current = next(
            (condition for condition in filters if len(condition) == 3 and condition[0] == key),
            None,
        )
        if current and current[1] == "in":
            current[2] = sorted(set(current[2]) | set(incoming))
        else:
            filters.append([key, "in", sorted(incoming)])
    managed = [{"dt": "Client Script", "filters": filters}]

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
    exported = [
        {
            **{
                key: value
                for key, value in row.items()
                if key not in _FIXTURE_DROPPED_KEYS and not key.startswith("_")
            },
            "doctype": "Client Script",
        }
        for row in rows
    ]
    path = app_path / app_path.name / "fixtures" / "client_script.json"
    kept: list[dict[str, Any]] = []
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path} is not valid JSON; fix it before exporting") from error
        if not isinstance(current, list) or any(not isinstance(row, dict) for row in current):
            raise ValueError(f"{path} is not a list of fixture rows; fix it before exporting")
        names = {row.get("name") for row in exported}
        kept = [
            row
            for row in current
            if row.get("name") not in names
            and not (row.get("dt") in doctypes and row.get("module") == module)
        ]
    return path, _frappe_json(kept + exported, ensure_ascii=False)


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


@server.tool()
def list_doctypes(
    app: str | None = None,
    module: str | None = None,
    include_child_tables: bool = True,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """List DocTypes on the site, optionally filtered by app/module."""
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
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
            return []
        filters = [["module", "in", module_names]]
    if module:
        filters.append(["module", "=", module])
    if not include_child_tables:
        filters.append(["istable", "=", 0])
    rows = _request(
        "GET",
        _resource("DocType"),
        params={
            "fields": '["name","module","custom","issingle","istable"]',
            "filters": _filters(filters),
            "limit_page_length": limit,
            "order_by": "module asc,name asc",
        },
    )
    return rows


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
    the same form. `full` returns every metadata property as `{"doctype": ..., "children":
    {child_doctype: ...}}`; use it only when editing a DocType needs every property.
    """
    if detail not in ("compact", "full"):
        raise ValueError('detail must be "compact" or "full"')
    bundle = _meta_bundle(doctype)
    if detail == "full":
        return bundle
    meta = bundle["doctype"]
    compact = {key: meta[key] for key in _COMPACT_DOCTYPE_KEYS if meta.get(key)}
    compact["fields"] = _compact_fields(meta)
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
) -> list[dict[str, Any]]:
    """List permission-filtered records and fields through the standard Frappe REST API.

    `fields` and `filters` may use the DocType's fields plus the standard columns `name`,
    `owner`, `creation`, `modified`, `modified_by`, `docstatus`, and `idx`. `filters` is
    either an object (`{"status": "Open"}`, `{"grand_total": [">", 100]}`) or a list of
    `[field, operator, value]` / `[child_doctype, field, operator, value]` conditions, where
    `child_doctype` is the DocType of one of this DocType's Table fields. Operators: =, !=,
    <, >, <=, >=, like, not like, in, not in, between, is, timespan.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    bundle = _meta_bundle(doctype)
    requested = fields or ["name"]
    if not isinstance(requested, list) or len(requested) > 40:
        raise ValueError("fields must be a list of no more than 40 field names")
    _assert_readable_fields(bundle["doctype"], requested)
    _assert_filters(bundle, filters)
    return _request(
        "GET",
        _resource(doctype),
        params={
            "fields": json.dumps(requested),
            "filters": _filters(filters),
            "limit_page_length": limit,
            "order_by": "modified desc",
        },
    )


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
        slug = _slug(name)
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
    slug = _slug(doctype)
    try:
        _, module_path = _app_path(app, module)
    except (RuntimeError, ValueError):
        folder = f"{app}/{app}/{_slug(module)}/doctype/{slug}"
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
        count = _request(
            "GET",
            "/api/method/frappe.client.get_count",
            params={"doctype": doctype, "filters": _filters(filters)},
        )
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
    the app's `fixtures`; `hooks.py` is untouched when there are none.

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
    client_scripts = _client_script_fixture(app_path, covered, module)
    if client_scripts:
        payloads.append(client_scripts)
        payloads.append(_fixture_plan(app_path, covered, module))
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
    summary = {"app": app, "module": module, "doctypes": covered, "empty": empty}
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
