"""Standalone stdio MCP server that talks to Frappe through its public HTTP API."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

logging.getLogger("httpx").setLevel(logging.WARNING)


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
_PROTECTED_APPS = {"frappe", "erpnext"}
_DEFAULT_LOCAL_URL = "http://127.0.0.1:8000"
_DEFAULT_LOCAL_SITE = "cubazone.localhost"


def _client() -> httpx.Client:
    global _session
    if _session is not None:
        return _session

    configured_url = os.environ.get("FRAPPE_URL") or None
    base_url = (configured_url or _DEFAULT_LOCAL_URL).rstrip("/")
    timeout = float(os.environ.get("FRAPPE_TIMEOUT", "30"))
    headers = {"Accept": "application/json"}
    site = os.environ.get("FRAPPE_SITE")
    if site is None and base_url == _DEFAULT_LOCAL_URL:
        site = _DEFAULT_LOCAL_SITE
    if site:
        headers["Host"] = site
    key, secret = os.environ.get("FRAPPE_API_KEY"), os.environ.get("FRAPPE_API_SECRET")
    if key or secret:
        if not key or not secret:
            raise RuntimeError("Set both FRAPPE_API_KEY and FRAPPE_API_SECRET")
        headers["Authorization"] = f"token {key}:{secret}"
        _session = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)
        return _session

    username = os.environ.get("FRAPPE_USERNAME", "Administrator")
    password = os.environ.get("FRAPPE_PASSWORD")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    local_target = parsed.hostname in local_hosts or bool(
        parsed.hostname and parsed.hostname.endswith(".localhost")
    )
    if not password and local_target:
        password = "admin"
    if parsed.scheme != "https" and not local_target:
        raise RuntimeError("Password login is allowed only over HTTPS or to localhost")
    if not password:
        raise RuntimeError("Set an API key/secret or FRAPPE_PASSWORD for remote sites")
    _session = httpx.Client(
        base_url=base_url, headers=headers, timeout=timeout, follow_redirects=False
    )
    response = _session.post("/api/method/login", data={"usr": username, "pwd": password})
    _check(response)
    return _session


def _check(response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if response.is_error:
        message = payload.get("_server_messages") if isinstance(payload, dict) else None
        if not message and isinstance(payload, dict):
            message = payload.get("exception") or payload.get("message")
        raise RuntimeError(f"Frappe HTTP {response.status_code}: {message or 'request failed'}")
    if isinstance(payload, dict) and payload.get("exc"):
        raise RuntimeError("Frappe returned an application error")
    if isinstance(payload, dict):
        if "data" in payload:
            return payload["data"]
        if "message" in payload:
            return payload["message"]
    return payload


def _request(method: str, path: str, **kwargs: Any) -> Any:
    return _check(_client().request(method, path, **kwargs))


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


def _doctype(doctype: str) -> dict[str, Any]:
    """Return metadata composed by Frappe's native Desk metadata endpoint."""
    if not _SAFE_NAME.fullmatch(doctype):
        raise ValueError("Invalid DocType name")
    result = _request(
        "GET",
        "/api/method/frappe.desk.form.load.getdoctype",
        params={"doctype": doctype},
    )
    docs = result.get("docs") if isinstance(result, dict) else None
    if not isinstance(docs, list) or not docs or not isinstance(docs[0], dict):
        raise RuntimeError("Frappe returned an unexpected DocType metadata response")
    return docs[0]


def _assert_fields(doctype: str, values: dict[str, Any], *, writing: bool = False) -> None:
    if not isinstance(values, dict) or not values:
        raise ValueError("values must be a non-empty object")
    schema = _doctype(doctype)
    allowed = {row.get("fieldname") for row in schema.get("fields", []) if row.get("fieldname")}
    for key in values:
        if key in _RESERVED_FIELDS and (writing or key != "name"):
            raise ValueError(f"Field cannot be used: {key}")
        if key != "name" and key not in allowed:
            raise ValueError(f"Field cannot be used: {key}")
        if writing and next(
            (f for f in schema.get("fields", []) if f.get("fieldname") == key), {}
        ).get("read_only"):
            raise ValueError(f"Read-only field cannot be written: {key}")


def _expected_modified(doctype: str, name: str, expected: str | None) -> dict[str, Any]:
    doc = _request("GET", _resource(doctype, name))
    if not expected or str(doc.get("modified")) != expected:
        raise ValueError("Document changed since preview; request a fresh preview")
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


def _assert_remote_module(app: str, module: str) -> None:
    if not _SAFE_NAME.fullmatch(app) or not _SAFE_NAME.fullmatch(module):
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
    if len(rows) != 1 or rows[0].get("app_name") != app:
        raise ValueError(f"Module {module} does not belong to app {app} on the target site")


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
            "after the request settled. Partial files were removed when it was safe; "
            "inspect the selected module before retrying."
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
    schema = _doctype(doctype)
    targets = [(doctype, module_path / "custom" / f"{_slug(doctype)}.json")]
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


@server.tool()
def export_form_customizations(
    app: str,
    module: str,
    doctype: str,
    sync_on_migrate: bool = True,
    with_permissions: bool = False,
    confirm: bool = False,
    expected_preview: str | None = None,
) -> dict[str, Any]:
    """Preview, then call Frappe's native Customize Form export method.

    Frappe generates the files on the site host. The preview fingerprints the
    current source records and lists expected paths; Frappe does not expose a
    dry-run form of this method, so the MCP does not fabricate a file diff.
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
        {key: item[key] for key in ("doctype", "path", "current_sha256")}
        for item in file_previews
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
    filters: dict[str, Any] | None = None,
    fields: list[str] | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List permission-filtered records and fields through the standard Frappe REST API."""
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    _doctype(doctype)
    requested = fields or ["name"]
    if len(requested) > 40:
        raise ValueError("Request no more than 40 fields")
    _assert_fields(doctype, {name: None for name in requested})
    _assert_fields(doctype, filters or {}) if filters else None
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
    """Read one document, selecting fields and bounding any returned child-table rows."""
    if not 1 <= child_row_limit <= 200:
        raise ValueError("child_row_limit must be between 1 and 200")
    schema = _doctype(doctype)
    requested = set(
        fields or [row.get("fieldname") for row in schema.get("fields", []) if row.get("fieldname")]
    )
    requested.add("name")
    _assert_fields(doctype, {field: None for field in requested})
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
    """Preview or create a document via the native REST resource API."""
    _assert_fields(doctype, values, writing=True)
    if not confirm:
        return {
            "preview": True,
            "action": "create_document",
            "doctype": doctype,
            "fields": sorted(values),
            "requires_confirmation": True,
        }
    return _request("POST", _resource(doctype), json={"data": {"doctype": doctype, **values}})


@server.tool()
def update_document(
    doctype: str,
    name: str,
    values: dict[str, Any],
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or update a record; confirmation requires the preview's modified token."""
    _assert_fields(doctype, values, writing=True)
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
    return _request("PUT", _resource(doctype, name), json={"data": values})


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
            "ignore_prepared_report": False,
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
    try:
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
    app: str, module: str, doctype: str, field: dict[str, Any], confirm: bool = False
) -> dict[str, Any]:
    """Preview or create a Custom Field through the standard Frappe REST API."""
    _assert_app_module(app, module)
    if not _SAFE_NAME.fullmatch(doctype) or not field.get("fieldname"):
        raise ValueError("A DocType and fieldname are required")
    schema = _doctype(doctype)
    existing = {row.get("fieldname") for row in schema.get("fields", [])}
    if field["fieldname"] in existing:
        raise ValueError("Field already exists")
    data = {"doctype": "Custom Field", "dt": doctype, "module": module, **field}
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


@server.tool()
def set_field_property(
    app: str,
    module: str,
    doctype: str,
    property: str,
    value: str,
    property_type: str,
    fieldname: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or save a Customize Form value as a Property Setter."""
    _assert_app_module(app, module)
    schema = _doctype(doctype)
    if fieldname and fieldname not in {row.get("fieldname") for row in schema.get("fields", [])}:
        raise ValueError("Unknown fieldname")
    allowed = {
        "label",
        "hidden",
        "read_only",
        "reqd",
        "default",
        "in_list_view",
        "in_standard_filter",
        "options",
        "description",
        "depends_on",
        "mandatory_depends_on",
        "read_only_depends_on",
        "collapsible",
        "bold",
        "width",
    }
    if property not in allowed:
        raise ValueError("Property is not in the supported Customize Form allowlist")
    data = {
        "doctype": "Property Setter",
        "doctype_or_field": "DocField" if fieldname else "DocType",
        "doc_type": doctype,
        "field_name": fieldname,
        "module": module,
        "property": property,
        "value": value,
        "property_type": property_type,
        "is_system_generated": 0,
    }
    if not confirm:
        return {
            "preview": True,
            "action": "set_field_property",
            "data": data,
            "requires_confirmation": True,
        }
    return _request("POST", _resource("Property Setter"), json={"data": data})


@server.tool()
def rollback_export(
    app: str,
    module: str,
    rollback_id: str,
    expected_preview: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or restore files from an export manifest without overwriting later edits."""
    app_path, _ = _assert_app_module(app, module)
    if not re.fullmatch(r"export-[a-f0-9]{32}\.json", rollback_id):
        raise ValueError("Invalid rollback ID")
    backup_root = _safe_app_file(app_path, app_path / ".mcp-frappe-backups")
    manifest_path = _safe_app_file(app_path, backup_root / rollback_id)
    if not manifest_path.is_file():
        raise ValueError("Export rollback manifest was not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list) or len(manifest) > 55:
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
    manifest_path.unlink()
    if backup_root.is_dir() and not any(backup_root.iterdir()):
        backup_root.rmdir()
    return {"rolled_back": True, "app": app, "rollback_id": rollback_id, "files": len(manifest)}


def main() -> None:
    """Run the standalone server using the MCP stdio transport."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
