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
2. Call `get_doctype_schema` for the selected DocType.
3. Call `list_documents` with explicit fields, filters, and a small limit.
4. Call `get_document` only for records that need full detail.

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
- The exporter never writes DocType JSON; Frappe writes standard DocType files itself in developer mode. It doesn't export site-custom DocTypes, arbitrary Frappe state, or app code.
