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

- [ ] **New metadata source.** Add `_meta_bundle(doctype)`, which calls `GET /api/method/frappe.desk.form.load.getdoctype?doctype=...`. The response has no `message` key, so `_check` returns the whole payload; read `payload["docs"]`, and adjust `_check` if needed. Return `{"doctype": docs[0], "children": {child_name: meta, ...}}`.
- [ ] **Cache it.** Use a per-process cache with a TTL (`FRAPPE_META_TTL` seconds, default 300; `0` turns it off). Remove a DocType's entry after any MCP schema mutation on it: `create_doctype`, `create_custom_field`, `set_field_property`, `set_role_permissions`. The `fake_site` fixture must clear the cache between tests.
- [ ] **Switch record tools to it.** `list_documents`, `get_document`, `create_document`, `update_document`, and field validation should use `_meta_bundle`. They must **never** call `/api/resource/DocType`, `Custom Field`, or `Property Setter`. Child-table field validation (stage 02) should use `children` from the bundle.
- [ ] **Delete the hand-written merge.** Remove `_doctype`'s merge of Custom Fields and Property Setters and `_sort_merged_fields`, since Frappe's `Meta` already does it. First confirm in the Frappe source that `get_meta` applies `insert_after` ordering and property setters. Remove or rewrite the tests that cover the deleted helpers.
- [ ] **Keep raw reads for admin tools only.** `_doctype_raw` stays only where the raw DocType record is really needed (for example `_doctype_exists`, used by `create_doctype`). Reword the remaining callers or move them to `_meta_bundle`.
- [ ] **Compact schema by default.** Add `get_doctype_schema(doctype, detail: Literal["compact", "full"] = "compact")`.
  - `compact` returns DocType flags (`name`, `module`, `istable`, `issingle`, `is_submittable`, `is_tree`, `autoname`, `title_field`, `custom`). It returns only data fields, skipping layout types such as Section/Column/Tab Break, HTML, Button, and Fold. Each field has only `fieldname`, `label`, `fieldtype`, `options`, `reqd`, `read_only`, `hidden`, `default`, `unique`, and `permlevel`, and falsy keys are dropped. Child tables appear compactly under `child_tables`.
  - `full` returns the bundle unchanged.
- [ ] **Smaller `get_document` default.** When `fields` is omitted, return only non-empty data fields, plus `name`, `doctype`, `modified`, and `docstatus`. Truncate `Table MultiSelect` as well as `Table`.
- [ ] **Tests:**
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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Measured size reduction (sample or live):
- Notes for the next stage:
