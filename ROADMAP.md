# Roadmap

Frappe MCP is intended to work independently of ERPNext and to discover installed apps at runtime. The roadmap describes current scope and likely future work; it is not a promise of a release date.

## Implemented

1. **Runtime discovery and reads** — installed apps, modules, visible DocTypes, readable schema and fields, filtered document lists, individual documents, and child rows.
2. **Document writes** — create, update, delete, submit, cancel, and explicitly whitelisted method calls. Mutations use previews and confirmations; existing-document changes use `expected_modified`; explicit-name creates support safe identical replays.
3. **Schema customization** — standard DocType creation, Custom Fields, Property Setters, Client Scripts, and role permission updates through native Frappe controllers.
4. **Portable customization export** — selected DocType JSON and Custom Field, Property Setter, Client Script, and Custom DocPerm fixtures; diff preview, `preview_id` confirmation, file backups, and rollback manifests.
5. **Extended Frappe surfaces** — reports, workflows, workspaces, print formats, files, and translations.

## Future considerations

- Publish a broader compatibility matrix covering additional Frappe release lines and common app combinations.
- Add more real HTTP acceptance scenarios for role permutations, unusual DocType metadata, and export/rollback edge cases.
- Evaluate paginated discovery/read tools for large sites while preserving explicit bounded results.
- Consider controlled file upload only if a safe, permission-aware contract is defined.
- Improve user-facing installation and client configuration examples as supported MCP clients are verified.

## Security boundaries

- Do not add arbitrary SQL or Python execution.
- Preserve the connected Frappe user's permissions and field-level access.
- Require preview and explicit confirmation for changes.
- Keep file writes within installed app trees and expose diffs/backups/rollback.
- Treat whitelisted methods as potentially side-effecting and non-idempotent.
