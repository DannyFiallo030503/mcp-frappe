# AI agent guide

## Operating principles

- Begin with `list_installed_apps`, `list_doctypes`, and `get_doctype_schema`; do not assume an ERPNext, HRMS, or custom app catalog.
- Use the target site's naming and field options exactly as returned by Frappe.
- Request only fields and rows needed. Keep output limits low and narrow filters.
- Frappe permissions are enforced remotely. If a request is denied, report the missing access; do not try alternate APIs to bypass it. The server already renews an expired password session and retries once, so an HTTP 401 or 403 that reaches you is a real denial or a credential problem; do not retry it.
- Treat every mutation as potentially side-effecting because native Frappe hooks and validations run.
- Review previews before confirming. For record updates/deletes, pass the exact `modified` token returned in the preview; if it changed, start over.
- For DocTypes and fixtures, preview the app/module destination and hashes. After export, inspect the local diff and let the developer handle Git review and migrations.
- Never place credentials in tool arguments, source code, logs, screenshots, or evidence.

## Read a record type

1. Call `list_doctypes` with an app or module filter.
2. Call `get_doctype_schema` for the selected DocType. The default `compact` detail gives the data fields with their type, options, and flags, plus child tables; use it for reading and writing records. Ask for `detail="full"` only when editing a DocType needs every property (layout fields, permissions, depends_on, and so on).
3. Call `list_documents` with explicit fields, filters, and a small limit.
4. Call `get_document` only for records that need full detail. Without `fields` it returns only fields that have a value; pass `fields` when you need specific ones, empty values, or whole child rows.

Schema reads are cached for a few minutes (`FRAPPE_META_TTL`). The MCP refreshes a DocType after changing it with its own schema tools; if the developer just changed a DocType in Desk, the old schema may show until the cache expires.

Filters may use the standard columns (`docstatus`, `creation`, `modified`, `owner`, ...) as well as DocType fields. For example, submitted invoices created in October (results come back most recently modified first):

```json
{
  "doctype": "Sales Invoice",
  "fields": ["name", "customer", "grand_total", "creation"],
  "filters": [
    ["docstatus", "=", 1],
    ["creation", "between", ["2026-10-01", "2026-10-31"]]
  ],
  "limit": 20
}
```

`docstatus` is 0 for drafts, 1 for submitted, and 2 for cancelled documents. To match on a child-table value, name the child DocType first: `["Sales Invoice Item", "item_code", "=", "ITEM-001"]`. Read the allowed operators in the tool reference rather than guessing; an unsupported operator is rejected.

## Change a record

1. Read the current record and schema.
2. Call `update_document` with the intended values and `confirm: false`.
3. Confirm that the preview names the expected document and fields.
4. Repeat with `confirm: true` and the exact `expected_modified` value. If the result is "Document changed since preview", someone saved the record in between: re-read it and start over.
5. Read the record again to verify the result.

Use the same preview-first pattern for creation/deletion. Do not pass `name`, `docstatus`, `owner`, or other standard columns as values; they are rejected. The exception is `name` on `create_document` for a DocType whose `autoname` is `Prompt`. Do not create records in a live system without explicit user authorization.

## Change a DocType

Pick the tool by who owns the DocType. Read its `module` from `get_doctype_schema`, and its app from `list_installed_apps`:

- **Your own app's DocType, or a site-custom DocType** (`custom` is set): change it directly with `update_doctype`. A standard DocType needs Developer Mode on the site, and Frappe then rewrites the DocType JSON in the app; commit that file with the app.
- **A Frappe or ERPNext DocType** (`Customer`, `User`, `Sales Invoice`, ...): `update_doctype` refuses it. Add fields with `create_custom_field` and change properties with `set_field_property` (see below), then export them with `export_customizations`.

### Add a field to my DocType

1. Read the schema: `get_doctype_schema(doctype="Lab Sample")`. Pick where the field goes (`insert_after`) and check its fieldname is free.
2. Preview: `update_doctype(doctype="Lab Sample", operations=[{"op": "add_field", "insert_after": "title", "field": {"fieldname": "customer", "fieldtype": "Link", "options": "Customer", "label": "Customer"}}])`.
3. Check `changes` lists only what you meant, and read every `warnings` entry to the user.
4. Confirm: the same call with the same `operations`, `confirm=True`, and `expected_modified` set to the preview's `modified`.
5. Read the schema again to verify. For a standard DocType, the app's `<doctype>.json` now has the field; show the user the Git diff.

