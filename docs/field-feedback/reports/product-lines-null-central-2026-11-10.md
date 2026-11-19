# Investigation of null product lines in a downstream service

- **Date:** 2026-11-10
- **Task reference:** `product-lines-null-central-2026-11-10`
- **Connection:** `dev-local` (sanitized alias for an existing local development site)
- **Purpose:** Investigate why a downstream service received null product-line values after the line became derived from Item Group ancestry.
- **MCP tools:** `get_site_info` and `list_documents` (`Item`, `Item Group`); follow-up calls included `count_documents` (`Item`). All were read-only.
- **Outcome:** The sampled Items had a populated Item Group and an empty legacy product-line field. The MCP toolset did not expose the app's whitelisted method needed to compare its live response ([FR-2026-005](../feature-requests.md#fr-2026-005--call-an-apps-whitelisted-method-to-check-its-response)). Source inspection indicated the intended serialization derives the line from Item Group, so the cause was not confirmed by MCP. A later authenticated API check showed the empty selector symptom had stopped reproducing after related backend deployments; exclusive causality remained unverified.
- **Scope:** Detailed internal deployment identifiers, hostnames, tracker links, product/group names, and raw responses are omitted.
