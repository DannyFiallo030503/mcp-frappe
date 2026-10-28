# Installation and configuration

## Deployment model

Frappe MCP is a standalone MCP process launched by the AI host over `stdio`. It connects to the target Frappe site over HTTP. Do not run `bench get-app`, `bench install-app`, `bench migrate`, or restart the target site for this integration.

## Install

```bash
git clone https://github.com/DannyFiallo030503/mcp-frappe.git
cd mcp-frappe
uv sync
```

The executable is `mcp-frappe`; alternatively run `uv run python -m frappe_mcp.standalone` from the checkout.

## Environment

| Variable | Required | Description |
|---|---:|---|
| `FRAPPE_URL` | Yes | Site base URL, e.g. `http://127.0.0.1:32783` |
| `FRAPPE_SITE` | No | Site host name sent as the HTTP `Host` header for multi-site Bench routing |
| `FRAPPE_API_KEY` | Token auth | API key; use together with `FRAPPE_API_SECRET` |
| `FRAPPE_API_SECRET` | Token auth | API secret |
| `FRAPPE_USERNAME` | Password auth | Login username; defaults to `Administrator` |
| `FRAPPE_PASSWORD` | Password auth | Password; never stored in this repository |
| `FRAPPE_APPS_PATH` | DocType creation and export | Local Bench `apps/` directory visible to the MCP process |
| `FRAPPE_TIMEOUT` | No | HTTP timeout in seconds; a positive number, defaults to `30`. The server refuses to start with any other value |
| `FRAPPE_META_TTL` | No | Seconds to cache DocType metadata; zero or a positive number, defaults to `300`. `0` turns the cache off. The server refuses to start with any other value |
| `FRAPPE_ALLOW_INSECURE_HTTP` | No | Set to `YES` to allow plain HTTP to non-local hosts on a trusted development network; logs a warning at startup |

Use either both API token variables or a username/password. Token auth is preferred for remote sites. Password login uses Frappe's `/api/method/login`.

Both auth modes require HTTPS unless `FRAPPE_URL` points at a loopback host (`localhost`, `127.0.0.1`, `::1`) or a `.localhost` name. If the site is only reachable over plain HTTP by another name, for example a Docker service such as `http://frontend:8080`, set `FRAPPE_ALLOW_INSECURE_HTTP=YES`; do this only on a development network you trust. With password auth, a failed login is not cached, and an expired session is renewed automatically: the server logs in again and retries the rejected request once. A real permission denial is never retried. See [security.md](security.md#sessions). Every request runs as the configured Frappe user. This setup uses `Administrator`; Frappe grants that user all DocType permissions independently of Developer Mode. An Administrator password is highly privileged; use it only in a trusted development environment, and supply it through the MCP host's secret store/environment.

## MCP host configuration

Generic local server declaration:

```json
{
  "mcpServers": {
    "frappe": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/mcp-frappe", "run", "mcp-frappe"],
      "env": {
        "FRAPPE_URL": "http://127.0.0.1:32783",
        "FRAPPE_SITE": "site1.localhost",
        "FRAPPE_USERNAME": "Administrator",
        "FRAPPE_PASSWORD": "${FRAPPE_PASSWORD}"
      }
    }
  }
}
```

Environment expansion is host-specific. Set the secret outside this shared JSON file. If the MCP host runs in a container, ensure it can reach the Frappe host/port and only mount the app directory required for exports.

Without `FRAPPE_APPS_PATH`, discovery, reads, record CRUD, and the database-backed `create_custom_field` and `set_field_property` tools work (their `app` and `module` arguments are optional and checked only against the remote site), but standard DocType creation and file export cannot verify or write app source files. Set it to the actual Bench `apps` directory visible to the MCP process, not an arbitrary parent path. Each requested app/module must exist below it and the module must match the remote site's Module Def ownership. The target Frappe site must be running and reachable at `FRAPPE_URL`; provide the HTTP port and `FRAPPE_SITE` when host-based multi-site routing needs it.

Standard DocType creation (`is_custom: false`, the default) requires **Developer Mode on the target Frappe site**, an installed custom app, and an existing module in that app. Frappe's native DocType controller writes the standard JSON/controller/client/test files into that app. Site-custom DocTypes (`is_custom: true`) are database-backed and require an existing remote Module Def, but no local app checkout, `FRAPPE_APPS_PATH`, or Developer Mode; they do not produce app files, and Frappe does not allow them to be Virtual. Developer Mode is not required for ordinary record operations or database-backed Custom Fields/Property Setters. The minimum supported site setup is a reachable Frappe + ERPNext site; other apps are discovered at runtime.

## Verify

In the MCP host, connect and call `get_site_info`, then `list_doctypes`. For a raw login smoke check, keep the password out of shell history and output. An invalid password or missing role should fail through the standard Frappe API response; do not enable Guest access to work around it.

## Removal

Remove the `frappe` server entry from the MCP host configuration and delete the standalone checkout if no longer needed. No app uninstall, site migration, or Bench change is required.
