# Using this server next to Frappe's built-in MCP

Frappe v16 and later include an MCP server on every site. It and this server do different jobs, and they work well together in one Claude Code session. The native server changes with every Frappe release, so check the [Frappe MCP guide](https://docs.frappe.io/framework/guides-integration/mcp) before relying on a detail below.

## What each server does

| | Frappe's built-in MCP (v16+) | mcp-frappe (this server) |
|---|---|---|
| Runs | Inside the site, Streamable HTTP at `/api/v2/mcp` | A local stdio process next to your Bench, over the site's REST API |
| Login | OAuth 2, or an API key and secret | An API key and secret, or a password |
| Frappe v15 | No | Yes |
| Records | List, read, count, create, update, delete | The same, plus submit and cancel; no other document methods |
| Method calls | Any whitelisted method, document methods (`call_method`, `call_document_method`) | None |
| DocTypes | Read the schema | Create, edit, delete, and promote DocTypes |
| Customizations | Only as raw records | Custom Fields, Property Setters, role permissions |
| App files | No | Diff and drift checks, native `custom/` exports, rollback |
| Workflows, Notifications, Report Builder reports, Workspaces | Only as raw records | Checked tools that refuse code fields |
| Preview and confirmation | No | Every write |
| Read-only mode | No (the site-wide switch turns it off) | `FRAPPE_MCP_READ_ONLY`; a remote site starts read-only |

Both servers act as the logged-in Frappe user, and Frappe's permission checks apply to every call.

## Which one to use for what

- **Record data and method calls:** the native server. It covers every document method and whitelisted method, and it is already on the site.
- **Development:** this server. Changing DocTypes, customizing Frappe and ERPNext forms, keeping the site and the app's files in sync, and setting up Workflows, Notifications, reports, and Workspaces need the app checkout and a preview before each write, which the native server doesn't offer.
- **Reading schemas:** either. This server's `get_doctype_schema` returns a compact form by default and says which Workflow is active.

The server's own instructions tell the model the same: when another Frappe MCP server is connected, use it for record data and method calls, and this one for DocTypes, customizations, and app files.

## Configure both in Claude Code

Give the two servers different names. The Frappe guide uses `frappe` for the native server, so this project's docs use `frappe-dev`.

1. Enable the native server on the site: as a System Manager, open **System Settings > AI Agents** and check **Enable MCP** (it is on by default for new sites). Then add it:

   ```bash
   claude mcp add --transport http frappe http://site1.localhost:8000/api/v2/mcp
   claude mcp login frappe
   ```

   `claude mcp login` signs in through the site's OAuth 2; set up OAuth on the site first, as the Frappe guide explains. Without OAuth, send an API key instead: `--header "Authorization: token <api key>:<api secret>"`.

2. Add this server with the record toolset switched off, so `list_documents`, `get_document`, and the other record tools aren't listed twice:

   ```bash
   claude mcp add frappe-dev \
     -e FRAPPE_URL=http://127.0.0.1:8000 -e FRAPPE_SITE=site1.localhost \
     -e FRAPPE_API_KEY="$FRAPPE_API_KEY" -e FRAPPE_API_SECRET="$FRAPPE_API_SECRET" \
     -e FRAPPE_APPS_PATH=/absolute/path/to/frappe-bench/apps \
     -e FRAPPE_MCP_TOOLSETS=read,customize,developer \
     -- uvx mcp-frappe
   ```

   The `read` toolset still includes `list_documents`, `get_document`, `count_documents`, and `search_records`, which the developer workflows use to check their result. Leaving out `data` drops only the record writes: create, update, delete, submit, and cancel go through the native server.

3. Run `uvx mcp-frappe check` with the same environment. It prints how many tools and characters each toolset adds to the session, so you can see what switching a toolset off saves.

Both servers can use the same Frappe user. A developer account on a development site is the usual choice; use an Administrator key only there.

## Tool names

Both servers have tools named `list_documents`, `get_document`, `count_documents`, `create_document`, `update_document`, `delete_document`, and `get_doctype_schema`. Claude Code prefixes each tool with its server name (`mcp__frappe__list_documents`, `mcp__frappe-dev__list_documents`), so they don't collide, but the model still has to choose between them. With `FRAPPE_MCP_TOOLSETS=read,customize,developer` only the read tools overlap, and the instruction above settles which server handles writes.

## Checked so far

The side-by-side setup has not yet been run on a site; see [not yet checked on a site](compatibility-and-validation.md#not-yet-checked-on-a-site). The configuration above follows the Frappe MCP guide as of 2026-11-19.
