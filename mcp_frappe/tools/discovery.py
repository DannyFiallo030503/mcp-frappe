"""Runtime discovery tools for all apps installed on the current Frappe site."""

import json

import frappe

from mcp_frappe.mcp import mcp


def _readable_fields(meta, parent_doctype: str | None = None) -> set[str]:
    if frappe.session.user == "Administrator":
        fields = {field.fieldname for field in meta.fields if field.fieldname}
    else:
        fields = set(
            meta.get_permitted_fieldnames(
                user=frappe.session.user,
                permission_type="read",
                parenttype=parent_doctype,
            )
        )
    fields.add("name")
    return fields


@mcp.tool()
def list_installed_apps() -> list[dict[str, str]]:
    """List apps installed on this site, including their module names."""
    result = []
    for app in frappe.get_installed_apps():
        modules = frappe.get_all(
            "Module Def",
            filters={"app_name": app},
            fields=["name"],
            order_by="name asc",
        )
        result.append({"app": app, "modules": [row.name for row in modules]})
    return result


@mcp.tool()
def list_doctypes(
    app: str | None = None,
    module: str | None = None,
    include_child_tables: bool = True,
) -> list[dict]:
    """List DocTypes from installed apps, optionally filtered by app or module."""
    installed = set(frappe.get_installed_apps())
    if app and app not in installed:
        frappe.throw(f"App is not installed on this site: {app}", frappe.ValidationError)

    filters = {} if include_child_tables else {"istable": 0}
    if module:
        filters["module"] = module
    rows = frappe.get_all(
        "DocType",
        filters=filters,
        fields=["name", "module", "custom", "issingle", "istable"],
        order_by="module asc, name asc",
    )

    modules_by_app = {
        installed_app: {
            row.name
            for row in frappe.get_all(
                "Module Def", filters={"app_name": installed_app}, fields=["name"]
            )
        }
        for installed_app in installed
    }
    result = []
    for row in rows:
        if row.istable:
            parents = frappe.get_all(
                "DocField",
                filters={"options": row.name, "fieldtype": "Table"},
                fields=["parent"],
                distinct=True,
            )
            if not any(frappe.has_permission(parent.parent, "read") for parent in parents):
                continue
        elif not frappe.has_permission(row.name, "read"):
            continue

        owner = next(
            (candidate for candidate, modules in modules_by_app.items() if row.module in modules),
            "custom" if row.custom else "frappe",
        )
        if app and owner != app:
            continue
        result.append(
            {
                "doctype": row.name,
                "module": row.module,
                "app": owner,
                "custom": bool(row.custom),
                "single": bool(row.issingle),
                "child_table": bool(row.istable),
            }
        )
    return result


@mcp.tool()
def get_doctype_schema(doctype: str, parent_doctype: str | None = None) -> dict:
    """Return readable DocType fields and metadata for the current user."""
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    meta = frappe.get_meta(doctype)
    if meta.istable and not parent_doctype:
        frappe.throw("parent_doctype is required to inspect a child table", frappe.ValidationError)
    if not frappe.has_permission(doctype, "read", parent_doctype=parent_doctype):
        frappe.throw(f"Not permitted to inspect DocType: {doctype}", frappe.PermissionError)

    readable_fields = _readable_fields(meta, parent_doctype)
    fields = []
    for field in meta.fields:
        if field.fieldname not in readable_fields:
            continue
        fields.append(
            {
                "fieldname": field.fieldname,
                "label": field.label,
                "fieldtype": field.fieldtype,
                "options": field.options,
                "reqd": bool(field.reqd),
                "read_only": bool(field.read_only),
                "hidden": bool(field.hidden),
                "permlevel": field.permlevel,
                "default": getattr(field, "default", None),
                "description": getattr(field, "description", None),
                "in_list_view": bool(getattr(field, "in_list_view", False)),
                "in_standard_filter": bool(getattr(field, "in_standard_filter", False)),
                "unique": bool(getattr(field, "unique", False)),
                "precision": getattr(field, "precision", None),
                "depends_on": getattr(field, "depends_on", None),
            }
        )
    module = frappe.db.get_value("Module Def", meta.module, "app_name")
    return {
        "doctype": doctype,
        "module": meta.module,
        "app": module,
        "custom": bool(meta.custom),
        "single": bool(meta.issingle),
        "child_table": bool(meta.istable),
        "parent_doctype": parent_doctype,
        "is_submittable": bool(meta.is_submittable),
        "is_tree": bool(getattr(meta, "is_tree", False)),
        "title_field": getattr(meta, "title_field", None),
        "search_fields": meta.get_search_fields(),
        "sort_field": getattr(meta, "sort_field", None),
        "sort_order": getattr(meta, "sort_order", None),
        "autoname": getattr(meta, "autoname", None),
        "track_changes": bool(getattr(meta, "track_changes", False)),
        "fields": fields,
    }


