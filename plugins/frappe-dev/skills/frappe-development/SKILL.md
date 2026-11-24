---
name: frappe-development
description: Use when working on a Frappe or ERPNext app against a development site through the frappe-dev MCP server - creating or changing DocTypes, fields, child tables, or permissions; customizing Frappe or ERPNext forms with Custom Fields and Property Setters; checking drift between the site and the app's files; exporting customizations; running migrate; seeding test data; or setting up Workflows, Notifications, Report Builder reports, and Workspaces.
---

# Frappe development with the frappe-dev server

The `frappe-dev` MCP server works on one Frappe site through its REST API, as one Frappe user, and reads and writes the app checkout under the Bench's `apps` folder. Every write is two calls: a preview, then the same call with `confirm=true` and the token the preview returned (`expected_modified` or `expected_preview`). Show the user each preview before you confirm.

## Start here

1. `get_site_info`: the user, the Frappe version, the installed apps, and which apps have a checkout (standard DocTypes and exports need one). If the call fails on settings or login, ask the user to run `uvx mcp-frappe check` with the same settings; never ask them to paste a key or password into the chat.
2. Find the DocType with `list_doctypes(search=...)`, then read `get_doctype_schema` before you change anything. Use the fieldnames it returns.

If the site runs under Aspire or Docker and the connection fails, inspect the AppHost's host-published HTTP endpoint first (`aspire describe frappe` in Aspire). Set `FRAPPE_URL` to its Frappe desk URL, not the container's internal target port or the `socketio` endpoint. Use Aspire's default table output; JSON output can include environment values. A `GET /api/method/ping` response confirms the HTTP route only; `mcp-frappe check` still needs valid credentials. `localhost` and `.localhost` resolve where the MCP process runs, so a remote MCP service cannot use a workstation-local Aspire URL; run this stdio MCP locally or use a secured endpoint reachable from the remote host. Never change ports or enable Guest access to work around a failed login.

## Pick the tool by who owns the DocType

- **The user's own app, or a site-custom DocType:** `create_doctype`, `update_doctype` (one `operations` list per save), `rename` for fields and DocTypes, `delete_doctype`, `promote_custom_doctype` to move a site-custom prototype into the app.
- **Frappe or ERPNext DocTypes** (Customer, User, ToDo, Sales Invoice, ...): never edit them. Customize them with `customize_doctype` (several changes in one Customize Form save), or `create_custom_field` and `set_field_property` for one change. Then export with `export_customizations` (or `export_form_customizations`).
- **Workflows, Notifications, Report Builder reports, Workspaces:** the `upsert_*` tools. The record tools refuse these DocTypes and name the right tool.

## The loop

Change, then compare, export, sync, check:

1. **Change** with the tools above.
2. **Compare:** `diff_doctype(doctype)` for one DocType, `list_app_drift(app)` for the whole app. `in_sync` is the goal.
3. **Export** customizations of standard DocTypes to the app with `export_customizations`. Your own app's standard DocTypes need no export: Frappe writes their JSON on save in developer mode.
4. **Sync:** if `run_bench` is listed, `run_bench(command="migrate", app=...)` or `reload-doc` for one DocType that is `missing_on_site`. If it isn't listed, tell the user to run `bench migrate`; don't run Bench another way.
5. **Check:** `list_app_drift` again, `list_pending_patches(app)` for patches that haven't run, and `get_recent_errors(since=...)` for what failed on the server.

Details, verdicts, and what to tell the user at each step: [references/loop.md](references/loop.md).

## Test data and undo

- Seed records with `create_documents` (up to 100 rows, all or nothing), not one `create_document` per record.
- Take back a change this server made: `list_mcp_changes`, then `undo_mcp_change(entry_id)` with its preview. Record writes are journaled only when the user set `FRAPPE_MCP_JOURNAL_RECORDS=YES`.

More: [references/data-and-undo.md](references/data-and-undo.md).

## Edit the app code with the coding agent

Use the coding agent's workspace for app code; this MCP is not a Python or shell execution tool.

1. Read the target app's `AGENTS.md` and local development instructions. Confirm the app checkout from `get_site_info` and the tool's `FRAPPE_APPS_PATH` result. If the checkout is not available in the coding agent's workspace, stop before editing and explain that boundary.
2. Inspect `git status` before editing. Preserve existing user changes and work only in the requested app. For Frappe internals, verify behavior against the site's major version (v15 or v16) and the checked-out source; do not infer behavior from a different major version.
3. Edit controllers, hooks, tests, and other app files in the coding-agent workspace. Keep schema changes in the MCP workflow above so the site operation uses Frappe's native validation and the MCP's preview. Never use arbitrary Python, shell, or SQL as a workaround for a missing tool or permission denial.
4. Run the app's focused tests using its documented workflow. If `run_bench` is available, preview and show the exact `run-tests` command before confirming it; otherwise describe the command for the developer to run. Do not test against a production site.
5. If a change needs migration, inspect drift and pending patches first. Preview `migrate` or `reload-doc`; stop if the preview shows site-ahead or diverged metadata. Afterward, check `list_app_drift`, `list_pending_patches`, and `get_recent_errors` as applicable.
6. Review and show the app's Git diff and test result. Leave commit, push, and deployment to the user unless they explicitly request them.

## Next to Frappe's built-in MCP server

If Frappe's own MCP server is connected too (it has tools such as `call_method` and `search_doctypes`), use it for record data and whitelisted method calls, and `frappe-dev` for DocTypes, customizations, app files, and configuration records. Both have tools named `list_documents` and `get_document`: pick by server, not by tool name.

## Rules

- A permission error is final: say which access is missing; never work around it.
- A stale token means the site changed: preview again.
- The server refuses fields Frappe runs as code (Python conditions, Jinja templates, scripts). Tell the user to set those in Desk or in the app's files.
- If a tool you need isn't listed, the user's toolset or read-only setting leaves it out: tell them, don't find another route.
- After file changes, show the user the app's Git diff; they commit.
