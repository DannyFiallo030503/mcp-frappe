"""Run real MCP-over-HTTP checks against the isolated prueba.localhost site.

The credential file is read locally and never written to evidence. This is a
one-run acceptance suite; test records are intentionally identifiable by the
MCP E2E prefix so they can be removed from the prueba site afterward.
"""
from __future__ import annotations

import ast
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

URL = os.environ.get(
    "MCP_FRAPPE_URL", "http://127.0.0.1:8001/api/method/mcp_frappe.mcp.handle_mcp"
)
CREDENTIALS = Path(
    os.environ.get(
        "MCP_FRAPPE_CREDENTIALS",
        str(Path.home() / ".config" / "mcp-frappe" / "test-credentials.json"),
    )
)
OUT = Path(__file__).with_name("real_http_evidence.jsonl")
credentials = json.loads(CREDENTIALS.read_text(encoding="utf-8"))
AUTH = f"token {credentials['api_key']}:{credentials['api_secret']}"
RUN = uuid.uuid4().hex[:8]
sequence = 0


def decode(value):
    if isinstance(value, dict) and "structuredContent" in value:
        return value["structuredContent"]
    content = value.get("content", []) if isinstance(value, dict) else []
    text = next((part.get("text", "") for part in content if part.get("type") == "text"), "")
    for parser in (json.loads, ast.literal_eval):
        try:
            return parser(text)
        except Exception:
            pass
    return text


def summarize(value):
    if isinstance(value, dict):
        # Preserve useful proof while avoiding large HTML/workspace data.
        if "html" in value:
            return {**{k: v for k, v in value.items() if k != "html"},
                    "html_chars": len(value["html"]), "html_excerpt": value["html"][:160]}
        if "content" in value and isinstance(value["content"], str):
            return {**{k: v for k, v in value.items() if k != "content"},
                    "content_chars": len(value["content"]), "content_excerpt": value["content"][:160]}
        return value
    if isinstance(value, list):
        return {"count": len(value), "sample": value[:5]}
    if isinstance(value, str):
        return value[:700]
    return value


def request(method, body=None, auth=True, headers=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    request_headers = dict(headers or {})
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    if auth:
        request_headers["Authorization"] = AUTH
    req = urllib.request.Request(URL, data=data, headers=request_headers, method=method)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as error:
        status, raw = error.code, error.read()
    except Exception as error:
        status, raw = 0, str(error).encode()
    elapsed_ms = round((time.monotonic() - started) * 1000)
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = raw.decode("utf-8", "replace")
    return status, parsed, elapsed_ms


def record(label, status, response, elapsed_ms, tool=None, arguments=None, expected=None):
    global sequence
    sequence += 1
    result = response.get("result", response) if isinstance(response, dict) else response
    entry = {
        "seq": sequence,
        "label": label,
        "http_status": status,
        "elapsed_ms": elapsed_ms,
        "tool": tool,
        "arguments": arguments,
        "is_error": bool(result.get("isError")) if isinstance(result, dict) else None,
        "response": summarize(decode(result) if isinstance(result, dict) else result),
        "expected": expected,
    }
    with OUT.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    print(f"{sequence:02d} {label}: HTTP {status}; error={entry['is_error']}; {elapsed_ms} ms")
    return result


def rpc(method, params=None, authenticated=True, label=None, tool=None, arguments=None):
    global sequence
    body = {"jsonrpc": "2.0", "id": sequence + 1, "method": method}
    if params is not None:
        body["params"] = params
    status, response, elapsed = request("POST", body, auth=authenticated)
    record(label or method, status, response, elapsed, tool=tool, arguments=arguments,
           expected="HTTP 200" if authenticated else "HTTP 403")
    return response


def call(tool, arguments=None, label=None):
    params = {"name": tool, "arguments": arguments or {}}
    response = rpc("tools/call", params, label=label or tool, tool=tool, arguments=arguments or {})
    result = response.get("result", {}) if isinstance(response, dict) else {}
    return result, decode(result)


OUT.write_text("", encoding="utf-8")
rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "mcp-frappe-real-e2e", "version": "1"}}, authenticated=False, label="anonymous_initialize_rejected")
rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "mcp-frappe-real-e2e", "version": "1"}}, label="authenticated_initialize")
rpc("tools/list", {}, label="tools_list")

