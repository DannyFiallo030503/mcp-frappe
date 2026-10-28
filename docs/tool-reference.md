# Tool reference

All tools execute in the standalone MCP process and use the configured Frappe HTTP session. Native Frappe permissions apply to every remote call. With password auth, an expired session is renewed and the rejected call retried once without any action from the caller; a real permission denial is returned unchanged. List tools cap result sizes; mutation tools preview before acting unless noted.

## Discovery and records

| Tool | Purpose | Important arguments |
|---|---|---|
| `list_installed_apps` | Group Module Def records by owning app | none |
| `list_doctypes` | List site DocTypes | `app`, `module`, `include_child_tables`, `limit` (max 1000) |
| `get_doctype_schema` | Read one DocType definition merged with Custom Fields and Property Setters | `doctype` |
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
| `export_customizations` | Export DocType JSON and selected customization fixtures | Confirm with the current `preview_id` |
| `rollback_export` | Restore or remove files from one prior export | Preview, then confirm with the current rollback `preview_id` |

Frappe still decides whether the user may perform the operation. A preview is not a substitute for checking permissions, and a confirmed mutation may trigger native hooks or side effects.

`create_doctype` accepts native DocField objects in `fields`, so Frappe's DocType controller validates field types and properties. DocType-level values such as `autoname`, `track_changes`, `title_field`, `search_fields`, `sort_field`, and `sort_order` go in `properties`; the MCP restricts this object to recognized DocType settings. Role rules go in `permissions`.

By default, `is_custom: false` creates a standard DocType. It requires Developer Mode, and the app/module must exist on the site and under `FRAPPE_APPS_PATH`. Frappe writes the standard JSON and controller scaffolding; it also creates JavaScript/test scaffolds for non-child DocTypes. The MCP stages Frappe's own templates before the metadata request and reconciles a development-server restart against the site's DocType record. It refuses source writes to the protected `frappe` and `erpnext` apps.

Set `is_custom: true` to create a site-custom DocType. Frappe stores that definition in site metadata and does not generate app JSON/Python/JavaScript files; this path still requires an existing remote Module Def belonging to `app`, but does not require a local checkout, `FRAPPE_APPS_PATH`, or Developer Mode. Frappe rejects custom Virtual DocTypes. The live v15 matrix verified site-custom metadata and record CRUD; see the [manual report](../e2e/manual_functional_report.md).

`set_role_permissions` uses Frappe's System Manager-only Permission Manager methods. The connected account must have the System Manager role, and the target DocType and role must already exist. The tool does not create roles or assign roles to users.

## File export details

`FRAPPE_APPS_PATH` must point to a local Bench `apps/` directory. The requested app and module must exist below it, and the corresponding remote Module Def must name that app as owner. Path traversal and escaping symlinks are rejected. Source export refuses the protected `frappe` and `erpnext` apps. The export preview reports destination paths, bounded diffs, and content hashes. Existing files are backed up under the selected app's `.mcp-frappe-backups/` directory; inspect and commit the resulting files with Git. A successful rollback consumes its manifest and backup files.

The standalone exporter handles standard DocType JSON, Custom Field, Property Setter, Client Script, and Custom DocPerm fixtures. It adds or updates a clearly marked fixture block in the app's `hooks.py`, preserving text outside that managed block. It creates local backups and a rollback manifest, and refuses rollback if an exported file changed after export. It does not deploy files, run migrations, or rollback remote database changes.

## Deliberate exclusions

There is no arbitrary Python execution, SQL execution, or unrestricted whitelisted-method dispatcher. Other Frappe features such as workflow actions, file downloads, translations, print rendering, and arbitrary app-specific method calls require explicit wrappers and are not exposed by this standalone release yet. Do not infer a tool exists from the retained legacy source tree.
