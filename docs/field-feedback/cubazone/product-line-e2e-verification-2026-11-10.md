# Product line end-to-end verification

- **Date:** 2026-11-10
- **Task reference:** `product-line-e2e-verification-2026-11-10`
- **Connection:** `cubazone-local` (sanitized alias for an existing local development site)
- **Purpose:** Compare ERP product-line metadata with the existing Local projection and assess the limits of the end-to-end flow.
- **MCP tools:** `get_site_info`, `list_documents` (`Item`, `Item Group`), and `count_documents` (`Item`). Calls were read-only and passed.
- **Outcome:** Reads showed the legacy product-line field could be empty under the derived-line model. API tests covered creation queueing, mapping, and filters, but did not prove the complete authenticated Central/Local-to-ERP flow. The overall result was partial.
- **Scope:** Record counts, QA identifiers, hostnames, and business values are omitted.
