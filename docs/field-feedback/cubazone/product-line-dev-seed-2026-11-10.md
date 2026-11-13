# Development product line seed

- **Date:** 2026-11-10
- **Task reference:** `product-line-dev-seed-2026-11-10`
- **Connection:** `cubazone-local` (sanitized alias for an existing local development site)
- **Purpose:** Review Item Group and Item metadata before validating a development seed and its product-line projection.
- **MCP tools:** `get_site_info`, `get_doctype_schema` (`Item Group`, `Item`), `list_documents` (`Item Group`, `Item`, `Patch Log`), `count_documents` (`Item Group`, `Item`), `list_app_drift`, and `diff_doctype` (`Discount Rule`, `Item Informative Attribute`). Calls returned successfully.
- **Outcome:** The MCP calls were read-only. Seed creation and verification used the existing Bench/site outside MCP. The task later verified projection behavior, while authenticated remote-site verification remained pending.
- **Scope:** No site URL, product names/codes, raw responses, or credentials are included.
