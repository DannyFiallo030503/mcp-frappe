"""Runtime access to Frappe reports, workflows, workspaces, print, files and translations."""

import base64
import json
import os

import frappe
from frappe.utils.file_manager import get_file, get_file_path

from mcp_frappe.mcp import mcp


@mcp.tool()
def list_reports(doctype: str | None = None, limit: int = 100) -> list[dict]:
    """List reports visible to the current user, optionally for a reference DocType."""
    if not 1 <= limit <= 200:
        frappe.throw("limit must be between 1 and 200", frappe.ValidationError)
    filters = {"ref_doctype": doctype} if doctype else {}
    rows = frappe.get_list(
        "Report", filters=filters,
        fields=["name", "ref_doctype", "report_type", "is_standard", "disabled"],
        order_by="name asc", page_length=limit,
    )
    result = []
    for row in rows:
        if row.disabled or not frappe.has_permission(row.ref_doctype, "report"):
            continue
        # run_report also enforces Report document read permission. Keep this
        # discovery result aligned so callers are not offered reports that
        # the same user cannot actually run.
        report = frappe.get_doc("Report", row.name)
        if (
            frappe.has_permission("Report", "read", doc=report)
            and report.is_permitted()
        ):
            result.append(dict(row))
    return result


@mcp.tool()
def run_report(report_name: str, filters: dict | None = None, limit: int = 200) -> dict:
    """Run a permitted Frappe Query/Script Report and cap rows returned to the caller."""
    if not 1 <= limit <= 500:
        frappe.throw("limit must be between 1 and 500", frappe.ValidationError)
    if not frappe.db.exists("Report", report_name):
        frappe.throw(f"Unknown Report: {report_name}", frappe.DoesNotExistError)
    report = frappe.get_doc("Report", report_name)
    if report.disabled or not frappe.has_permission(report.ref_doctype, "report"):
        frappe.throw(f"Not permitted to run report: {report_name}", frappe.PermissionError)
    if not frappe.has_permission("Report", "read", doc=report):
        frappe.throw(f"Not permitted to inspect report: {report_name}", frappe.PermissionError)
    if filters is not None and not isinstance(filters, dict):
        frappe.throw("filters must be an object", frappe.ValidationError)
    from frappe.desk.query_report import run

    result = run(report_name, filters=filters or {}, user=frappe.session.user)
    rows = result.get("result") or []
    if not isinstance(rows, list):
        frappe.throw("Report returned an unsupported result type", frappe.ValidationError)
    total = len(rows)
    result["result"] = rows[:limit]
    result["returned_rows"] = min(total, limit)
    result["truncated"] = total > limit
    return json.loads(frappe.as_json(result, ensure_ascii=False))


@mcp.tool()
def list_workflows(doctype: str | None = None, limit: int = 100) -> list[dict]:
    """List active workflows whose DocTypes are readable by this user."""
    if not 1 <= limit <= 200:
        frappe.throw("limit must be between 1 and 200", frappe.ValidationError)
    filters = {"is_active": 1}
    if doctype:
        filters["document_type"] = doctype
    rows = frappe.get_list(
        "Workflow", filters=filters,
        fields=["name", "document_type", "workflow_state_field", "send_email_alert"],
        order_by="name asc", page_length=limit,
    )
    return [dict(row) for row in rows if frappe.has_permission(row.document_type, "read")]


@mcp.tool()
def get_workflow_actions(doctype: str, name: str) -> list[dict]:
    """Return the native workflow transitions currently available for a document."""
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(f"Not permitted to read DocType: {doctype}", frappe.PermissionError)
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    from frappe.model.workflow import get_transitions

    return get_transitions(doc)


@mcp.tool()
def apply_workflow_action(
    doctype: str,
    name: str,
    action: str,
    expected_modified: str | None = None,
    confirm: bool = False,
) -> dict:
    """Preview then apply a currently valid native workflow action."""
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(f"Not permitted to read DocType: {doctype}", frappe.PermissionError)
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    from frappe.model.workflow import get_transitions, get_workflow

    transition = next(
        (
            row
            for row in get_transitions(doc)
            if (row.get("action") if isinstance(row, dict) else row.action) == action
        ),
        None,
    )
    if not transition:
        frappe.throw("Action is not available for this document and user", frappe.ValidationError)
    workflow = get_workflow(doctype)
    preview = {
        "preview": True,
        "doctype": doctype,
        "name": name,
        "action": action,
        "current_state": doc.get(workflow.workflow_state_field),
        "modified": str(doc.modified),
        "next_state": (
            transition.get("next_state")
            if isinstance(transition, dict)
            else transition.next_state
        ),
    }
    if not confirm:
        return {**preview, "requires_confirmation": True}
    if not expected_modified or str(doc.modified) != expected_modified:
        frappe.throw(
            "Document changed since preview; review the workflow action again",
            frappe.ValidationError,
        )
    from frappe.model.workflow import apply_workflow

    updated = apply_workflow(doc.as_dict(), action)
    return {
        "applied": True,
        "doctype": doctype,
        "name": updated.name,
        "modified": str(updated.modified),
        "action": action,
    }


@mcp.tool()
def list_workspaces(limit: int = 100) -> list[dict]:
    """List workspaces visible under Frappe's Workspace permissions."""
    if not 1 <= limit <= 200:
        frappe.throw("limit must be between 1 and 200", frappe.ValidationError)
    rows = frappe.get_list(
        "Workspace", fields=["name", "title", "module", "public", "for_user", "is_hidden"],
        order_by="sequence_id asc", page_length=limit,
    )
    return [dict(row) for row in rows]


@mcp.tool()
def get_workspace(name: str) -> dict:
    """Read one workspace, including its cards, links and layout after native permission checks."""
    doc = frappe.get_doc("Workspace", name)
    doc.check_permission("read")
    return json.loads(frappe.as_json(doc.as_dict(), ensure_ascii=False))


@mcp.tool()
def list_print_formats(doctype: str, limit: int = 100) -> list[dict]:
    """List enabled print formats for a DocType readable by the current user."""
    if not frappe.db.exists("DocType", doctype):
        frappe.throw(f"Unknown DocType: {doctype}", frappe.DoesNotExistError)
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(f"Not permitted to read DocType: {doctype}", frappe.PermissionError)
    if not 1 <= limit <= 200:
        frappe.throw("limit must be between 1 and 200", frappe.ValidationError)
    rows = frappe.get_list(
        "Print Format", filters={"doc_type": doctype, "disabled": 0},
        fields=["name", "doc_type", "print_format_type", "standard", "default_print_language"],
        order_by="name asc", page_length=limit,
    )
    return [dict(row) for row in rows]


@mcp.tool()
def render_print_format(
    doctype: str, name: str, print_format: str | None = None, max_chars: int = 100000
) -> dict:
    """Render a document using Frappe's permission-checked print view and cap HTML size."""
    if not frappe.has_permission(doctype, "read") or not frappe.has_permission(doctype, "print"):
        frappe.throw(f"Not permitted to print DocType: {doctype}", frappe.PermissionError)
    if not 1000 <= max_chars <= 500000:
        frappe.throw("max_chars must be between 1000 and 500000", frappe.ValidationError)
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    if print_format:
        if not frappe.db.exists("Print Format", print_format):
            frappe.throw(f"Unknown Print Format: {print_format}", frappe.DoesNotExistError)
        fmt = frappe.get_doc("Print Format", print_format)
        if fmt.doc_type != doctype:
            frappe.throw("Print Format belongs to another DocType", frappe.ValidationError)
        fmt.check_permission("read")
    html = frappe.get_print(doctype, name, print_format=print_format, as_pdf=False)
    return {
        "doctype": doctype,
        "name": name,
        "print_format": print_format,
        "html": html[:max_chars],
        "truncated": len(html) > max_chars,
    }


@mcp.tool()
def list_files(doctype: str | None = None, name: str | None = None, limit: int = 100) -> list[dict]:
    """List files accessible to the current user, optionally attached to a document."""
    if not 1 <= limit <= 200:
        frappe.throw("limit must be between 1 and 200", frappe.ValidationError)
    filters = {}
    if doctype:
        filters["attached_to_doctype"] = doctype
    if name:
        if not doctype:
            frappe.throw(
                "doctype is required when filtering by attached document name",
                frappe.ValidationError,
            )
        filters["attached_to_name"] = name
    rows = frappe.get_list(
        "File", filters=filters,
        fields=[
            "name", "file_name", "file_url", "is_private", "file_size",
            "attached_to_doctype", "attached_to_name",
        ],
        order_by="creation desc", page_length=limit,
    )
    result = []
    for row in rows:
        file_doc = frappe.get_doc("File", row.name)
        if frappe.has_permission("File", "read", doc=file_doc):
            result.append(dict(row))
    return result


@mcp.tool()
def read_file(name: str, max_bytes: int = 1048576) -> dict:
    """Read an accessible Frappe File as UTF-8 text or base64 with a strict size cap."""
    if not 1 <= max_bytes <= 5000000:
        frappe.throw("max_bytes must be between 1 and 5000000", frappe.ValidationError)
    doc = frappe.get_doc("File", name)
    doc.check_permission("read")
    path = get_file_path(doc.name)
    if not path or not os.path.isfile(path):
        frappe.throw("File content is unavailable", frappe.DoesNotExistError)
    size = os.path.getsize(path)
    if size > max_bytes:
        frappe.throw(f"File exceeds max_bytes ({size} > {max_bytes})", frappe.ValidationError)
    filename, content = get_file(doc.name)
    if isinstance(content, str):
        return {"name": doc.name, "file_name": filename, "encoding": "utf-8", "content": content}
    return {
        "name": doc.name,
        "file_name": filename,
        "encoding": "base64",
        "content": base64.b64encode(content).decode("ascii"),
    }


@mcp.tool()
def search_translations(language: str, query: str, limit: int = 50) -> list[dict]:
    """Search translation records for a language using Frappe list permissions."""
    if not 1 <= limit <= 200:
        frappe.throw("limit must be between 1 and 200", frappe.ValidationError)
    if not language or not query.strip():
        frappe.throw("language and query are required", frappe.ValidationError)
    rows = frappe.get_list(
        "Translation", filters={"language": language, "source_text": ["like", f"%{query}%"]},
        fields=["name", "language", "source_text", "translated_text", "context"],
        order_by="source_text asc", page_length=limit,
    )
    return [dict(row) for row in rows]
