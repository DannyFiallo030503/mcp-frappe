# Stage 04: Native customization export

**Depends on:** 03 · **Size:** medium · **Fixes:** bugs 8 and 9 in [assessment.md](assessment.md)

## Why

`export_customizations` writes fixtures to `<module>/fixtures/`, a folder `bench migrate` never reads. So an "exported" customization silently doesn't deploy. It also overwrites the DocType JSON that Frappe already wrote with a REST dump in a different format.

## Read first

- `standalone.py`: `export_customizations`, `rollback_export`, `_fixture_plan`, `_file_preview`, `_atomic_write`
- Frappe `frappe/modules/utils.py`:
  - `export_customizations` (the reference format and path);
  - `sync_customizations` and `sync_customizations_for_doctype` (how it is imported).
- Frappe `frappe/utils/fixtures.py`: `import_fixtures` reads `<app>/<app>/fixtures/*.json`, and `export_fixtures`.

## Decision

Write Frappe's **native module customization file** locally: `<app>/<app>/<module_slug>/custom/<scrub(doctype)>.json`.

- Use exactly the structure that `frappe.modules.utils.export_customizations` produces: `custom_fields`, `property_setters`, `custom_perms`, `links`, `doctype`, and `sync_on_migrate`.
- Recurse into child-table DocTypes, as Frappe does.
- Keep the MCP's preview, `preview_id`, backups, and rollback.

The alternative is to call the whitelisted server method. That writes on the server's filesystem with no preview or rollback, and requires developer mode on the target, so it's rejected as the default. If you find a strong reason to switch, record it in Handoff.

## Tasks

- [ ] **Stop exporting DocType JSON** (bug 9). In developer mode, standard DocType files are Frappe's responsibility. Explain this in the tool docstring and the docs.
- [ ] **Write native custom files** (bug 8). For each requested DocType, and each child-table DocType it uses, build the native structure from REST reads:
  - Custom Field filtered by `dt`;
  - Property Setter filtered by `doc_type`;
  - `DocType Link` filtered by `parent`;
  - Custom DocPerm only when `with_permissions=True`.

  Apply the module filter only when the new `apply_module_filter` argument is true, matching Frappe. Set `sync_on_migrate` from an argument that defaults to `True`. Skip writing a file when it would be empty, as Frappe does.
- [ ] **Remove system columns.** Strip per-row fields that Frappe's own export wouldn't need to diff cleanly (`owner`, `creation`, `modified_by`, ...). Compare with a file exported by Frappe from the same version. If you can't produce one, note it in Handoff and keep the full `fields="*"` rows, which is what Frappe writes.
- [ ] **Client Scripts.** These aren't part of the module custom file. Write them to the app-level `<app>/<app>/fixtures/client_script.json` (filtered by `dt` and `module`) and keep the managed `hooks.py` block only for Client Script. If there are no Client Scripts, don't touch `hooks.py`.
- [ ] **Old exports still roll back.** Old manifests store app-relative paths, so `rollback_export` should keep working unchanged. Add a test.
- [ ] **Tests** (using `tmp_path` and `fake_site`):
  - the output path is under `custom/` with the scrubbed file name;
  - the JSON keys match the native structure;
  - child-table recursion;
  - an empty export writes nothing;
  - Client Script goes to app-level fixtures;
  - no DocType JSON is written;
  - export then rollback restores the original tree byte for byte.

## Acceptance criteria

- The unit tests above pass.
- `docs/tool-reference.md`, `docs/ai-agent-guide.md`, and `docs/architecture.md` describe the native paths and that `bench migrate` imports them.
- **Optional live check (only if the user asks, on a disposable site):** export a Custom Field, remove it from the site, run `bench --site <site> migrate`, and confirm it reappears. Record the result in Handoff and in `docs/compatibility-and-validation.md`.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Exporting site-custom DocTypes (backlog). Running migrate from the MCP; that's never allowed.

## Handoff

- Status: done.
- Branch / commits: `stage-04-native-export`, based on `origin/main` at `1504d79`; one commit, "Export customizations to Frappe's native custom/ files".
- What changed:
  - `export_customizations` writes `<app>/<app>/<module_slug>/custom/<scrub(doctype)>.json` with the keys of Frappe v16.33.1's `frappe.modules.utils.export_customizations`: `custom_fields`, `property_setters`, `custom_perms`, `links`, `doctype`, `sync_on_migrate`. Rows are read over REST with `fields=["*"]` and `order_by="name asc"`, like Frappe's `get_all`. The file is serialized like `frappe.as_json` (indent 1, sorted keys, ASCII-escaped, no trailing newline). DocType Link is a child table, so it is listed with `parent=DocType`.
  - It recurses through table fields of the merged schema (`_doctype()`, so custom table fields count), skips DocTypes with nothing to export (`empty` in the result), and returns `nothing_to_export: true` with no `preview_id` when no file is left. Confirming then raises.
  - New arguments: `with_permissions` (default false), `apply_module_filter` (default false), `sync_on_migrate` (default true). The tool docstring now documents every argument.
  - DocType JSON is no longer exported. The old check that each DocType belongs to `module` was dropped: Frappe's export allows any DocType (the usual case is customizing another app's DocType), and `module` only picks the folder.
  - Client Scripts (filtered by `dt` in the covered DocTypes and `module`) go to `<app>/<app>/fixtures/client_script.json` in `bench export-fixtures` row format (adds `doctype`, drops `owner`, `creation`, `modified_by`, `idx`, `lft`, `rgt` and `_`-columns). Rows already in that file for other DocTypes or modules are kept. `_fixture_plan` now manages only a `Client Script` entry and drops entries for other DocTypes left by older exports. `hooks.py` is untouched when there are no Client Scripts.
  - Manifest entries gain `created_dirs`; `rollback_export` removes those directories once empty, so export plus rollback restores the tree byte for byte. Old manifests (no `created_dirs`, paths under `<module>/fixtures/`) still roll back. The file cap is `_EXPORT_MAX_FILES = 200` for both export and rollback (was 55); rows per read are capped at 1,000 and a larger result is refused rather than truncated.
  - Tests: `tests/test_export.py` (18 tests). e2e runners updated for the new return shape (`nothing_to_export`, native `custom/` path check). Docs: tool reference, AI agent guide, architecture, security, compatibility, development, README.
- Deviations from this plan:
  - System columns are **not** stripped from the native file. Frappe's own export writes full `fields="*"` rows, and no Frappe-exported reference file was available (no live site run), so the plan's fallback applies.
  - Child-table recursion goes as deep as the schema does, with a visited set, instead of exactly one level; Frappe recurses the same way.
- Live migrate check result (if run): not run; the user chose unit tests only. `docs/compatibility-and-validation.md` says the migrate round trip is unverified.
- Notes for the next stage:
  - `_app_path` still uses `_slug(module)` for the module folder, while Frappe uses `scrub(module)`. They agree for ordinary module names (letters, digits, spaces, hyphens, underscores) but differ for names with other characters such as `.`. Stage 07 (app sync) should switch to `scrub` when it compares files.
  - Each export calls `_doctype()` for every covered DocType (3 requests each) plus 3-4 row reads; stage 05's metadata cache will cut that.
