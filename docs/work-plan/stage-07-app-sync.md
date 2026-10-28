# Stage 07: Site and app sync, portable custom DocTypes

**Depends on:** 04, 06 · **Size:** medium · **Fixes:** structural gap 9 in [assessment.md](assessment.md)

## Why

A developer works on two copies of the same schema: the site's database and the app's files in git. They drift all the time:

- someone edits a DocType on the site with developer mode off, so no JSON was written;
- a `git pull` brings DocType JSON that the site hasn't migrated yet;
- a customization was made on the site but never exported to `<module>/custom/`;
- a DocType was built as site-custom (`custom=1`) while prototyping and now has to ship with the app.

Today nothing tells the developer which of these happened. They find out when `bench migrate` overwrites their site changes, or when a deploy is missing a field.

## Read first

- `standalone.py`: `_app_path`, `_assert_remote_module`, `_assert_writable_app`, `_doctype_raw`, `export_customizations` and its native `custom/` format (after stage 04), `create_doctype` (how a standard DocType's files are laid out)
- The stage 04 and stage 06 Handoff sections
- Frappe:
  - `frappe/modules/import_file.py` (`import_file_by_path`): how migrate decides whether a DocType JSON is newer than the site (it compares `modified`; check the details);
  - `frappe/modules/utils.py` (`sync_customizations`, `scrub`, `get_doc_path`);
  - `frappe/core/doctype/doctype/doctype.py`: what happens when an existing DocType's `custom` flag goes from 1 to 0 and `module` points to an app module (does `on_update` export the JSON and controller boilerplate?).

## Design

- **Read-only comparison first.** The comparison tools never write anything. They read local files only under `FRAPPE_APPS_PATH`, through `_app_path`, so the path checks are the same as for writes.
- **Normalize before comparing.** Drop keys that always differ: `modified`, `creation`, `owner`, `modified_by`, child-row `name`, `parent`, `parenttype`, `idx` (compare order separately), and null or default values. Write the normalization as one helper with its own tests; the diff is only as good as it is.
- **Promote, don't export, custom DocTypes.** For "ship this custom DocType with the app", the native path is to turn it into a standard DocType of an app module and let Frappe write the files in developer mode. Confirm that path in the source first. If Frappe doesn't export on that change, fall back to fixtures (`fixtures` in `hooks.py`, through the existing managed block) and record why in Handoff.

## Tasks

- [ ] **`diff_doctype(doctype, app=None)`.** Compare the site's DocType with `<app>/<app>/<module_slug>/doctype/<scrub>/<scrub>.json`. Return field-level and property-level differences (the same diff shape as stage 06), which side is newer by `modified`, and a one-line verdict: `in_sync`, `site_ahead` (export needed, or developer mode was off), `app_ahead` (`bench migrate` needed), or `diverged`. Also compare `<module>/custom/<scrub>.json` with the site's Custom Fields and Property Setters, if the file exists. Bound the output.
- [ ] **`list_app_drift(app, module=None, limit=50)`.** For every DocType folder in the app's modules and every DocType the site has in those modules, report the verdict only: missing on the site, missing in the app, or one of the `diff_doctype` verdicts. Page with `offset` if the app is large.
- [ ] **`promote_custom_doctype(doctype, app, module, confirm=False, expected_modified=None)`.** Turn a site-custom DocType into a standard DocType of `app`/`module`:
  - check the app is writable and the module belongs to it (`_assert_writable_app`, `_assert_remote_module`, `_app_path`);
  - refuse if the target folder already exists;
  - the preview lists the files Frappe will create, and warns that developer mode must be on;
  - on confirm, update the DocType (`custom=0`, `module`) with `modified` in the body, reconcile a Bench reload as in stage 06, then check the files exist and report them.
- [ ] **Tests:**
  - the normalization ignores system keys and child-row names but catches order changes;
  - each verdict (`in_sync`, `site_ahead`, `app_ahead`, `diverged`, missing on either side);
  - local paths are resolved through `_app_path` (a symlink escape is refused);
  - `promote_custom_doctype` refuses protected apps, foreign modules, existing folders, and stale `expected_modified`;
  - a `TransportError` during promotion is reconciled.

## Acceptance criteria

- The new tools are registered and in the contract test.
- `docs/ai-agent-guide.md` has two recipes: "what changed between my site and my app" and "ship a prototype DocType with my app". `docs/tool-reference.md` documents the verdicts. `docs/security.md` says the comparison reads files only under `FRAPPE_APPS_PATH`.
- **Optional live check (only if the user asks, on a disposable site):** create a custom DocType, promote it into a test app, and check `diff_doctype` reports `in_sync`. Clean up.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Running `bench migrate` or any other Bench command. Fixing drift automatically. Comparing controller code.

## Handoff

- Status: done.
- Branch / commits: `stage-07-app-sync`, based on `main` at `2cb0dfa` (stages 05 and 06 merged).
- What changed:
  - `diff_doctype(doctype, app=None)`: reads the raw `DocType` record and compares it with `<app>/<app>/<scrub(module)>/doctype/<scrub>/<scrub>.json`. It returns `verdict`, a one-line `summary`, `doctype_file` (`path`, `site_modified`, `app_modified`, `newer`, `migrate_imports_file`, and `changes` in update_doctype's shape, read from site to app, plus `tables` for other child tables such as `permissions`), `customizations`, and `warnings`. `app` defaults to the module's app. With another app, only that app's customizations are compared. Every `<module>/custom/<scrub>.json` of the app is compared with the site's Custom Fields (keyed by fieldname) and Property Setters (keyed by `field:property`). Site rows tagged with another app's module are skipped unless the file holds them. With no file, the site's rows for this app (or with no module) show as `missing_in_app`. Output is bounded by `_bounded` (100 items per list or object, 500 characters per string, and a `truncated` flag).
  - `_normalize_doctype` / `_compare_doctypes`: drop the system keys (`_DOCTYPE_SYSTEM_KEYS`, `_CHILD_SYSTEM_KEYS`), `_` columns, and blank values, and take the order from `field_order` when the file has one. Moved fields are the ones outside the longest common order (`difflib.SequenceMatcher`), so one move isn't reported as several.
  - Verdicts: `in_sync` when the normalized content matches. Otherwise `app_ahead` if the file's `modified` is newer. If the site is newer, `diverged` when the file also changed since the last migrate (`migration_hash` is set and differs from the file's MD5), else `site_ahead`. With equal or unreadable timestamps, `app_ahead` if the file changed, else `diverged`. `site_custom` covers `custom=1` DocTypes. `migrate_imports_file` is true when the hash is missing or differs, and then a `site_ahead`/`diverged` result warns that migrate would replace the site's changes.
  - `list_app_drift(app, module=None, limit=50, offset=0)`: one DocType list read (`name`, `module`, `custom`, `modified`, `migration_hash`) plus the local `doctype/` folders, paired by `(module, scrub(name))`, sorted, and paged (`limit` 1-200, `next_offset`). A pair whose file MD5 equals `migration_hash` and whose `modified` matches is `in_sync` without another request; any other pair costs one DocType read. Adds `missing_on_site`, `missing_in_app`, `site_custom`, per-page `counts`, and `modules_missing_locally`.
  - `promote_custom_doctype(doctype, app, module, confirm=False, expected_modified=None)`. It refuses: protected apps, a module of another app, a custom Module Def (Frappe's `write_document_file` exports those into the site folder), a DocType that is already standard, a `default_print_format` (Frappe throws for standard DocTypes), and an existing target folder (or symlink). The preview lists the files (JSON, `.py`, plus `.js` and test for non-child, `_tree.js` for trees, and web templates when `has_web_view` is set). It warns about Link/Table fields that point to site-custom DocTypes (one `DocType` read filtered by `custom=1`), about Custom Fields and Property Setters that stay customizations (counted), and about a module move. Confirm writes the controller templates with `_prepare_native_doctype_scaffolding`, then `_wait_for_server_ready`, then `PUT {custom: 0, module, modified}`. It maps `TimestampMismatchError` and `CannotCreateStandardDoctypeError`. On `TransportError` it waits, re-reads, and succeeds only if the DocType is no longer custom, is in the target module, and `modified` moved. If the DocType is still custom afterwards, the staged folder is removed. `_forget_meta` runs in `finally`. The result has `file_status` and `native_files_complete`.
  - Paths: `_app_path` is split into `_app_root` + `_module_dir` (same checks, shared by reads and writes), and `_app_file` resolves a read strictly, refuses a file that escapes the module folder, and caps size at 5 MB. `_slug` is removed. Module folders, DocType folders, and staged controller names now use `_scrub` (Frappe's `scrub`), as the stage 04 handoff asked. They differ for DocType names such as `Lab - Sample` (`lab___sample` in Frappe, `lab_sample` before). `_assert_remote_module` now returns the Module Def row with `custom`.
  - Tests: `tests/test_app_sync.py` (33 tests) covers normalization and order, the compare shape, every verdict on both tools, symlink escapes of the JSON file and of the module folder, customization files, paging, and promotion (preview files, link warnings, confirm, protected apps, foreign or custom module, existing folder, already standard, print format, stale token, developer mode refusal with cleanup, reload reconciled, and reload without the change with cleanup). There is also an MCP round-trip for `list_app_drift`, and the tool-set test is updated.
  - Docs: tool-reference ("Site and app sync" with the verdict table and normalization rules), ai-agent-guide (both recipes), security, architecture, compatibility, capability study, `CLAUDE.md`.
- Promotion path chosen (native or fixtures) and why: native. In Frappe v16.33.1, `DocType.on_update` runs `export_doc` and `make_controller_template` whenever `not self.custom and not frappe.flags.in_import and developer_mode`. Nothing in `validate` blocks a `custom` 1 -> 0 change beyond `check_developer_mode` and the standard-DocType `default_print_format` rule. `make_boilerplate` skips existing files, so the MCP writes the controller templates first, as `create_doctype` does, and the save only writes the JSON.
- How migrate decides a JSON is newer (finding): for DocTypes, `import_file_by_path` does **not** use `modified`. It skips a DocType only when `DocType.migration_hash` equals the MD5 of the file's bytes. With no stored hash, or a different one, it re-imports, even when the site's `modified` is newer, then stores the hash and sets `modified` to `now()` if the site was newer. For other document types it skips when the database `modified` is at least the file's. A developer-mode save writes the file but leaves `migration_hash` alone, so the next migrate re-imports identical content. `migration_hash` is a hidden Data field of DocType, readable over REST, and `strip_default_fields` removes it from the export. `sync_customizations` applies every row of a `custom/` file with `sync_on_migrate` on each migrate (update by `dt`+`fieldname` for Custom Fields) and never deletes site rows.
- Deviations from this plan:
  - The verdict also uses `migration_hash`, not only `modified`, because that is what migrate compares. Divergence can't always be proven from two timestamps: after a developer-mode save followed by a site edit with developer mode off, the result says `diverged` rather than `site_ahead`, because migrate would overwrite the site in that state.
  - Added the `site_custom` verdict (to both tools) and `tables` in the diff (permissions, actions, links, states).
  - `diff_doctype` reads the System Manager-only `DocType` resource for the raw record, as `update_doctype` does. It needs `migration_hash` and the unmerged field list, which `getdoctype` doesn't give.
  - `_scrub` replaces `_slug` everywhere, including `create_doctype` and `_doctype_files`. That's a small behavior change for DocType names with consecutive separators.
  - The e2e runners were not changed: no existing tool's signature or return shape changed, and none exercises the new tools yet.
- Notes for the next stage:
  - Live check not run (the user chose unit tests only). Still open: promote a custom DocType into a test app on a disposable site, then check that `diff_doctype` says `in_sync`. That checks the normalization against a real REST record, which may hold keys the hand-written fixtures don't.
  - Frappe source was read from v16.33.1 on GitHub (no local Bench on this machine). v15 was not compared; `migration_hash` exists there too, but check `import_file_by_path` before relying on it.
  - Stages 08 and 09 export Workflow, Notification, Report, and Workspace records, which migrate imports by `modified` (not by hash). If they need drift checks, `_compare_doctypes` and `_bounded` can be reused, but the verdict rule differs.
