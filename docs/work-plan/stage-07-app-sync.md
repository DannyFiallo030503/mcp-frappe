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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Promotion path chosen (native or fixtures) and why:
- How migrate decides a JSON is newer (finding):
- Deviations from this plan:
- Notes for the next stage:
