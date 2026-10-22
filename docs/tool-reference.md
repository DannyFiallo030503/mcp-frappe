# Tool reference

All tools execute in the standalone MCP process and use the configured Frappe HTTP session. Native Frappe permissions apply to every remote call. List tools cap result sizes; mutation tools preview before acting unless noted.

## Discovery and records

| Tool | Purpose | Important arguments |
|---|---|---|
| `list_installed_apps` | Group Module Def records by owning app | none |
| `list_doctypes` | List site DocTypes | `app`, `module`, `include_child_tables`, `limit` (max 1000) |
| `get_doctype_schema` | Read DocType metadata composed by Frappe's Desk metadata endpoint | `doctype` |
| `list_documents` | List records through Frappe REST | `doctype`, `filters`, `fields`, `limit` (max 100) |
| `get_document` | Read a record through Frappe REST, including system `docstatus` when present | `doctype`, `name` |
| `run_report` | Run a report through Frappe's Query Report endpoint | `report_name`, `filters`, `limit` (max 500) |

The generic resource API is the common access path for standard and custom app DocTypes. A custom DocType or field that the connected user cannot read remains inaccessible. Filter expressions are passed to Frappe's REST resource endpoint and must be appropriate for that endpoint.

## Mutations

| Tool | Purpose | Confirmation |
|---|---|---|
| `create_document` | Create using native DocType validation | First call with `confirm: false`; inspect, then confirm |
| `update_document` | Update a record using the native resource API | Confirm with `expected_modified` from the preview |
| `delete_document` | Delete a record | Confirm with `expected_modified` from the preview |
| `create_doctype` | Create a standard app DocType or a site-custom DocType through Frappe's native controller | `fields`, `is_submittable`, `is_child_table`, `is_single`, `is_tree`, `is_custom`, `properties`, and `permissions`; preview, inspect, then confirm |
| `set_role_permissions` | Add or update role permissions through Frappe's native Permission Manager | `doctype`, `role`, `permissions`, `permlevel`; preview, then confirm |
| `create_custom_field` | Add a Custom Field | Preview, then confirm |
| `set_field_property` | Add a Property Setter for an allowlisted form property | Preview, then confirm |
| `export_form_customizations` | Export Customize Form data to the app module's native `custom/<doctype>.json` file | `app`, destination `module`, `doctype`; defaults `sync_on_migrate` to true; preview, then confirm |
| `rollback_export` | Restore or remove files from one prior export | Preview, then confirm with the current rollback `preview_id` |

Frappe still decides whether the user may perform the operation. A preview is not a substitute for checking permissions, and a confirmed mutation may trigger native hooks or side effects.

`create_doctype` accepts native DocField objects in `fields`, so Frappe's DocType controller validates field types and properties. DocType-level values such as `autoname`, `track_changes`, `title_field`, `search_fields`, `sort_field`, and `sort_order` go in `properties`; the MCP restricts this object to recognized DocType settings. Role rules go in `permissions`.

By default, `is_custom: false` creates a standard DocType. It requires Developer Mode, and the app/module must exist on the site and under `FRAPPE_APPS_PATH`. The MCP posts the DocType through Frappe's REST resource API; Frappe's DocType controller owns schema setup and generates the standard JSON and controller scaffolding. The MCP does not precreate these files. It reconciles a development-server restart against the site's DocType record. It refuses source writes to the protected `frappe` and `erpnext` apps.

Set `is_custom: true` to create a site-custom DocType. Frappe stores that definition in site metadata and does not generate app JSON/Python/JavaScript files; this path still requires an existing remote Module Def belonging to `app`, but does not require a local checkout, `FRAPPE_APPS_PATH`, or Developer Mode. Frappe rejects custom Virtual DocTypes. The live v15 matrix verified site-custom metadata and record CRUD; see the [manual report](../e2e/manual_functional_report.md).

`set_role_permissions` uses Frappe's System Manager-only Permission Manager methods. The connected account must have the System Manager role, and the target DocType and role must already exist. The tool does not create roles or assign roles to users.

## File export details

`FRAPPE_APPS_PATH` must point to the app tree visible to the MCP. The requested app and destination module must exist below it, and the corresponding remote Module Def must name that app as owner. `export_form_customizations` calls Frappe's whitelisted `frappe.modules.utils.export_customizations` method, which writes the native `custom/<doctype>.json` format on the Frappe host and uses `sync_on_migrate` by default. The source DocType may belong to another app. Preview reports the source-state fingerprint and expected target paths; Frappe exposes no dry-run for this method, so the MCP does not invent a content diff. Frappe Developer Mode and the System Manager role are required.

If Frappe and the MCP see the same app directory, the MCP detects changed files, keeps backups, and provides a guarded `rollback_export`. If they do not share that directory, Frappe still performs the export but the MCP cannot inspect, back up, or roll back the host-side files; the result reports that limitation. Review and commit the generated files through normal source control. The MCP does not deploy files, run migrations, or roll back remote database changes.

Fixture export is not exposed as an MCP tool. In this Frappe version, `bench export-fixtures` calls `frappe.utils.fixtures.export_fixtures`, which has no public HTTP method equivalent. Run the Bench command in the target Bench when fixtures are needed; a future MCP wrapper must invoke a constrained native adapter rather than reimplement fixture serialization.

## Deliberate exclusions

There is no arbitrary Python execution, SQL execution, or unrestricted whitelisted-method dispatcher. Other Frappe features such as workflow actions, file downloads, translations, print rendering, and arbitrary app-specific method calls require explicit wrappers and are not exposed by this standalone release yet. `set_field_property` creates a native Property Setter document through REST; it does not submit the full Customize Form editor state or cover links/actions/states. Do not infer a tool exists from the retained legacy source tree.
