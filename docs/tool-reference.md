# Tool reference

All tools execute in the standalone MCP process and use the configured Frappe HTTP session. Native Frappe permissions apply to every remote call. With password auth, an expired session is renewed and the rejected call retried once without any action from the caller; a real permission denial is returned unchanged. List tools cap result sizes; mutation tools preview before acting unless noted.

## Discovery and records

| Tool | Purpose | Important arguments |
|---|---|---|
| `list_installed_apps` | Group Module Def records by owning app | none |
| `list_doctypes` | List site DocTypes | `app`, `module`, `include_child_tables`, `limit` (max 1000) |
| `get_doctype_schema` | Read one DocType definition merged with Custom Fields and Property Setters | `doctype` |
| `list_documents` | List records through Frappe REST | `doctype`, `filters` (object or list), `fields` (max 40), `limit` (max 100) |
| `get_document` | Read a record through Frappe REST; always returns `name`, `modified`, and `docstatus` | `doctype`, `name`, `fields`, `child_row_limit` (max 200) |
| `run_report` | Run a report through Frappe's Query Report endpoint | `report_name`, `filters`, `limit` (max 500) |

The generic resource API is the common access path for standard and custom app DocTypes. A custom DocType or field that the connected user cannot read remains inaccessible.

### Readable fields

`fields` and filters accept the DocType's fields (including Custom Fields) plus these standard columns: `name`, `owner`, `creation`, `modified`, `modified_by`, `docstatus`, and `idx`. Filters on a child table may also use `parent`, `parenttype`, and `parentfield`. Any other name is rejected before the request is sent.

```json
{"doctype": "Sales Invoice", "fields": ["name", "modified", "docstatus"], "filters": {"docstatus": 1}}
```

### Filter shapes

`list_documents` accepts the filter shapes Frappe's REST API accepts:

| Shape | Example |
|---|---|
| Object, equality | `{"status": "Open"}` |
| Object, operator | `{"grand_total": [">", 100]}` |
| List of `[field, operator, value]` | `[["status", "=", "Open"], ["creation", ">=", "2026-10-01"]]` |
| List of `[doctype, field, operator, value]` | `[["Sales Invoice Item", "item_code", "=", "ITEM-001"]]` |

All conditions are combined with AND. In the four-element form, `doctype` must be the listed DocType itself or the `options` of one of its `Table` or `Table MultiSelect` fields, and the field must exist on that DocType; the filter then matches parents with at least one such child row.

### Filter operators

`=`, `!=`, `<`, `>`, `<=`, `>=`, `like`, `not like`, `in`, `not in`, `between`, `is`, `timespan`. Operators are matched case-insensitively; anything else is rejected with the allowed list.

| Operator | Example |
|---|---|
| `like` / `not like` | `["title", "like", "%invoice%"]` |
| `in` / `not in` | `["status", "in", ["Open", "Overdue"]]` |
| `between` | `["posting_date", "between", ["2026-10-01", "2026-10-31"]]` |
| `is` | `["due_date", "is", "set"]` (or `"not set"`) |
| `timespan` | `["creation", "timespan", "this week"]` (also `today`, `last month`, `this year`, ...) |

## Mutations

