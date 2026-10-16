# Compatibility and Validation

## Compatibility status

The current implementation was exercised on the following isolated Frappe site stack:

| App | Version |
|---|---:|
| Frappe Framework | 15.96.0 |
| ERPNext | 15.96.1 |
| HRMS | 15.60.0 |
| cuba | 14.3.7 |
| erp_app | 0.0.1 |
| prueba | 0.0.1 |

These versions were left unchanged during validation. This evidence demonstrates behavior for that installed stack; it does not establish compatibility with all Frappe release lines. Validate each target version and app combination on a separate site before deployment.

## Real endpoint acceptance run

The MCP endpoint was exercised over authenticated HTTP on `prueba.localhost`. Calls used MCP JSON-RPC through the installed Frappe endpoint; tool Python functions were not called directly. The unrelated `cubazone.localhost` site was not used.

Recorded evidence:

- 84 HTTP exchanges: 83 HTTP 200 responses and one expected HTTP 403 for an anonymous initialization attempt.
- 80 MCP tool calls covering all 30 tool names exposed by `tools/list`.
- 75 tool calls completed successfully; 5 produced expected MCP errors for negative checks (stale update timestamp, file-size cap, non-whitelisted method, restricted field, and nonexistent workflow document).
- Additional exercised flows included schema/app discovery, child DocTypes, customization creation, export/rollback, document lifecycle, workflow, reports, files, print output, workspace, and translations.

The line-by-line coverage matrix and failure/fix notes are in [`../e2e/REAL_HTTP_REPORT.md`](../e2e/REAL_HTTP_REPORT.md). Raw JSONL request summaries are in [`../e2e/real_http_evidence.jsonl`](../e2e/real_http_evidence.jsonl), and the HTTP runner is [`../e2e/run_real_http.py`](../e2e/run_real_http.py). Evidence excludes API credentials. It records request arguments and selected response summaries; treat it as test data, not a general sanitized production log.

## Fixes exercised during acceptance

The real HTTP run exposed and verified fixes for stale Frappe module ownership maps between requests, child Table-field write validation, JSON-RPC data leaking into native `form_dict`, report listing that did not match report-role access, and non-serializable `Path` export results. Exact behavior and coverage sequence numbers are documented in the E2E report.

## Permission coverage and limitations

The test identity had `System Manager` but did not have HRMS roles. The run successfully read the Attendance schema, while protected HR DocTypes were correctly denied. This confirms permission enforcement; it does not show that the MCP can read every protected DocType. Tools return only what the authenticated Frappe user is permitted to access.

The server can discover every installed app's metadata at runtime, but “universal discovery” is not equivalent to universal access. Version-specific behavior in each installed app must be checked on the target version.

## File operation note

The current MCP tool set has no upload operation. The acceptance run used Frappe's official upload endpoint only to prepare a text and binary File fixture, then used `list_files` and `read_file` through MCP. The binary read returned base64. This setup method is documented so that file-read coverage is not mistaken for MCP upload support.

## Test environment cleanup

The temporary API user and credential file used by the run were deleted. Failed/temporary DocTypes and customization records were cleaned. The isolated site retains named MCP E2E demonstration examples, an active sample workflow, a Print Format, a translation, and attachment fixtures so the environment can be inspected. The test site is not a clean-room or production data source.

The runner's credentials are no longer present in this repository or the test site. To repeat the run, create a dedicated test identity and provide a new local credential JSON through `MCP_FRAPPE_CREDENTIALS` as described in the [development guide](development.md).
