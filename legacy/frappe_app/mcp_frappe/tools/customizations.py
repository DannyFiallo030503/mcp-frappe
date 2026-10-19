"""Schema and Customize Form operations backed by Frappe's native documents."""

import ast
import difflib
import hashlib
import importlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import frappe

from mcp_frappe.mcp import mcp

_BEGIN = "# BEGIN MCP FRAPPE MANAGED FIXTURES"
_END = "# END MCP FRAPPE MANAGED FIXTURES"


def _require_system_manager() -> None:
    if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
        frappe.throw("System Manager role is required for schema changes", frappe.PermissionError)


def _require_installed_app(app: str) -> Path:
    if app not in frappe.get_installed_apps():
        frappe.throw(f"App is not installed on this site: {app}", frappe.ValidationError)
    return Path(frappe.get_app_path(app)).resolve()


def _require_app_module(app: str, module: str) -> Path:
    app_path = _require_installed_app(app)
    module_app = frappe.db.get_value("Module Def", module, "app_name")
    if module_app != app:
        frappe.throw(f"Module {module} does not belong to app {app}", frappe.ValidationError)

    # Site module maps can be stale after app installation. Trust the installed
    # Module Def only after verifying ownership above, then refresh this entry
    # for Frappe's native get_module_path()/export controllers.
    if frappe.local.module_app is None:
        frappe.local.module_app = {}
    frappe.local.module_app[frappe.scrub(module)] = app

    module_path = (app_path / frappe.scrub(module)).resolve()
    if not module_path.is_relative_to(app_path) or not module_path.is_dir():
        frappe.throw(
            "Module path is outside the selected app or does not exist", frappe.ValidationError
        )
    if not os.access(module_path, os.W_OK):
        frappe.throw("Selected app module is not writable", frappe.PermissionError)
    return module_path


@mcp.tool()
def create_doctype(
    app: str,
    module: str,
    name: str,
    fields: list[dict],
    is_submittable: bool = False,
    is_child_table: bool = False,
    confirm: bool = False,
) -> dict:
    """Create a standard app DocType and export its JSON into the selected module."""
    _require_system_manager()
    module_path = _require_app_module(app, module)
    if not name.strip() or not fields:
        frappe.throw("DocType name and at least one field are required", frappe.ValidationError)
    if frappe.db.exists("DocType", name):
        frappe.throw(f"DocType already exists: {name}", frappe.ValidationError)

    filename = frappe.scrub(name) + ".json"
    destination = module_path / "doctype" / frappe.scrub(name) / filename
    if destination.exists():
        frappe.throw(
            f"Refusing to overwrite existing app file: {destination}", frappe.ValidationError
        )
    if not confirm:
        return {
            "preview": True,
            "action": "create_doctype",
            "app": app,
            "module": module,
            "doctype": name,
            "fields": len(fields),
            "child_table": is_child_table,
            "destination": str(destination),
            "requires_confirmation": True,
        }

    doc = frappe.get_doc(
        {
            "doctype": "DocType",
            "name": name,
            "module": module,
            "custom": 0,
            "istable": int(is_child_table),
            "is_submittable": int(is_submittable),
            "fields": fields,
        }
    )
    doc.insert()
    doc.export_doc()
    return {"created": True, "doctype": name, "app": app, "file": str(destination)}


@mcp.tool()
def create_custom_field(
    app: str,
    module: str,
    doctype: str,
    field: dict,
    confirm: bool = False,
) -> dict:
    """Create a Custom Field through Frappe's native Custom Field controller."""
    _require_system_manager()
    _require_app_module(app, module)
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    if not frappe.has_permission(doctype, "write"):
        frappe.throw(f"Not permitted to customize DocType: {doctype}", frappe.PermissionError)

    field_data = dict(field)
    fieldname = field_data.get("fieldname")
    if not fieldname:
        frappe.throw("Custom Field fieldname is required", frappe.ValidationError)
    if frappe.get_meta(doctype).has_field(fieldname):
        frappe.throw(f"Field already exists on {doctype}: {fieldname}", frappe.ValidationError)
    field_data.update({"doctype": "Custom Field", "dt": doctype, "module": module})
    if not confirm:
        return {
            "preview": True,
            "action": "create_custom_field",
            "app": app,
            "doctype": doctype,
            "field": field_data,
            "requires_confirmation": True,
        }

    custom_field = frappe.get_doc(field_data)
    custom_field.insert()
    return {"created": True, "doctype": doctype, "fieldname": custom_field.fieldname, "app": app}