| Tool | Purpose | Confirmation |
|---|---|---|
| `create_document` | Create using native DocType validation | First call with `confirm: false`; inspect, then confirm |
| `update_document` | Update a record using the native resource API | Confirm with `expected_modified` from the preview |
| `delete_document` | Delete a record | Confirm with `expected_modified` from the preview |
| `create_doctype` | Create a standard app DocType or a site-custom DocType through Frappe's native controller | `fields`, `is_submittable`, `is_child_table`, `is_single`, `is_tree`, `is_custom`, `properties`, and `permissions`; preview, inspect, then confirm |
| `set_role_permissions` | Add or update role permissions through Frappe's native Permission Manager | `doctype`, `role`, `permissions`, `permlevel`; preview, then confirm |
| `create_custom_field` | Add a Custom Field | `doctype`, `field`, optional `app`/`module`; preview, then confirm |
| `set_field_property` | Create or update the Property Setter for an allowlisted form property | `doctype`, `property`, `value`, optional `fieldname`/`property_type`/`app`/`module`; preview, then confirm (with `expected_modified` when the setter exists) |
| `export_customizations` | Export site customizations to Frappe's native `custom/` files (and Client Scripts to app fixtures) so `bench migrate` applies them | `app`, `module`, `doctypes`, optional `with_permissions`/`apply_module_filter`/`sync_on_migrate`; confirm with the current `preview_id` |
| `rollback_export` | Restore or remove files from one prior export | Preview, then confirm with the current rollback `preview_id` |

Frappe still decides whether the user may perform the operation. A preview is not a substitute for checking permissions, and a confirmed mutation may trigger native hooks or side effects.

`create_document` and `update_document` accept only the DocType's own fields: standard columns (`name`, `owner`, `docstatus`, `modified`, child-table `parent` columns, ...) and read-only fields are rejected. The one exception is `name` on `create_document` when the DocType's `autoname` is `Prompt` (naming rule "Set by user"); it is sent to Frappe as `__newname`, the way Desk does.

`update_document` checks `expected_modified` before writing and also sends it in the update, so Frappe compares it with the stored value under a row lock. If the document changed in between, the update fails with "Document changed since preview"; request a fresh preview.

`create_doctype` accepts native DocField objects in `fields`, so Frappe's DocType controller validates field types and properties. DocType-level values such as `autoname`, `track_changes`, `title_field`, `search_fields`, `sort_field`, and `sort_order` go in `properties`; the MCP restricts this object to recognized DocType settings. Role rules go in `permissions`.

By default, `is_custom: false` creates a standard DocType. It requires Developer Mode, and the app/module must exist on the site and under `FRAPPE_APPS_PATH`. Frappe writes the standard JSON and controller scaffolding; it also creates JavaScript/test scaffolds for non-child DocTypes. The MCP stages Frappe's own templates before the metadata request and reconciles a development-server restart against the site's DocType record. It refuses source writes to the protected `frappe` and `erpnext` apps.

Set `is_custom: true` to create a site-custom DocType. Frappe stores that definition in site metadata and does not generate app JSON/Python/JavaScript files; this path still requires an existing remote Module Def belonging to `app`, but does not require a local checkout, `FRAPPE_APPS_PATH`, or Developer Mode. Frappe rejects custom Virtual DocTypes. The live v15 matrix verified site-custom metadata and record CRUD; see the [manual report](../e2e/manual_functional_report.md).

`create_custom_field` and `set_field_property` are database-backed site customizations. They need no local app checkout, `FRAPPE_APPS_PATH`, or Developer Mode. `app` and `module` are optional: `module` only tags the record for a later export. When `module` is given, its Module Def must exist on the site; when `app` is given too, the module must belong to it. `app` alone is rejected.

`create_custom_field` takes the Custom Field values in `field` (`fieldname`, `fieldtype`, `label`, `insert_after`, ...). The target DocType and module come only from the arguments: a `field` that contains `doctype`, `dt`, or `module` is rejected. A `fieldname` that already exists on the DocType is rejected. To edit or remove a Custom Field afterwards, use `update_document` or `delete_document` on the `Custom Field` DocType; its name is `<DocType>-<fieldname>`.

`set_field_property` sets `property` on `fieldname`, or on the DocType itself when `fieldname` is omitted. The supported properties and their types follow Customize Form:

| Type | Properties | Accepted `value` |
|---|---|---|
| `Check` | `hidden`, `read_only`, `reqd`, `in_list_view`, `in_standard_filter`, `collapsible`, `bold` | `0`, `1`, `"0"`, `"1"`, or a boolean; stored as `"0"`/`"1"` |
| `Data` | `label`, `width`, `depends_on`, `mandatory_depends_on`, `read_only_depends_on` | a string |
| `Text` | `options`, `description`, `default` | a string |

