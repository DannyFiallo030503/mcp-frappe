"""Permission-aware document writes backed by native Frappe controllers."""

import json
from typing import Any

import frappe

from mcp_frappe.mcp import mcp

_PROTECTED_FIELDS = {
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


def _get_meta(doctype: str):
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    meta = frappe.get_meta(doctype)
    if meta.istable:
        frappe.throw("Operate on child rows through their parent document", frappe.ValidationError)
    return meta


def _get_doc(doctype: str, name: str | None, meta):
    if meta.issingle:
        return frappe.get_doc(doctype)
    if not name:
        frappe.throw("name is required for non-single DocTypes", frappe.ValidationError)
    return frappe.get_doc(doctype, name)


def _validate_values(meta, values: dict, permission_type: str, parenttype: str | None = None):
    if not isinstance(values, dict) or not values:
        frappe.throw("values must be a non-empty object", frappe.ValidationError)

    user = frappe.session.user
    permitted = set(
        meta.get_permitted_fieldnames(
            user=user,
            permission_type=permission_type,
            parenttype=parenttype,
        )
    )
    for fieldname, value in values.items():
        if fieldname in _PROTECTED_FIELDS or not meta.has_field(fieldname):
            frappe.throw(f"Field cannot be written: {fieldname}", frappe.ValidationError)
        field = meta.get_field(fieldname)
        # Child-table fields are authorized through the parent DocType's
        # operation permission; their rows are checked against the child
        # DocType below with ``parenttype`` context. Frappe omits Table fields
        # from get_permitted_fieldnames() on some standard DocTypes (notably
        # Workflow), even though the user can edit them in the native form.
        if fieldname not in permitted and field.fieldtype != "Table":
            frappe.throw(f"Not permitted to write field: {fieldname}", frappe.PermissionError)

        if field.read_only:
            frappe.throw(f"Read-only field cannot be written: {fieldname}", frappe.ValidationError)
        if field.fieldtype != "Table":
            continue
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            frappe.throw(
                f"Table field {fieldname} must be a list of objects", frappe.ValidationError
            )
        child_meta = frappe.get_meta(field.options)
        for row in value:
            _validate_values(child_meta, row, permission_type, parenttype=meta.name)


def _assert_permission(doctype: str, permission_type: str, doc=None):
    if not frappe.has_permission(doctype, permission_type, doc=doc):
        frappe.throw(
            f"Not permitted to {permission_type} {doctype}" + (f" {doc.name}" if doc else ""),
            frappe.PermissionError,
        )


def _modified(doc) -> str | None:
    return frappe.as_json(doc.modified, ensure_ascii=False) if doc.modified else None


def _check_expected_modified(doc, expected_modified: str | None):
    latest_modified = frappe.db.get_value(doc.doctype, doc.name, "modified", for_update=True)
    if latest_modified is None:
        frappe.throw(
            f"Document no longer exists: {doc.doctype} {doc.name}", frappe.DoesNotExistError
        )
    current = frappe.as_json(latest_modified, ensure_ascii=False)
    if not expected_modified:
        frappe.throw("expected_modified from the preview is required", frappe.ValidationError)
    if current != expected_modified:
        frappe.throw(
            "Document changed after preview; request a fresh preview before confirming",
            frappe.TimestampMismatchError,
        )


def _normalize(value: Any):
    if hasattr(value, "as_dict"):
        value = value.as_dict(no_nulls=True)
    if isinstance(value, dict):
        return {
            key: _normalize(item)
            for key, item in value.items()
            if key not in _PROTECTED_FIELDS and not key.startswith("__")
        }
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return json.loads(frappe.as_json(value, ensure_ascii=False))


def _matches_requested_values(doc, values: dict) -> bool:
    for fieldname, expected in values.items():
        current = doc.get(fieldname)
        if not _matches_value(current, expected):
            return False
    return True


def _matches_value(current, expected) -> bool:
    if isinstance(expected, dict):
        if current is None:
            return False
        for key, value in expected.items():
            current_value = current.get(key) if hasattr(current, "get") else None
            if not _matches_value(current_value, value):
                return False
        return True
    if isinstance(expected, list):
        if not isinstance(current, list) or len(current) != len(expected):
            return False
        return all(
            _matches_value(current_value, expected_value)
            for current_value, expected_value in zip(current, expected, strict=True)
        )
    return _normalize(current) == _normalize(expected)


@mcp.tool()
def create_document(
    doctype: str,
    values: dict,
    name: str | None = None,
    confirm: bool = False,
) -> dict:
    """Preview or create a document through its native Frappe controller.

    Supplying an explicit name makes retries idempotent: the same values return
    the existing document; a different payload for that name is rejected.
    """
    meta = _get_meta(doctype)
    _assert_permission(doctype, "create")
    _validate_values(meta, values, "create")

    if name and frappe.db.exists(doctype, name):
        existing = _get_doc(doctype, name, meta)
        _assert_permission(doctype, "read", existing)
        if _matches_requested_values(existing, values):
            return {
                "already_exists": True,
                "doctype": doctype,
                "name": existing.name,
                "idempotent_replay": True,
            }
        frappe.throw("The requested name belongs to a different document", frappe.ValidationError)

    if not confirm:
        return {
            "preview": True,
            "action": "create_document",
            "doctype": doctype,
            "name": name,
            "fields": sorted(values),
            "idempotent": bool(name),
            "requires_confirmation": True,
        }

    doc = frappe.get_doc({"doctype": doctype, **values})
    doc.insert(set_name=name)
    return {"created": True, "doctype": doctype, "name": doc.name, "modified": _modified(doc)}


@mcp.tool()
def update_document(
    doctype: str,
    values: dict,
    name: str | None = None,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict:
    """Preview or update fields through Frappe validation and permissions."""
    meta = _get_meta(doctype)
    doc = _get_doc(doctype, name, meta)
    _assert_permission(doctype, "write", doc)
    _validate_values(meta, values, "write")

    if not confirm:
        return {
            "preview": True,
            "action": "update_document",
            "doctype": doctype,
            "name": doc.name,
            "modified": _modified(doc),
            "fields": sorted(values),
            "requires_confirmation": True,
        }

    _check_expected_modified(doc, expected_modified)
    doc.update(values)
    doc.save()
    return {"updated": True, "doctype": doctype, "name": doc.name, "modified": _modified(doc)}


@mcp.tool()
def delete_document(
    doctype: str,
    name: str,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict:
    """Preview or permanently delete one document after explicit confirmation."""
    meta = _get_meta(doctype)
    if meta.issingle:
        frappe.throw("Single DocTypes cannot be deleted with this tool", frappe.ValidationError)
    doc = _get_doc(doctype, name, meta)
    _assert_permission(doctype, "delete", doc)

    if not confirm:
        return {
            "preview": True,
            "action": "delete_document",
            "doctype": doctype,
            "name": doc.name,
            "modified": _modified(doc),
            "requires_confirmation": True,
        }

    _check_expected_modified(doc, expected_modified)
    frappe.delete_doc(doctype, doc.name)
    return {"deleted": True, "doctype": doctype, "name": doc.name}


def _change_docstatus(
    doctype: str,
    name: str,
    action: str,
    expected_modified: str | None,
    confirm: bool,
) -> dict:
    meta = _get_meta(doctype)
    if not meta.is_submittable:
        frappe.throw(f"DocType is not submittable: {doctype}", frappe.ValidationError)
    doc = _get_doc(doctype, name, meta)
    expected_docstatus = 0 if action == "submit" else 1
    if doc.docstatus != expected_docstatus:
        frappe.throw(
            f"Cannot {action} document with docstatus {doc.docstatus}", frappe.ValidationError
        )
    _assert_permission(doctype, action, doc)

    if not confirm:
        return {
            "preview": True,
            "action": f"{action}_document",
            "doctype": doctype,
            "name": doc.name,
            "docstatus": doc.docstatus,
            "modified": _modified(doc),
            "requires_confirmation": True,
        }

    _check_expected_modified(doc, expected_modified)
    getattr(doc, action)()
    return {
        action + "ted" if action == "submit" else "cancelled": True,
        "doctype": doctype,
        "name": doc.name,
        "docstatus": doc.docstatus,
        "modified": _modified(doc),
    }


@mcp.tool()
def submit_document(
    doctype: str,
    name: str,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict:
    """Preview or submit a submittable document through its native controller."""
    return _change_docstatus(doctype, name, "submit", expected_modified, confirm)


@mcp.tool()
def cancel_document(
    doctype: str,
    name: str,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict:
    """Preview or cancel a submitted document through its native controller."""
    return _change_docstatus(doctype, name, "cancel", expected_modified, confirm)


@mcp.tool()
def call_whitelisted_method(
    method: str,
    arguments: dict | None = None,
    confirm: bool = False,
) -> dict:
    """Preview or invoke an installed app method explicitly whitelisted by Frappe."""
    if not isinstance(method, str) or not method.strip() or "<locals>" in method:
        frappe.throw("A dotted whitelisted method path is required", frappe.ValidationError)
    if arguments is not None and not isinstance(arguments, dict):
        frappe.throw("arguments must be an object", frappe.ValidationError)

    fn = frappe.get_attr(method)
    frappe.is_whitelisted(fn)
    allowed_methods = frappe.allowed_http_methods_for_whitelisted_func.get(fn, [])
    if "POST" not in allowed_methods:
        frappe.throw("Whitelisted method does not allow POST requests", frappe.PermissionError)
    arguments = arguments or {}
    if not confirm:
        return {
            "preview": True,
            "action": "call_whitelisted_method",
            "method": method,
            "arguments": arguments,
            "effects_unknown": True,
            "requires_confirmation": True,
        }

    # This tool runs inside the MCP HTTP request, whose form_dict contains the
    # JSON-RPC envelope (id/method/params). Some native whitelisted methods
    # consume frappe.form_dict directly instead of only their function args;
    # isolate it to the explicitly confirmed argument object for the call.
    original_form_dict = frappe.local.form_dict
    frappe.local.form_dict = frappe._dict(arguments)
    try:
        result = frappe.call(fn, **arguments)
    finally:
        frappe.local.form_dict = original_form_dict
    return {"called": True, "method": method, "result": json.loads(frappe.as_json(result))}
