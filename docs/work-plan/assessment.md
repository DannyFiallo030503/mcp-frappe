# Assessment (2026-10-25)

This is the investigation the work plan is based on. It records the project's state at commit `e8fbd0c`. Don't update it as stages land; record progress in each stage's Handoff section instead.

## What exists

- **One module**, `frappe_mcp/standalone.py` (about 1,500 lines). It's a FastMCP stdio server with 15 tools. It talks to a Frappe site over REST using an API token or password login.
- **Strengths:** native permissions are preserved; there is no SQL or eval; mutations go through preview and confirm; updates and deletes are bound to the document's `modified` timestamp; filesystem writes can't escape `FRAPPE_APPS_PATH`; exports have backups and rollback; and the code reconciles state when a developer-mode Bench reloads mid-request.
- **Evidence:** one populated Frappe/ERPNext **v15** development site, tested as Administrator only. There is no limited-user run, no v16 run, and no ERPNext business-process run.
- **Where the effort went:** about 40% of the code and most of the e2e evidence cover `create_doctype` and export. Reading data has no pagination and no counts.
- **Tests:** 20 unit tests, all passing. There is no CI. `ruff check frappe_mcp` reports 6 errors.

## Confirmed bugs

Each bug was reproduced with mocked HTTP or confirmed in the Frappe v16.33.1 source.

| # | Location | Problem | Stage |
|---|---|---|---|
| 1 | `_assert_fields`, `standalone.py:339`; `list_documents` `:872-873` | `docstatus`, `creation`, `modified`, and `owner` can't be filtered or selected; each returns `Field cannot be used`. | 02 |
| 2 | `list_documents` `:861`, `:873` | List-form filters (`[["status","=","Open"]]`) are rejected, so `or` and range queries can't be expressed. | 02 |
| 3 | `_client` `:171-175` | A failed password login is cached as the session, so later calls run as Guest. There's no re-login after the session expires. | 01 |
| 4 | `_client` `:150-156` | Token auth is allowed over plain HTTP to remote hosts; only password login checks for HTTPS. | 01 |
| 5 | `create_custom_field` `:1255` | Caller-supplied `dt` and `module` inside `field` override the validated values, bypassing the MCP's own checks. | 03 |
| 6 | `create_custom_field` `:1248`, `set_field_property` `:1280` | Both require `FRAPPE_APPS_PATH` and a local checkout. The installation docs say they don't. | 03 |
| 7 | `set_field_property` `:1321` | Frappe names Property Setters `{doc_type}-{field_name or row_name or "main"}-{property}` (`frappe/custom/doctype/property_setter/property_setter.py`), so changing the same property a second time fails as a duplicate. | 03 |
| 8 | `export_customizations` `:1361` | Fixtures are written to `<module>/fixtures/`. `bench migrate` reads only `<app>/<app>/fixtures/` (`frappe/utils/fixtures.py`, `import_fixtures`) and `<module>/custom/` (`frappe/modules/utils.py`, `sync_customizations`), so exported files are never imported. | 04 |
| 9 | `export_customizations` `:1339-1344` | DocType JSON is exported from the REST response (nulls, child-row `name`/`parent`) instead of Frappe's native export format. In developer mode Frappe already writes that file itself. | 04 |
| 10 | `update_document` `:955-956` | The stale-document check runs before the write, so changes can slip in between. Sending `modified` in the PUT body makes Frappe check it atomically (`Document.check_if_latest` compares it with `_original_modified`). | 02 |

## Structural gaps

1. **Admin-only in practice.** Every record tool reads metadata through `/api/resource/DocType`, `Custom Field`, and `Property Setter`. Only System Manager and Administrator can read those. The whitelisted `frappe.desk.form.load.getdoctype` returns merged metadata, plus child-table metadata, to any logged-in user. → Stage 05
2. **Token cost and request count.** DocField has 63 non-layout properties, and the REST response includes all of them, nulls too. For large ERPNext DocTypes a schema read is likely tens of thousands of tokens. Nothing is cached: one filtered `list_documents` makes 10 HTTP requests, and `get_document` makes 7. → Stage 05
3. **Missing read primitives:** no offset or `order_by`, no count, no DocType name search (a full ERPNext + HRMS site exceeds the 1,000-row cap), no fuzzy record search, no report discovery, and no report filter definitions. → Stages 06–07
4. **No ERPNext transaction verbs:** submit, cancel, amend, workflow, mapped documents ("make Sales Invoice from Sales Order"), comments, assignments, and attachments are all missing. → Stages 08–10
5. **The confirmation step is mostly for show.** The model sets `confirm=true` itself. No tool has MCP annotations, so hosts can't auto-approve reads and prompt on writes. The server sends no `instructions`, and tools have one-line descriptions with no per-argument docs. → Stage 07
6. **Distribution.** The package isn't on PyPI, there's no CI, `setup.py` and `requirements.txt` duplicate `pyproject.toml`, and there's no read-only mode. → Stages 00, 07, 11
7. **Stale docs:**
   - `compatibility-and-validation.md` lists defects that have since been fixed.
   - `UPSTREAM.md` says vendored code lives in `frappe_mcp/`.
   - The acceptance report quotes 14 tools and 8 tests.
   - Docs and tests use site-specific names (`cubazone`, `CubaZone`), against the "generic" rule in CONTRIBUTING. → Stages 00, 12

## Frappe endpoints the plan relies on

All were confirmed present and whitelisted in v16.33.1:

| Purpose | Endpoint |
|---|---|
| Merged metadata | `frappe.desk.form.load.getdoctype` (result is in `docs`, not `message`) |
| Count | `frappe.client.get_count` |
| Fuzzy search | `frappe.desk.search.search_link` |
| App versions | `frappe.utils.change_log.get_versions` |
| Report filters | `frappe.desk.query_report.get_script` |
| Submit / cancel | `frappe.client.submit` (takes the full doc, so it's timestamp-checked), `frappe.client.cancel` |
| Workflow | `frappe.model.workflow.get_transitions`, `frappe.model.workflow.apply_workflow` |
| Mapped documents | `frappe.model.mapper.make_mapped_doc` |
| Comments | `frappe.desk.form.utils.add_comment` |
| Assignments | `frappe.desk.form.assign_to.add` |
| Timeline / docinfo | `frappe.desk.form.load.get_docinfo` |
| File upload | `upload_file` (`frappe/handler.py`) |
| Native customization export | `frappe.modules.utils.export_customizations` (developer mode only) |

There is **no** whitelisted `get_roles` in v16; stage 06 covers how to find the current user's roles.
