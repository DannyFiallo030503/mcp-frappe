# Stage 20: Batch inserts and undo for MCP changes

**Depends on:** 15 · **Size:** large (split it at the line between the two parts if needed) · **Source:** [field feedback](../field-feedback/README.md), [native MCP comparison](native-mcp-comparison.md)

## Why

- **Test data takes one round trip per record.** `product-line-readiness-2026-11-10` needed four preview and confirmation pairs to create four QA Item Groups. Seeding a development site is a daily task. One previewed batch, inserted in a single transaction, is faster and leaves nothing half done.
- **Changes are hard to undo.** A developer trying a change wants to take it back: a DocType created by mistake, a Custom Field on the wrong DocType, a Property Setter, test records. `rollback_export` covers only files. The native MCP has no undo at all.

## Read first

- `standalone.py`: `create_document`, `_assert_writable_fields`, the preview-id helpers, `delete_document`, `delete_doctype`, `_wait_for_document_deleted`, `rollback_export` (the manifest and backup pattern)
- Frappe source: `frappe/client.py` `insert_many`:
  - its limit (200 rows on recent versions);
  - whether a failure on one row rolls back the whole request (Frappe commits at the end of a successful request and rolls back on an exception);
  - whether it's whitelisted for POST on v15 and v16.

## Part 1: batch inserts

- [ ] **New data tool `create_documents(doctype, rows, confirm=False, expected_preview=None)`:**
  - 1–100 rows. Every row is validated like `create_document` (writable fields, naming), before anything is sent.
  - The preview returns the row count, the field set, per-row problems, and a `preview_id` over the rows.
  - On confirmation, insert through `frappe.client.insert_many` if Part 1's investigation shows it is all-or-nothing. If it isn't, insert one by one, and on a failure delete the rows already created, in reverse order, and report it.
  - Return the created names.
- [ ] **Child tables** are allowed inside each row and validated against the child schema, as `create_document` does.
- [ ] **Tests:** validation per row, the preview id, a mid-batch failure (nothing left behind), and the limits.

## Part 2: undo for changes the MCP made

- [ ] **A change journal.** Each confirmed write records an entry: tool, DocType and name, what was created, and for updates the values before the change (taken from the preview read). It goes in a local JSON file under `FRAPPE_APPS_PATH/.mcp-frappe-journal/<site-label-or-host>.json`, or in memory when there is no apps path. The file is bounded (last 200 entries) and holds no secrets.
  - Writes of record data in business DocTypes are journaled only when the user turns it on (`FRAPPE_MCP_JOURNAL_RECORDS=YES`), because they can hold customer data.
- [ ] **New read tool `list_mcp_changes(limit=20)`** returns the newest journal entries.
- [ ] **New tool `undo_mcp_change(entry_id, confirm=False, expected_preview=None)`**, with an inverse for each kind of entry:
  - a created record or DocType: delete it, through the existing delete tools and their checks;
  - a created Custom Field: delete it;
  - a Property Setter: delete it, or restore the old value;
  - an update: write the old values back, bound to the current `modified`.

  The preview shows the inverse operation, and is refused if the target changed after the journaled change (its `modified` differs). Undo refuses entries that have no safe inverse: submitted documents, deleted records, exports (use `rollback_export`), and Bench commands.
- [ ] **Tests:** the inverse for each kind, refusal when the target changed later, the journal bound, and no secrets or record values in the journal unless opted in.

## Acceptance criteria

- One preview and one confirmation create up to 100 records, all or nothing.
- The last MCP change to a DocType, Custom Field, Property Setter, or test record can be undone with a preview, and the undo is refused when someone changed the target afterwards.
- Docs:
  - `docs/tool-reference.md`;
  - `docs/ai-agent-guide.md` (seeding test data; trying a change and undoing it);
  - `docs/security.md` (the journal contents and location);
  - CHANGELOG.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Multi-step changesets across tools with one confirmation: reconsider after this stage, with field evidence. Undoing changes made outside the MCP.

## Handoff

