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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Live migrate check result (if run):
- Notes for the next stage:
