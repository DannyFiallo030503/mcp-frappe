# Compatibility and validation

## Standalone server

The standalone MCP server was exercised through its real MCP stdio transport against the existing Aspire-managed development site on **2026-10-19**, using `Administrator` for the requested manual operations. Credentials are not included in evidence. The connected stack was Frappe 15.96.0, ERPNext 15.96.1, HRMS 15.60.0, Cuba 14.3.7, and erp_app 0.0.1.

The [manual functional report](../e2e/manual_functional_report.md) covers real MCP calls as Administrator, broad DocType/field/property creation, generated files and database checks, child rows, tree links, role permissions, Custom Fields, Property Setters, record CRUD, export/rollback, cleanup, and the failures found. The redacted [DocType matrix evidence](../e2e/doctype_matrix_evidence.json), [acceptance report](../e2e/standalone_real_http_report.md), and [evidence summary](../e2e/standalone_real_http_evidence.json) cover the scripted matrix and smoke/mutation/customization checks. The test runners are in `e2e/run_standalone_*.py`.

The mutation, customization, and DocType-matrix acceptance runners are opt-in and refuse non-loopback targets. They create unique temporary data and delete records/metadata through Frappe; the customization runner also restores exported app files and consumes its backup manifest. Frappe leaves physical SQL tables after DocType deletion, so inspect and remove only verified-empty test tables separately. The manual matrix cleanup confirmed zero remaining `MCP Matrix` metadata, Single values, or tables, and the `erp_app` Git working tree was clean.

Manual DocType checks confirmed representative native files and database behavior, but exposed important defects: Single DocType files could be written without a persisted DocType row; Frappe's autoreloader can disconnect the MCP after writes, leaving an uncertain result; and delete left an empty physical table. Two further defects found in that run are now fixed: `get_document` always returns `docstatus`, and generated-file writes (DocType creation and exports) refuse the `frappe` and `erpnext` apps. The manual report records cleanup and remaining gaps. This does not establish visual equality against Desk, compatibility with every Frappe version/custom app, or ERPNext end-to-end business process coverage. The historical [HTTP acceptance report](../e2e/REAL_HTTP_REPORT.md) documents the former Frappe-hosted implementation and is retained for migration history; it is not evidence for the standalone server.

For the intended Frappe + ERPNext minimum, generic-app contract, exact current tool coverage, missing surfaces, and the proposed clean-site/version matrix, see the [Frappe capability study](frappe-capability-study.md). The current evidence is a v15 development site, not a clean-site or v16 compatibility claim.

## Current boundaries

- Read and core document operations use standard Frappe REST APIs.
- Reports use Frappe's Query Report endpoint.
- DocType metadata comes from Frappe's `frappe.desk.form.load.getdoctype`, so Frappe itself merges Custom Fields and Property Setters and orders the fields (checked against the v16.33.1 source: `Meta.process` calls `add_custom_fields`, `apply_property_setters`, and `sort_fields`). Record tools no longer need System Manager to validate fields; this has not yet been checked live as a non-System-Manager user.
- App export writes Frappe's native module customization files (`<module>/custom/<doctype>.json`, the format of Frappe v16.33.1's `export_customizations`) and Client Script fixtures in `<app>/fixtures/client_script.json` with a managed `hooks.py` block. It backs up overwritten files and supports guarded rollback. That `bench migrate` re-imports an exported Custom Field has not yet been checked on a live site.
- `update_doctype` and `delete_doctype` follow Frappe v16.33.1's source: the developer-mode check in `DocType.validate`, child-row sync in `Document.update_child_table`, `on_update` exporting the JSON, the DocType branch of `frappe.delete_doc` (no link check, table kept, folder removed in developer mode), and Customize Form's `ALLOWED_FIELDTYPE_CHANGE`. Neither has been run against a live site yet, and v15 was not compared.
- Workflow actions, submit/cancel/amend/rename, file download/upload, print rendering, translations, workspace operations, user-role membership/User Permissions, and arbitrary app-specific methods remain outside this standalone release.
- File writes require a local app checkout and `FRAPPE_APPS_PATH`. Standard DocType writes can cause a development Bench to reload its web worker and disconnect the MCP request even when some DB/filesystem changes succeeded. Reconcile site and source state before retrying; this behavior is a known correctness issue, not a successful-operation guarantee.

Validate every new Frappe release line or unusual app combination on a disposable site before relying on it.
