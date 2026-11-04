# Compatibility and validation

What has been run against a real Frappe site, on which version, and where the evidence is. Unit tests (`uv run pytest -q`, on Python 3.10 and 3.12 in CI) cover every tool against a fake site built from Frappe's source; this page is about live sites.

## Matrix

Each cell links to the redacted evidence for that run, or says **not tested**. All current runs used server 0.3.0 on 2026-11-03, on two fresh development sites in developer mode, each with ERPNext and a disposable test app:

- **v15**: Frappe 15.126.0, ERPNext 15.124.4, Python 3.12
- **v16**: Frappe 16.55.1, ERPNext 16.53.0, Python 3.14

Both used MariaDB 11.4. The runs span commits `3f55057` to `4f966d1`; the only server change in between is the `upsert_workspace` fix described [below](#found-by-the-live-runs). The developer-flow evidence records its exact commit.

| Area | Tools | Frappe v15 (Administrator) | Frappe v16 (Administrator) | Sales User only (v15 and v16) |
|---|---|---|---|---|
| Setup check | `mcp-frappe check` | passed (run by hand; output not saved, it holds only versions and statuses) | passed (same) | passed on v16 |
| Discovery and schema | `list_installed_apps`, `list_doctypes`, `get_doctype_schema` | passed ([smoke](../e2e/evidence/2026-11-03-v15-smoke.json)) | passed ([smoke](../e2e/evidence/2026-11-03-v16-smoke.json)) | `list_doctypes` denied (403); the other two passed ([v15](../e2e/evidence/2026-11-03-v15-smoke-sales-user.json), [v16](../e2e/evidence/2026-11-03-v16-smoke-sales-user.json)) |
| Site information | `get_site_info` | passed ([smoke](../e2e/evidence/2026-11-03-v15-smoke.json), [developer flow](../e2e/evidence/2026-11-03-v15-developer-flow.json)) | passed ([smoke](../e2e/evidence/2026-11-03-v16-smoke.json), [developer flow](../e2e/evidence/2026-11-03-v16-developer-flow.json)) | passed |
| Record reads | `list_documents`, `get_document` | passed ([smoke](../e2e/evidence/2026-11-03-v15-smoke.json), [mutation](../e2e/evidence/2026-11-03-v15-mutation.json)) | passed ([smoke](../e2e/evidence/2026-11-03-v16-smoke.json), [mutation](../e2e/evidence/2026-11-03-v16-mutation.json)) | `list_documents` passed; `get_document` not tested |
| Counts and search | `count_documents`, `search_records` | passed ([smoke](../e2e/evidence/2026-11-03-v15-smoke.json)) | passed ([smoke](../e2e/evidence/2026-11-03-v16-smoke.json)) | passed |
| Record writes | `create_document`, `update_document`, `delete_document` | passed on ToDo, stale token refused ([mutation](../e2e/evidence/2026-11-03-v15-mutation.json)) | passed on ToDo, stale token refused ([mutation](../e2e/evidence/2026-11-03-v16-mutation.json)) | not tested |
| Submit and cancel | `submit_document`, `cancel_document` | passed ([developer flow](../e2e/evidence/2026-11-03-v15-developer-flow.json)) | passed ([developer flow](../e2e/evidence/2026-11-03-v16-developer-flow.json)) | not tested |
| Reports | `list_reports`, `get_report_filters`, `run_report` | passed ([smoke](../e2e/evidence/2026-11-03-v15-smoke.json)) | passed ([smoke](../e2e/evidence/2026-11-03-v16-smoke.json)) | passed |
| DocType creation | `create_doctype` (standard and site-custom) | passed for regular, child, Single, Tree, submittable, Virtual, and site-custom, with all 43 field types ([matrix](../e2e/evidence/2026-11-03-v15-doctype-matrix.json), [developer flow](../e2e/evidence/2026-11-03-v15-developer-flow.json)) | passed, same coverage ([matrix](../e2e/evidence/2026-11-03-v16-doctype-matrix.json), [developer flow](../e2e/evidence/2026-11-03-v16-developer-flow.json)) | n/a (developer tool) |
| DocType edits and deletion | `update_doctype`, `delete_doctype` | passed: add, move, change, and remove a field, then the app JSON and `diff_doctype` checked; deletion in cleanup ([developer flow](../e2e/evidence/2026-11-03-v15-developer-flow.json)) | passed, same ([developer flow](../e2e/evidence/2026-11-03-v16-developer-flow.json)) | n/a |
| Site and app sync | `diff_doctype`, `list_app_drift`, `promote_custom_doctype` | passed: `in_sync` after create, edit, and promotion; drift counts unchanged after cleanup ([developer flow](../e2e/evidence/2026-11-03-v15-developer-flow.json)) | passed, same ([developer flow](../e2e/evidence/2026-11-03-v16-developer-flow.json)) | n/a |
| Custom Fields and Property Setters | `create_custom_field`, `set_field_property` | passed, 43 field types and every allowlisted property ([matrix](../e2e/evidence/2026-11-03-v15-doctype-matrix.json), [customization](../e2e/evidence/2026-11-03-v15-customization.json)) | passed, same ([matrix](../e2e/evidence/2026-11-03-v16-doctype-matrix.json), [customization](../e2e/evidence/2026-11-03-v16-customization.json)) | n/a |
| Role permissions | `set_role_permissions` | not tested (0.2.0 passed for one role: [manual report](../e2e/history/manual_functional_report.md)) | not tested | n/a |
| Customization export and rollback | `export_customizations`, `rollback_export` | passed, native `custom/` format ([customization](../e2e/evidence/2026-11-03-v15-customization.json), [developer flow](../e2e/evidence/2026-11-03-v15-developer-flow.json)) | passed, same ([customization](../e2e/evidence/2026-11-03-v16-customization.json), [developer flow](../e2e/evidence/2026-11-03-v16-developer-flow.json)) | n/a |
| Workflows and Notifications | `get_workflow`, `upsert_workflow`, `list_notifications`, `upsert_notification` | passed ([developer flow](../e2e/evidence/2026-11-03-v15-developer-flow.json), [smoke](../e2e/evidence/2026-11-03-v15-smoke.json)) | passed ([developer flow](../e2e/evidence/2026-11-03-v16-developer-flow.json), [smoke](../e2e/evidence/2026-11-03-v16-smoke.json)) | `get_workflow` and `list_notifications` denied (403) |
| Reports and Workspaces | `upsert_report`, `get_workspace`, `upsert_workspace` | passed; the Workspace file is written to the app ([developer flow](../e2e/evidence/2026-11-03-v15-developer-flow.json)) | passed after the fix [below](#found-by-the-live-runs) ([developer flow](../e2e/evidence/2026-11-03-v16-developer-flow.json)) | `get_workspace` passed |

The **Sales User** column comes from a temporary user with only that role, running the smoke runner's limited-user mode, which calls each read tool once. The user was deleted afterwards. The three refusals are Frappe's own permission checks on `DocType`, `Workflow`, and `Notification` records; the [tool reference](tool-reference.md#metadata-and-who-can-use-the-record-tools) lists them.

The 0.2.0 runs from 2026-10-16/19 (Frappe 15.96, ERPNext 15.96) are kept in [e2e/history](../e2e/history/) for reference.

## Filling in a cell

The runners in `e2e/` launch the server over real MCP stdio, as a host would. Run them only on a disposable development site on a loopback or `.localhost` host, in developer mode, with Frappe, ERPNext, and a disposable test app under `FRAPPE_APPS_PATH`. The test app must own at least one DocType, and its Git checkout should be clean, because the developer flow compares `git status` before and after. Set the connection variables from [installation and configuration](installation-and-configuration.md), then:

```bash
export FRAPPE_TEST_APP=<test app> FRAPPE_TEST_MODULE=<one of its modules>
uv run python e2e/run_standalone_smoke.py                                                   # read-only
FRAPPE_ALLOW_MUTATION_TEST=YES uv run python e2e/run_standalone_mutation_acceptance.py
FRAPPE_ALLOW_CUSTOMIZATION_TEST=YES uv run python e2e/run_standalone_customization_acceptance.py
FRAPPE_ALLOW_DOCTYPE_MATRIX=YES uv run python e2e/run_standalone_doctype_matrix.py
FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES uv run python e2e/run_standalone_developer_flow.py
```

For the limited-user column, log in as a temporary user without System Manager, unset `FRAPPE_TEST_APP`, and run `FRAPPE_SMOKE_LIMITED_USER=YES uv run python e2e/run_standalone_smoke.py`.

| Runner | Matrix rows it fills |
|---|---|
| `run_standalone_smoke.py` | Discovery and schema, site information, record reads, counts and search, reports (needs ERPNext), and one call to each read tool; with the test app set, previews of DocType creation, customization, and export. In limited-user mode, only the per-tool read calls |
| `run_standalone_mutation_acceptance.py` | Record writes |
| `run_standalone_customization_acceptance.py` | Custom Fields and Property Setters, customization export and rollback |
| `run_standalone_doctype_matrix.py` | DocType creation, Custom Fields and Property Setters (every field type and variant) |
| `run_standalone_developer_flow.py` | DocType creation, edits and deletion, site and app sync, customization export and rollback, submit and cancel, Workflows and Notifications, reports and Workspaces |

The developer flow creates a child table and a submittable DocType in the test app and checks that `diff_doctype` reports `in_sync`. It then edits the DocType with `update_doctype` and checks the app JSON and `diff_doctype` again. Next it adds a Custom Field to `ToDo` (`FRAPPE_TEST_CUSTOMIZE_DOCTYPE` picks another `frappe` DocType), exports it, and rolls the export back. It promotes a site-custom DocType into the app, then creates, submits, cancels, and deletes a document. Finally it adds a Workflow, a Notification, a Report Builder report, and a Workspace (and checks Frappe wrote the Workspace to the module), removes everything, and checks that the test module's files and `list_app_drift` match their state before the run. It needs Administrator (deleting standard DocTypes requires it). It prints JSON with the MCP version and commit, the site's app versions, whether Frappe wrote standard files (which shows developer mode was on), and `pass`, `fail`, or `skipped` per area, and exits non-zero when anything failed.

Save each runner's output (stdout only) as `e2e/evidence/<date>-v<major>-<runner>.json`. Before committing it, read it for hostnames other than placeholders or `.localhost`, credentials, and record values (the runners print none, but a site's app list can name private apps). Then update the matrix cell with the version, the role, and the link.

To run v15 and v16 Benches side by side on one machine, give one of them other ports (`bench set-config -g webserver_port`, `socketio_port`, `file_watcher_port`, `redis_cache`, `redis_queue`, `redis_socketio`, then `bench setup redis` and `bench setup procfile`).

## Found by the live runs

- **Workspaces were not exported on Frappe v16.** v16 (checked on 16.55.1) writes a Workspace to its module only when its new `standard` flag is set; v15 writes every public Workspace. `upsert_workspace` sent only `public`, so on v16 the Workspace was saved on the site but never reached the app. It now sends `standard: 1` when the Workspace DocType has that field, and the developer flow fails if the file is missing.
- **The DocType matrix runner mixed progress lines into its JSON.** It now prints progress to stderr. The 2026-11-03 v16 matrix evidence had those lines removed by hand; its JSON is unchanged.
- **The smoke runner stopped at the first denied tool,** so a limited-user run could not show which tools work. It now probes each read tool separately.

Behaviors the source review flagged as open, now confirmed on both versions:

- `getdoctype` gives schema to a user without System Manager, so the record tools work for that user.
- `update_doctype` and `promote_custom_doctype` saves make Frappe write the JSON and boilerplate. `delete_doctype` removes the module folder in developer mode.
- A freshly created, edited, or promoted DocType compares `in_sync` in `diff_doctype` against the site's REST record.
- The native `custom/<doctype>.json` export holds the Custom Field, and rollback restores the module's files.
- Workflows (with new Workflow State and Action masters), Notifications, Report Builder reports with a sampled `get_list`, and Workspaces save on both versions.
- `get_count`, `search_link`, `get_versions`, `frappe.client.submit`, and `frappe.client.cancel` behave the same on v15 and v16 for these calls.
- On v15, a Sales User can list `Report` records.

## Known issues from the 0.2.0 runs

The [manual report](../e2e/history/manual_functional_report.md) found these on v15:

- A Single DocType's files could be written without a persisted DocType row. `create_doctype` now checks that Frappe saved the record; the current DocType matrix creates a Single on both versions.
- Frappe's autoreloader can drop the MCP request after a standard DocType write, leaving the result uncertain. The write tools wait for the site and re-read what was saved (`reconciled_after_server_reload`). No reload interrupted a request in the 2026-11-03 runs, so that path is still covered by unit tests only.
- Deleting a DocType leaves its database table. This is Frappe's behavior; `delete_doctype`'s preview says so (`frappe_keeps`).
- `get_document` omitted `docstatus`, and exports could target `frappe` or `erpnext`. Both are fixed and unit-tested.

## Not yet checked on a site

- `set_role_permissions` on the current server (no runner calls it).
- That `bench migrate` re-imports an exported Custom Field from `custom/`.
- How Frappe normalizes a Workflow's `doc_status` and `allow_self_approval` when they are read back.
- Record writes, submit, and cancel as a user without System Manager.

## Boundaries

- Applying workflow actions to documents, amend, rename, file upload and download, print rendering, translations, private Workspaces, User Permissions, and app-specific methods are not available; see the [roadmap](../ROADMAP.md).
- File writes need a local app checkout under `FRAPPE_APPS_PATH` that is the site's own Bench `apps` folder. A developer-mode Bench reloads its web worker after many of these writes; reconcile the site and the files before retrying anything a tool reported as uncertain.
- Validate every new Frappe release line, or an unusual app combination, on a disposable site before relying on it.