- Status: done (unit tests only; the new live runner hasn't been run).
- Branch / commits: `stage-20-batch-and-undo`, one commit on top of `main` (`ec9b157`).
- What changed:
  - **`create_documents(doctype, rows, expected_preview, confirm)`** (data toolset): 1-100 rows, each checked by `_assert_new_record()` (the record-tool limits, writable fields, `name` only for Prompt naming, the new `_assert_child_rows()`, no `Code` fields). The preview returns `count`, `fields`, per-row `problems` (duplicate prompted names included), and `preview_id` (None while there are problems). Confirm posts every row to `frappe.client.insert_many` in one request; a Frappe error becomes "Nothing was created; Frappe rolled back the whole batch: ..." with the same `exc_type`. Returns `{"created", "doctype", "names"}`.
  - **`create_document` checks child rows** through the same helper: each child value must be a list of objects whose keys are child fields (plus an optional matching `doctype`); standard columns are refused. Read-only child fields stay allowed, because mapped drafts set them (an existing test caught this).
  - **The change journal**: `_journal()` appends `{id, at, tool, target, undo | no_undo}` to `FRAPPE_APPS_PATH/.mcp-frappe-journal/<label|site|host>.json` (folder 0700, file 0600, `_atomic_write`, symlinks refused), or to `_journal_memory` without an apps path; newest 200 kept. Confirmed results gain `mcp_change`; a journal failure adds `journal_error` and never fails the write (the error never contains the apps path). Tools with an inverse write their own entry (`_SELF_JOURNALED`); `_tool()`'s wrapper journals every other confirmed write through `_journal_call()` (identifying arguments only, `no_undo` from `_NO_UNDO`). Record writes need `FRAPPE_MCP_JOURNAL_RECORDS=YES`; `_CONFIG_DOCTYPES` (schema DocTypes plus the record-tool redirect DocTypes) don't.
  - **`list_mcp_changes(limit=20)`** (read toolset, 1-50) and **`undo_mcp_change(entry_id, expected_preview, confirm)`** (data toolset, destructive). Inverses: `delete_documents` (created records, Custom Fields, new Property Setters; one list read checks `modified` and docstatus 0, then `delete_document` per record, newest first; a stopped batch keeps the rest in the entry), `update_document` (old values from the confirm read; child rows trimmed to `name`, `idx`, and child fields; Password fields and values over 50,000 characters get no inverse), `set_field_property` (old value of an existing setter), `delete_doctype` (created DocTypes), `update_doctype` (`_inverse_doctype_operations()`: remove added fields, restore properties and changed field values, re-add removed fields and move moved ones after their old predecessor, walking the old order). The preview runs the inverse tool's own preview and refuses when a journaled `modified` differs; the inverse runs with `_journal_paused` set, then the entry gets `undone_at`. Undo refuses when the inverse tool's toolset isn't selected.
  - Tool-list caps raised by 3,000 (40,000 / 42,500 / 41,500 / 44,000), as the user chose; the three tools add about 2,350 characters. The MCP `instructions` are unchanged (they are at 1,494 of 1,500 characters).
  - Tests: `tests/test_batch_and_undo.py` (43), an autouse fixture in `conftest.py` that empties the in-memory journal and unsets the opt-in, and updates to the tool-set, budget, and Custom Field result tests.
  - Live runner: `e2e/run_standalone_batch_undo.py` (`FRAPPE_ALLOW_MUTATION_TEST=YES`; ToDos only, journal in memory).
  - Docs: tool-reference ("Batch inserts", "Change journal and undo"), ai-agent-guide ("Seed test data", "Try a change, then undo it"), security ("Change journal"), installation (`FRAPPE_MCP_JOURNAL_RECORDS`), architecture, compatibility (matrix row and runner, not run), ROADMAP, llms.txt, CHANGELOG, CLAUDE.md.
- `insert_many` behavior found (v15 / v16): identical in Frappe v15.126.0 and v16.55.1. `@frappe.whitelist(methods=["POST", "PUT"])`; refuses more than 200 docs; inserts each with `insert_doc` → `frappe.get_doc(doc).insert()` (a child-table doc is appended to its parent and the parent saved). `frappe.app.application` commits only when the request ends without an exception (v15 `sync_database` plus a `rollback` in `finally` for unsafe methods; v16 `db.rollback(chain=True)` in the `except`), so one failing row rolls back the batch. Exceptions: a controller or hook that calls `frappe.db.commit()`, and DDL on MariaDB, which commits implicitly. Also checked: a DocType save never drops a column (`frappe.model.delete_fields` is used only by patches; `trim_tables` is a command), so an undo that re-adds a removed field gets its data back.
- Deviations from this plan:
  - No one-by-one fallback: `insert_many` is all-or-nothing on both versions.
  - `customize_doctype`, `rename`, `set_role_permissions`, the `upsert_*` tools, and `promote_custom_doctype` are journaled without an inverse (their `no_undo` names the tool that reverses them). The plan's inverse list didn't name them; `customize_doctype`'s save touches many setters and Custom Fields at once and would need its own inverse planning.
  - A new DocType-level Property Setter that replaced field-level ones has no inverse: deleting it wouldn't bring them back.
  - `update_doctype` edits are undoable (the plan's generic "an update" rule), through inverse `update_doctype` operations so its checks (lossy fieldtype changes, record counts) run again.
  - The undo tool lives in the `data` toolset and needs the inverse tool's toolset to be selected too.
  - Entry ids are 12 hex characters; timestamps are local time with offset.
  - `mcp-frappe check` doesn't report the journal.
  - The live runner wasn't run: the user chose unit tests only.
- Notes for the next stage: run `e2e/run_standalone_batch_undo.py` on the v15 and v16 Benches and fill the matrix row. The journal file is read-modify-write without a lock, so two server processes for the same site can lose an entry; one process per site is the documented setup. An inverse for `customize_doctype` (setters and Custom Fields created, updated, removed) and for `upsert_*` updates would be the natural next undo kinds, with field evidence.
