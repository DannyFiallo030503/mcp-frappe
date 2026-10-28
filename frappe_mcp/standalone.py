"""Standalone stdio MCP server that talks to Frappe through its public HTTP API."""

from __future__ import annotations

import ast
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
from typing import Any
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


def _doctype_raw(doctype: str) -> dict[str, Any]:
    if not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")
    result = _request("GET", _resource("DocType", doctype))
    if not isinstance(result, dict):
        raise RuntimeError("Unexpected DocType response")
    return result


def _sort_merged_fields(schema: dict[str, Any]) -> None:
    """Apply Frappe's field_order and Custom Field insert_after rules to metadata."""
    fields = schema.get("fields", [])
    if not fields:
        return
    by_name = {row.get("fieldname"): row for row in fields if row.get("fieldname")}
    if not by_name:
        return

    explicit_order = schema.get("field_order")
    if isinstance(explicit_order, str):
        try:
            explicit_order = json.loads(explicit_order)
        except json.JSONDecodeError:
            explicit_order = []
    order = [name for name in explicit_order or [] if name in by_name]
    if not order:
        order = [
            row["fieldname"] for row in fields
            if row.get("fieldname") and not row.get("is_custom_field")
        ]

    insert_after: dict[str, list[str]] = {}
    for row in fields:
        name = row.get("fieldname")
        if not name or name in order or not row.get("is_custom_field"):
            continue
        anchor = row.get("insert_after")
        if anchor:
            insert_after.setdefault(str(anchor), []).append(name)
        else:
            order.insert(0, name)

    # Frappe resolves chains such as a custom field inserted after another
    # custom field in repeated passes, preserving each anchor's creation order.
    while True:
        changed = False
        for anchor in list(insert_after):
            if anchor not in order:
                continue
            index = order.index(anchor) + 1
            for name in insert_after.pop(anchor):
                order.insert(index, name)
                index += 1
            changed = True
        if not changed:
            break
    for remaining in insert_after.values():
        order.extend(name for name in remaining if name not in order)

    order.extend(name for name in by_name if name not in order)
    schema["fields"] = [by_name[name] for name in order]
    for index, row in enumerate(schema["fields"], 1):
        row["idx"] = index


def _doctype(doctype: str) -> dict[str, Any]:
    """Return the base DocType merged with site-level Custom Fields and Property Setters."""
    schema = _doctype_raw(doctype)
    fields = schema.setdefault("fields", [])
    known = {row.get("fieldname") for row in fields if row.get("fieldname")}
    custom_fields = _request(
        "GET",
        _resource("Custom Field"),
        params={
            "fields": '["*"]',
            "filters": _filters({"dt": doctype}),
            "limit_page_length": 500,
            "order_by": "idx asc",
        },
    )
    for custom_field in custom_fields:
        fieldname = custom_field.get("fieldname")
        if fieldname and fieldname not in known:
            custom_field["is_custom_field"] = 1
            fields.append(dict(custom_field))
            known.add(fieldname)

    setters = _request(
        "GET",
        _resource("Property Setter"),
        params={
            "fields": '["field_name","property","value","property_type","doctype_or_field"]',
            "filters": _filters({"doc_type": doctype}),
            "limit_page_length": 500,
            "order_by": "creation asc",
        },
    )
    by_field = {row.get("fieldname"): row for row in fields if row.get("fieldname")}
    for setter in setters:
        key = setter.get("property")
        if not key:
            continue
        value = setter.get("value")
        kind = setter.get("property_type")
        try:
            if kind == "Check":
                value = bool(int(value))
            elif kind == "Int":
                value = int(value)
            elif kind in {"Float", "Currency", "Percent"}:
                value = float(value)
        except (TypeError, ValueError):
            continue
        fieldname = setter.get("field_name")
        target = by_field.get(fieldname) if fieldname else schema
        if target is not None:
            target[key] = value
    _sort_merged_fields(schema)
    return schema


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


def _assert_filters(schema: dict[str, Any], filters: dict[str, Any] | list[Any] | None) -> None:
    """Check every Frappe filter shape against the DocType and its child tables.

    Accepted: `{field: value}`, `{field: [operator, value]}`, and a list of
    `[field, operator, value]` or `[doctype, field, operator, value]` conditions, where
    `doctype` is this DocType or the `options` of one of its Table fields.
    """
    if not filters:
        return
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
    tables = {
        row.get("options")
        for row in schema.get("fields", [])
        if row.get("fieldtype") in _TABLE_FIELDTYPES and row.get("options")
    }
    child_schemas: dict[str, dict[str, Any]] = {}
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
            elif target in tables:
                if target not in child_schemas:
                    child_schemas[target] = _doctype(target)
                _assert_readable_fields(child_schemas[target], [field], child_table=True)
            else:
                choices = ", ".join(sorted(tables)) or "none"
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
        children = [
            row.get("options")
            for row in _doctype(doctype).get("fields", [])
            if row.get("fieldtype") in _TABLE_FIELDTYPES and row.get("options")
        ]
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


@server.tool()
def get_doctype_schema(doctype: str) -> dict[str, Any]:
    """Read the DocType schema and field metadata visible through Frappe's API."""
    return _doctype(doctype)


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
    schema = _doctype(doctype)
    requested = fields or ["name"]
    if not isinstance(requested, list) or len(requested) > 40:
        raise ValueError("fields must be a list of no more than 40 field names")
    _assert_readable_fields(schema, requested)
    _assert_filters(schema, filters)
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

    `fields` may name the DocType's fields and the standard columns (`owner`, `creation`,
    `modified`, `modified_by`, `docstatus`, `idx`); `name`, `modified`, and `docstatus` are
    always returned.
    """
    if not 1 <= child_row_limit <= 200:
        raise ValueError("child_row_limit must be between 1 and 200")
    schema = _doctype(doctype)
    requested = set(fields or _schema_fields(schema))
    requested.add("name")
    _assert_readable_fields(schema, requested)
    doc = _request("GET", _resource(doctype, name))
    result = {
        key: value
        for key, value in doc.items()
        if key in requested or key in {"doctype", "name", "modified", "docstatus"}
    }
    truncated = []
    child_tables = {
        row.get("fieldname") for row in schema.get("fields", []) if row.get("fieldtype") == "Table"
    }
    for field in child_tables & requested:
        rows = result.get(field)
        if isinstance(rows, list) and len(rows) > child_row_limit:
            result[field] = rows[:child_row_limit]
            truncated.append(field)
    if truncated:
        result["_truncated_child_tables"] = sorted(truncated)
    return result


@server.tool()
def create_document(doctype: str, values: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
    """Preview or create a document via the native REST resource API.

    `values` may include `name` only when the DocType's autoname is `Prompt` (naming rule
    "Set by user"); otherwise Frappe names the record itself.
    """
    schema = _doctype(doctype)
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
    return _request("POST", _resource(doctype), json={"data": {"doctype": doctype, **data}})


@server.tool()
def update_document(
    doctype: str,
    name: str,
    values: dict[str, Any],
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or update a record; confirmation requires the preview's modified token."""
    _assert_writable_fields(_doctype(doctype), values)
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

    schema = _doctype_raw(doctype)
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
    schema = _doctype(doctype)
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
    return _request("POST", _resource("Custom Field"), json={"data": data})


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
    schema = _doctype(doctype)
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
