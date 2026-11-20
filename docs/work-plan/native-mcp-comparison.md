# Native Frappe MCP: comparison and direction

**Written:** 2026-11-19, as background for stages 14–21. The native server changes with every Frappe release, so check the [Frappe MCP guide](https://docs.frappe.io/framework/guides-integration/mcp) again before you rely on a detail below.

## What Frappe ships

Frappe v16 and later include an MCP server. Each site runs its own, and it is on by default for new sites (System Settings > AI Agents > Enable MCP).

- **Transport and login:** Streamable HTTP at `/api/v2/mcp`, with OAuth 2 or an API key and secret. The tools run with the connected user's Frappe permissions.
- **13 tools:**
  - `search_doctypes`, `get_doctype_schema`;
  - `search_methods`, `get_method_schema`, `list_document_methods`;
  - `list_documents`, `get_document`, `count_documents`;
  - `create_document`, `update_document`, `delete_document`;
  - `call_method` (any whitelisted method, or an API Server Script);
  - `call_document_method` (for example `submit` or `cancel`).
- **Safety:** Frappe's permission checks, and only whitelisted methods can be called. There is no preview or confirmation step and no read-only mode in the MCP server; the site-wide toggle is the only switch.
- **`frappectl`:** a command-line client for REST API v2. It has profiles that are read-only by default, `doc` commands (including `submit`, `cancel`, and `amend`), read-only SQL through System Console, `file upload` and `download`, `report run`, `method call`, and `frappectl assistant`, which starts Claude Code, Codex, or another agent with a Frappe system prompt.
- **`frappe/mcp`** (the library that lets an app act as an MCP server) is experimental. Its README now points new projects to the built-in server.

## Where each one wins

| | Native (v16+) | mcp-frappe |
|---|---|---|
| Install | Nothing to install on v16 | `uvx mcp-frappe` (on PyPI since 0.3.0, 2026-11-20) |
| Frappe v15 | No | Yes |
| Record CRUD, document methods, whitelisted method calls | Complete | CRUD, submit, and cancel only; no method calls |
| Create and edit DocTypes, Custom Fields, Property Setters | No | Yes |
| Site and app sync (drift, diff, native export, rollback) | No | Yes |
| Workflows, Notifications, Report Builder reports, Workspaces | Only as raw records | Validated tools that refuse code fields |
| Preview and confirmation | No | Every write |
| Read-only mode | `frappectl` profiles only | `FRAPPE_MCP_READ_ONLY` |
| Tool-list size | 13 tools | 35 tools, about 60,000 characters (about 15,000 tokens) of schemas on 2026-11-19; at most 32,000 since stage 15 |
| Multi-user remote access | Yes (HTTP and OAuth) | No; one local stdio process per site |

## Direction

Don't compete on record CRUD or method calls: the native server is already on every v16 site. Be the MCP for the **development loop**, which needs what the native server can't reach: the site and the app checkout together, plus the Bench that connects them.

1. **Make adoption easy:** publish to PyPI, cut the tool-list cost, and document how to run both servers side by side (stage 15).
2. **Fix what real use found** (stage 14; see [field feedback](../field-feedback/README.md)).
3. **Add development features the native server lacks:**
   - read the app source and the site's state together: pending patches, recent errors, the app's whitelisted methods (stage 16);
   - call only the app methods the user has allowlisted (stage 17);
   - run the Bench commands that close the loop, behind an opt-in (stage 18);
   - full Customize Form saves, and renames (stage 19);
   - batch inserts in one transaction, and undo for changes the MCP made (stage 20).
4. **Show the difference:** ship a Claude Code plugin with a skill for developer workflows, and run an evaluation suite against the native server (stage 21).

Deprioritized, because the native server or `frappectl` covers them: the document lifecycle and collaboration backlog, print and PDF, and the Streamable HTTP transport.
