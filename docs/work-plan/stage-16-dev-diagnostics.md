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

- Status: done.
- Branch / commits: `stage-16-dev-diagnostics`, one commit on top of `main` (`c9ac226`).
- What changed:
  - **`list_pending_patches(app)`**: `_parse_patches()` reads `<app>/<app>/patches.txt` (through `_app_root()`/`_app_file()`) the way `frappe.modules.patch_handler` does: INI sections when a `[header]` comes before the first entry (`#`/`;` comment lines, whole line as the patch, so `#2` markers stay), else the old one-patch-per-line format as `pre_model_sync`. Sections other than `pre_model_sync`/`post_model_sync` and duplicate lines become `warnings`. `_patch_log()` pages through every `Patch Log` row (`patch`, `skipped`; 1,000 per request, refuses more than 50,000). As in `run_all`, a row with `skipped=1` doesn't count as run (the entry is pending with `skipped: true`), and `finally:` is dropped for the comparison. Returns `format` (`sections`/`plain`/`missing`), `entries`, `applied_count`, `pending` (file order, `patch`/`section`/`line`), `pending_count`, `unknown_in_log` (rows whose dotted path starts with `<app>.` and no line names), `unknown_in_log_count`, `warnings`; lists capped at 200.
  - **`get_recent_errors(since, method, reference_doctype, limit=20, offset=0)`**: `Error Log`, `creation desc`, filters `creation >=` (ISO, no time zone), `method like %…%`, `reference_doctype =`. `_error_row()` keeps `name`, `creation`, `method`, `reference_doctype`, `reference_name`, `exception` (last non-empty line, ≤500 chars), `traceback` (last 40 lines, last 4,000 chars), `traceback_truncated`. Returns `_page()`; `limit` 1-50. `offset` was added so the page can be followed.
  - **`list_app_methods(app, search, limit=100, offset=0)`**: `os.walk` over the package (symlinked folders not followed; `tests`, `test`, `node_modules`, `__pycache__`, hidden folders skipped), each `.py` resolved and skipped if outside the app, >1 MB, or unparsable (reported in `skipped`, at most 50); at most 5,000 files (`files_truncated`). Only files containing `whitelist` are `ast.parse`d. Recognizes `@frappe.whitelist`/`(...)` (also `import frappe as x`) and bare `@whitelist`/aliases from `from frappe import whitelist`. Module-level functions → `kind: module`; methods of module-level classes → `kind: document` with `doctype` from the controller folder's JSON `name` (else `null`). Rows: `method`, `kind`, `doctype`, `args` (`name`, `annotation`, `default` via `ast.unparse`; `self` dropped), `doc` (first docstring paragraph, ≤300), `allow_guest`, `methods` (Frappe's default four when absent), `file` (`<app>/<path>.py:<def line>`). Returns `_page()` plus `files_scanned`, `files_truncated`, `skipped`.
  - All three in the `read` toolset (so `readOnlyHint`), in `test_server_registers_expected_tools`, and in the read-only smoke runner (`get_recent_errors` as a probe; the other two in the `FRAPPE_APPS_PATH` block).
  - 30 unit tests in `tests/test_diagnostics.py` (sections, comments, `execute:`/`finally:`, applied/pending/skipped/unknown, paging and the row cap, list bounds, missing file, missing checkout, `execute:` never run; error filters, argument checks, trimming by lines and characters, paging; each decorator form, aliases, a local `whitelist` not counted, class method with DocType, search and paging, skipped folders, syntax error, symlink escape without leaking the absolute path, byte and file caps, app never imported).
  - Docs: tool reference (new "Developer diagnostics" section, toolset table), AI agent guide ("After a change, check" — drift, pending patches, recent errors — and "Which methods does my app expose"), architecture, security, compatibility ("not yet checked"), README, `llms.txt`, CHANGELOG (Unreleased), CLAUDE.md.
- Error Log fields on v15 vs v16: checked in `error_log.json` of v15.126.0 and v16.55.1. Both have `method`, `error` (Code), `reference_doctype`, `reference_name`, `seen`, `trace_id`; v16 adds `fingerprint`, `fingerprint_stats`, and `metadata`. No mapping needed; the tool reads only the shared fields. Both are System Manager only. `Patch Log` (`patch`, `skipped`, `traceback`) is the same in both, readable by Administrator and System Manager. `patch_handler.py` differs only by v16's `never_skip_patches` hook, which doesn't change what counts as run.
- Deviations from this plan:
  - **Tool-list budget raised to 34,000** (user's choice): the three tools add ~1,540 characters (after trimming their descriptions to two lines each), which put the list at 33,390 against the 32,000 cap with only ~150 left. `TOOL_LIST_BUDGET` is 34,000 in `tests/test_mcp_contract.py`; recorded in the tool reference, architecture, CLAUDE.md, the comparison doc, and the changelog. Per toolset now: read 19 tools, about 11,700 characters.
  - `get_recent_errors` has an `offset` argument (not in the plan's signature) so `_page()` can be followed.
  - `list_pending_patches` returns `format` and `warnings` and caps its lists at 200 with exact counts; `list_app_methods` reports the DocType of a controller method from the folder's JSON `name`, not a title-cased folder name.
  - Live check not run (user chose unit tests only). Instead, the parser was compared with Frappe's own `configparser` reading on the real `frappe` and `erpnext` `patches.txt` of v15.126.0 and v16.55.1 (identical entries and order), and `list_app_methods` on an ERPNext v16 checkout found 803 methods, the same as a `grep` for the decorators, in about 2 seconds over 2,798 files.
- Notes for the next stage:
  - About 600 characters remain under the 34,000 budget. Stage 17's `call_method` (or whatever it is called) will need its own room: either trim, or raise the budget again on purpose.
  - Stage 17 can reuse `list_app_methods`' scanner (`_file_methods()`, `_whitelist_call()`, `_method_args()`) to check an allowlisted method exists and to show its arguments; the scanner reports what the checkout declares, not what the running site loaded.
  - `list_app_methods` re-scans the whole package on every call (no cache); fine for apps up to ERPNext's size, but a cache keyed on file mtimes would help if it is called in a loop.
