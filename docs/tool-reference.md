# Tool reference

All tools execute in the standalone MCP process and use the configured Frappe HTTP session. Native Frappe permissions apply to every remote call. With password auth, an expired session is renewed and the rejected call retried once without any action from the caller; a real permission denial is returned unchanged. List tools cap result sizes; mutation tools preview before acting unless noted.

## Discovery and records

| Tool | Purpose | Important arguments |
|---|---|---|
| `list_installed_apps` | Group Module Def records by owning app | none |
| `list_doctypes` | List site DocTypes | `app`, `module`, `include_child_tables`, `limit` (max 1000) |
| `get_doctype_schema` | Read one DocType's metadata as Frappe's form sees it, Custom Fields and Property Setters applied | `doctype`, `detail` (`compact` or `full`) |
| `list_documents` | List records through Frappe REST | `doctype`, `filters` (object or list), `fields` (max 40), `limit` (max 100) |
| `get_document` | Read a record through Frappe REST; always returns `doctype`, `name`, `modified`, and `docstatus` | `doctype`, `name`, `fields`, `child_row_limit` (max 200) |
| `run_report` | Run a report through Frappe's Query Report endpoint | `report_name`, `filters`, `limit` (max 500) |

The generic resource API is the common access path for standard and custom app DocTypes. A custom DocType or field that the connected user cannot read remains inaccessible.

### Metadata and who can use the record tools

The record tools (`list_documents`, `get_document`, `create_document`, `update_document`) and `get_doctype_schema` read DocType metadata from Frappe's form loader, `frappe.desk.form.load.getdoctype`. It returns `frappe.get_meta` for the DocType and its child tables, with Custom Fields, Property Setters, and Frappe's own field order already applied, to any logged-in user. The record tools never read the `DocType`, `Custom Field`, or `Property Setter` resources, which only System Manager can list. So they work for any user who has permission on the DocType; Frappe still checks that permission on every record read and write.

Metadata is cached in the MCP process for `FRAPPE_META_TTL` seconds (default 300; `0` turns the cache off). `create_doctype`, `update_doctype`, `delete_doctype`, `create_custom_field`, `set_field_property`, and `set_role_permissions` drop the affected DocType, and any DocType that uses it as a child table, from the cache. Creating, updating, or deleting a `DocType`, `DocField`, `DocPerm`, `Custom Field`, `Property Setter`, or `Custom DocPerm` record through the record tools clears the whole cache. A change made outside the MCP (in Desk, say) shows up once its cache entry expires.

### Schema detail

`get_doctype_schema` returns the compact form unless `detail` is `full`:

- `compact` (default): the DocType flags `name`, `module`, `istable`, `issingle`, `is_submittable`, `is_tree`, `autoname`, `title_field`, and `custom`, then `fields` with the data fields only. Layout and display fields (Section Break, Column Break, Tab Break, HTML, Button, Fold, Heading, Image, Attachment Gallery) are left out. Each field has only `fieldname`, `label`, `fieldtype`, `options`, `reqd`, `read_only`, `hidden`, `default`, `unique`, and `permlevel`. Keys whose value is false, `0`, or empty are left out. `child_tables` maps each Table and Table MultiSelect field's DocType to its fields in the same form.
- `full`: `{"doctype": <meta>, "children": {<child DocType>: <meta>}}` with every property Frappe returns (nulls already dropped by Frappe), including layout fields, permissions, links, and actions. Form assets such as client script source and print formats are left out.

```json
{"name": "ToDo", "module": "Desk", "fields": [{"fieldname": "status", "label": "Status", "fieldtype": "Select", "options": "Open\nClosed\nCancelled", "default": "Open"}]}
```

### Default `get_document` output

Without `fields`, `get_document` returns the DocType's data fields that have a value: `null`, `""`, and empty tables are left out, and `0` is kept. Child rows keep only their data fields with a value, plus `name` and `idx`. With `fields`, it returns exactly those fields, and child rows come back whole. Either way, Table and Table MultiSelect fields return at most `child_row_limit` rows, and `_truncated_child_tables` lists the cut fields.

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
| `update_doctype` | Change an existing DocType of your own app, or a site-custom DocType: add, change, remove, and move fields, and set DocType settings | `doctype`, `operations`; preview, then confirm with the same operations and `expected_modified` |
| `delete_doctype` | Delete a DocType created by mistake during development | Preview, then confirm with `expected_modified` |
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

### Editing DocTypes

