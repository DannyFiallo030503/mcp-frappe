# Frappe MCP task usage log

One entry per task that invoked a Frappe MCP tool or was blocked while attempting to use MCP because the tools or connection were unavailable. Group calls by task and record exact tool names, including failed attempts. Distinguish tool-call outcome from task outcome.

Do not paste raw tool output. Evidence links point to reviewed, sanitized task reports. Connection aliases are operational labels only; no private site URLs, credentials, customer data, or business identifiers belong here.

| Date (UTC) | Task reference | MCP connection alias(es) | Tools used | Purpose | Tool outcome | Task outcome | Safe evidence / feedback |
|---|---|---|---|---|---|---|---|
| 2026-11-09 | `product-line-as-item-group` | `cubazone-local` | `get_site_info`, `get_doctype_schema`, `list_documents`, `update_document` (preview and confirm), `create_custom_field` (preview and confirm), `create_doctype` (preview), `export_customizations` (previews), `list_doctypes`, `list_app_drift` | Inspect current schema and app drift while planning the product-line contract | Partial; Frappe refused a field-type conversion, as expected | Partial | [Task report](cubazone/product-line-as-item-group.md); [FR-2026-004](feature-requests.md#fr-2026-004--safely-sync-an-app-doctype-that-is-missing-from-the-site) |
| 2026-11-09 | `doctype-sync-investigation` | `cubazone-local` | `list_doctypes`, `get_doctype_schema`, `list_documents`, `list_app_drift` | Inspect missing app DocTypes and patch status | Passed | Partial | [Task report](cubazone/doctype-sync-investigation.md) |
| 2026-11-09 | `image-sync-verification` | `cubazone-local` | `get_site_info`, read-only Item queries | Verify connected site context and QA item image state | Partial; initial site-info call timed out, later read succeeded | Partial | [Task report](cubazone/image-sync-verification.md) |
| 2026-11-09 | `image-sync-e2e` | `cubazone-local` | Read-only Item queries | Verify QA item image state around a synthetic sync run | Passed | Passed | [Task report](cubazone/image-sync-e2e.md) |
| 2026-11-10 | `product-line-readiness-2026-11-10` | `cubazone-local` | `get_site_info`, `get_doctype_schema`, `list_documents`, `count_documents`, `create_document` (four previews and confirmations) | Inspect product-line prerequisites and prepare a QA Item Group hierarchy | Passed | Partial | [Task report](cubazone/product-line-readiness-2026-11-10.md) |
| 2026-11-10 | `product-lines-null-central-2026-11-10` | `cubazone-local` | `get_site_info`, `list_documents` (`Item`, `Item Group`) | Investigate null product-line values after deriving the line from Item Group | Passed | Partial; MCP could not invoke the whitelisted method needed to verify its live response | [Task report](cubazone/product-lines-null-central-2026-11-10.md) |
| 2026-11-10 | `product-line-e2e-verification-2026-11-10` | `cubazone-local` | `get_site_info`, `list_documents`, `count_documents` | Compare ERP metadata with the existing Local projection | Passed | Partial; authenticated end-to-end flow remained unverified | [Task report](cubazone/product-line-e2e-verification-2026-11-10.md) |
| 2026-11-10 | `product-line-repair-run-2026-11-10` | `cubazone-local` | `get_site_info`, `list_documents`, `count_documents` | Verify source hierarchy and the outcome of a projection repair | Passed | Partial; a separate count endpoint issue was handled outside MCP | [Task report](cubazone/product-line-repair-run-2026-11-10.md) |
| 2026-11-10 | `product-line-dev-seed-2026-11-10` | `cubazone-local` | `get_site_info`, `get_doctype_schema`, `list_documents`, `count_documents`, `list_app_drift`, `diff_doctype` | Review development seed prerequisites and later verify projection behavior | Passed | Partial; remote authenticated verification remained pending | [Task report](cubazone/product-line-dev-seed-2026-11-10.md) |
| 2026-11-10 | `selector-follow-up-2026-11-10` | `cubazone-local` | `get_site_info`, `list_documents`, `count_documents` | Review selector behavior and compare after related backend deployments | Passed | Partial; causal attribution was not isolated | [Task report](cubazone/product-lines-null-central-2026-11-10.md) |
| 2026-11-11 | `frappe-health-repair` | `cubazone-local` | `get_site_info`, `get_doctype_schema`, `list_documents`, `count_documents`, `list_app_drift`, `diff_doctype` | Diagnose Frappe availability timeouts and verify recovery | Partial; initial calls timed out, later calls passed | Passed | [Task report](cubazone/frappe-health-repair.md) |
| 2026-11-11 | `product-ordering-verification` | `cubazone-local` | `get_site_info`, `count_documents`, `list_documents` | Compare product ordering between ERP and Local projection | Passed | Partial; Central HTTP comparison was unavailable | [Task report](cubazone/product-ordering-verification.md) |
| 2026-11-11 | `backend-audit-site-info-2026-11-11` | `cubazone-local` | `get_site_info` | Confirm app/version context before an audit | Passed | Partial; no business method was called | [Task report](cubazone/backend-audit-site-info-2026-11-11.md) |
| 2026-11-12 | `currency-exchange-schema-review` | `cubazone-local` | `get_site_info`, `get_doctype_schema`, `diff_doctype` | Inspect standard schema and app drift before planning a draft feature | Passed | Partial; feature decisions remained open | [Task report](cubazone/currency-exchange-schema-review.md) |

## Outcome meanings

- **Tool outcome:** whether MCP calls returned the expected result. Use `partial` if some calls failed or were blocked.
- **Task outcome:** whether the intended result was independently verified. Use `partial` or `unverified` when task-level behavior was not fully checked.
- A permission denial or documented safety refusal is not an MCP defect. File a bug only for an unexpected tool failure or incorrect result.

## Reporting rules

For every task that invokes Frappe MCP, keep one sanitized task report with its objective, connection alias, exact MCP tools and purpose, per-operation outcome, safe evidence, errors, and pending work. If an operation is missing, add or update a feature request; if a tool has a reproducible defect, add or update a bug report. Link the report and any feedback item here.

Never include raw tool output, credentials, private site URLs, user identities, customer or business data, private tracker links, or internal deployment identifiers. Use a safe artifact link or write `none`.
