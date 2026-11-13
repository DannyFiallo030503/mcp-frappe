# Product line derived from Item Group

- **Date:** 2026-11-09
- **Task reference:** `product-line-as-item-group`
- **Connection:** `cubazone-local` (sanitized alias for an existing local development site)
- **Purpose:** Inspect the current Item and Warehouse schema while changing the backend product-line contract.
- **MCP tools:** `get_site_info`, `get_doctype_schema` (`Item`, `Warehouse`), `list_documents` (`Custom Field`), `update_document` (preview and confirmation), `create_custom_field` (preview and confirmation), `create_doctype` (preview), `export_customizations` (previews), `list_doctypes`, and `list_app_drift`.
- **Outcome:** Reads and previews behaved as expected. Frappe rejected a Data-to-Link field conversion; this was an expected framework restriction. A required existing custom field was created. Export previews that would affect unrelated metadata were not confirmed, and the DocType creation preview refused to overwrite an app file. Later authorized Bench migration synchronized the missing DocTypes.
- **Scope:** This record summarizes MCP activity only. Internal tracker links, private site details, raw responses, and business identifiers are omitted.