`property_type` is inferred from this table; a value passed in must match it. The tool first looks for an existing Property Setter for the same DocType, field, and property:

- None exists: the preview has `mode: "create"` and confirmation creates one. A new DocType-level setter makes Frappe delete the field-level setters for the same property on that DocType; the preview lists those fields in `replaces_field_setters`.
- One exists: the preview has `mode: "update"`, the setter's `name`, `current_value`, the new `value`, and `modified`. Confirm with `expected_modified` set to that `modified`; the setter is updated in place with the same row-locked timestamp check as `update_document`.

`set_role_permissions` uses Frappe's System Manager-only Permission Manager methods. The connected account must have the System Manager role, and the target DocType and role must already exist. The tool does not create roles or assign roles to users.

## File export details

`FRAPPE_APPS_PATH` must point to a local Bench `apps/` directory. The requested app and module must exist below it, and the corresponding remote Module Def must name that app as owner. Path traversal and escaping symlinks are rejected. Source export refuses the protected `frappe` and `erpnext` apps. The export preview reports destination paths, bounded diffs, and content hashes. Existing files are backed up under the selected app's `.mcp-frappe-backups/` directory; inspect and commit the resulting files with Git. A successful rollback consumes its manifest and backup files.

`export_customizations` writes Frappe's native module customization file, the one Customize Form's "Export Customizations" writes, to `<app>/<app>/<module>/custom/<doctype>.json` (the DocType name lower-cased, spaces and hyphens replaced by `_`). The file has the keys `custom_fields`, `property_setters`, `custom_perms`, `links`, `doctype`, and `sync_on_migrate`, with full rows as Frappe exports them, and is serialized like `frappe.as_json`. Every child-table DocType the DocType uses gets its own file. A DocType with no Custom Fields, Property Setters, or (when exported) Custom DocPerms gets no file and is listed under `empty`; when nothing at all is left the preview returns `nothing_to_export: true` and no `preview_id`.

- `with_permissions` (default false) adds the DocType's Custom DocPerm rows. On migrate, Frappe deletes the site's Custom DocPerms for that DocType and inserts the exported ones.
- `apply_module_filter` (default false) keeps only Custom Fields and Property Setters whose `module` is the selected module, like Frappe's "Apply Module Export Filter".
- `sync_on_migrate` (default true; Frappe's own default is false) makes `bench migrate` import the file through `sync_customizations`.

The export may target any DocType, including one owned by another app such as `Customer`; `module` only chooses where the file goes. Client Scripts for the exported DocTypes whose `module` is the selected module go to the app-level `<app>/<app>/fixtures/client_script.json`, in the row format of `bench export-fixtures`. Rows already in that file for other DocTypes or modules are kept. Only then does the exporter add or update a clearly marked block in the app's `hooks.py` that registers a `Client Script` fixture filter; text outside the block is preserved, and fixture entries for other DocTypes left there by older exports are dropped from it. Without Client Scripts, `hooks.py` is not touched.

DocType JSON is never written: in developer mode Frappe writes standard DocType files itself, and `create_doctype` relies on that.

The exporter creates local backups and a rollback manifest, and `rollback_export` refuses if an exported file changed after export. Rollback restores overwritten files, deletes created files, and removes the directories the export created once they are empty. Manifests from older exports, which wrote to `<module>/fixtures/`, still roll back. The MCP does not deploy files, run migrations, or roll back remote database changes.

## Deliberate exclusions

There is no arbitrary Python execution, SQL execution, or unrestricted whitelisted-method dispatcher. Other Frappe features such as workflow actions, file downloads, translations, print rendering, and arbitrary app-specific method calls require explicit wrappers and are not exposed by this standalone release yet. Do not infer a tool exists from the retained legacy source tree.
