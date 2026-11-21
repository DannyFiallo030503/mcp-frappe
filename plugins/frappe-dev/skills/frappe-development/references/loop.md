# The change, compare, export, sync, check loop

## Change a DocType of your own app

1. `get_doctype_schema(doctype)`. Pick `insert_after` positions and check fieldnames are free.
2. Preview `update_doctype(doctype, operations=[...])`. Operations: `add_field`, `update_field` (`set` the properties), `remove_field`, `move_field`, `set_properties`. Put every change in one list: each confirmation is one save, and a standard DocType save can make the Bench reload.
3. Read `changes` and every `warnings` entry to the user. Removing a field hides its data but keeps the column; a lossy fieldtype change is refused; a new mandatory field makes records without a value fail to save until filled.
4. Confirm with `confirm=true` and `expected_modified` from the preview.
5. Read the schema again. For a standard DocType, the app's `<doctype>.json` changed: show the Git diff.

A new DocType: `create_doctype(app, module, name, fields, ...)`. A child table is a DocType with `is_child_table=true`, then a `Table` field whose `options` names it.

Never rename a field with remove plus add: the data stays under the old column. `rename(kind="field", ...)` writes a patch and a `patches.txt` line; the field reads empty until migrate runs the patch.

## Customize a Frappe or ERPNext DocType

- `customize_doctype(doctype, operations=[...])`: `add_custom_field`, `set_property`, `move_field`, `change_fieldtype`. One save, the same result as Customize Form in Desk. Confirm with `preview_id`.
- `create_custom_field(doctype, field={...})` and `set_field_property(doctype, property, value, fieldname=...)` for one change. Pass `module` when you will export with `apply_module_filter`.
- Remove a Custom Field: `delete_document(doctype="Custom Field", name="<DocType>-<fieldname>")`.
- Export: `export_customizations(app, module, doctypes=[...])` writes `<app>/<app>/<module>/custom/<doctype>.json`, which `bench migrate` imports. `rollback_export` undoes an export.

## Compare: verdicts

From `diff_doctype` and `list_app_drift`:

| Verdict | Meaning | What to do |
|---|---|---|
| `in_sync` | Site and files agree | Nothing |
| `site_ahead` | The site has the newer DocType | Save it once in developer mode so Frappe rewrites the file, then commit |
| `app_ahead`, `missing_on_site` | The files are newer, or the site never imported them | Migrate, or `reload-doc` for one DocType |
| `diverged` | Both changed | Show the user both sides; don't migrate or export until they decide |
| `missing_in_app` (customizations) | Custom Fields or Property Setters not exported | `export_customizations` |
| `site_custom` | A DocType that lives only on the site | `promote_custom_doctype` if it should ship with the app |

## Sync with `run_bench`

Listed only when the user set `FRAPPE_BENCH_PATH` for a local site. Commands: `migrate`, `reload-doc`, `export-fixtures`, `clear-cache`, `run-tests`. Show the preview's `argv`, `warning`, and `preflight` first. A `migrate` preview listing `site_ahead` or `diverged` DocTypes would overwrite site changes: stop and ask. On `ok: false`, read the end of `output` and `get_recent_errors`; don't rerun blindly. For `run-tests`, read `tests.status`: Frappe v15 exits 0 when tests fail.

## Check

1. `list_app_drift(app)`: what you touched should be `in_sync`.
2. `list_pending_patches(app)`: `pending` hasn't run here; `skipped: true` means a migrate skipped it after a failure. Show `execute:` lines as text, never run them.
3. `get_recent_errors(since=...)`: read `exception` first; quote only the traceback lines that explain the failure, since tracebacks can hold record values.

## Configuration records

- `upsert_workflow(doctype, name, states, transitions)`: new documents start in the first state; states with `doc_status` 1 or 2 need a submittable DocType; roles must exist. Transition conditions are code and are refused.
- `upsert_notification(name, document_type, event, channel, recipients, subject, message)`: recipients by role or by a document field.
- `upsert_report(name, ref_doctype, columns, filters=...)`: Report Builder only; Query and Script Reports are code.
- `upsert_workspace(...)`: shortcuts, link cards, number cards.

Export a Workflow with `export_customizations(..., with_workflows=True)`.
