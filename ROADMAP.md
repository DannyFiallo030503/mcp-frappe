# Roadmap

Frappe MCP is a standalone stdio server. The roadmap separates implemented behavior from Frappe surfaces that still need explicit, permission-aware API wrappers.

See the [Frappe capability study](docs/frappe-capability-study.md) for the detailed framework/ERPNext inventory, coverage definitions, DocType visual-equivalence criteria, and test strategy.

## Implemented

1. **Standalone host integration** — launch by an MCP host over stdio; connect to any configured Frappe site over HTTP with API token or local password login.
2. **Runtime discovery** — installed apps/modules represented by Module Def, DocTypes, and schemas merged with Custom Fields and Property Setters.
3. **Document access** — bounded lists and document reads through Frappe's resource API.
4. **Core document mutations** — create/update/delete with previews and confirmation; update/delete require a current `modified` token.
5. **Reports** — run permitted Query/Script Reports with bounded MCP output.
6. **Customization** — create standard DocTypes, Custom Fields, and allowlisted Property Setters through Frappe APIs.
7. **Portable export and rollback** — DocType JSON, selected fixtures, managed `hooks.py` fixture registration, preview hashes, local backups, manifests, and guarded rollback.
8. **Validation** — unit coverage and real MCP stdio acceptance runs on an isolated local development site.

## Future work

- Workflow transitions and lifecycle actions.
- Permission-aware workspace, print, file, and translation tools.
- More role combinations, error-path coverage, and additional Frappe versions.
- Better report pagination where supported by the target Frappe report API.
- Explicit support for idempotent exports when a Bench reload interrupts the MCP response.
- Controlled custom app method adapters with per-method allowlists.

## Security boundaries

- Do not add arbitrary SQL, Python, or unrestricted method dispatch.
- Preserve the connected user's Frappe permissions.
- Preview and explicitly confirm mutations; bind existing-document changes to fresh state.
- Restrict filesystem writes to the configured app root and keep backups/rollback information.
- Treat Administrator credentials as full access and keep them outside source control.
