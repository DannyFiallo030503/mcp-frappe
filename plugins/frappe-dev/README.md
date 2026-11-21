# frappe-dev plugin for Claude Code

Installs the [mcp-frappe](https://github.com/DannyFiallo030503/mcp-frappe) server as `frappe-dev` and the `frappe-development` skill, which tells Claude which tool fits which DocType and how to run the change, compare, export, migrate, and check loop.

## Install

```bash
claude plugin marketplace add DannyFiallo030503/mcp-frappe
claude plugin install frappe-dev@mcp-frappe
```

Or inside Claude Code: `/plugin marketplace add DannyFiallo030503/mcp-frappe`, then `/plugin install frappe-dev@mcp-frappe`.

Claude Code asks for the settings when the plugin is enabled. Change them later with `claude plugin configure frappe-dev@mcp-frappe`.

| Setting | Required | Server variable |
|---|---|---|
| Site URL | Yes | `FRAPPE_URL` |
| Site name | Multi-site Bench | `FRAPPE_SITE` |
| API key, API secret | Yes; kept in the system's secure credential store | `FRAPPE_API_KEY`, `FRAPPE_API_SECRET` |
| Bench apps folder | For standard DocTypes, drift checks, and exports | `FRAPPE_APPS_PATH` |
| Bench folder | Only to let the server run Bench commands | `FRAPPE_BENCH_PATH` |
| Toolsets | No | `FRAPPE_MCP_TOOLSETS` |

The server needs [uv](https://docs.astral.sh/uv/) (`uvx`) and Python 3.10 or later. Check the settings from a terminal with `uvx mcp-frappe check` and the same variables exported.

Password login, `FRAPPE_SITE_LABEL`, `FRAPPE_MCP_METHODS`, `FRAPPE_MCP_JOURNAL_RECORDS`, and the other settings aren't plugin options. For those, add the server with `claude mcp add` as the [installation guide](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/installation-and-configuration.md) shows, and keep the plugin for the skill (disable its server with `/mcp`).

Next to Frappe's built-in MCP server (named `frappe`), set Toolsets to `read,customize,developer,bench` so the record tools aren't listed twice. See [using both servers](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/native-frappe-mcp.md).
