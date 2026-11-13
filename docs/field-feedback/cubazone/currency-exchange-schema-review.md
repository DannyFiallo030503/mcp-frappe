# Currency Exchange schema review

- **Date:** 2026-11-12
- **Task reference:** `currency-exchange-schema-review`
- **Connection:** `cubazone-local` (sanitized alias for an existing local development site)
- **Purpose:** Inspect the standard Currency Exchange schema and compare it with the app customization before planning a draft feature.
- **MCP tools:** `get_site_info`, `get_doctype_schema` (`Currency Exchange`, compact detail), and `diff_doctype` (`Currency Exchange`, app `erp_app`). All calls passed.
- **Outcome:** The site and app schema were reported in sync. The feature remained gated on decisions about identity, metadata, and synchronization semantics.
- **Scope:** Read-only. No site URL, account identity, raw response, or credentials are included.
