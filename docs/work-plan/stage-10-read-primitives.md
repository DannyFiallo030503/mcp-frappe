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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Developer-mode flag finding:
- Notes for the next stage:
