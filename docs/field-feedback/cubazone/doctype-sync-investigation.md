# Doctype synchronization investigation

- **Date:** 2026-11-09
- **Task reference:** `doctype-sync-investigation`
- **Connection:** `cubazone-local` (sanitized alias for an existing local development site)
- **Purpose:** Check whether two app DocTypes were present on the site and review migration status before deciding how to resolve the drift.
- **MCP tools:** `list_doctypes`, `get_doctype_schema` (Patch Log), `list_documents` (Patch Log; selected fields only), `list_app_drift` (`erp_app`). All calls returned successfully.
- **Outcome:** The app defined DocTypes missing from the site, and pending app patches were present. No MCP error or site change occurred during this investigation. A later, separately authorized Bench migration synchronized the DocTypes and ran the pending patches.
- **Scope:** MCP reads only. No raw responses, site URL, business records, or credentials are included.
