# Stage 06: Edit existing DocTypes

**Depends on:** 05 · **Size:** large · **Fixes:** structural gap 8 in [assessment.md](assessment.md)

## Why

`create_doctype` can scaffold a DocType, but most development is changing one that already exists: add a field, make one mandatory, move it to another section, add a child table, turn on tracking, change the naming rule. Today the only way is `update_document` on the `DocType` record. That gives no schema-aware validation, no readable preview, no handling of the Bench reload a standard DocType edit triggers, and no guard against silently deleting fields by leaving them out of the `fields` list.

## Read first

- `standalone.py`: `create_doctype`, `_build_doctype_payload`, `_DOCTYPE_PROPERTIES`, `_doctype_exists`, `_doctype_raw`, `_wait_for_doctype`, `_wait_for_server_ready`, `_expected_modified`, `_assert_writable_app`, `_assert_app_module`, `_assert_remote_module`, `update_document`
- The stage 05 Handoff (metadata cache and invalidation)
- Frappe `frappe/core/doctype/doctype/doctype.py`. Check each of these before relying on it:
  - the developer-mode check in `validate` (a standard DocType can't be saved without developer mode);
  - which fieldtype changes `validate` allows (look for the allowed-change list, and compare it with `frappe/custom/doctype/customize_form/customize_form.py`);
  - `on_update`: when it exports the JSON to the app and whether it regenerates controller files;
  - what happens to the database column of a removed field (`frappe/database/schema.py`, `updatedb`).
- Frappe `frappe/model/document.py`: how a PUT with a child table list inserts, updates, and deletes rows by `name`.

## Design

- **One tool, a list of operations.** `update_doctype(doctype, operations, confirm=False, expected_modified=None)`. Each operation is a small dict, so the model never has to send the whole `fields` list back:
  - `{"op": "add_field", "field": {...}, "insert_after": "fieldname"}`;
  - `{"op": "update_field", "fieldname": "...", "set": {...}}`;
  - `{"op": "remove_field", "fieldname": "..."}`;
  - `{"op": "move_field", "fieldname": "...", "insert_after": "fieldname"}`;
  - `{"op": "set_properties", "set": {...}}` for DocType settings in `_DOCTYPE_PROPERTIES`.

  The tool reads the current DocType, applies the operations in order to a copy, and sends one PUT with the full `fields` list. Rows keep their `name`, so Frappe updates them instead of recreating them.
- **Who it's for.** Standard DocTypes of a writable app (not `_PROTECTED_APPS`), and site-custom DocTypes (`custom=1`). For DocTypes of `frappe` or `erpnext`, refuse and point to `create_custom_field` and `set_field_property`.
- **Field validation.** Reuse the field rules from `create_doctype` (`_build_doctype_payload`): allowed fieldtypes, `_SAFE_NAME` fieldnames, `options` required for `Link`, `Table`, and `Select`, and `Link`/`Table` targets that exist. A `Table` target must be a child DocType (`istable=1`).
- **Refuse what loses data silently.** Changing a `fieldname` is refused (renames are in the backlog). `remove_field` on a field that holds data is allowed only after the preview says so: if the tool can count non-empty values with `frappe.client.get_count`, show the count.
- **Code fields.** `set_field_property` already accepts `depends_on`, `mandatory_depends_on`, and `read_only_depends_on`, whose `eval:` values are expressions Frappe evaluates. `update_doctype` may accept the same three, for consistency. Don't accept any other property that Frappe evaluates as code without asking the user first (rule 2 of the README).

## Tasks

- [ ] **`update_doctype` preview.** Return:
  - a schema diff: `added`, `removed`, `moved`, and `changed` (per field, `{property: {"from": old, "to": new}}`), plus DocType property changes;
  - the DocType's `modified` (confirmation must send it back as `expected_modified`);
  - for standard DocTypes, the app files Frappe will rewrite (the DocType JSON path under `FRAPPE_APPS_PATH`, checked with `_app_path`), and a note that developer mode must be on;
  - warnings: removed fields and their value counts, fieldtype changes that change the column type, `reqd` added to a field that has empty rows.
- [ ] **Confirm.** Check `expected_modified`, send the PUT with `modified` in the body (the same atomic check stage 02 adds to `update_document`), invalidate the stage 05 metadata cache for the DocType and for any child DocType touched.
- [ ] **Reload tolerance.** A standard DocType edit makes Frappe write the JSON, which can make a developer-mode Bench reload. On `httpx.TransportError`, wait for the server (`_wait_for_server_ready`) and re-read the DocType. Report success if `modified` moved and the fields match the intended result; report failure otherwise. Never resend blindly.
- [ ] **`delete_doctype(doctype, confirm=False, expected_modified=None)`.** For DocTypes created during development by mistake. Read `on_trash` and `after_delete` in `doctype.py` first, and state in the preview what Frappe removes: the table, the app folder in developer mode, linked records such as Custom Fields. Refuse if other DocTypes link to it through `Link` or `Table` fields (list them), and refuse protected apps. `destructiveHint=True` once stage 11 adds annotations.
- [ ] **Tests** (with `fake_site`):
  - each operation produces the right `fields` list, and existing rows keep their `name`;
  - the diff lists added, removed, moved, and changed fields;
  - a fieldname change is refused;
  - a protected-app DocType is refused with a hint to the customize tools;
  - invalid `Link`/`Table` targets are refused;
  - the PUT carries `modified`, and a stale `expected_modified` is refused;
  - a `TransportError` during the PUT is reconciled by re-reading;
  - the metadata cache is invalidated;
  - `delete_doctype` refuses a DocType that others link to.

## Acceptance criteria

- `update_doctype` and `delete_doctype` are registered and in the contract test.
- `docs/tool-reference.md` documents the operation list with an example. `docs/ai-agent-guide.md` has an "add a field to my DocType" recipe and explains when to use `update_doctype` and when to use the customize tools. `docs/architecture.md` covers the reload reconciliation for this write path.
- **Optional live check (only if the user asks, on a disposable site):** on a test DocType in a test app, add, move, change, and remove a field, and check that the app's JSON matches. Clean up.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Renaming DocTypes or fields. Editing controller Python or JavaScript files (a coding agent with file access does that). Customize Form for protected apps (stage 03 tools).

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Removed-field column finding:
- Allowed fieldtype changes found:
- Deviations from this plan:
- Notes for the next stage:
