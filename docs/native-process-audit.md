# Native Frappe Process Audit

## Scope and basis

This audit compares every tool exposed by the standalone MCP with the Frappe
15.96.0 source available in the development Bench. The comparison follows the
Desk action to its JavaScript call, whitelisted method or DocType controller,
and generated files or side effects. The runtime checks below also exercised
the active Aspire site, configured to run Frappe `v16.34.0`. This is not a
claim of parity across all Frappe releases.

The MCP should translate an AI request into a bounded Frappe operation. Frappe
must own validation, persistence, metadata composition, file generation, and
other operation-specific behavior. MCP-side previews, confirmation tokens,
input bounds, and transport are orchestration; they must not replace the
framework operation or fabricate its output.

## Findings

| MCP tool | Current execution path | Assessment |
|---|---|---|
| `list_installed_apps` | Lists `Module Def` records and groups by `app_name`. | Frappe-backed, but this is a module-derived app list and can omit installed apps without Module Def records. |
| `list_doctypes` | Queries `Module Def` and `DocType` through REST. | Frappe-backed discovery. |
| `get_doctype_schema` | Calls `frappe.desk.form.load.getdoctype`. | Delegates metadata composition and child metadata to the same Desk loader used by Frappe. Confirmed on the Aspire site. |
| `list_documents`, `get_document` | Reads through `/api/resource/...`. | Uses Frappe's permission-aware document APIs. MCP bounds fields and child rows. |
| `create_document`, `update_document`, `delete_document` | Uses `/api/resource/...`, invoking Frappe document insert/save/delete behavior. | Uses native document lifecycle. The `modified` preview check is an MCP-side preflight, not an atomic compare-and-swap. |
| `run_report` | Calls `frappe.desk.query_report.run` with `ignore_prepared_report=False`. | Uses Frappe's default prepared-report behavior. |
| `create_doctype` | Posts a `DocType` through REST and lets Frappe handle scaffolding. | Frappe's `DocType.on_update` performs schema setup, `export_doc`, `make_controller_template`, and cache clearing. MCP no longer precreates templates that could make Frappe skip native generation. |
| `set_role_permissions` | Calls `frappe.core.page.permission_manager.permission_manager.add` and `.update`. | Uses the native Permission Manager methods and its System Manager checks. |
| `create_custom_field` | Inserts a `Custom Field` through REST. | Uses the native Custom Field controller, including field validation, schema update, and cache clearing. |
| `set_field_property` | Inserts a `Property Setter` through REST. | Frappe's Property Setter controller runs. This is a native direct setter operation, but it is not the complete Customize Form save flow, which compares with base values, removes redundant setters, handles field order and links/actions/states, and performs related validation. The tool is documented with this narrower behavior. |
| `export_form_customizations` | Previews source-state and target paths, then calls Frappe's whitelisted `frappe.modules.utils.export_customizations`. | Delegates serialization, recursive child export, module path resolution, and file generation to Frappe. Confirmed on the Aspire site for Customer and its Party Account child; native files were then rolled back cleanly. MCP requires System Manager to match the Desk Customize Form access model. Frappe enforces Developer Mode. File diff/backup/rollback is available only when the Frappe host and MCP share the app tree. |
| Fixture export | Not exposed as a standalone MCP tool. | Frappe's `bench export-fixtures` runs `frappe.utils.fixtures.export_fixtures`, which is not a whitelisted HTTP method in the audited release. The former MCP serializer was removed rather than presented as native Frappe export. A future wrapper needs a constrained native adapter or Bench runner. |
| `rollback_export` | Restores files produced by an MCP-triggered export from a local backup manifest. | MCP-only file safety helper; Frappe has no matching rollback operation for source files. It works only when the export writes into the shared tree visible to the MCP. |

## Native entry points confirmed in Frappe 15.96.0

- Desk Customize Form export calls the whitelisted
  `frappe.modules.utils.export_customizations(module, doctype,
  sync_on_migrate, with_permissions)`.
- Desk metadata loading calls the whitelisted
  `frappe.desk.form.load.getdoctype`; its metadata bundle is produced from
  `frappe.desk.form.meta.get_meta` and includes child-table metadata.
- Standard DocType creation invokes the DocType controller. Its `on_update`
  performs schema updates, `export_doc`, controller template generation, and
  cache clearing.
- `bench export-fixtures --app <app>` calls
  `frappe.utils.fixtures.export_fixtures`. It is a Bench command, not a
  whitelisted HTTP API in this Frappe release.
- The generic REST resource endpoints call Frappe's normal document
  permissions and lifecycle. The Custom Field controller validates the field
  and updates the target DocType schema.

## Remediation status and remaining work

1. **Applied:** schema loading calls Frappe's Desk metadata method.
2. **Applied:** standard DocType creation no longer stages its own scaffold;
   Frappe generates the files from its controller.
3. **Applied:** report execution honors Frappe's default prepared-report policy.
4. **Applied:** Customize Form export calls Frappe's native exporter. The
   MCP preview is a source-state confirmation summary, not a fabricated export
   diff. Local rollback is conditional on a shared filesystem.
5. **Applied:** the MCP's local fixture serializer and hooks editor were
   removed. Fixture export remains an explicit Bench operation until there is
   a constrained native adapter.
6. **Constrained:** `set_field_property` is a direct Property Setter operation
   through the native resource API. It does not claim full Customize Form
   save semantics; full editor parity would require a dedicated wrapper around
   the native `CustomizeForm.save_customization` flow and its complete form
   state.
7. **Applied:** rollback is limited to changed files that the MCP can see and
   back up; it does not reverse Frappe database changes or server-only files.

## Live validation on Aspire

The MCP connected to the local `cubazone.localhost` site using its local
`Administrator` / `admin` defaults. An MCP stdio client listed all 15 tools,
read installed apps, and fetched the Customer schema. A direct read of
`frappe.auth.get_logged_user` confirmed the authenticated identity was
Administrator. The Customer schema came from Frappe's metadata endpoint and
contained 85 fields.

The first export preview exposed a permission edge case: the normal REST
resource endpoint returned HTTP 403 for `DocType Link`, including for
Administrator. The metadata from `frappe.desk.form.load.getdoctype` already
contains composed link metadata, so the preview now fingerprints that native
metadata instead of requesting the restricted resource. This preserves
Frappe's authorization boundary and allowed the export preview to complete.

The native export then wrote `customer.json` and `party_account.json` in the
shared `erp_app/CubaZone/custom` directory. Both had Frappe's expected JSON
shape and `sync_on_migrate: 1`. `rollback_export` removed both new files, the
backup directory was removed, and the `erp_app` Git worktree remained clean.
The export touched app files only; it did not mutate site metadata or database
records.

The runtime check does not cover standard DocType creation or report execution
on the live site. Those changes have source review and unit coverage only; do
not treat this as broad v16 or Desk parity evidence.

## Compatibility follow-up

The confirmed source paths are Frappe 15.96.0. Before claiming compatibility
with another release, verify the corresponding Desk JavaScript, whitelisted
methods, controller hooks, and file formats in that release. Exercise native
calls on a disposable site and compare the resulting files and metadata with a
Desk-created control.
