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

- Status: done.
- Branch / commits: `stage-06-update-doctype`, based on `main` at `f717b0c` (stage 05 merged).
- What changed:
  - `update_doctype(doctype, operations, expected_modified=None, confirm=False)` with `add_field`, `update_field`, `remove_field`, `move_field`, `set_properties`. It reads the raw `DocType` record (row `name`s kept), applies the operations to a copy (`_plan_doctype_update`), and on confirm PUTs the full `fields` list (with `idx` renumbered), the changed settings, and `modified`. The preview has `changes` (`added` with `insert_after`, `removed`, `moved`, `changed` per property `{from, to}`, `properties`), `warnings`, `modified`, and for standard DocTypes `app_files` (resolved under `FRAPPE_APPS_PATH` when set, else app-relative) and `requires_developer_mode`.
  - Validation: fieldtype must be one of the 44 DocField types; new fieldnames match `^[a-z][a-z0-9_]{0,63}$` and may not clash with record fields or Custom Fields (read from the meta bundle); Link/Select/Table/Table MultiSelect need `options`; Link and Table targets are checked in one `DocType` list read, and Table / Table MultiSelect targets must have `istable=1` (a Link to the DocType itself is allowed). Field properties are allowlisted (`_DOCFIELD_PROPERTIES`, from v16.33.1 `docfield.json`); DocType settings use `_DOCTYPE_PROPERTIES` minus the tables `actions`, `links`, `states`. An operation list that changes nothing is refused.
  - Data safety: a fieldname change is refused. `remove_field` warns with the count of records holding a value (`frappe.client.get_count`, `[[field, "is", "set"]]`). A fieldtype change within Customize Form's `ALLOWED_FIELDTYPE_CHANGE` groups only warns; any other change of a data field is refused unless the count is 0 (an unknown count also refuses); changes to or from Table types are refused. `reqd` added warns with the count of empty records; `unique` added warns. Single and Virtual DocTypes get no counts.
  - Confirm: stale `expected_modified` refused before the save; `TimestampMismatchError` maps to the stale message; `CannotCreateStandardDoctypeError` explains developer mode. On `httpx.TransportError` it waits with `_wait_for_server_ready`, re-reads, and succeeds only if `modified` moved and the field order, every property the operations set, and the settings match (`_doctype_matches`); it never resends. `_forget_meta` runs (in `finally`) for the DocType and every child DocType whose Table field was added, removed, or changed.
  - `delete_doctype(doctype, expected_modified=None, confirm=False)`: refuses protected-app standard DocTypes and DocTypes used by other DocTypes' Link/Table/Table MultiSelect fields (`DocField` list with `parent=DocType`, plus `Custom Field`; self-links are ignored), listing them. Preview gives the record count, `customizations_left` (Custom Field and Property Setter counts), `frappe_removes`, `frappe_keeps`, and for standard DocTypes the app folder, `requires_developer_mode`, `requires_administrator`. Confirm reconciles a reload with `_wait_for_document_deleted`.
  - Tests: `tests/test_update_doctype.py` (28 tests, 45 cases with parametrization) and an MCP round-trip in `tests/test_mcp_contract.py`; tool set test updated.
  - Docs: tool-reference ("Editing DocTypes" with the operation table and an example), ai-agent-guide ("Change a DocType", which tool to use, and the "Add a field to my DocType" recipe), architecture (edit path and reload reconciliation), security, compatibility, capability study, `CLAUDE.md`.
- Removed-field column finding: Frappe v16.33.1 never drops a column on DocType save. `DbTable.alter` (`frappe/database/schema.py`) only adds columns, changes types, and adds/drops indexes; the removed field's column and values stay until `bench trim-tables`. The preview says so.
- Allowed fieldtype changes found: `DocType.validate` does not restrict fieldtype changes at all; `DbTable.alter` just changes the column type. Only Customize Form (and Custom Field) restrict them, with `ALLOWED_FIELDTYPE_CHANGE` in `customize_form.py`: Currency/Float/Percent, Small Text/Data, Text/Data, Text/Text Editor/Code/Signature/HTML Editor, Data/Select, Text/Small Text/Long Text, Text/Data/Barcode, Code/Geolocation, Table/Table MultiSelect. `update_doctype` uses these groups as its "safe" list (`_FIELDTYPE_CHANGE_GROUPS`).
- Other Frappe findings (v16.33.1, local Bench): `check_developer_mode` raises `CannotCreateStandardDoctypeError` for non-custom DocTypes without developer mode. `on_update` runs `updatedb`, then (standard, developer mode, not importing) `export_doc`, `make_controller_template` (skips existing files), `set_base_class_for_controller`, and `export_types_to_controller` (only when the app's hooks set `export_python_type_annotations`). `Document.update_child_table` deletes rows whose `name` is not in the list and `db_update`s the rest. DocType has no `on_trash`; `frappe.delete_doc`'s DocType branch deletes the record and its child rows, Table-type Custom Fields pointing to it, and `__global_search` rows, and in developer mode `delete_controllers` removes the whole folder. It does not drop the table, does not check links, and leaves Custom Fields / Property Setters on the DocType. Only Administrator can delete a standard DocType (`check_permission_and_not_submitted`).
- Deviations from this plan:
  - `_build_doctype_payload` has no per-field rules to reuse (it checks only names, uniqueness, and properties), so the field rules are new in `_check_field_row` / `_field_values`. `create_doctype` is unchanged: applying the stricter rules there could break the e2e DocType matrix, so that is left for a later stage.
  - Beyond `collapsible_depends_on`, the tool also refuses `ignore_xss_filter` and `options` on HTML and Button fields (HTML options are rendered as markup in the form, and Button options name a server method). That is stricter than the plan, so it doesn't relax rule 2.
  - Fieldtype changes outside the Customize Form groups are refused when the field holds data, rather than only warned about.
  - `delete_doctype` refuses on links in the first call (an error listing them) rather than returning a preview with a refusal flag.
  - Developer mode can't be read over the API, so previews note it instead of checking it; the save error is translated instead.
  - The e2e runners were not changed (no existing signature or return shape changed) and none exercises the new tools yet.
- Notes for the next stage:
  - Stage 07 can use `_editable_doctype`, `_doctype_files`, and `_count_records`. `update_doctype` is the only path that writes a standard DocType after creation, and Frappe writes its JSON, so a drift check can compare the site's DocType record with `<module>/doctype/<slug>/<slug>.json`.
  - Not run live: the optional live check (add, move, change, remove on a test DocType and compare the app JSON) is still open, as is how Frappe normalizes values on save (Select options, `in_list_view` defaults). Reconciliation compares only the properties the operations set, so that normalization doesn't cause false mismatches.
  - v15 was not compared.
