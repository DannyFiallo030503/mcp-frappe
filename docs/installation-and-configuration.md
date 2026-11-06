# Installation and configuration

## Deployment model

Frappe MCP is a standalone MCP process launched by the AI host over `stdio`. It connects to the target Frappe site over HTTP. Do not run `bench get-app`, `bench install-app`, `bench migrate`, or restart the target site for this integration.

## Install

Run the published package with [`uvx`](https://docs.astral.sh/uv/guides/tools/); it installs `mcp-frappe` into a cached, isolated environment on first use:

```bash
uvx mcp-frappe --version
```

Until the first PyPI release is out, use `uvx --from git+https://github.com/DannyFiallo030503/mcp-frappe mcp-frappe`. `pipx run mcp-frappe` and `pip install mcp-frappe` also work. Pin a version with `uvx mcp-frappe@0.3.0`; [CHANGELOG.md](../CHANGELOG.md) lists what each release changes, breaking changes first.

For development, use a checkout:

```bash
git clone https://github.com/DannyFiallo030503/mcp-frappe.git
cd mcp-frappe
uv sync --extra dev
```

There, run `uv run mcp-frappe` or `uv run python -m frappe_mcp.standalone`.

## Command line

| Command | What it does |
|---|---|
| `mcp-frappe` | Serves MCP over stdio. This is what an MCP host launches. Nothing but the protocol is written to stdout; logs and startup errors go to stderr. |
| `mcp-frappe check` | Reads the settings below, logs in, and prints the user, the app versions, the selected toolsets, and whether `FRAPPE_APPS_PATH` is usable. Exits 0 when ready, 1 otherwise. It never prints an API key, secret, or password, and it prints `FRAPPE_URL` without any credentials in it. |
| `mcp-frappe --version` | Prints the version. |

Run `check` with the same environment the host will use before adding the server to a host. It catches bad settings (an unknown toolset, an invalid `FRAPPE_TIMEOUT`), unreachable sites, rejected credentials, a Guest session, and an `apps` checkout that is missing or read-only. It reports developer mode as unknown, because Frappe has no API for it.

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
| `FRAPPE_MCP_TOOLSETS` | No | Comma-separated toolsets to expose: `read`, `data`, `customize`, `developer`. Defaults to all four. The server refuses to start with an unknown name. See [tool-reference.md](tool-reference.md#toolsets-read-only-mode-and-annotations) |
| `FRAPPE_MCP_READ_ONLY` | No | `YES` exposes only the `read` toolset, for checking a site that must not change; `NO` or unset changes nothing. The server refuses to start with any other value, or with `YES` and a non-`read` toolset in `FRAPPE_MCP_TOOLSETS` |

Use either both API token variables or a username/password. Token auth is preferred for remote sites. Password login uses Frappe's `/api/method/login`.

Both auth modes require HTTPS unless `FRAPPE_URL` points at a loopback host (`localhost`, `127.0.0.1`, `::1`) or a `.localhost` name. If the site is only reachable over plain HTTP by another name, for example a Docker service such as `http://frontend:8080`, set `FRAPPE_ALLOW_INSECURE_HTTP=YES`; do this only on a development network you trust. With password auth, a failed login is not cached, and an expired session is renewed automatically: the server logs in again and retries the rejected request once. A real permission denial is never retried. See [security.md](security.md#sessions). Every request runs as the configured Frappe user. This setup uses `Administrator`; Frappe grants that user all DocType permissions independently of Developer Mode. An Administrator password is highly privileged; use it only in a trusted development environment, and supply it through the MCP host's secret store/environment.

## MCP host configuration

The host launches `uvx mcp-frappe` with the settings in its environment. The [README](../README.md#configure) has ready snippets for Claude Code (`claude mcp add frappe -e FRAPPE_URL=... -- uvx mcp-frappe`) and Claude Desktop. A generic local server declaration:

```json
{
  "mcpServers": {
    "frappe": {
      "command": "uvx",
      "args": ["mcp-frappe"],
      "env": {
        "FRAPPE_URL": "http://127.0.0.1:8000",
        "FRAPPE_SITE": "site1.localhost",
        "FRAPPE_USERNAME": "Administrator",
        "FRAPPE_PASSWORD": "${FRAPPE_PASSWORD}"
      }
    }
  }
}
```

From a development checkout, use `"command": "uv"` with `"args": ["--directory", "/absolute/path/to/mcp-frappe", "run", "mcp-frappe"]`.

Environment expansion is host-specific. Set the secret outside this shared JSON file. If the MCP host runs in a container, ensure it can reach the Frappe host/port and only mount the app directory required for exports.

Without `FRAPPE_APPS_PATH`, discovery, reads, record CRUD, and the database-backed `create_custom_field` and `set_field_property` tools work (their `app` and `module` arguments are optional and checked only against the remote site), but standard DocType creation and file export cannot verify or write app source files. Set it to the actual Bench `apps` directory visible to the MCP process, not an arbitrary parent path. Each requested app/module must exist below it and the module must match the remote site's Module Def ownership. The target Frappe site must be running and reachable at `FRAPPE_URL`; provide the HTTP port and `FRAPPE_SITE` when host-based multi-site routing needs it. `export_form_customizations` additionally requires System Manager and calls Frappe's native exporter; the local app path must point to the same app tree when you expect the MCP process to report changed files.

Standard DocType creation (`is_custom: false`, the default) requires **Developer Mode on the target Frappe site**, an installed custom app, and an existing module in that app. Frappe's native DocType controller writes the standard JSON/controller/client/test files into that app. Site-custom DocTypes (`is_custom: true`) are database-backed and require an existing remote Module Def, but no local app checkout, `FRAPPE_APPS_PATH`, or Developer Mode; they do not produce app files, and Frappe does not allow them to be Virtual. Developer Mode is not required for ordinary record operations or database-backed Custom Fields/Property Setters. The minimum supported site setup is a reachable Frappe + ERPNext site; other apps are discovered at runtime.

## Verify

Run `mcp-frappe check` (above) with the host's environment first. Then, in the MCP host, connect and call `get_site_info`, then `list_doctypes`. An invalid password or missing role fails through the standard Frappe API response; do not enable Guest access to work around it.

## Removal

Remove the `frappe` server entry from the MCP host configuration (`claude mcp remove frappe` in Claude Code). `uv cache clean mcp-frappe` drops the cached `uvx` environment; delete the checkout if you used one. No app uninstall, site migration, or Bench change is required.
