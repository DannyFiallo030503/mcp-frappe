# Tool Reference

The MCP server currently exposes 30 tools. Inputs are JSON objects whose property names match the argument names below. Optional parameters may be omitted. Exact JSON Schemas are available from the server through MCP `tools/list`; this guide explains behavior and operating limits.

All calls run as the authenticated Frappe user. A tool's presence does not grant permission to use it. Errors from Frappe permission and validation checks are expected outcomes, not reasons to bypass the checks.

## Discovery and document reads

### `list_installed_apps()`

Returns installed app names and their Module Def names. No arguments.

### `list_doctypes(app?, module?, include_child_tables=true)`

Lists DocTypes visible to the current user. `app` must be installed; `module` filters by module name. Child table DocTypes are included by default, but are listed only when at least one parent DocType is readable. Each item includes `doctype`, `module`, owning `app`, `custom`, `single`, and `child_table`.

### `get_doctype_schema(doctype, parent_doctype?)`

Returns DocType metadata and only fields readable by the current user. Child-table schema inspection requires its `parent_doctype`. Output includes module/app, custom/single/child-table flags, submittable/tree metadata, title/search/sort/autoname metadata, and readable field descriptors (type, options, required/read-only/hidden/permission level/default/description and list/filter flags).

### `list_documents(doctype, filters?, fields?, limit=20)`

Lists non-child documents through Frappe list permissions. `fields` defaults to `name`; every requested field must be readable. Filters may use searchable or readable field names. `limit` must be 1–100. Results sort by `modified desc`. Read child rows through the parent document.

### `get_document(doctype, name?, fields?, child_row_limit=100)`

Reads one non-child document and selected readable fields. For Single DocTypes, omit `name`; otherwise `name` is required. If fields are omitted, readable fields are returned. Readable child-table rows are included only when their parent field is requested; `child_row_limit` must be 1–200. The result marks child tables truncated when applicable.

## Document writes

Every write tool with `confirm` defaults to preview mode (`false`). Review the preview before the second call. Do not confirm a changed request using an old preview.

### `create_document(doctype, values, name?, confirm=false)`

Creates through the native Frappe controller and validates writable fields. `values` must be a non-empty object. `name` is optional. An explicit name provides idempotent replay for the same requested field values; a conflicting existing record fails. Without a name, autoname behavior is delegated to Frappe and replay is not guaranteed to be idempotent.

### `update_document(doctype, values, name?, expected_modified?, confirm=false)`

Updates through `doc.save()` with native validation and write permissions. `name` is required except for a Single DocType. Preview returns `modified`; confirmation requires that exact value as `expected_modified`.

### `delete_document(doctype, name, expected_modified?, confirm=false)`

Permanently deletes a non-Single document through Frappe after delete permission. Preview returns the current `modified`; confirmation requires that exact value.

### `submit_document(doctype, name, expected_modified?, confirm=false)`

Submits a submittable document through its controller. Requires current `docstatus=0`, native submit permission, and `expected_modified` on confirmation.

### `cancel_document(doctype, name, expected_modified?, confirm=false)`

Cancels a submitted document through its controller. Requires current `docstatus=1`, native cancel permission, and `expected_modified` on confirmation.

### `call_whitelisted_method(method, arguments?, confirm=false)`

Calls an installed app's dotted method path only when Frappe recognizes it as whitelisted and allows POST. It always previews first; preview labels effects as unknown. Confirmation executes the method with the supplied argument object. The tool cannot assess arbitrary method side effects or idempotency. Do not call untrusted methods.

## Schema customization and export

All tools in this section require `System Manager`. The selected app must be installed, and selected modules must belong to that app. Operations use native Frappe documents/controllers where applicable.

### `create_doctype(app, module, name, fields, is_submittable=false, is_child_table=false, confirm=false)`

Creates a standard (non-custom) DocType and exports its JSON into the app's module `doctype/` directory. `fields` is a non-empty array of Frappe DocField objects; consult Frappe's DocType metadata and use valid field types/options. Existing DocTypes and destination files are rejected rather than overwritten.

### `create_custom_field(app, module, doctype, field, confirm=false)`

Creates a native Custom Field on `doctype`. `field` is a Custom Field field object and must include a unique `fieldname`; the server sets its DocType and module. The target DocType must be writable by the connected user.

### `set_field_property(app, module, doctype, property, value, property_type, fieldname?, confirm=false)`

Creates a native Property Setter, equivalent to a Customize Form property override. If `fieldname` is omitted, the property targets the DocType; otherwise it targets that DocField. `property_type` must be a valid Frappe property type and `value` is passed as text.

