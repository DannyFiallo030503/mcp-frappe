# Architecture

## Request flow

```mermaid
flowchart LR
  Client[AI or MCP client] -->|Streamable HTTP + Frappe token| Endpoint[/api/method/mcp_frappe.mcp.handle_mcp]
  Endpoint --> Registry[MCP tool registry]
  Registry --> Tools[Discovery, document, customization, advanced tools]
  Tools --> Auth[Frappe session and permission APIs]
  Auth --> Site[(Frappe site database and files)]
  Tools --> AppFiles[Installed app module files]
```

The `mcp_frappe` app provides the Frappe endpoint and domain tools. `frappe_mcp/` contains the vendored MCP transport implementation based on upstream `frappe/mcp`. The transport is integrated into the app package so it does not introduce incompatible Werkzeug or Pydantic pins into the Bench environment. See [UPSTREAM.md](../UPSTREAM.md) and [third-party notices](../THIRD_PARTY_LICENSES-frappe-mcp.txt).

## Package layout

| Path | Responsibility |
|---|---|
| `mcp_frappe/mcp.py` | MCP server instance, endpoint registration, request-time module map refresh, tool loading |
| `mcp_frappe/tools/discovery.py` | Installed apps, DocTypes, schemas, document reads |
| `mcp_frappe/tools/documents.py` | Document writes, lifecycle operations, whitelisted method bridge |
| `mcp_frappe/tools/customizations.py` | DocType and Customize Form operations, role permissions, app export and rollback |
| `mcp_frappe/tools/advanced.py` | Reports, workflows, workspaces, print formats, files, translations |
| `frappe_mcp/server/` | Vendored MCP HTTP protocol, tool schemas/handlers, prompts support |
| `e2e/` | Real HTTP acceptance runner, evidence, and validation report |

## Runtime discovery

The app does not maintain fixed lists for ERPNext, HRMS, or custom app DocTypes. Tools discover installed apps and DocType metadata from the current site. The available tools and data are therefore site- and user-specific.

At each endpoint request, `handle_mcp` reconstructs `frappe.local.module_app` from installed apps' Module Def records. This avoids stale module ownership mappings when app/module metadata changes after process startup and supports native DocType export controllers.

## Permission model

The current Frappe session user is used throughout. Read operations use Frappe permission APIs and filter readable fields. Write operations check action permissions and validate fields before invoking native document controllers. See [security](security.md) for security boundaries and [tool reference](tool-reference.md) for per-tool behavior.

## Export layout

`export_customizations` writes selected standard DocType JSON into the owning app/module and exports selected Custom Field, Property Setter, Client Script, and Custom DocPerm records as fixtures. It maintains only a clearly delimited MCP-managed section in `hooks.py`, preserving content outside that section. Existing file contents are backed up, output is written atomically, and a manifest records paths/backups under the app's MCP backup directory.

The preview includes a diff and content-derived `preview_id`. Confirmation is rejected unless the caller submits that current ID. `rollback_export` previews the inverse file operations and then restores the backup or removes files created by the export. This mechanism changes app files; it does not undo database records, run migrations, or deploy changes to other sites.

## Adding a tool

1. Add a focused function in the appropriate `mcp_frappe/tools/` module and decorate it with `@mcp.tool()`.
2. Add type annotations and a useful docstring; the transport uses these to expose the MCP input schema and description.
3. Apply native Frappe permission and validation checks. Keep results JSON serializable and bounded.
4. For writes, add preview/confirmation semantics and stale-preview handling where applicable. Do not add arbitrary Python/SQL execution.
5. Add focused automated coverage in `mcp_frappe/tests/` (and transport tests under `frappe_mcp/server/` when relevant).
6. Update the [tool reference](tool-reference.md), [AI guide](ai-agent-guide.md), and validation documentation when behavior changes.
7. Exercise the endpoint over real authenticated HTTP on a disposable test site before release.

## Extension boundaries

Frappe app-specific APIs should be discovered at runtime, not hardcoded by ERPNext/HRMS versions. Avoid coupling this app to the CubaZone platform's `erp_app`, backend, or frontend. A generic Frappe MCP must be installable independently of those projects.