Put several changes in one `operations` list rather than calling the tool once per change: each confirmation is one save, and a standard DocType save can make the Bench reload. Other operations: `update_field` (`set` the properties to change), `remove_field`, `move_field` (`insert_after: null` moves to the top), and `set_properties` for DocType settings such as `track_changes` or `title_field`.

Things to tell the user before confirming:

- Removing a field hides its data but doesn't delete it; the column stays until `bench trim-tables`. The preview says how many records have a value.
- A fieldtype change that could lose data is refused. Suggest a new field instead, or clearing the old values first.
- Making a field mandatory when records leave it empty means those records fail to save until filled; the preview counts them.
- Renaming a field is not supported. Don't simulate it with remove plus add: the data stays under the old column.

`delete_doctype` is only for a DocType created by mistake. It refuses a DocType that other DocTypes link to and lists them. Its preview says what Frappe removes (in Developer Mode, the whole folder with controller code) and what stays (the database table and its records). Ask the user before confirming.

## Keep the site and the app in sync

### What changed between my site and my app

1. Start broad: `list_app_drift(app="my_app")`. Each item has a verdict; page with `offset=next_offset` when `next_offset` is set.
2. For each DocType that isn't `in_sync`, call `diff_doctype(doctype=...)` and read `summary`, `doctype_file.changes`, and `warnings`. `changes` reads from the site (`from`) to the app file (`to`): `added` fields exist only in the file, `removed` only on the site.
3. Tell the user what to do for each verdict:
   - `app_ahead` or `missing_on_site`: run `bench migrate`.
   - `site_ahead`: the site has the newer DocType. With developer mode on, save it once (any `update_doctype` change, or Desk) so Frappe rewrites the file, then commit it. If the result warns that migrate would re-import the file, do this before anyone migrates.
   - `diverged`: both sides changed. Show the user the changes and let them decide; don't migrate or export until they have.
   - `missing_in_app` on customizations: export them with `export_customizations`.
   - `site_custom`: see the next recipe.
4. Never run Bench commands yourself; the MCP doesn't, and the user's site may hold work that `bench migrate` would replace.

### Ship a prototype DocType with my app

1. Check the DocType is site-custom: `diff_doctype(doctype="Lab Sample")` says `site_custom`.
2. Pick the app and a module of it (`list_installed_apps`). The module folder must exist under `FRAPPE_APPS_PATH`.
3. Preview: `promote_custom_doctype(doctype="Lab Sample", app="my_app", module="My Module")`. Read `files` and every `warnings` entry to the user: other site-custom DocTypes it links to (promote those first, or the app ships a broken Link), and Custom Fields or Property Setters that stay customizations.
4. Confirm with `confirm=True` and `expected_modified` set to the preview's `modified`. The site needs developer mode.
5. Check `native_files_complete`, then run `diff_doctype` again: it should say `in_sync`. Show the user the new folder in the Git diff.

## Approval flows and alerts

Workflows and Notifications are configuration records. Use `upsert_workflow` and `upsert_notification`, not `create_document`: they check roles, states, fields, and templates, and keep Python conditions and Jinja code out. Never try to work around a refused condition or template by writing the record with `create_document` or `update_document`; tell the user what was refused and why.

### Add an approval workflow

1. Read the DocType: `get_doctype_schema(doctype="Leave Request")`. Note `is_submittable` (only then can a state have `doc_status` 1 or 2) and the field to set on approval, if any.
2. Check what exists: `get_workflow(doctype="Leave Request")`. If there is an active Workflow, ask whether to replace it (same `name`) or create another (the new one becomes active and Frappe deactivates the old).
3. Confirm the role names with the user (`list_documents(doctype="Role", fields=["name"])`). Every `allow_edit` and `allowed` role must exist.
4. Preview `upsert_workflow` with the states in order (the first one is the draft starting state) and the transitions. Show the user `creates` (masters that will be added), `state_field` (Frappe adds a hidden `workflow_state` Custom Field if needed), `deactivates`, and every `warnings` entry. A state that no transition reaches is refused; add the missing transition.
5. Confirm with `confirm=True` (plus `expected_modified` from the preview when replacing). If the user can't express a rule with roles and states and asks for a transition condition, explain that conditions are Python that Frappe evaluates and this server doesn't write them; they can add one in Desk.
6. To ship it with the app: `export_customizations(app=..., module=..., doctypes=["Leave Request"], with_workflows=True)`, review, and confirm with the `preview_id`.

### Notify on submit

1. Read the DocType with `get_doctype_schema` and pick the recipient field: `owner`, a Data field with options Email, or a Link to User. Or use a role.
2. Write `subject` and `message` as plain text (Markdown or HTML is fine) with `{{ doc.<fieldname> }}` placeholders for real fields, and only the allowed filters (`upper`, `round(2)`, `default("n/a")`, ...). No `{% if %}`, loops, or `frappe.*` calls: the tool refuses them. To send only some documents, use `filters` (for example `{"status": "Approved"}`), never a Python condition.
3. Preview:
   ```text
   upsert_notification(name="Leave Submitted", document_type="Leave Request", event="Submit",
       channel="Email", recipients=[{"receiver_by_document_field": "leave_approver"}],
       subject="Leave {{ doc.name }} submitted",
       message="{{ doc.employee_name }} asks for leave from {{ doc.from_date }} to {{ doc.to_date }}.")
   ```
   Check `record` and `warnings` with the user. For an in-app alert instead of email, use `channel="System Notification"`.
4. Confirm with `confirm=True` (plus `expected_modified` when replacing an existing rule; its `changes` show what moves).
5. To ship it with the app, preview again with `is_standard=True` and `module` set to a module of the user's app, show `app_files`, and confirm. The site needs developer mode; Frappe writes the files.

## Customize a site

1. Discover the owning app and module on the remote site.
2. For a standard DocType or an export, verify that the app/module exists below the configured `FRAPPE_APPS_PATH`. Custom Fields and Property Setters don't need a local checkout; pass `module` (and `app`) only if you will export them later.
3. Preview `create_doctype`, `create_custom_field`, or `set_field_property`.
4. Confirm only after reviewing names, field types, options, and destination.
5. To export, call `export_customizations`, review every path, diff, and `preview_id`, then confirm with that ID. If it returns `nothing_to_export`, there is nothing to confirm. Tell the user that `bench migrate` applies the exported files.
6. If the export needs reversal, preview `rollback_export` and confirm with its latest `preview_id`.
7. Inspect the app's Git diff and use the project's normal deployment process. The MCP does not migrate or deploy.

### Custom Fields and form properties

- Add a field: `create_custom_field(doctype="Customer", field={"fieldname": "custom_region", "fieldtype": "Data", "label": "Region", "insert_after": "customer_name"})`. Don't put `doctype`, `dt`, or `module` inside `field`; they are rejected.
- Edit a Custom Field: `update_document` on the `Custom Field` DocType, name `<DocType>-<fieldname>` (for example `Customer-custom_region`). Remove it with `delete_document` on the same record. Both follow the usual preview and `expected_modified` steps.
- Change a form property (label, hidden, reqd, ...): `set_field_property`. Leave out `property_type`; the tool infers it. Check properties take `0`/`1` or booleans. If the preview says `mode: "update"`, a Property Setter already exists: confirm with the preview's `modified` as `expected_modified`. If a DocType-level preview lists `replaces_field_setters`, Frappe will delete those field-level setters; tell the user before confirming.

### Exporting customizations

- `export_customizations(app="my_app", module="My Module", doctypes=["Customer"])` writes `my_app/my_app/my_module/custom/customer.json`, Frappe's native customization file, plus one file per child table of `Customer` that has customizations. `bench migrate` imports it.
- Pass `with_permissions=True` only when the user wants Custom DocPerms in the app; on migrate they replace the site's Custom DocPerms for that DocType.
- Pass `apply_module_filter=True` to export only the Custom Fields and Property Setters created with that `module`. Set `module` when you create them if you plan to use this.
- Client Scripts with a matching `module` go to `my_app/my_app/fixtures/client_script.json`, and only then is `hooks.py` changed.
- Pass `with_workflows=True` to also export the DocTypes' Workflows (and the Workflow State and Workflow Action Master records they use) as fixtures. `bench migrate` re-imports them on every run, overwriting site edits; tell the user.
- The exporter never writes DocType JSON; Frappe writes standard DocType files itself in developer mode. It doesn't export site-custom DocTypes (use `promote_custom_doctype`), arbitrary Frappe state, or app code.