@mcp.tool()
def list_documents(
    doctype: str,
    filters: dict | None = None,
    fields: list[str] | None = None,
    limit: int = 20,
) -> list[dict]:
    """List documents using the current user's Frappe permissions."""
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    if frappe.get_meta(doctype).istable:
        frappe.throw("Read child table rows through their parent document", frappe.ValidationError)
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(f"Not permitted to read DocType: {doctype}", frappe.PermissionError)
    if limit < 1 or limit > 100:
        frappe.throw("limit must be between 1 and 100", frappe.ValidationError)

    meta = frappe.get_meta(doctype)
    permitted = _readable_fields(meta)
    requested_fields = fields or ["name"]
    if not requested_fields or any(field not in permitted for field in requested_fields):
        frappe.throw(
            "One or more requested fields are missing or not readable", frappe.PermissionError
        )

    allowed_filters = set(meta.get_search_fields()) | permitted | {"name"}
    filters = filters or {}
    if any(field not in allowed_filters for field in filters):
        frappe.throw("Filters must use searchable or readable fields", frappe.ValidationError)

    rows = frappe.get_list(
        doctype,
        filters=filters,
        fields=requested_fields,
        order_by="modified desc",
        page_length=limit,
    )
    return [dict(row) for row in rows]


@mcp.tool()
def get_document(
    doctype: str,
    name: str | None = None,
    fields: list[str] | None = None,
    child_row_limit: int = 100,
) -> dict:
    """Read one permitted document, including readable child-table rows."""
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    meta = frappe.get_meta(doctype)
    if meta.istable:
        frappe.throw("Read child table rows through their parent document", frappe.ValidationError)
    if meta.issingle:
        doc = frappe.get_doc(doctype)
    else:
        if not name:
            frappe.throw("name is required for non-single DocTypes", frappe.ValidationError)
        doc = frappe.get_doc(doctype, name)
    if not frappe.has_permission(doctype, "read", doc=doc):
        frappe.throw(
            f"Not permitted to read document: {doctype} {doc.name}", frappe.PermissionError
        )
    if child_row_limit < 1 or child_row_limit > 200:
        frappe.throw("child_row_limit must be between 1 and 200", frappe.ValidationError)

    readable_fields = _readable_fields(meta)
    requested_fields = fields or sorted(readable_fields)
    unknown = [field for field in requested_fields if field != "name" and not meta.has_field(field)]
    if unknown:
        frappe.throw(f"Unknown fields on {doctype}: {', '.join(unknown)}", frappe.ValidationError)
    restricted = [field for field in requested_fields if field not in readable_fields]
    if restricted:
        frappe.throw(f"Fields are not readable: {', '.join(restricted)}", frappe.PermissionError)

    result = {"doctype": doctype, "name": doc.name}
    truncated_tables = []
    for field in meta.fields:
        if field.fieldname not in requested_fields:
            continue
        value = doc.get(field.fieldname)
        if field.fieldtype != "Table":
            result[field.fieldname] = _json_value(value)
            continue

        child_meta = frappe.get_meta(field.options)
        child_readable_fields = _readable_fields(child_meta, parent_doctype=doctype)
        rows = []
        for child in (value or [])[:child_row_limit]:
            if not frappe.has_permission(field.options, "read", doc=child, parent_doctype=doctype):
                continue
            rows.append(
                {
                    key: _json_value(child.get(key))
                    for key in sorted(child_readable_fields)
                    if key == "name" or child_meta.has_field(key)
                }
            )
        if len(value or []) > child_row_limit:
            truncated_tables.append(field.fieldname)
        result[field.fieldname] = rows
    if truncated_tables:
        result["_truncated_child_tables"] = truncated_tables
    return result


def _json_value(value):
    return json.loads(frappe.as_json(value, ensure_ascii=False))
