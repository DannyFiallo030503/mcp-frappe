# Real HTTP Validation of Frappe MCP

## Scope

Validation ran against the isolated `prueba.localhost` site using the authenticated MCP endpoint:

```text
http://127.0.0.1:8001/api/method/mcp_frappe.mcp.handle_mcp
```

The MCP JSON-RPC requests were sent over HTTP to the installed server. Tool Python functions were not called directly. `cubazone.localhost` was not used.

Versions installed during validation:

| App | Version |
|---|---:|
| Frappe Framework | 15.96.0 |
| ERPNext | 15.96.1 |
| HRMS | 15.60.0 |
| cuba | 14.3.7 |
| erp_app | 0.0.1 |
| prueba | 0.0.1 |

These versions were left unchanged.

## Overall result

- 84 HTTP exchanges were recorded: 83 HTTP 200 responses and one expected HTTP 403 when anonymous access was attempted.
- 80 MCP tool calls covered all 30 tool names returned by `tools/list`.
- 75 tool calls succeeded. Five returned expected MCP errors for negative checks: stale `expected_modified`, file-size cap, unwhitelisted method, unreadable field, and nonexistent workflow document.
- Previews, confirmations, subsequent reads, workflow transition, fixture export, and rollback were exercised.
- Evidence: [`real_http_evidence.jsonl`](./real_http_evidence.jsonl). Runner: [`run_real_http.py`](./run_real_http.py). The evidence contains no credentials.

The runner needs a Frappe API user with appropriate site permissions and a local JSON credential file containing `api_key` and `api_secret`, selected by `MCP_FRAPPE_CREDENTIALS`. The temporary user and file used for this run were removed afterward.

## Tool coverage

Sequence numbers refer to `real_http_evidence.jsonl`.

| Tool | Real operation exercised | Evidence sequences |
|---|---|---:|
| `create_doctype` | Preview and creation of standard parent and child DocTypes in an app module | 20–23 |
| `create_custom_field` | Custom Field preview and creation | 27–28 |
| `set_field_property` | Property Setter/Customize Form preview and creation | 29–30 |
| `create_client_script` | Client Script preview and creation | 31–32 |
| `set_role_permissions` | Preview and permission update for System Manager | 24–25, 33–34 |
| `export_customizations` | Fixture and standard DocType export preview and confirmation with `expected_preview` | 35–36 |
| `rollback_export` | Preview and restoration of exported files | 37–38 |
| `list_installed_apps` | Discovery of seven installed apps | 4 |
| `list_doctypes` | App-filtered lists, including child tables | 5–11 |
| `get_doctype_schema` | Frappe, ERPNext, HRMS, cuba, erp_app, MCP app, and child table schemas | 12–17, 26 |
| `list_documents` | Filtered, bounded document query | 18, 41 |
| `get_document` | Document reads and rejection of a restricted field | 19, 42, 75 |
| `create_document` | Preview, confirmed create, and idempotent replay with explicit name | 39–40, 48–49, 54–55, 81 |
| `update_document` | Preview, stale `expected_modified` rejection, confirmed update | 43–45 |
| `delete_document` | Preview, confirmation with `expected_modified`, and test-record cleanup | 60–61, 77–80 |
| `submit_document` | Preview and submit of submittable document | 56–57 |
| `cancel_document` | Preview and cancel after submit | 58–59 |
| `call_whitelisted_method` | Preview/call of `frappe.client.get_count`; rejection of a non-whitelisted method | 46–47, 74 |
| `list_reports` | Visible reports filtered using Report role permissions | 62 |
| `run_report` | Report Builder and Script Report execution | 63–64 |
| `list_workflows` | Discovery of active test workflow | 50 |
| `get_workflow_actions` | Available transition and rejection for missing document | 51, 76 |
| `apply_workflow_action` | Preview and apply; subsequent read verified new state | 52–53 |
| `list_workspaces` | List of visible workspaces | 65 |
| `get_workspace` | Read of Home workspace | 66 |
| `list_print_formats` | Discovery of test Print Format | 67 |
| `render_print_format` | HTML rendering with named and standard formats | 68 |
| `list_files` | List attachments on a document | 69, 83 |
| `read_file` | UTF-8, binary base64, and size-limit rejection | 70–71, 84 |
| `search_translations` | Matching and no-match queries | 72–73 |

Binary read was verified with bytes that are not valid UTF-8 and returned base64 `//79AA==`. One setup request to Frappe's official `/api/method/upload_file` endpoint created that test attachment; listing and reading were performed through MCP. The current MCP tool set does not include upload.

## Apps and permissions

`list_installed_apps` found `frappe`, `erpnext`, `hrms`, `cuba`, `erp_app`, `mcp_frappe`, and `prueba`. At validation time, `list_doctypes` returned 218, 226, 77, 74, 5, 5, and 0 DocTypes respectively. Accessible schemas were inspected for Frappe (`Currency`), ERPNext (`Company`), HRMS (`Attendance`), erp_app (`Product Category`), cuba (`City`), and MCP E2E test DocTypes; a child table schema was also read through its parent.

The test identity had `System Manager` but not HRMS-specific roles. Some protected HRMS schema requests were correctly denied. `Attendance` provided a positive HRMS schema read. The server respects the connected user's Frappe permissions; it does not grant universal access to protected data.

## Failures found and fixes verified

1. **Stale module map across HTTP requests.** After creating a DocType, a later request could fail with `Module Frappe MCP not found`. `handle_mcp` now refreshes `frappe.local.module_app` from installed apps' Module Def records on every request. DocType creation and document creation then succeeded across separate HTTP requests.
2. **Child Table fields rejected by write-field validation.** Table fields are now authorized through parent DocType operation permission and each child row is validated with parent context. Workflow creation with child state/transition rows and workflow application succeeded afterward.
3. **JSON-RPC envelope leaked into `form_dict` for whitelisted methods.** `call_whitelisted_method` now temporarily isolates `frappe.local.form_dict` to confirmed arguments and restores the original context. `frappe.client.get_count` returned `1` through MCP.
4. **`list_reports` exposed reports the user could not run due to report role restrictions.** Listing now checks `Report.is_permitted()` in addition to DocType report permission. The visible report count changed from 21 to 18, excluding the restricted `Absenteeism Rate` report.
5. **Export result contained a non-serializable `Path`.** The `hooks` result is now returned as text. The MCP response became structured, exposed the `rollback_id`, and the export rollback completed.

The partial DocType left by an early failed call was deleted. Temporary export fixtures were rolled back; `hooks.py` had no remaining MCP-managed fixture block and the fixture directory was empty. Random DocTypes and temporary customizations created by incomplete runs were removed.

## Retained demonstration data

The isolated `prueba.localhost` site retains valid demonstration examples `MCP E2E Live Record` and `MCP E2E Second Record`, an active sample workflow, a Print Format, a translation, and test attachments. The temporary API user and its private credential file were deleted. Some demonstration fixtures remain intentionally available for inspection.

## Conclusion and limits

All 30 tools exposed by this version were exercised over HTTP with positive paths and applicable negative checks. No unresolved defect was found in those exercised flows. This is acceptance evidence for the exact installed stack and tested account, not a guarantee for every Frappe release, every custom app, every permission role, or every DocType configuration.

File upload is not an MCP tool in this version. The official Frappe endpoint was used only to prepare test input for MCP file listing and reading.
