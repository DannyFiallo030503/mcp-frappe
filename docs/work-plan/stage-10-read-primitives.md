# Stage 10: Read primitives, submit and cancel

**Depends on:** 05 (can run in parallel with 06–09) · **Size:** medium · **Fixes:** structural gap 3 (reads) and part of gap 4 in [assessment.md](assessment.md)

## Why

A developer checks their work by reading the site: does the new field have values, how many records does the migration leave, which DocType was it again on a site with a thousand of them, is developer mode on. The server can't page past the first 100 rows, sort, count, search DocType names, or say which site and versions it is connected to. And a developer who builds a submittable DocType can create test drafts but can't submit or cancel them to check the controller logic.

## Read first

- `standalone.py`: `list_documents`, `list_doctypes`, `list_installed_apps`, `update_document`, `_expected_modified`
- Frappe:
  - `frappe/client.py` (`get_count`; `submit(doc)` rebuilds the document from the full dict, so `modified` is timestamp-checked; `cancel(doctype, name)` is not);
  - `frappe/desk/search.py` (`search_link`, `search_widget`);
  - `frappe/utils/change_log.py` (`get_versions`);
  - `frappe/auth.py` (`get_logged_user`);
  - `frappe/model/workflow.py`: how an active workflow blocks direct docstatus changes.

## Tasks

### Reads

- [ ] **Paging and sorting in `list_documents`.** Add `offset` (sent as `limit_start`), `order_by`, and `or_filters`.
  - `order_by` must look like `<field> asc|desc`, comma-separated if there are several, and each field must be readable.
  - **Return shape (breaking, acceptable before 1.0):** `{"rows": [...], "offset": n, "limit": n, "has_more": bool}`. Compute `has_more` by fetching `limit + 1` rows. Update the e2e runners.
