# Stage 05: Metadata layer (cache, compact schema, fewer requests)

**Depends on:** 02 · **Size:** large · **Fixes:** structural gaps 1 and 2 in [assessment.md](assessment.md)

## Why

Every developer tool starts by reading a schema, and stages 06–09 read it even more often:

- The schema is returned with all 63 DocField properties per field, nulls included. Large ERPNext DocTypes cost tens of thousands of tokens, which crowds out the developer's own code in the model's context.
- Nothing is cached, so one filtered list makes 10 HTTP requests.
- Record tools read metadata through REST resources that only System Manager can read. Developers usually connect as Administrator, so this matters less for them, but `getdoctype` fixes it for free: the record tools then work for any user with permission on the DocType.

## Read first

- `standalone.py`: `_doctype_raw`, `_doctype`, `_sort_merged_fields`, `_assert_*fields` (renamed in stage 02), `get_doctype_schema`, `get_document`, `set_role_permissions`, `export_customizations`
- Frappe `frappe/desk/form/load.py`:
  - `getdoctype`: whitelisted, with no permission check beyond a logged-in user, and its result goes in the `docs` key;
  - `get_meta_bundle`: the main DocType first, then one entry per table field's child DocType, all `as_dict(no_nulls=True)`.
- Frappe `frappe/desk/form/meta.py`, `frappe/model/meta.py` (`Meta`, field sorting, and how custom fields and property setters are applied)

## Tasks

- [x] **New metadata source.** Add `_meta_bundle(doctype)`, which calls `GET /api/method/frappe.desk.form.load.getdoctype?doctype=...`. The response has no `message` key, so `_check` returns the whole payload; read `payload["docs"]`, and adjust `_check` if needed. Return `{"doctype": docs[0], "children": {child_name: meta, ...}}`.
- [x] **Cache it.** Use a per-process cache with a TTL (`FRAPPE_META_TTL` seconds, default 300; `0` turns it off). Remove a DocType's entry after any MCP schema mutation on it: `create_doctype`, `create_custom_field`, `set_field_property`, `set_role_permissions`. The `fake_site` fixture must clear the cache between tests.
- [x] **Switch record tools to it.** `list_documents`, `get_document`, `create_document`, `update_document`, and field validation should use `_meta_bundle`. They must **never** call `/api/resource/DocType`, `Custom Field`, or `Property Setter`. Child-table field validation (stage 02) should use `children` from the bundle.
- [x] **Delete the hand-written merge.** Remove `_doctype`'s merge of Custom Fields and Property Setters and `_sort_merged_fields`, since Frappe's `Meta` already does it. First confirm in the Frappe source that `get_meta` applies `insert_after` ordering and property setters. Remove or rewrite the tests that cover the deleted helpers.
- [x] **Keep raw reads for admin tools only.** `_doctype_raw` stays only where the raw DocType record is really needed (for example `_doctype_exists`, used by `create_doctype`). Reword the remaining callers or move them to `_meta_bundle`.
- [x] **Compact schema by default.** Add `get_doctype_schema(doctype, detail: Literal["compact", "full"] = "compact")`.
  - `compact` returns DocType flags (`name`, `module`, `istable`, `issingle`, `is_submittable`, `is_tree`, `autoname`, `title_field`, `custom`). It returns only data fields, skipping layout types such as Section/Column/Tab Break, HTML, Button, and Fold. Each field has only `fieldname`, `label`, `fieldtype`, `options`, `reqd`, `read_only`, `hidden`, `default`, `unique`, and `permlevel`, and falsy keys are dropped. Child tables appear compactly under `child_tables`.
  - `full` returns the bundle unchanged.
- [x] **Smaller `get_document` default.** When `fields` is omitted, return only non-empty data fields, plus `name`, `doctype`, `modified`, and `docstatus`. Truncate `Table MultiSelect` as well as `Table`.
- [x] **Tests:**
  - `get_document` and `list_documents` make no requests to `/api/resource/DocType*`, `Custom Field`, or `Property Setter`;
  - the second call for the same DocType doesn't fetch metadata again;
  - the cache is invalidated after `create_custom_field`;
  - the compact output contains no layout fields and no falsy keys;
  - child-table validation uses the bundle.

  Base the `getdoctype` sample responses on the real response shape (a hand-written fixture is fine).

## Acceptance criteria

- A filtered `list_documents` makes at most 2 HTTP requests cold and 1 warm (assert it with `fake_site`).
- Compact schema output for a 100-field sample DocType is at least 5 times smaller than `full`. Assert the ratio in a test.
- `docs/tool-reference.md`, `docs/architecture.md`, and `docs/security.md` say that record tools now work for any user with permissions on the DocType. `docs/ai-agent-guide.md` says to use `compact` by default and `full` only when editing a DocType needs every property.
- **Optional live check (only if the user asks):** on a disposable site, read a DocType and a record as a non-System-Manager user who has read permission. Record the result.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