@mcp.tool()
def set_field_property(
    app: str,
    module: str,
    doctype: str,
    property: str,
    value: str,
    property_type: str,
    fieldname: str | None = None,
    confirm: bool = False,
) -> dict:
    """Apply a Customize Form property change by inserting a Property Setter."""
    _require_system_manager()
    _require_app_module(app, module)
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    if not frappe.has_permission(doctype, "write"):
        frappe.throw(f"Not permitted to customize DocType: {doctype}", frappe.PermissionError)
    if fieldname and not frappe.get_meta(doctype).has_field(fieldname):
        frappe.throw(f"Unknown field on {doctype}: {fieldname}", frappe.ValidationError)

    data = {
        "doctype": "Property Setter",
        "doctype_or_field": "DocField" if fieldname else "DocType",
        "doc_type": doctype,
        "module": module,
        "field_name": fieldname,
        "property": property,
        "value": value,
        "property_type": property_type,
        "is_system_generated": 0,
    }
    if not confirm:
        return {
            "preview": True,
            "action": "set_field_property",
            "app": app,
            "module": module,
            "setter": data,
            "requires_confirmation": True,
        }

    setter = frappe.get_doc(data)
    setter.insert()
    return {
        "created": True,
        "property_setter": setter.name,
        "doctype": doctype,
        "fieldname": fieldname,
    }


@mcp.tool()
def create_client_script(
    app: str,
    module: str,
    doctype: str,
    name: str,
    script: str,
    view: str = "Form",
    enabled: bool = True,
    confirm: bool = False,
) -> dict:
    """Create a Client Script through Frappe's native controller."""
    _require_system_manager()
    _require_app_module(app, module)
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    if not name.strip():
        frappe.throw("Client Script name is required", frappe.ValidationError)
    if frappe.db.exists("Client Script", name):
        frappe.throw(f"Client Script already exists: {name}", frappe.ValidationError)
    if view not in {"Form", "List"}:
        frappe.throw("view must be Form or List", frappe.ValidationError)
    if not script.strip():
        frappe.throw("Client Script cannot be empty", frappe.ValidationError)
    data = {
        "doctype": "Client Script",
        "name": name,
        "dt": doctype,
        "script": script,
        "view": view,
        "enabled": int(enabled),
        "module": module,
    }
    if not confirm:
        return {
            "preview": True,
            "action": "create_client_script",
            "app": app,
            "module": module,
            "doctype": doctype,
            "name": name,
            "view": view,
            "enabled": enabled,
            "script": script,
            "requires_confirmation": True,
        }

    doc = frappe.get_doc(data)
    doc.insert()
    return {"created": True, "client_script": doc.name, "doctype": doctype, "app": app}


_PERMISSION_FLAGS = {
    "read", "write", "create", "delete", "submit", "cancel", "amend", "report",
    "export", "import", "share", "print", "email", "select",
}
_PERMISSION_MANAGER_EXCLUDED_DOCTYPES = {
    "DocType", "Patch Log", "Module Def", "Transaction Log",
}


