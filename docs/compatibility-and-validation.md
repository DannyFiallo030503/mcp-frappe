# Compatibility and validation

What has been run against a real Frappe site, on which version, and where the evidence is. Unit tests (`uv run pytest -q`, on Python 3.10 and 3.12 in CI) cover every tool against a fake site built from Frappe v16.33.1's source; this page is about live sites.

## Matrix

Each cell links to the redacted evidence for that run, or says **not tested**. "0.2.0" means the run used the server as it was on 2026-10-16/19, before the work-plan stages that changed most tool contracts. Treat those cells as history: the paths passed then, and the current code has not been re-run on a live site yet.

| Area | Tools | Frappe v15 (15.96, ERPNext 15.96) | Frappe v16 |
|---|---|---|---|
| Setup check | `mcp-frappe check` | not tested | not tested |
| Discovery and schema | `list_installed_apps`, `list_doctypes`, `get_doctype_schema` | 0.2.0, Administrator: passed ([smoke](../e2e/history/standalone_real_http_evidence.json)) | not tested |
| Site information | `get_site_info` | not tested | not tested |
| Record reads | `list_documents`, `get_document` | 0.2.0, Administrator: passed ([smoke](../e2e/history/standalone_real_http_evidence.json)) | not tested |
| Counts and search | `count_documents`, `search_records` | not tested | not tested |
| Record writes | `create_document`, `update_document`, `delete_document` | 0.2.0, Administrator: passed on ToDo, stale tokens refused ([mutation](../e2e/history/standalone_real_http_evidence.json)) | not tested |
| Submit and cancel | `submit_document`, `cancel_document` | not tested | not tested |
| Reports | `list_reports`, `get_report_filters`, `run_report` | `run_report` 0.2.0, Administrator: passed ([smoke](../e2e/history/standalone_real_http_evidence.json)); the other two not tested | not tested |
| DocType creation | `create_doctype` (standard and site-custom) | 0.2.0, Administrator: passed for regular, child, Single, Tree, submittable, Virtual, and site-custom, with all 43 field types ([matrix](../e2e/history/doctype_matrix_evidence.json), [manual report](../e2e/history/manual_functional_report.md)) | not tested |
| DocType edits and deletion | `update_doctype`, `delete_doctype` | not tested | not tested |
| Site and app sync | `diff_doctype`, `list_app_drift`, `promote_custom_doctype` | not tested | not tested |
| Custom Fields and Property Setters | `create_custom_field`, `set_field_property` | 0.2.0, Administrator: passed, 43 field types and 15 properties ([matrix](../e2e/history/doctype_matrix_evidence.json)) | not tested |
| Role permissions | `set_role_permissions` | 0.2.0, Administrator: passed for one role ([manual report](../e2e/history/manual_functional_report.md)) | not tested |
| Customization export and rollback | `export_customizations`, `rollback_export` | 0.2.0 fixture format: passed ([customization](../e2e/history/standalone_real_http_evidence.json)); the native `custom/` format (current) not tested | not tested |
| Workflows and Notifications | `get_workflow`, `upsert_workflow`, `list_notifications`, `upsert_notification` | not tested | not tested |
| Reports and Workspaces | `upsert_report`, `get_workspace`, `upsert_workspace` | not tested | not tested |

All runs so far used Administrator. A limited-user run (a temporary user with only `Sales User`, running the read-only smoke runner) has not happened; a column for it will be added when it does. Until then, which read tools work for a non-System-Manager user is known only from Frappe's source (see [tool reference](tool-reference.md#metadata-and-who-can-use-the-record-tools)).

## Filling in a cell

The runners in `e2e/` launch the server over real MCP stdio, as a host would. Run them only on a disposable development site on a loopback or `.localhost` host, in developer mode, with Frappe, ERPNext, and a disposable test app under `FRAPPE_APPS_PATH`. Set the connection variables from [installation and configuration](installation-and-configuration.md), then:

```bash
export FRAPPE_TEST_APP=<test app> FRAPPE_TEST_MODULE=<one of its modules>
uv run python e2e/run_standalone_smoke.py                                                   # read-only
FRAPPE_ALLOW_MUTATION_TEST=YES uv run python e2e/run_standalone_mutation_acceptance.py
FRAPPE_ALLOW_CUSTOMIZATION_TEST=YES uv run python e2e/run_standalone_customization_acceptance.py
FRAPPE_ALLOW_DOCTYPE_MATRIX=YES uv run python e2e/run_standalone_doctype_matrix.py
FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES uv run python e2e/run_standalone_developer_flow.py
```

| Runner | Matrix rows it fills |
|---|---|
| `run_standalone_smoke.py` | Discovery and schema, site information, record reads, `run_report` (needs ERPNext); with the test app set, previews of DocType creation, customization, and export |
| `run_standalone_mutation_acceptance.py` | Record writes |
| `run_standalone_customization_acceptance.py` | Custom Fields and Property Setters, customization export and rollback |
| `run_standalone_doctype_matrix.py` | DocType creation, Custom Fields and Property Setters (every field type and variant) |
| `run_standalone_developer_flow.py` | DocType creation, edits and deletion, site and app sync, customization export and rollback, submit and cancel, Workflows and Notifications, reports and Workspaces |