New tools (stages 06 and 10). Editing DocTypes (stage 06).

## Handoff

- Status: done.
- Branch / commits: `stage-05-metadata-layer`, based on `main` at `65d27c6`.
- What changed:
  - `_meta_bundle(doctype)` reads `GET /api/method/frappe.desk.form.load.getdoctype` and returns `{"doctype": meta, "children": {child: meta}}`. `_check` needed no change: the response has no `message`/`data` key, so the whole payload comes back and `docs` is read from it.
  - Per-process cache `_meta_cache` with `FRAPPE_META_TTL` (default 300, `0` = off; invalid values refuse startup like `FRAPPE_TIMEOUT`). `_forget_meta(doctype)` drops the DocType and every cached bundle holding it as a child; it runs (in `finally`, so failed or uncertain writes count too) after `create_custom_field`, `set_field_property`, `set_role_permissions` confirms and after `create_doctype` creation. `create/update/delete_document` on `_SCHEMA_DOCTYPES` (DocType, DocField, DocPerm, Custom Field, Property Setter, Custom DocPerm) clears the whole cache, since editing a Custom Field record by name doesn't say which DocType it belongs to without another read.
  - Deleted `_doctype`, `_doctype_raw`, and `_sort_merged_fields` and their two ordering tests. `set_role_permissions` now reads `istable`/`is_virtual` from the bundle, so `_doctype_raw` had no callers left; `_doctype_exists` (used by `create_doctype`) keeps its own `DocType` list read.
  - `_assert_filters(bundle, filters)` validates child-table conditions against `bundle["children"]` (no extra request). `_customized_doctypes` (export) walks child tables from the bundle.
  - `get_doctype_schema(doctype, detail="compact"|"full")` as specified; compact `child_tables` is `{child_doctype: [fields]}`.
  - `get_document` without `fields`: data fields with a value plus `doctype`, `name`, `modified`, `docstatus`; child rows reduced to their non-empty data fields plus `name` and `idx`. Table MultiSelect is truncated like Table.
  - `fake_site.add_doctype()` now answers `getdoctype` (children looked up at request time; unregistered child fails loudly), `fake_site.meta_calls()` lists metadata reads, and the fixture clears `_meta_cache` and unsets `FRAPPE_META_TTL`. New `tests/test_metadata.py` (realistic hand-written `getdoctype` fixture with all DocField Check/Int properties and form assets) plus an MCP contract test for the `detail` enum.
  - `e2e/run_standalone_doctype_matrix.py` reads schemas through a `full_schema()` helper (`detail="full"`), because it checks layout field types, `is_virtual`, `description`, and Fold ordering. Smoke, mutation, and customization runners work with the compact default unchanged. Not run (no live site requested).
  - Docs: tool-reference (metadata source, cache, schema detail, `get_document` default), architecture (new Metadata section), security, ai-agent-guide, installation-and-configuration (`FRAPPE_META_TTL`), compatibility, `CLAUDE.md`.
- Deviations from this plan:
  - `full` is the bundle minus Frappe's form assets (`__js`, `__list_js`, `__print_formats`, `__dashboard`, templates, ...). They are client source and print templates, not schema, and can be tens of KB, which conflicts with bounded outputs.
  - Default `get_document` keeps `0`/`false` values (only `null`, `""`, and `[]` count as empty), and also compacts child rows, which the plan didn't ask for.
  - Cache invalidation also covers record writes to schema DocTypes (see above).
- Measured size reduction (sample or live): 100-field sample (`tests/test_metadata.py::_sales_order_bundle`): `full` 129,203 bytes of JSON, `compact` 6,544 bytes, 19.7x smaller. Not measured live.
- Frappe source checked (v16.33.1, local Bench): `getdoctype` is whitelisted with no permission check beyond login and extends `frappe.response.docs` with `get_meta_bundle`; `get_meta_bundle` returns the DocType then one `as_dict(no_nulls=True, parenttype=...)` per `table_fields` (Table and Table MultiSelect) field; `Meta.process` calls `add_custom_fields` (sets `is_custom_field`), `apply_property_setters` (cast by `property_type`), and `sort_fields` (`field_order`, then `insert_after`). v15 was not checked: its `getdoctype` also takes `cached_timestamp`, which we don't send.
- Notes for the next stage:
  - Use `_meta_bundle(doctype)["doctype"]` for any schema read and call `_forget_meta(doctype)` after every metadata write (stage 06's `update_doctype` must). Never read `/api/resource/DocType` for validation.
  - Developer-mode Benches don't cache form meta (`frappe.desk.form.meta.get_meta`), but our TTL cache still does; a change made in Desk shows after at most `FRAPPE_META_TTL` seconds.
  - Optional live check as a non-System-Manager user is still open.
