# Frappe health repair

- **Date:** 2026-11-11
- **Task reference:** `frappe-health-repair`
- **Connection:** `cubazone-local` (sanitized alias for an existing local development site)
- **Purpose:** Diagnose MCP timeouts while the existing Frappe service was down and verify recovery.
- **MCP tools:** `get_site_info` (two timeouts, then success), `get_doctype_schema` (`Patch Log`, `Item`, `Error Log`), `list_documents` (`Patch Log`, `Custom Field`, `Error Log`), `count_documents` (`Error Log`), `list_app_drift`, and `diff_doctype` (`Item Informative Attribute`).
- **Outcome:** Read calls succeeded after the existing service recovered. No MCP defect was found. A separately authorized migration completed, and a service health probe passed. Some data classification follow-up remained outside the MCP task.
- **Scope:** No private URL, business identifiers, raw tool output, or credentials are included.
