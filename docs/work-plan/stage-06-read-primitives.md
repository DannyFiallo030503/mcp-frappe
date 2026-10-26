# Stage 06: Read primitives (pagination, count, search, site info)

**Depends on:** 05 · **Size:** medium · **Fixes:** structural gap 3 (reads) in [assessment.md](assessment.md)

## Why

An agent answering business questions needs a few things the server doesn't offer yet: paging past the first 100 rows, sorting, counting, finding the right DocType by name, finding a record by fuzzy text ("customer Acme"), and knowing who it is connected as and which versions are installed.

## Read first

- `standalone.py`: `list_documents`, `list_doctypes`, `list_installed_apps`
- Frappe:
  - `frappe/client.py` (`get_count`);
  - `frappe/desk/search.py` (`search_link`, `search_widget`);
  - `frappe/utils/change_log.py` (`get_versions`);
  - `frappe/auth.py` (`get_logged_user`).

## Tasks

- [ ] **Paging and sorting in `list_documents`.** Add `offset` (sent as `limit_start`), `order_by`, and `or_filters`.
  - `order_by` must look like `<field> asc|desc`, comma-separated if there are several, and each field must be readable.
  - **Return shape (breaking, acceptable before 1.0):** `{"rows": [...], "offset": n, "limit": n, "has_more": bool}`. Compute `has_more` by fetching `limit + 1` rows. Update the e2e runners.
- [ ] **New tool `count_documents(doctype, filters=None)`.** Call `frappe.client.get_count`, and validate filters the same way as `list_documents`.
- [ ] **Search in `list_doctypes`.** Add `search` (a case-insensitive substring of the name, sent as `["name", "like", "%...%"]`), `offset`, and `is_submittable` in the output. Use the same `has_more` return shape.
- [ ] **DocType discovery for non-admins (investigate first).** Listing `DocType` needs System Manager. Check whether `search_link` with `doctype="DocType"` works for an ordinary user (read `search_widget`'s permission check). If it does, fall back to it when the listing returns 403. If not, document the limitation and suggest `search_records` on known DocTypes instead.
- [ ] **New tool `search_records(doctype, text, filters=None, limit=10)`.** Call `frappe.desk.search.search_link` and return `value`, `label`, and `description`. Cap `limit` at 50.
- [ ] **New tool `get_site_info()`.** Return:
  - the current user (`frappe.auth.get_logged_user`);
  - installed apps with `version` and `title` only, from `frappe.utils.change_log.get_versions`;
  - the Frappe major version, as a separate key;
  - the user's roles (investigate first). There is **no** whitelisted `get_roles` in v16. Try `GET /api/resource/User/<user>` and read the `roles` child rows. Confirm in the source whether ordinary users can read their own User record. If not, leave roles out and say so in the output.
- [ ] **Reconsider `list_installed_apps`.** Module Def can be read by `All` (checked in v16), so it may stay. Consider merging app versions from `get_site_info` into it, or folding it into `get_site_info`. If you remove it, update `test_server_registers_expected_tools`.
- [ ] **Tests:**
  - offset and `has_more`;
  - `order_by` validation, rejecting `name; drop` and unknown fields;
  - `or_filters` is forwarded;
  - `count_documents` validates filters;
  - `list_doctypes` search produces the right filter;
  - `search_records` caps its limit;
  - `get_site_info` trims the response from `get_versions`.

## Acceptance criteria

- The new tools are registered and covered by the contract test.
- `docs/tool-reference.md` documents every new argument and tool. `docs/ai-agent-guide.md` starts its workflow with `get_site_info`, then `list_doctypes(search=...)` / `search_records`, then `get_doctype_schema`.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Reports (stage 07). Linked-document graphs.

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Non-admin DocType discovery finding:
- Roles finding:
- Notes for the next stage:
