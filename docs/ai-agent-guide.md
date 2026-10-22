# AI agent guide

## Operating principles

- Begin with `list_installed_apps`, `list_doctypes`, and `get_doctype_schema`; do not assume an ERPNext, HRMS, or custom app catalog.
- Use the target site's naming and field options exactly as returned by Frappe.
- Request only fields and rows needed. Keep output limits low and narrow filters.
- Frappe permissions are enforced remotely. If a request is denied, report the missing access; do not try alternate APIs to bypass it.
- Treat every mutation as potentially side-effecting because native Frappe hooks and validations run.
- Review previews before confirming. For record updates/deletes, pass the exact `modified` token returned in the preview; if it changed, start over.
- For standard DocTypes, review the Frappe-generated files. For Customize Form export, review the source-state fingerprint and target paths before confirmation; inspect the generated diff in Git when the app tree is shared. Fixture export uses Frappe's Bench command, outside the MCP.
- Never place credentials in tool arguments, source code, logs, screenshots, or evidence.

## Read a record type

1. Call `list_doctypes` with an app or module filter.
2. Call `get_doctype_schema` for the selected DocType.
3. Call `list_documents` with explicit fields, filters, and a small limit.
4. Call `get_document` only for records that need full detail.

## Change a record

1. Read the current record and schema.
2. Call `update_document` with the intended values and `confirm: false`.
3. Confirm that the preview names the expected document and fields.
4. Repeat with `confirm: true` and the exact `expected_modified` value.
5. Read the record again to verify the result.

Use the same preview-first pattern for creation/deletion. Do not create records in a live system without explicit user authorization.

## Customize a site

1. Discover the owning app and module on the remote site.
2. Verify that the app/module exists below configured `FRAPPE_APPS_PATH`.
3. Preview `create_doctype`, `create_custom_field`, or `set_field_property`.
4. Confirm only after reviewing names, field types, options, and destination.
5. For Custom Fields, Property Setters, or other Customize Form changes, call `export_form_customizations` for the target DocType and destination app module. Keep `sync_on_migrate: true`, review the source-state fingerprint, target paths, and `preview_id`, then confirm with that ID. Frappe writes its native `custom/<doctype>.json` export on the Frappe host. The MCP can show changed content and offer local rollback only when the app directory is shared with its process.
6. For fixture export, use Frappe's Bench `export-fixtures` command. The standalone MCP does not recreate this server-side operation because there is no equivalent public HTTP endpoint in the audited Frappe version.
7. If the export needs reversal, preview `rollback_export` and confirm with its latest `preview_id`.
8. Inspect the app's Git diff and use the project's normal deployment process. The MCP does not migrate or deploy.

`export_form_customizations` delegates to Frappe's native Customize Form export and honors its `sync_on_migrate` setting. It does not export arbitrary Frappe state or custom app code. `set_field_property` creates a native Property Setter document, but does not represent the full Customize Form save operation.