# Discover apps and DocTypes from core, ERPNext, HRMS, and installed custom apps.
for tool, args in [
    ("list_installed_apps", {}),
    ("list_doctypes", {"app": "frappe", "include_child_tables": True}),
    ("list_doctypes", {"app": "erpnext", "include_child_tables": True}),
    ("list_doctypes", {"app": "hrms", "include_child_tables": True}),
    ("list_doctypes", {"app": "cuba", "include_child_tables": True}),
    ("list_doctypes", {"app": "erp_app", "include_child_tables": True}),
    ("list_doctypes", {"app": "mcp_frappe", "include_child_tables": True}),
    ("list_doctypes", {"app": "prueba", "include_child_tables": True}),
    ("get_doctype_schema", {"doctype": "Currency"}),
    ("get_doctype_schema", {"doctype": "Company"}),
    ("get_doctype_schema", {"doctype": "Attendance"}),
    ("get_doctype_schema", {"doctype": "Product Category"}),
    ("get_doctype_schema", {"doctype": "City"}),
    ("get_doctype_schema", {"doctype": "MCP E2E Live Record"}),
    ("list_documents", {"doctype": "ToDo", "filters": {"description": ["like", "%MCP%"]}, "fields": ["name", "description", "status"], "limit": 10}),
    ("get_document", {"doctype": "MCP E2E Live Record", "name": "MCP-LIVE-WF-1"}),
]:
    call(tool, args)

# Create a child DocType and inspect it through the real MCP endpoint.
child_name = f"MCP E2E Evidence Row {RUN}"
child_args = {"app": "mcp_frappe", "module": "Frappe MCP", "name": child_name,
              "fields": [{"fieldname": "value", "label": "Value", "fieldtype": "Data"}],
              "is_child_table": True}
call("create_doctype", child_args, "create_child_doctype_preview")
call("create_doctype", {**child_args, "confirm": True}, "create_child_doctype_confirm")
parent_name = f"MCP E2E Evidence Parent {RUN}"
parent_args = {"app": "mcp_frappe", "module": "Frappe MCP", "name": parent_name,
               "fields": [{"fieldname": "title", "label": "Title", "fieldtype": "Data", "reqd": 1},
                          {"fieldname": "rows", "label": "Rows", "fieldtype": "Table", "options": child_name}]}
call("create_doctype", parent_args, "create_parent_doctype_preview")
call("create_doctype", {**parent_args, "confirm": True}, "create_parent_doctype_confirm")
parent_permissions = {"doctype": parent_name, "role": "System Manager", "permissions": {"read": True, "create": True, "write": True, "delete": True}}
call("set_role_permissions", parent_permissions, "child_parent_permissions_preview")
call("set_role_permissions", {**parent_permissions, "confirm": True}, "child_parent_permissions_confirm")
call("get_doctype_schema", {"doctype": child_name, "parent_doctype": parent_name}, "inspect_child_doctype")

# Customize Form, custom field, script, role permission, export and rollback.
dt = "MCP E2E Live Record"
module_args = {"app": "mcp_frappe", "module": "Frappe MCP", "doctype": dt}
field_name = f"mcp_e2e_{RUN}"
cf_args = {**module_args, "field": {"fieldname": field_name, "label": f"Evidence {RUN}", "fieldtype": "Data"}}
call("create_custom_field", cf_args, "custom_field_preview")
call("create_custom_field", {**cf_args, "confirm": True}, "custom_field_confirm")
prop_args = {**module_args, "fieldname": field_name, "property": "label", "value": f"Verified {RUN}", "property_type": "Data"}
call("set_field_property", prop_args, "customize_form_preview")
call("set_field_property", {**prop_args, "confirm": True}, "customize_form_confirm")
script_args = {**module_args, "name": f"MCP E2E Script {RUN}", "script": f'frappe.ui.form.on("{dt}", {{ refresh(frm) {{}} }});', "view": "Form", "enabled": True}
call("create_client_script", script_args, "client_script_preview")
call("create_client_script", {**script_args, "confirm": True}, "client_script_confirm")
permission_args = {"doctype": dt, "role": "System Manager", "permissions": {"email": True}}
call("set_role_permissions", permission_args, "role_permission_preview")
call("set_role_permissions", {**permission_args, "confirm": True}, "role_permission_confirm")
export_args = {"app": "mcp_frappe", "doctypes": [dt], "modules": ["Frappe MCP"], "include_standard_doctypes": True}
_, export_preview = call("export_customizations", export_args, "export_preview")
preview_id = export_preview.get("preview_id") if isinstance(export_preview, dict) else None
_, export_result = call("export_customizations", {**export_args, "expected_preview": preview_id, "confirm": True}, "export_confirm")
rollback_id = export_result.get("rollback_id") if isinstance(export_result, dict) else None
call("rollback_export", {"app": "mcp_frappe", "rollback_id": rollback_id}, "rollback_preview")
call("rollback_export", {"app": "mcp_frappe", "rollback_id": rollback_id, "confirm": True}, "rollback_confirm")

