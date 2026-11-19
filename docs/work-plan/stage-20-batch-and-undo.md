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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- `insert_many` behavior found (v15 / v16):
- Deviations from this plan:
- Notes for the next stage:
