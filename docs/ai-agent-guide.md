# AI Agent Guide

This guide is for an AI assistant operating Frappe through MCP. Treat the live site's metadata, permissions, tool descriptions, and previews as authoritative. Never assume a DocType, field, app, workflow, or permission exists because it exists in another Frappe site.

## Operating principles

1. **Discover before use.** Start with installed apps, visible DocTypes, and the schema of the target DocType.
2. **Use only returned fields.** Do not guess fieldnames, field types, allowed values, workflow actions, or report filters.
3. **Respect the connected identity.** A tool returning no records or a permission error does not authorize another route or a broader account.
4. **Preview every change.** For tools with `confirm`, first call without confirmation, inspect the result, and only then confirm the exact operation.
5. **Carry concurrency tokens forward.** For update, delete, submit, cancel, and workflow operations, pass back the exact `expected_modified` returned by the preview. If it is stale or missing, get a new preview.
6. **Verify after mutation.** Re-read the affected document/schema or inspect the resulting export diff.
7. **Minimize data.** Ask for only the fields and rows necessary. File contents, report output, and rendered HTML may contain sensitive data.
8. **Do not infer side effects.** A whitelisted method can do arbitrary work permitted by that method; only its explicit preview and server-side contract are known.

## Discovery sequence

```text
list_installed_apps()
list_doctypes(app="erpnext", include_child_tables=false)
get_doctype_schema(doctype="Customer")
list_documents(doctype="Customer", fields=["name", "customer_name"], limit=20)
get_document(doctype="Customer", name="CUST-0001")
```

Substitute app and DocType names from the live results. Child DocTypes can be listed, but `get_doctype_schema` needs `parent_doctype` to inspect a child table, and `get_document` returns child rows through the parent document. `list_documents` does not read child rows directly.

## Safe document creation

Example flow (the schema and required fields must be discovered first):

```json
{"doctype":"ToDo","values":{"description":"Review stock variance"},"name":"MCP-REVIEW-STOCK-001"}
```

Call `create_document` with `confirm` omitted or `false`. Review the preview's DocType, name, field list, and `requires_confirmation`. If it matches the user's intent, call again with the same arguments and `confirm: true`, then read the returned record.

An explicit `name` makes a create retry idempotent only when the same name and submitted values refer to the same existing document. A matching retry returns `already_exists`; a conflicting payload for that name fails. Without an explicit name, Frappe autoname is used and retries are not guaranteed to be idempotent.

## Safe updates and deletes

1. Read the current document.
2. Call `update_document` or `delete_document` without `confirm`.
3. Show the proposed fields/action to the user when the interaction requires approval. Inspect `modified` from the preview.
4. Confirm by repeating the same request with `confirm: true` and `expected_modified` copied exactly from the preview.
5. If the confirmation reports a stale timestamp, do not retry with the old token. Re-read, preview again, and reassess the changes.

Example update:

```json
{"doctype":"ToDo","name":"TODO-0001","values":{"description":"Updated description"}}
```

Then confirm with the preview's `modified` value:

```json
{"doctype":"ToDo","name":"TODO-0001","values":{"description":"Updated description"},"expected_modified":"<exact preview value>","confirm":true}
```

`delete_document` permanently deletes a document after native Frappe delete permission and the confirmation/concurrency checks pass. Never delete based only on a broad query or ambiguous user instruction.

## Submit, cancel, and workflows

- Check `get_doctype_schema` for `is_submittable` before using submit/cancel.
- Use `submit_document` or `cancel_document` with a fresh preview and `expected_modified`.
- For a workflow, call `get_workflow_actions` for the exact document. Use only an action returned for that user/document.
- Call `apply_workflow_action` first without confirmation; inspect `current_state`, `next_state`, action, and `modified`. Confirm with the same action and `expected_modified`.
- Verify the resulting state by reading the document.

Do not substitute direct field writes for a workflow transition when an active workflow governs the document.

## Schema and Customize Form operations

These tools require the `System Manager` role and target an installed app/module:

- `create_doctype`: creates a standard DocType file in the selected app module.
- `create_custom_field`: creates a native Custom Field.
- `set_field_property`: creates a Property Setter corresponding to Customize Form.
- `create_client_script`: creates a native Client Script record.
- `set_role_permissions`: adds or changes selected permission flags at one permission level.

For each, inspect the preview and target app/module/DocType carefully. Confirm only after checking for duplicate names, field types, intended permissions, and the requested destination. These operations make live site changes; they are not merely drafts.

`set_role_permissions` changes only the permission flags included in `permissions`; omitted flags retain the previewed value. New rules follow the native permission manager behavior. Be especially careful with `write`, `delete`, `submit`, `cancel`, `export`, `share`, and `email`.

## Export and rollback

For portable changes:

1. Choose an installed target app and inspect its modules using `list_installed_apps` and `list_doctypes`.
2. Call `export_customizations` without confirmation. Review every diff, fixture selection, module, and standard DocType destination.
3. Confirm using the exact returned `preview_id` as `expected_preview` and `confirm: true`. If the preview changed, request a new preview.
4. Preserve the returned `rollback_id`. Review the target app's working tree and commit the generated files using the app's normal source-control workflow.
5. To undo, call `rollback_export` for a preview, review all listed paths, then confirm. Rollback restores/removes files; it does not reverse every live database record or deploy/migrate another site.

Do not export to an unrelated app. Standard DocTypes must belong to the selected app/module. Export output includes fixture files and updates a managed section in `hooks.py`; existing content outside that section is preserved.

## Reports, workspaces, print formats, files, and translations

- Call `list_reports` before `run_report`; use only reports visible to the current user. Keep `limit` small and pass only relevant filters.
- Call `list_workspaces` before `get_workspace`; workspace data can be large.
- Call `list_print_formats` before `render_print_format`. Rendering requires both read and print permission and returns HTML that may contain user or business data.
- Call `list_files` before `read_file`; read only files needed for the task. The default cap is 1 MiB and maximum is 5,000,000 bytes. Binary data is returned as base64; UTF-8 data as text.
- `search_translations` requires a language and non-empty query. Do not assume search is global across every language.

File upload is not currently an MCP tool. Do not imply that `list_files` or `read_file` uploads or changes files.

## Whitelisted methods

`call_whitelisted_method` is a generic bridge to methods explicitly whitelisted by Frappe and installed apps. It still requires a preview and confirmation, and the result is serialized JSON. The server cannot determine a method's full side effects or guarantee idempotency. Before calling:

1. Confirm the exact dotted method path and purpose from trusted app documentation or source.
2. Inspect the method's expected arguments and side effects.
3. Preview the exact method and argument object.
4. Ask for user confirmation if the method can change data or trigger external effects.
5. Confirm once and verify the result without repeating the method blindly.

The endpoint does not offer arbitrary Python or SQL execution. Do not attempt to bypass that boundary.

## Error handling

- **Permission error:** report the denied operation; do not search for a bypass. Ask an administrator to grant a minimal role/permission if needed.
- **Validation error:** use live schema/tool error details to correct the request, then preview again.
- **Stale timestamp:** re-read and obtain a new preview; reassess instead of replaying an old confirmation.
- **Unknown DocType/app/module:** discover the installed site and correct the name; do not assume an app is installed.
- **Tool result is truncated:** lower report/row/byte/character scope or request a targeted result.
- **Transport/authentication failure:** confirm client URL, Streamable HTTP support, token format, network reachability, and Frappe user API access. Never weaken Guest permissions to resolve it.