`update_doctype` changes a DocType of your own app (a standard DocType, which needs Developer Mode) or a site-custom DocType (`custom = 1`). It refuses standard DocTypes of `frappe` and `erpnext`; customize those with `create_custom_field` and `set_field_property`. The tool reads the DocType record, applies `operations` in order to a copy, and saves the whole field list in one request. Fields that are not touched keep their row `name`, so Frappe updates them in place instead of recreating them.

| Operation | Shape | Notes |
|---|---|---|
| `add_field` | `{"op": "add_field", "field": {...}, "insert_after": "fieldname"}` | `field` needs `fieldname` (lowercase letters, digits, `_`, starting with a letter) and `fieldtype`. Without `insert_after` the field goes last; `null` puts it first. The fieldname may not exist on the DocType, Custom Fields included. |
| `update_field` | `{"op": "update_field", "fieldname": "...", "set": {...}}` | `fieldname` can't be changed (renames are not supported). |
| `remove_field` | `{"op": "remove_field", "fieldname": "..."}` | See the data note below. |
| `move_field` | `{"op": "move_field", "fieldname": "...", "insert_after": "fieldname"}` | `insert_after: null` moves the field to the top. |
| `set_properties` | `{"op": "set_properties", "set": {...}}` | DocType settings from the same allowlist as `create_doctype`'s `properties`, except the tables `actions`, `links`, and `states`. |

```json
{
  "doctype": "Lab Sample",
  "operations": [
    {"op": "add_field", "insert_after": "title",
     "field": {"fieldname": "customer", "fieldtype": "Link", "options": "Customer",
               "label": "Customer", "reqd": 1}},
    {"op": "update_field", "fieldname": "status", "set": {"in_list_view": 1}},
    {"op": "move_field", "fieldname": "notes", "insert_after": "customer"},
    {"op": "set_properties", "set": {"track_changes": 1}}
  ]
}
```

Field values may be DocField properties such as `label`, `fieldtype`, `options`, `reqd`, `hidden`, `read_only`, `default`, `description`, `in_list_view`, `in_standard_filter`, `unique`, `fetch_from`, `precision`, `length`, `permlevel`, and the other DocField settings; values are strings, numbers, booleans (stored as `0`/`1`), or `null`. `depends_on`, `mandatory_depends_on`, and `read_only_depends_on` are accepted, as in `set_field_property`. `collapsible_depends_on`, `ignore_xss_filter`, and `options` on HTML and Button fields are refused, because Frappe evaluates or renders them as code. Every fieldtype needs to be one Frappe knows; Link, Select, Table, and Table MultiSelect need `options`; Link and Table targets must exist, and a Table or Table MultiSelect target must be a child table DocType.

The preview returns:

- `changes`: `added` (with `insert_after`), `removed`, `moved`, `changed` (per field, `{property: {"from": old, "to": new}}`), and `properties` (DocType settings, in the same form);
- `warnings`: removed fields and how many records have a value in them, fieldtype changes, `reqd` added to a field that records leave empty (with the count), and `unique` added;
- `modified`, which confirmation must send back as `expected_modified`;
- for standard DocTypes, `app_files` (the DocType JSON Frappe rewrites, resolved under `FRAPPE_APPS_PATH` when that is set) and `requires_developer_mode`.

Removing a field doesn't delete data: Frappe keeps the database column and its values until `bench trim-tables`, but forms, reports, and the API stop showing them. A fieldtype change between related types (the groups Customize Form allows, such as Data/Small Text/Text or Currency/Float/Percent) only warns. Any other change of a field that holds data is refused; clear the values or add a new field. Counts come from `frappe.client.get_count` and follow the user's permissions; Single and Virtual DocTypes have no counts.

Confirmation re-reads the DocType, refuses if `modified` moved, and sends `modified` in the save so Frappe checks it again under a row lock. If a developer-mode Bench reloads during the save, the tool waits for the site, re-reads the DocType, and reports `reconciled_after_server_reload: true` only when the saved DocType holds the change; it never sends the change twice. The DocType's metadata, and that of any child table whose Table field changed, is dropped from the cache.

`delete_doctype` deletes a DocType created by mistake. It refuses `frappe` and `erpnext` DocTypes and any DocType that another DocType uses through a Link, Table, or Table MultiSelect field, Custom Fields included (the error lists them). The preview gives the record count and says what Frappe removes (the DocType record with its fields and permissions, Table-type Custom Fields that point to it, its global search entries, and for a standard DocType in Developer Mode its whole folder in the app, controller code included) and what it keeps (the `tab<DocType>` database table with its records, and the Custom Fields and Property Setters defined on the DocType, counted in `customizations_left`). Only Administrator can delete a standard DocType. Confirm with `expected_modified`.

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
