# mcp-frappe 0.4.0 against Platform Frappe 15

## Task and environment

Used the local `mcp-frappe` 0.4.0 process against the existing `cubazone.localhost` Frappe 15.96.0 site. The site reported ERPNext 15.96.1, HRMS 15.60.0, Cuba 14.3.7, and `erp_app` 0.0.1. The server registered 44 tools. The package test suite had passed earlier in this task: 1,072 tests.

The AppHost and existing MariaDB were reused. The DCP host proxy timed out, while the service-native `frappe.ping` returned HTTP 200 through the existing container network; a temporary loopback forwarder let the local MCP process reach it. Frappe was Running, though the DCP health status remained Unhealthy. `erp_app` had two pending data patches, so `migrate` was not run.

## Calls and outcomes

Read coverage included `get_site_info`, `list_installed_apps`, `list_doctypes`, `get_doctype_schema`, `list_documents`, `count_documents`, `search_records`, `get_document`, `diff_doctype`, `list_app_drift`, `list_pending_patches`, `get_recent_errors`, `list_app_methods`, `list_reports`, `get_report_filters`, `run_report`, `get_workflow`, `list_notifications`, `get_workspace`, and `list_mcp_changes`.

Write coverage included `create_doctype`, `create_document`, `create_documents`, `update_document`, `update_doctype`, `customize_doctype`, `set_field_property`, `create_custom_field`, `set_role_permissions`, `upsert_workflow`, `upsert_notification`, `upsert_report`, `upsert_workspace`, `submit_document`, `cancel_document`, `delete_document`, and `delete_doctype`. Each applied write used a preview where supported and was checked by a read or cleanup readback.

Created synthetic fixtures exercised site-custom and standard DocTypes, Child Table, Single, Tree, Submittable and ordinary DocTypes, plus 43 distinct Frappe 15 fieldtypes in a 44-field matrix. Record coverage included links, both child-table field types, atomic batch rollback, update, and `docstatus` 0→1→2. The Item Customize Form flow created a Custom Field and Property Setter. Permissions, Workflow, Notification, Report Builder and Workspace were also created and read back.

## Observed limitations and failures

- Initial DocType creation with `submit/cancel` permission rows failed validation because its test DocType was not submittable; retry with valid permissions succeeded.
- The first Field Matrix preview failed because Fold was not immediately before Section Break; correcting the order succeeded.
- The first Workflow preview referred to a field that did not yet exist; a retry without per-state field assignment succeeded.
- `set_field_property` needs the app module when saving on the standard `Item` DocType. Without it, preview failed before write. With `module=CubaZone`, it succeeded.
- Report Builder `upsert_report` returned a two-row sample and `get_report_filters` returned an empty filter list. `run_report` returned no rows/columns for that Report Builder because it calls Query Report execution; this is not a failed read of the saved Report Builder configuration. An existing Query Report ran successfully with zero data rows.
- Desk does not expose an operation equivalent to `create_documents` atomic multi-record rollback; mark that parity item non-comparable in manual results.
- The journal correctly marked submitted/cancelled records as lacking a safe inverse. They were removed with `delete_document` after cancellation.
- `delete_doctype` removed the schema records but intentionally left SQL tables. No manual SQL cleanup was attempted.
- `set_field_property` does not support restoring the DocType `field_order` property; its allowlist rejects that property. The Item order was restored through the existing site's Bench API from a pre-test captured 125-entry value. A subsequent read matched exactly. No unrelated Item customizations were changed.

## Cleanup and branches

Deleted the synthetic records, Workflow, Notification, Report, Workspace, all eight QA DocTypes, and both QA Custom Fields on Item. The QA-prefix DocType and Report searches and Notification list were empty afterward; Workspace lookup returned not found; Item schema contained no QA field. Item's saved field order matched the original value exactly. The generated Standard Row, Notification, Report and Workspace app files remain committed as evidence on the local `qa/mcp-v040-endpoint-matrix-20261121` branch.

Local `erp_app` branches `qa/mcp-v040-endpoint-matrix-20261121` and `qa/manual-mcp-v040-parity-20261121` share base `cf5ba3ca`. The manual guide and results template are in the manual branch; that manual run has not happened, so cross-method parity is not assessed. Generated outputs were moved under `docs/qa/mcp-v040-generated/` so their presence in the MCP branch cannot cause a later app sync to reinstall the deleted QA metadata.

The previous usage-log note saying “blocked before endpoint invocation” described the earlier failed attempt, before the temporary network path was available. This report supersedes that status for the continued run.

## Not covered in this run

This is not a complete 44-tool acceptance matrix. `rename`, `promote_custom_doctype`, `export_customizations`, `export_form_customizations`, `rollback_export`, `undo_mcp_change`, and the mutating Bench commands were not exercised. `migrate` was intentionally withheld because of pending business-data patches. Record those endpoints as unverified rather than passed.