### `create_client_script(app, module, doctype, name, script, view="Form", enabled=true, confirm=false)`

Creates a Client Script for the target DocType. `view` must be `Form` or `List`; `name` must be unique. `script` is JavaScript executed by the Desk client according to normal Frappe behavior. Treat script content as executable code and review it before confirmation.

### `set_role_permissions(doctype, role, permissions, permlevel=0, confirm=false)`

Creates or updates a Custom DocPerm rule through Frappe's Permission Manager. `permissions` must contain one or more boolean flags from: `read`, `write`, `create`, `delete`, `submit`, `cancel`, `amend`, `report`, `export`, `import`, `share`, `print`, `email`, `select`. `permlevel` must be integer 0–9. Omitted flags retain current values; preview returns current, requested, and resulting values. Some system DocTypes and automatic roles are not configurable through this operation.

### `export_customizations(app, doctypes, modules?, include_standard_doctypes=true, expected_preview?, confirm=false)`

Previews or writes selected customization files into an installed app. `doctypes` must be non-empty. `modules` defaults to all modules owned by `app`; every supplied module must belong to that app. Selected records include Custom Fields, Property Setters, Client Scripts, and Custom DocPerm rules for the selected DocTypes/modules. If enabled, standard DocTypes are exported as JSON only when they belong to the target app and a selected module.

Preview includes fixture membership, file diffs, and a content-derived `preview_id`. Confirmation must supply that exact value as `expected_preview`; a mismatch means preview again. The export backs up existing files and returns a `rollback_id`. Review and commit these files using the app's normal code workflow. Export does not deploy or migrate another site.

### `rollback_export(app, rollback_id, confirm=false)`

Previews or restores the file changes represented by an export manifest. Preview lists files to restore/remove. Confirmation restores prior file contents or removes files created by that export. The manifest is consumed on rollback. This is filesystem rollback, not a database rollback and not a deployment to other sites.

## Reports and Frappe UI metadata

### `list_reports(doctype?, limit=100)`

Lists enabled reports visible to the user, optionally filtered by reference DocType. Requires report permission on the reference DocType and permission to read/run the Report. `limit` must be 1–200.

### `run_report(report_name, filters?, limit=200)`

Runs an enabled Query/Script Report under the current user. Requires a readable Report and report permission on its reference DocType. `filters` is a JSON object; `limit` must be 1–500. Result rows are capped; output contains `returned_rows` and `truncated`.

### `list_workflows(doctype?, limit=100)`

Lists active workflows whose document types are readable to the user. Optional `doctype` filter; `limit` 1–200.

### `get_workflow_actions(doctype, name)`

Returns the native transitions currently available for this user and exact document. The action list can depend on document state, roles, and conditions.

### `apply_workflow_action(doctype, name, action, expected_modified?, confirm=false)`

Previews and applies a currently available native workflow action. Preview includes current/next state and `modified`. Confirmation requires the same action and exact preview `modified` value. Verify the document's new state afterward.

### `list_workspaces(limit=100)` and `get_workspace(name)`

Lists workspaces visible under native Frappe permissions (`limit` 1–200) or reads one permitted workspace including its layout/cards/links.

### `list_print_formats(doctype, limit=100)`

Lists enabled print formats for a readable DocType. `limit` 1–200.

### `render_print_format(doctype, name, print_format?, max_chars=100000)`

Renders HTML using Frappe's print view after document read and print permissions. An optional format must exist and belong to `doctype`. `max_chars` must be 1,000–500,000; output indicates truncation.

## Files and translations

### `list_files(doctype?, name?, limit=100)`

Lists readable File records, optionally attached to a DocType; `name` requires `doctype`. `limit` 1–200. This is not an upload tool.

### `read_file(name, max_bytes=1048576)`

Reads a permitted Frappe File. `max_bytes` must be 1–5,000,000 and the file is rejected when larger. UTF-8 content is returned as text; binary content is base64. Treat either result as potentially sensitive.

### `search_translations(language, query, limit=50)`

Searches readable Translation records by exact language and a contains match on source text. Language and non-empty query are required; `limit` 1–200.

## General limits and behavior

- MCP input schemas can be discovered dynamically with `tools/list`; this reference is explanatory and should be updated with code changes.
- List tools cap result counts; they are not unbounded exports.
- Read/write access is determined at request time from Frappe and may differ by user or site.
- Customization/export file operations require Bench filesystem permissions as well as Frappe authorization.
- No tool provides arbitrary SQL or Python execution.
