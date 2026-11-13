# Product ordering verification

- **Date:** 2026-11-11
- **Task reference:** `product-ordering-verification`
- **Connection:** `cubazone-local` (sanitized alias for an existing local development site)
- **Purpose:** Compare ERP product-name ordering with the existing Local projection after case-insensitive sorting changed.
- **MCP tools:** `get_site_info`, `count_documents` (`Item`, excluding variants), and `list_documents` (`Item`, selected name fields, ordered by item name; first and full pages). Calls were read-only and passed.
- **Outcome:** The shared ERP/Local records had matching relative order. The live ERP data did not contain case-only duplicate names; the Central HTTP comparison was not completed because authentication was unavailable. Overall end-to-end proof was partial.
- **Scope:** No item codes, product names, hostnames, raw responses, or credentials are included.
