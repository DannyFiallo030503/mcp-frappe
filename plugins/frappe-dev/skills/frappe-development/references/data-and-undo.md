# Test data and undo

## Seed records

1. Read the schema and build the rows.
2. Preview `create_documents(doctype, rows=[...])`, at most 100 rows. If the preview lists `problems`, fix those rows and preview again; there is no `preview_id` until every row passes.
3. Show the user `count` and `fields`, then confirm with the same rows and `expected_preview`.

The batch is all or nothing: if Frappe refuses a row, nothing was created. For more than 100 records, send several batches.

Submit and cancel a document to test a controller: `submit_document` and `cancel_document`, each previewed and confirmed with `expected_modified`. Ask first: submitting runs the controller, which may post ledger or stock entries.

## Undo a change this server made

1. `list_mcp_changes()` lists the newest changes with their `id`. A confirmed result also returns its id as `mcp_change`.
2. `undo_mcp_change(entry_id)` previews the inverse: delete created records, a Custom Field, a Property Setter, or a DocType; write an update's old values back; or reverse an `update_doctype` edit.
3. Confirm with `expected_preview`.

Undo is refused when the target changed after the journaled change: say so. Undo the newest change first when several touched the same target. Deletes, submit and cancel, exports (use `rollback_export` with the rollback id in the entry's target), renames, `customize_doctype`, and Bench commands have no undo; the entry's `no_undo` says what to do instead.

Record writes (`create_document`, `create_documents`, updates) are journaled only with `FRAPPE_MCP_JOURNAL_RECORDS=YES`; `list_mcp_changes` reports it as `records_journaled`. Without it, delete seeded records with `delete_document`.

## Method calls

This server calls only the app methods the user lists in `FRAPPE_MCP_METHODS` (`list_allowed_methods`). `list_app_methods(app, as_config=True)` suggests entries; the user adds them. With Frappe's built-in MCP server connected, use its `call_method` instead.