@mcp.tool()
def set_role_permissions(
    doctype: str,
    role: str,
    permissions: dict[str, bool],
    permlevel: int = 0,
    confirm: bool = False,
) -> dict:
    """Add or update a role permission rule through Frappe's permission manager."""
    _require_system_manager()
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    if doctype in _PERMISSION_MANAGER_EXCLUDED_DOCTYPES or frappe.get_meta(doctype).istable:
        frappe.throw("DocType is not configurable in Role Permission Manager", frappe.ValidationError)
    if not isinstance(permlevel, int) or isinstance(permlevel, bool) or not 0 <= permlevel <= 9:
        frappe.throw("permlevel must be an integer from 0 to 9", frappe.ValidationError)
    if not frappe.db.exists("Role", role) or frappe.db.get_value("Role", role, "disabled"):
        frappe.throw(f"Unknown or disabled role: {role}", frappe.ValidationError)
    from frappe.permissions import AUTOMATIC_ROLES

    if role in AUTOMATIC_ROLES:
        frappe.throw(f"Role cannot be configured in Role Permission Manager: {role}", frappe.ValidationError)
    if not permissions or set(permissions) - _PERMISSION_FLAGS:
        frappe.throw("permissions must contain one or more supported permission flags", frappe.ValidationError)
    if any(not isinstance(value, bool) for value in permissions.values()):
        frappe.throw("Permission values must be booleans", frappe.ValidationError)

    filters = {"parent": doctype, "role": role, "permlevel": permlevel, "if_owner": 0}
    current = frappe.db.get_value("Custom DocPerm", filters, list(_PERMISSION_FLAGS), as_dict=True)
    if not current:
        # The native Role Permission Manager creates new rules with read enabled.
        current = {flag: 0 for flag in _PERMISSION_FLAGS}
        current["read"] = 1
    resulting = {**{flag: bool(current.get(flag)) for flag in _PERMISSION_FLAGS}, **permissions}
    if not confirm:
        return {
            "preview": True,
            "action": "set_role_permissions",
            "doctype": doctype,
            "role": role,
            "permlevel": permlevel,
            "current": {flag: bool(current.get(flag)) for flag in _PERMISSION_FLAGS},
            "permissions": permissions,
            "resulting": resulting,
            "requires_confirmation": True,
        }

    permission_manager = importlib.import_module(
        "frappe.core.page.permission_manager.permission_manager"
    )
    permission_manager.frappe.only_for("System Manager")
    if not frappe.db.exists("Custom DocPerm", filters):
        permission_manager.add(doctype, role, permlevel)
    for flag, value in permissions.items():
        permission_manager.update(doctype, role, permlevel, flag, int(value))
    return {"updated": True, "doctype": doctype, "role": role, "permlevel": permlevel,
            "permissions": permissions}


def _fixture_filters(doctypes: list[str], modules: list[str]) -> list[dict]:
    return [
        {
            "dt": "Custom Field",
            "filters": [["dt", "in", doctypes], ["module", "in", modules]],
        },
        {
            "dt": "Property Setter",
            "filters": [["doc_type", "in", doctypes], ["module", "in", modules]],
        },
        {
            "dt": "Client Script",
            "filters": [["dt", "in", doctypes], ["module", "in", modules]],
        },
        {"dt": "Custom DocPerm", "filters": [["parent", "in", doctypes]]},
    ]


def _merge_managed_filters(existing: list[dict], added: list[dict]) -> list[dict]:
    merged = list(existing)
    for item in added:
        doctype = item["dt"]
        fieldname = {
            "Custom Field": "dt",
            "Property Setter": "doc_type",
            "Client Script": "dt",
            "Custom DocPerm": "parent",
        }[doctype]
        found = next((row for row in merged if row.get("dt", row.get("doctype")) == doctype), None)
        if found is None:
            merged.append(item)
            continue
        module_values = set()
        target_values = set()
        for condition in [*found.get("filters", []), *item["filters"]]:
            if len(condition) != 3 or condition[1] != "in":
                continue
            if condition[0] == fieldname:
                target_values.update(condition[2])
            elif condition[0] == "module":
                module_values.update(condition[2])
        found["filters"] = [[fieldname, "in", sorted(target_values)]]
        if module_values or any(condition[0] == "module" for condition in item["filters"]):
            found["filters"].append(["module", "in", sorted(module_values)])
    return merged


def _update_hooks(app_path: Path, fixtures: list[dict]) -> str:
    hooks = app_path / "hooks.py"
    if not hooks.is_file() or not os.access(hooks, os.W_OK):
        frappe.throw("Target app hooks.py is missing or not writable", frappe.PermissionError)
    source = hooks.read_text(encoding="utf-8")
    begin = source.find(_BEGIN)
    end = source.find(_END)
    existing: list[dict] = []
    if begin >= 0 or end >= 0:
        if begin < 0 or end < begin or source[end + len(_END) :].strip():
            frappe.throw(
                "Managed fixture block was edited or moved; review hooks.py manually",
                frappe.ValidationError,
            )
        block = source[begin + len(_BEGIN) : end]
        module = ast.parse(block)
        assignment = next(
            (
                node
                for node in module.body
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

    managed = _merge_managed_filters(existing, fixtures)
    block = (
        f"\n\n{_BEGIN}\n"
        f"mcp_frappe_exported_fixtures = {managed!r}\n"
        "fixtures = list(globals().get('fixtures', [])) + mcp_frappe_exported_fixtures\n"
        f"{_END}\n"
    )
    backup_dir = _backup_dir(app_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (backup_dir / f"hooks.py.{timestamp}.bak").write_text(
        hooks.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _write_atomic(hooks, source + block)
    return str(hooks)


def _planned_hooks(app_path: Path, fixtures: list[dict]) -> tuple[Path, str]:
    hooks = app_path / "hooks.py"
    if not hooks.is_file() or not os.access(hooks, os.W_OK):
        frappe.throw("Target app hooks.py is missing or not writable", frappe.PermissionError)
    source = hooks.read_text(encoding="utf-8")
    begin, end = source.find(_BEGIN), source.find(_END)
    existing: list[dict] = []
    if begin >= 0 or end >= 0:
        if begin < 0 or end < begin or source[end + len(_END) :].strip():
            frappe.throw("Managed fixture block is invalid; inspect hooks.py manually", frappe.ValidationError)
        module = ast.parse(source[begin + len(_BEGIN) : end])
        assignment = next((node for node in module.body if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "mcp_frappe_exported_fixtures"
                    for target in node.targets)), None)
        if assignment:
            existing = ast.literal_eval(assignment.value)
        source = source[:begin].rstrip()
    managed = _merge_managed_filters(existing, fixtures)
    block = (
        f"\n\n{_BEGIN}\n"
        f"mcp_frappe_exported_fixtures = {managed!r}\n"
        "fixtures = list(globals().get('fixtures', [])) + mcp_frappe_exported_fixtures\n"
        f"{_END}\n"
    )
    return hooks, source + block


def _backup_dir(app_path: Path) -> Path:
    return Path(frappe.get_site_path("private", "mcp_frappe_backups", app_path.name))


def _safe_app_file(app_path: Path, relative: str) -> Path:
    path = (app_path / relative).resolve()
    if not path.is_relative_to(app_path) or path == app_path:
        frappe.throw("Backup target is outside the selected app", frappe.ValidationError)
    return path


def _write_atomic(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix="mcp-frappe-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _export_fixture(
    app_path: Path,
    doctype: str,
    fieldname: str,
    selected: list[str],
    modules: list[str],
) -> Path:
    from frappe.core.doctype.data_import.data_import import export_json

    fixtures_dir = app_path / "fixtures"
    fixtures_dir.mkdir(exist_ok=True)
    destination = fixtures_dir / (frappe.scrub(doctype) + ".json")
    fd, temporary = tempfile.mkstemp(prefix="mcp-frappe-", suffix=".json", dir=fixtures_dir)
    os.close(fd)
    try:
        export_json(
            doctype,
            temporary,
            filters={fieldname: ["in", selected], "module": ["in", modules]},
            order_by="name asc",
        )
        with open(temporary, encoding="utf-8") as stream:
            exported = json.load(stream)
        previous = []
        if destination.exists():
            with destination.open(encoding="utf-8") as stream:
                previous = json.load(stream)
        preserved = [
            row
            for row in previous
            if row.get(fieldname) not in selected or row.get("module") not in modules
        ]
        by_name = {row["name"]: row for row in preserved if row.get("name")}
        by_name.update({row["name"]: row for row in exported if row.get("name")})
        output = [by_name[name] for name in sorted(by_name)]

        backup_dir = _backup_dir(app_path)
        backup_dir.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            (backup_dir / f"{destination.name}.{timestamp}.bak").write_text(
                destination.read_text(encoding="utf-8"), encoding="utf-8"
            )
        _write_atomic(destination, frappe.as_json(output, ensure_ascii=False))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def _fixture_plan(
    app_path: Path, doctype: str, fieldname: str, selected: list[str], modules: list[str]
) -> tuple[Path, str]:
    """Build a merged fixture payload without changing the app filesystem."""
    from frappe.core.doctype.data_import.data_import import export_json

    fixtures_dir = app_path / "fixtures"
    destination = fixtures_dir / (frappe.scrub(doctype) + ".json")
    temporary_dir = Path(tempfile.mkdtemp(prefix="mcp-frappe-preview-"))
    temporary = temporary_dir / "export.json"
    try:
        filters = {fieldname: ["in", selected]}
        if modules:
            filters["module"] = ["in", modules]
        export_json(doctype, str(temporary), filters=filters, order_by="name asc")
        exported = json.loads(temporary.read_text(encoding="utf-8"))
    finally:
        temporary.unlink(missing_ok=True)
        temporary_dir.rmdir()
    previous = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else []
    preserved = [
        row for row in previous
        if row.get(fieldname) not in selected
        or (modules and row.get("module") not in modules)
    ]
    by_name = {row["name"]: row for row in preserved if row.get("name")}
    by_name.update({row["name"]: row for row in exported if row.get("name")})
    content = frappe.as_json([by_name[name] for name in sorted(by_name)], ensure_ascii=False)
    return destination, content


def _diff(path: Path, content: str) -> dict:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True), content.splitlines(keepends=True),
            fromfile=str(path) if old else "/dev/null", tofile=str(path),
        )
    )
    return {"path": str(path), "exists": path.exists(), "diff": diff, "changed": old != content}


def _standard_doctype_plan(doctype: str) -> tuple[Path, str]:
    from frappe.modules.export_file import strip_default_fields

    doc = frappe.get_doc("DocType", doctype)
    data = doc.as_dict(no_nulls=True)
    doc.run_method("before_export", data)
    data = strip_default_fields(doc, data)
    app_path = Path(frappe.get_app_path(frappe.db.get_value("Module Def", doc.module, "app_name")))
    path = app_path / frappe.scrub(doc.module) / "doctype" / frappe.scrub(doctype) / f"{frappe.scrub(doctype)}.json"
    return path, frappe.as_json(data)


@mcp.tool()
def export_customizations(
    app: str,
    doctypes: list[str],
    modules: list[str] | None = None,
    include_standard_doctypes: bool = True,
    expected_preview: str | None = None,
    confirm: bool = False,
) -> dict:
    """Export selected standard DocTypes and customizations with preview and backups."""
    _require_system_manager()
    app_path = _require_installed_app(app)
    app_modules = [
        row.name
        for row in frappe.get_all(
            "Module Def", filters={"app_name": app}, fields=["name"], order_by="name asc"
        )
    ]
    selected_modules = modules or app_modules
    if not selected_modules or any(module not in app_modules for module in selected_modules):
        frappe.throw("Select only modules belonging to the target app", frappe.ValidationError)
    if not doctypes:
        frappe.throw("Select at least one DocType", frappe.ValidationError)
    missing = [doctype for doctype in doctypes if not frappe.db.exists("DocType", doctype)]
    if missing:
        frappe.throw(f"Unknown DocTypes: {', '.join(missing)}", frappe.DoesNotExistError)

    standard_doctypes = []
    for doctype in doctypes:
        meta = frappe.get_meta(doctype)
        if include_standard_doctypes and not meta.custom:
            owner = frappe.db.get_value("Module Def", meta.module, "app_name")
            if owner != app or meta.module not in selected_modules:
                frappe.throw(
                    f"Standard DocType {doctype} does not belong to selected app/module",
                    frappe.ValidationError,
                )
            standard_doctypes.append(doctype)

    filters = {"dt": ["in", doctypes], "module": ["in", selected_modules]}
    fields = frappe.get_all("Custom Field", filters=filters, fields=["name", "dt", "fieldname"])
    setters = frappe.get_all(
        "Property Setter",
        filters={"doc_type": ["in", doctypes], "module": ["in", selected_modules]},
        fields=["name", "doc_type", "field_name"],
    )
    scripts = frappe.get_all(
        "Client Script", filters={"dt": ["in", doctypes], "module": ["in", selected_modules]},
        fields=["name", "dt", "view"],
    )
    custom_permissions = frappe.get_all(
        "Custom DocPerm", filters={"parent": ["in", doctypes]}, fields=["name", "parent", "role"]
    )
    fixture_specs = [
        ("Custom Field", "dt", doctypes, selected_modules),
        ("Property Setter", "doc_type", doctypes, selected_modules),
        ("Client Script", "dt", doctypes, selected_modules),
        ("Custom DocPerm", "parent", doctypes, []),
    ]
    plans = [_fixture_plan(app_path, *spec) for spec in fixture_specs]
    if standard_doctypes:
        plans.extend(_standard_doctype_plan(name) for name in standard_doctypes)
    hooks_path, hooks_content = _planned_hooks(
        app_path, _fixture_filters(doctypes, selected_modules)
    )
    diffs = [_diff(path, content) for path, content in plans]
    diffs.append(_diff(hooks_path, hooks_content))
    preview_id = hashlib.sha256(
        frappe.as_json(diffs, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if not confirm:
        return {
            "preview": True,
            "app": app,
            "modules": selected_modules,
            "doctypes": doctypes,
            "custom_fields": [dict(row) for row in fields],
            "property_setters": [dict(row) for row in setters],
            "client_scripts": [dict(row) for row in scripts],
            "custom_permissions": [dict(row) for row in custom_permissions],
            "standard_doctypes": standard_doctypes,
            "diffs": diffs,
            "preview_id": preview_id,
            "requires_confirmation": True,
        }
    if expected_preview != preview_id:
        frappe.throw(
            "Export preview is missing or stale; review the latest preview before confirming",
            frappe.ValidationError,
        )

    backup_dir = _backup_dir(app_path)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backups = []
    rollback_id = f"export-{uuid.uuid4().hex}.json"
    manifest = []
    for path, content in [*plans, (hooks_path, hooks_content)]:
        relative = str(path.resolve().relative_to(app_path))
        backup = None
        if path.exists():
            backup = backup_dir / f"{uuid.uuid4().hex}.bak"
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            backups.append(str(backup))
        manifest.append({"path": relative, "backup": backup.name if backup else None})
    (backup_dir / rollback_id).write_text(frappe.as_json(manifest), encoding="utf-8")
    for path, content in [*plans, (hooks_path, hooks_content)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, content)
    files = [str(path) for path, _ in plans]
    return {
        "exported": True,
        "app": app,
        "modules": selected_modules,
        "doctypes": doctypes,
        "custom_fields": len(fields),
        "property_setters": len(setters),
        "client_scripts": len(scripts),
        "custom_permissions": len(custom_permissions),
        "standard_doctypes": standard_doctypes,
        "hooks": str(hooks_path),
        "fixtures": files,
        "backups": backups,
        "rollback_id": rollback_id,
        "apply_on_other_sites": "Install the app or run bench migrate on the target site.",
    }


@mcp.tool()
def rollback_export(app: str, rollback_id: str, confirm: bool = False) -> dict:
    """Restore files from an MCP export manifest; remove files created by that export."""
    _require_system_manager()
    app_path = _require_installed_app(app)
    if Path(rollback_id).name != rollback_id or not rollback_id.startswith("export-") or not rollback_id.endswith(".json"):
        frappe.throw("Invalid export rollback id", frappe.ValidationError)
    manifest_path = _backup_dir(app_path) / rollback_id
    if not manifest_path.is_file():
        frappe.throw("Export rollback manifest not found", frappe.DoesNotExistError)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        frappe.throw("Invalid export rollback manifest", frappe.ValidationError)
    changes = []
    for item in manifest:
        target = _safe_app_file(app_path, item["path"])
        backup_name = item.get("backup")
        backup_path = (_backup_dir(app_path) / backup_name).resolve() if backup_name else None
        if backup_path and not backup_path.is_relative_to(_backup_dir(app_path).resolve()):
            frappe.throw("Invalid backup path in rollback manifest", frappe.ValidationError)
        if backup_path and not backup_path.is_file():
            frappe.throw("Backup file missing or invalid", frappe.ValidationError)
        restored = backup_path.read_text(encoding="utf-8") if backup_path and backup_path.is_file() else ""
        changes.append({**_diff(target, restored), "remove_created_file": backup_name is None})
    if not confirm:
        return {"preview": True, "app": app, "rollback_id": rollback_id,
                "changes": changes, "requires_confirmation": True}
    for item in manifest:
        target = _safe_app_file(app_path, item["path"])
        backup_name = item.get("backup")
        if backup_name:
            backup_path = (_backup_dir(app_path) / backup_name).resolve()
            if not backup_path.is_relative_to(_backup_dir(app_path).resolve()):
                frappe.throw("Backup file missing or invalid", frappe.ValidationError)
            target.parent.mkdir(parents=True, exist_ok=True)
            _write_atomic(target, backup_path.read_text(encoding="utf-8"))
        elif target.exists():
            target.unlink()
    manifest_path.unlink()
    return {"rolled_back": True, "app": app, "rollback_id": rollback_id, "files": len(manifest)}