- [ ] **New tool `count_documents(doctype, filters=None)`.** Call `frappe.client.get_count`, and validate filters the same way as `list_documents`. Stage 06 may have added a private count helper for removed-field warnings; reuse it.
- [ ] **Search in `list_doctypes`.** Add `search` (a case-insensitive substring of the name, sent as `["name", "like", "%...%"]`), `offset`, `app` (DocTypes in that app's modules), and `custom` and `is_submittable` in the output. Use the same `has_more` return shape.
- [ ] **New tool `search_records(doctype, text, filters=None, limit=10)`.** Call `frappe.desk.search.search_link` and return `value`, `label`, and `description`. Cap `limit` at 50.
- [ ] **New tool `get_site_info()`.** Return:
  - the current user (`frappe.auth.get_logged_user`);
  - installed apps with `version` and `title` only, from `frappe.utils.change_log.get_versions`;
  - the Frappe major version, as a separate key;
  - whether developer mode is on (investigate first). Find a whitelisted endpoint that exposes it, such as the boot data Desk loads, and return only that flag. If there's none, leave it out and say so in the output; the developer tools then explain developer-mode errors when they happen.
  - whether `FRAPPE_APPS_PATH` is set and which of the site's apps it contains (local check, no paths outside it).
- [ ] **Reconsider `list_installed_apps`.** Module Def can be read by `All` (checked in v16), so it may stay. Consider merging app versions from `get_site_info` into it, or folding it into `get_site_info`. If you remove it, update `test_server_registers_expected_tools`.

### Submit and cancel (minimal)

These exist so a developer can test a submittable DocType's controller. Amend and workflow actions on documents are in the [backlog](backlog/lifecycle-workflow.md).

- [ ] **`submit_document(doctype, name, expected_modified=None, confirm=False)`.**
  - The preview shows `docstatus`, `modified`, and whether the DocType is submittable (from the stage 05 metadata). It also says whether an active Workflow exists for the DocType; if one does, refuse and say the workflow controls submission.
  - On confirm, read the document, check `modified`, and POST `frappe.client.submit` with the full document dict.
- [ ] **`cancel_document(doctype, name, expected_modified=None, confirm=False)`.** The document must have `docstatus` 1. The preview warns that cancelling runs the controller's `on_cancel`, which may reverse ledger or stock entries. On confirm, check `modified`, then call `frappe.client.cancel`.

### Tests

- [ ] offset and `has_more`;
- [ ] `order_by` validation, rejecting `name; drop` and unknown fields;
- [ ] `or_filters` is forwarded;
- [ ] `count_documents` validates filters;
- [ ] `list_doctypes` search and `app` produce the right filters;
- [ ] `search_records` caps its limit;
- [ ] `get_site_info` trims the response from `get_versions` and never returns local paths outside `FRAPPE_APPS_PATH`;
- [ ] submit refuses non-submittable DocTypes and DocTypes with an active workflow, and its payload contains `modified`;
- [ ] cancel requires docstatus 1.

## Acceptance criteria

- The new tools are registered and covered by the contract test.
- `docs/tool-reference.md` documents every new argument and tool. `docs/ai-agent-guide.md` starts its workflow with `get_site_info`, then `list_doctypes(search=...)`, then `get_doctype_schema`, and has a "test my submittable DocType" recipe (create a draft, submit, cancel).

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Reports (stage 11). Amend and workflow actions on documents (backlog). Linked-document graphs.

## Handoff

- Status: done.
- Branch / commits: `stage-10-read-primitives`, based on `main` at `4622620` (stages 00-09 merged).
- What changed:
  - `list_documents(doctype, filters, fields, limit, offset, order_by, or_filters)` returns `{"rows", "offset", "limit", "has_more"}`: it sends `limit_start` and `limit_page_length = limit + 1`, and `_page()` trims the extra row. `order_by` goes through the new `_order_terms()` (moved out of `_report_order_by`, which now uses it): `<fieldname> [asc|desc]`, up to 3, each a stored field or standard column (`_assert_value_column`, also moved up), rebuilt as `field dir` before sending; default `modified desc`. `or_filters` is validated by `_assert_filters` and sent only when given.
  - `count_documents(doctype, filters)` -> `{"doctype", "count"}` via a shared `_get_count()`; `_count_records` (stage 06, swallows errors) now wraps it, while the tool raises Frappe's error.
  - `list_doctypes(search, app, module, include_child_tables, limit=100 (max 500), offset)` returns the same page shape; rows add `is_submittable`. `search` must match `_SAFE_NAME` and is sent as `["name", "like", "%text%"]`.
  - `search_records(doctype, text, filters, limit=10 (max 50))` calls `frappe.desk.search.search_link` with only `doctype`, `txt`, `page_length`, and validated `filters`; never `query` or `ignore_user_permissions`. Results trimmed to `value`/`label`/`description` (500 chars each).
  - `get_site_info()`: `user`, `apps` (`app`/`title`/`version` from `get_versions`), `frappe_major_version`, `apps_path` (`configured`, or `exists: false`, or `site_apps_found`/`site_apps_missing` via `_app_root`; no paths), and `notes` (developer mode, below).
  - `list_installed_apps` kept unchanged: it gives each app's modules, which the write tools need and `get_versions` does not.
  - `submit_document` / `cancel_document(doctype, name, expected_modified, confirm)`. `_lifecycle_target()` drops the cached bundle, reads it fresh, and refuses non-submittable DocTypes, DocTypes with an active Workflow, and the wrong docstatus (0 for submit, 1 for cancel). Submit confirm re-reads with `_expected_modified` and POSTs `{"doc": <that document>}` to `frappe.client.submit`; `TimestampMismatchError` maps to the stale message. Cancel confirm checks `expected_modified`, then POSTs `{doctype, name}` to `frappe.client.cancel`. Both return `{submitted|cancelled, doctype, name, docstatus, modified}`.
  - `_meta_bundle` now carries `"workflow"` (the active Workflow's name from `__workflow_docs`, else `None`); `get_doctype_schema` shows it (`workflow` in compact only when set, always in full).
  - Tests: `tests/test_read_primitives.py` (53 cases), an MCP round-trip for a paged `list_documents` plus a `submit_document` preview, the tool-set test, and `test_metadata`'s full-schema keys.
  - e2e: all four runners read `["rows"]` from `list_documents`/`list_doctypes`; the smoke runner also calls `get_site_info` and records the app count and Frappe major version. Not run (the user chose unit tests only).
  - Docs: tool-reference (pages, sorting, OR filters, counts, search, site info, submit/cancel), ai-agent-guide (starts with `get_site_info` -> `list_doctypes(search=...)` -> `get_doctype_schema`; paging; "Test my submittable DocType" recipe), architecture, security, compatibility, capability study, README, installation, `CLAUDE.md`.
- Deviations from this plan:
  - `cancel_document` also refuses DocTypes with an active Workflow (the plan only asked it of submit): Frappe's `set_workflow_state_on_action` runs for cancel too, so a direct cancel would skip the workflow's transitions and roles the same way.
  - The active Workflow comes from the metadata bundle (`__workflow_docs`, served by `getdoctype` to any logged-in user) instead of a `Workflow` list read, which needs more than ordinary roles. Submit and cancel read it fresh.
  - `list_doctypes` default limit is 100 (was 500) and the cap is 500 (was 1000), now that it pages.
  - `count_documents` takes `filters` only: `frappe.client.get_count` has no `or_filters` parameter.
  - `order_by` takes up to 3 terms (the same limit as `upsert_report`) and defaults the direction to `asc`.
- Developer-mode flag finding (v16.33.1): no whitelisted method returns it. `boot.load_conf_settings` copies `developer_mode` into the boot info, but that is only rendered into the Desk page (`www/desk.py`, `frappe.boot = {...}`); `frappe.sessions.get` is not whitelisted. Scraping `/desk` would mean a large HTML page with session side effects (`get_csrf_token` commits), so `get_site_info` leaves it out and says so in `notes`.
- Other Frappe findings (v16.33.1): Frappe does **not** block a direct submit/cancel when a Workflow is active. `Document.validate_workflow` checks only `workflow_state` transitions, and `set_workflow_state_on_action` then moves the document to the first state with the new docstatus. `frappe.client.get_list` accepts `limit_start` and `or_filters`; `search_link`'s results are `{value, description, label?}` from `build_for_autosuggest`; on Postgres, `like` becomes `ILIKE`, so `search` is case-insensitive on both databases; `get_versions` also returns `description`, `branch`, `branch_version` (with a commit ref), `color`, and `logo`, which are dropped.
- Notes for the next stage:
  - Stage 11's annotations: `get_site_info`, `count_documents`, `search_records`, and the list tools are read-only; `submit_document` and `cancel_document` are mutations with previews (cancel is destructive in effect).
  - The `has_more` page shape is the pattern for any new list tool; `list_app_drift` still uses its own `next_offset`.
  - Not run live: everything above. v15 was not compared (v15's `get_versions` and `search_link` may differ).