The developer flow creates a child table and a submittable DocType in the test app and checks that `diff_doctype` reports `in_sync`. It then edits the DocType with `update_doctype` and checks the app JSON and `diff_doctype` again. Next it adds a Custom Field to `ToDo` (`FRAPPE_TEST_CUSTOMIZE_DOCTYPE` picks another `frappe` DocType), exports it, and rolls the export back. It promotes a site-custom DocType into the app, then creates, submits, cancels, and deletes a document. Finally it adds a Workflow, a Notification, a Report Builder report, and a Workspace, removes everything, and checks that the test module's files and `list_app_drift` match their state before the run. It needs Administrator (deleting standard DocTypes requires it). It prints JSON with the MCP version and commit, the site's app versions, whether Frappe wrote standard files (which shows developer mode was on), and `pass`, `fail`, or `skipped` per area, and exits non-zero when anything failed.

Save each runner's output as `e2e/evidence/<date>-v<major>-<runner>.json`. Before committing it, read it for hostnames other than placeholders or `.localhost`, credentials, and record values (the runners print none, but a site's app list can name private apps). Then update the matrix cell with the version, the role, and the link.

## Known issues from the 0.2.0 runs

The [manual report](../e2e/history/manual_functional_report.md) found these on v15. Each has a change since then, but none has been re-checked live:

- A Single DocType's files could be written without a persisted DocType row. `create_doctype` now checks that Frappe saved the record and removes the partial folder when it didn't.
- Frappe's autoreloader can drop the MCP request after a standard DocType write, leaving the result uncertain. The write tools now wait for the site and re-read what was saved (`reconciled_after_server_reload`) instead of assuming failure.
- Deleting a DocType leaves its database table. This is Frappe's behavior; `delete_doctype`'s preview says so (`frappe_keeps`).
- `get_document` omitted `docstatus`, and exports could target `frappe` or `erpnext`. Both are fixed and unit-tested.

## Checked against Frappe's source, not yet on a site

Every tool added after 0.2.0 follows Frappe v16.33.1's source, as recorded in each work-plan stage's handoff in [docs/work-plan](work-plan/README.md). v15 was not compared. The behaviors a live run most needs to confirm:

- **Metadata for non-System-Manager users.** `get_doctype_schema` and the record tools read `frappe.desk.form.load.getdoctype`, which any logged-in user may call (Frappe merges Custom Fields and Property Setters and orders the fields).
- **Developer-mode saves.** `update_doctype` and `promote_custom_doctype` rely on `DocType.on_update` exporting the JSON and boilerplate. `delete_doctype` relies on the DocType branch of `frappe.delete_doc` (no link check, table kept, folder removed in developer mode).
- **Drift verdicts.** `diff_doctype` and `list_app_drift` rely on `import_file_by_path` (a DocType JSON is re-imported when its MD5 differs from `migration_hash`) and on `export_doc`'s normalization. Whether a freshly promoted or edited DocType compares `in_sync` against a real REST record is the open question; the developer flow checks it.
- **Customization export.** The native `<module>/custom/<doctype>.json` follows Frappe v16.33.1's `export_customizations`. That `bench migrate` re-imports an exported Custom Field has not been checked.
- **Configuration records.** Workflows (`set_active`, `validate_docstatus`, the state Custom Field), Notifications (`export_module_json` for standard rules, the sandboxed Jinja filters), Report Builder settings (`reportview.save_report`, `get_list`), and Workspaces (`Workspace.validate`, the block editor's `content`, `validate_route_conflict`). How Frappe normalizes `doc_status` and `allow_self_approval` on read is unconfirmed.
- **Reads and lifecycle.** `frappe.client.get_list` paging and `or_filters`, `get_count`, `search_link`, `get_versions`, `frappe.client.submit` (which rebuilds the document, so its timestamp check applies) and `frappe.client.cancel`. v15's `get_versions` and `search_link` may differ.
- **Reports.** `list_reports` relies on the `Report` read permission v16 gives `Desk User`; on v15, check the connected user can list `Report`. `run_report` returns no rows for Report Builder reports; `upsert_report` samples those through `reportview.get_list`.

## Boundaries

- Applying workflow actions to documents, amend, rename, file upload and download, print rendering, translations, private Workspaces, User Permissions, and app-specific methods are not available; see the [roadmap](../ROADMAP.md).
- File writes need a local app checkout under `FRAPPE_APPS_PATH` that is the site's own Bench `apps` folder. A developer-mode Bench reloads its web worker after many of these writes; reconcile the site and the files before retrying anything a tool reported as uncertain.
- Validate every new Frappe release line, or an unusual app combination, on a disposable site before relying on it.