# Document lifecycle, stale-preview guard, native workflow and whitelisted API.
record_name = f"MCP-E2E-EVIDENCE-{RUN}"
values = {"title": f"Evidence {RUN}", "details": "Created and updated through real MCP HTTP", "workflow_state": "MCP E2E Draft"}
create_args = {"doctype": dt, "name": record_name, "values": values}
call("create_document", create_args, "document_create_preview")
call("create_document", {**create_args, "confirm": True}, "document_create_confirm")
call("list_documents", {"doctype": dt, "filters": {"name": record_name}, "fields": ["name", "title", "workflow_state"], "limit": 5})
call("get_document", {"doctype": dt, "name": record_name})
update_args = {"doctype": dt, "name": record_name, "values": {"details": "Updated through real MCP HTTP"}}
_, update_preview = call("update_document", update_args, "document_update_preview")
call("update_document", {**update_args, "expected_modified": '"stale"', "confirm": True}, "document_update_stale_rejected")
call("update_document", {**update_args, "expected_modified": update_preview.get("modified"), "confirm": True}, "document_update_confirm")
count_args = {"method": "frappe.client.get_count", "arguments": {"doctype": dt, "filters": {"name": record_name}}}
call("call_whitelisted_method", count_args, "whitelisted_method_preview")
call("call_whitelisted_method", {**count_args, "confirm": True}, "whitelisted_method_confirm")

# Existing test workflow has two states and a valid System Manager transition.
workflow_record = f"MCP-E2E-WF-{RUN}"
workflow_values = {"title": f"Workflow {RUN}", "workflow_state": "MCP E2E Draft"}
workflow_create = {"doctype": dt, "name": workflow_record, "values": workflow_values}
call("create_document", workflow_create, "workflow_document_create_preview")
call("create_document", {**workflow_create, "confirm": True}, "workflow_document_create_confirm")
call("list_workflows", {"doctype": dt, "limit": 20})
call("get_workflow_actions", {"doctype": dt, "name": workflow_record})
_, action_preview = call("apply_workflow_action", {"doctype": dt, "name": workflow_record, "action": "MCP E2E Approve"}, "workflow_action_preview")
call("apply_workflow_action", {"doctype": dt, "name": workflow_record, "action": "MCP E2E Approve", "expected_modified": action_preview.get("modified"), "confirm": True}, "workflow_action_confirm")

# Submit, cancel and delete use a separate record.
submit_name = f"MCP-E2E-SUBMIT-{RUN}"
submit_args = {"doctype": dt, "name": submit_name, "values": {"title": f"Submit {RUN}", "workflow_state": "MCP E2E Draft"}}
call("create_document", submit_args, "submittable_create_preview")
call("create_document", {**submit_args, "confirm": True}, "submittable_create_confirm")
_, submit_preview = call("submit_document", {"doctype": dt, "name": submit_name}, "submit_preview")
call("submit_document", {"doctype": dt, "name": submit_name, "expected_modified": submit_preview.get("modified"), "confirm": True}, "submit_confirm")
_, cancel_preview = call("cancel_document", {"doctype": dt, "name": submit_name}, "cancel_preview")
call("cancel_document", {"doctype": dt, "name": submit_name, "expected_modified": cancel_preview.get("modified"), "confirm": True}, "cancel_confirm")
_, delete_preview = call("delete_document", {"doctype": dt, "name": submit_name}, "delete_preview")
call("delete_document", {"doctype": dt, "name": submit_name, "expected_modified": delete_preview.get("modified"), "confirm": True}, "delete_confirm")

# Advanced discovery, reports, workspaces, print formats, files, translations.
call("list_reports", {"limit": 100})
call("run_report", {"report_name": "Asset Activity", "filters": {}, "limit": 2}, "run_report_builder")
call("run_report", {"report_name": "Database Storage Usage By Tables", "filters": {}, "limit": 2}, "run_script_report")
call("list_workspaces", {"limit": 30})
call("get_workspace", {"name": "Home"})
call("list_print_formats", {"doctype": dt, "limit": 20})
call("render_print_format", {"doctype": dt, "name": "MCP-LIVE-WF-1", "print_format": "MCP E2E Print Format", "max_chars": 2000})
call("list_files", {"doctype": dt, "name": "MCP-LIVE-WF-1", "limit": 20})
call("read_file", {"name": "1203268041", "max_bytes": 100})
call("read_file", {"name": "1203268041", "max_bytes": 5}, "read_file_size_cap_rejected")
call("search_translations", {"language": "en", "query": "MCP E2E special", "limit": 5})
call("search_translations", {"language": "en", "query": "no-match-e2e", "limit": 5}, "translation_no_match")

# Security and input checks expected to fail safely.
call("call_whitelisted_method", {"method": "frappe.get_all", "arguments": {}}, "unwhitelisted_method_rejected")
call("get_document", {"doctype": "Report", "name": "Asset Activity", "fields": ["name", "filters"]}, "restricted_report_field_rejected")
call("get_workflow_actions", {"doctype": dt, "name": "does-not-exist"}, "unknown_workflow_document_rejected")

# Clean document records created in this evidence pass. Keep schema/customization
# fixtures until the report is reviewed; all names are explicitly MCP E2E.
for name in (record_name, workflow_record):
    _, preview = call("delete_document", {"doctype": dt, "name": name}, f"cleanup_preview_{name}")
    if isinstance(preview, dict) and preview.get("modified"):
        call("delete_document", {"doctype": dt, "name": name, "expected_modified": preview["modified"], "confirm": True}, f"cleanup_confirm_{name}")
print(f"Evidence written: {OUT}")
