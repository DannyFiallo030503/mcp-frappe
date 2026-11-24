# Choosing between mcp-frappe, Frappe's built-in MCP, and frappectl

Frappe's current integration guide (checked 2026-11-23) documents two built-in options for **Frappe v16 and later**: a Streamable HTTP MCP server at `/api/v2/mcp` and `frappectl`, a CLI for REST API v2. The local site checked for this roadmap is Frappe 15.96, so neither is a replacement there. Check the [official Frappe MCP/CLI guide](https://docs.frappe.io/framework/guides-integration/mcp) before relying on version-specific details; upstream tools evolve independently from this package.

## What each server does

| | Frappe's built-in MCP (v16+) | `frappectl` CLI (v16+) | mcp-frappe (this server) |
|---|---|---|---|
| Runs | Inside the site, Streamable HTTP at `/api/v2/mcp` | Local command-line client over REST API v2 | Local stdio process over Frappe HTTP APIs |
| Login | OAuth 2 or API key/secret | OAuth, API key/secret, or environment | API key/secret or password |
| Frappe v15 | No | No | Yes |
| General records | List, read, count, create, update, delete | List, read, create, update, delete, submit, cancel, amend | List, read, count, create, update, delete, submit, cancel |
| Methods | Discover and call whitelisted methods and document methods | Raw API command; also includes a SQL query command | Only configured methods in `FRAPPE_MCP_METHODS` (none by default) |
| Files and reports | No dedicated file or report tool | File upload/download and report run | Reports and filters; no file upload/download |
| Developer metadata | Read schema | Inspect DocTypes; generic document CLI | Create, edit, delete, and promote DocTypes; Custom Fields, Property Setters, and role permissions |
| App files and Bench | No | No local app-file synchronization or fixed Bench bridge | Drift/diff, native customization exports and rollback, fixed opt-in Bench commands |
| Workflows, Notifications, Report Builder reports, Workspaces | Available through method/document tools | Raw API path | Validated tools that refuse code fields |
| Mutation safeguards | Frappe permissions; method HTTP verbs and checks | New profiles are read-only by default; writes require writable configuration | Preview/confirmation for writes, stale-write checks, optional read-only remote mode |

All three clients act as the logged-in Frappe user for their site operations. Frappe's permission checks apply to document and method calls; `frappectl` also offers a raw API command and a SQL query command, so configure its access accordingly.

## Which one to use for what

`get_site_info` and `mcp-frappe check` report a version-based recommendation. On v15, use mcp-frappe. On v16+, use Frappe's built-in MCP or `frappectl` for generic records and lifecycle actions when their authentication and safety model fits; use mcp-frappe for app-aware customization, drift, previews, bounded method allowlists, and fixed Bench operations. Frappe's guide says the built-in MCP and `frappectl` require v16 or later. `frappectl` is not installed in the current host, and neither v16 option was live-tested against the existing v15 site. If multiple clients are configured, avoid overlapping writable paths for the same task. The version recommendation is guidance only; mcp-frappe does not configure or invoke another MCP/CLI.

- **Generic data and lifecycle on v16+:** use the native server or `frappectl` if configured; each includes functionality that overlaps record operations here. On v15, use mcp-frappe. For an unknown version, check the site first.
- **Development:** this server. Changing DocTypes, customizing Frappe and ERPNext forms, keeping the site and the app's files in sync, and setting up Workflows, Notifications, reports, and Workspaces need the app checkout and a preview before each write, which the native server doesn't offer.
- **Reading schemas:** either. This server's `get_doctype_schema` returns a compact form by default and says which Workflow is active.

The server's own instructions route by the detected Frappe major: v15 uses this standalone server; v16+ uses the native MCP for data only when the native endpoint is enabled and connected; unknown versions stay on standalone until checked. This server reports guidance but cannot configure or call another MCP.

## Configure both in Claude Code

Give the two servers different names. The Frappe guide uses `frappe` for the native server, so this project's docs use `frappe-dev`.

1. Enable the native server on the site: as a System Manager, open **System Settings > AI Agents** and check **Enable MCP** (it is on by default for new sites). Then add it:

   ```bash
   claude mcp add --transport http frappe http://site1.localhost:8000/api/v2/mcp
   claude mcp login frappe
   ```

   `claude mcp login` signs in through the site's OAuth 2; set up OAuth on the site first, as the Frappe guide explains. Without OAuth, send an API key instead: `--header "Authorization: token <api key>:<api secret>"`.

2. Add this server with the record toolset switched off and Bench enabled, so record writes are not duplicated and Bench tools remain available:

   ```bash
   claude mcp add frappe-dev \
     -e FRAPPE_URL=http://127.0.0.1:8000 -e FRAPPE_SITE=site1.localhost \
     -e FRAPPE_API_KEY="$FRAPPE_API_KEY" -e FRAPPE_API_SECRET="$FRAPPE_API_SECRET" \
     -e FRAPPE_APPS_PATH=/absolute/path/to/frappe-bench/apps \
     -e FRAPPE_MCP_TOOLSETS=read,customize,developer,bench \
     -- uvx mcp-frappe
   ```

   With the [Claude Code plugin](installation-and-configuration.md#claude-code-plugin), set its **Toolsets** option to `read,customize,developer,bench` instead.

   `read` still includes `list_documents`, `get_document`, `count_documents`, and `search_records`, which developer workflows use to inspect results. `bench` exposes the opt-in Bench tools when `FRAPPE_BENCH_PATH` is configured. Leaving out `data` drops record writes, which go through native MCP on v16+.

3. Run `uvx mcp-frappe check` with the same environment. It prints how many tools and characters each toolset adds to the session, so you can see what switching a toolset off saves.

Both servers can use the same Frappe user. A developer account on a development site is the usual choice; use an Administrator key only there.

## Tool names

Both servers have tools named `list_documents`, `get_document`, `count_documents`, `create_document`, `update_document`, `delete_document`, and `get_doctype_schema`. Claude Code prefixes each tool with its server name (`mcp__frappe__list_documents`, `mcp__frappe-dev__list_documents`), so they don't collide, but the model still has to choose between them. With `FRAPPE_MCP_TOOLSETS=read,customize,developer` only read tools overlap; add `bench` when the local Bench tools are configured.

## Checked so far

The [developer-task evaluations](evaluations.md) compare this server, the native one, and both on the same tasks. In the first run (v16), the native server handled record-writing tasks and could not do tasks that need the Bench or app files. The `both` configuration ran headless in that run. `frappectl` was not included in that evaluation. The host used for this roadmap has no `frappectl` executable; no installation or site login was attempted. Consult the official guide and compatibility matrix before using a different client/version.
