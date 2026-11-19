# Stage 16: Developer diagnostics: patches, errors, and app methods

**Depends on:** 15 · **Size:** medium · **Source:** [field feedback](../field-feedback/README.md), [native MCP comparison](native-mcp-comparison.md)

## Why

During development, an agent keeps asking three questions. The field tasks answered them by hand, or couldn't answer them:

1. **Which of the app's patches haven't run on this site?** `doctype-sync-investigation` and `product-line-dev-seed` read `Patch Log` with `list_documents` and compared it against the app by eye.
2. **What just failed on the server?** `frappe-health-repair` and `product-line-repair-run` read `Error Log` through the generic tools, which return whole tracebacks and no time filter.
3. **Which whitelisted methods does the app expose, and with which arguments?** `product-lines-null-central` couldn't check a method's live response. The native server answers this with `search_methods`, but only for what is deployed on a v16 site.

All three combine the site with the local app checkout. That combination is this server's advantage, and it works on v15.

## Read first

- `standalone.py`: `_app_root`, `_module_dir`, `_app_file`, `_APP_FILE_MAX_BYTES`, `list_app_drift` (a read tool that combines the site and app files), `_page`
- Frappe source:
  - `frappe/modules/patch_handler.py`: the `patches.txt` format, `[pre_model_sync]` and `[post_model_sync]` sections, `execute:` lines, comments, and how a patch's name is matched against `Patch Log.patch`;
  - `frappe/core/doctype/error_log/error_log.json` on v15 and v16: field names and who can read it;
  - `frappe/__init__.py` `whitelist()`: its arguments (`allow_guest`, `methods`, `xss_safe`).

## Tasks

- [ ] **New read tool `list_pending_patches(app)`:**
  - Parse `<app>/<app>/patches.txt` through `_app_file()` (same containment and size checks).
  - Compare each entry with `Patch Log` (`patch` field), paging through all rows.
  - Return `pending` in file order (`patch`, `section`, `line`), `applied_count`, and `unknown_in_log` (log rows for this app with no line in the file, bounded).
  - `execute:` lines are returned as text and never run.
  - Without `FRAPPE_APPS_PATH` or the app checkout, fail with the same message as `diff_doctype`.
- [ ] **New read tool `get_recent_errors(since=None, method=None, reference_doctype=None, limit=20)`:**
  - Read `Error Log` newest first, filtered by `creation >= since` (ISO datetime), `method` (substring), and `reference_doctype`.
  - Each row has `name`, `creation`, `method`, `reference_doctype`, `reference_name`, the exception line, and the **last** 40 lines of the traceback, capped at about 4,000 characters.
  - Return `_page()`.
  - If v15 and v16 field names differ, map them and record the difference.
  - Tracebacks can contain record values, so the docstring says to show the user only what is needed.
- [ ] **New read tool `list_app_methods(app, search=None, limit=100, offset=0)`:**
  - Scan the app's Python files with `ast`; never import or run them.
  - Find functions and methods decorated with `frappe.whitelist(...)`, `@whitelist(...)` after `from frappe import whitelist`, or the bare decorator.
  - Each row has:
    - `method` (dotted path from the app package);
    - `kind` (`module` or `document`, for a method on a Document controller class, with the DocType from the folder);
    - `args` (names, defaults, and annotations through `ast.unparse`);
    - the first paragraph of the docstring, `allow_guest`, `methods`, and `file:line`.
  - Skip `tests/`, `node_modules`, hidden folders, and symlinks that leave the app. Cap the files read and the bytes per file. Return `_page()`.
  - `search` matches part of the dotted path.
- [ ] **All three are in the `read` toolset**, with annotations, and fit the stage 15 budget.
- [ ] **Unit tests** with `tmp_path` app trees and `fake_site`:
  - patches: sections, comments, `execute:` lines, applied, pending, and unknown patches, and a missing file;
  - errors: filters, traceback trimming, and paging;
  - methods: each decorator form, a class method, a syntax error in one file (skipped and reported), a symlink escape, and the size caps.

## Acceptance criteria

- An agent can answer the three questions above with one call each, on v15 and v16.
- Nothing from the app checkout is imported or run.
- Docs: `docs/tool-reference.md`, and `docs/ai-agent-guide.md`. Add a "After a change, check:" step to the developer workflow: drift, pending patches, recent errors.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Calling the methods (stage 17). Running patches (stage 18). Scheduler and background job logs: add them only if Error Log proves insufficient.

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Error Log fields on v15 vs v16:
- Deviations from this plan:
- Notes for the next stage:
