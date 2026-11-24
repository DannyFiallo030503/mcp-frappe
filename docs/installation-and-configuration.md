# Installation and configuration

## Deployment model

Frappe MCP is a standalone MCP process launched by the AI host over `stdio`. It connects to the target Frappe site over HTTP. Do not run `bench get-app`, `bench install-app`, `bench migrate`, or restart the target site for this integration. The server itself runs Bench commands only when you opt in with `FRAPPE_BENCH_PATH` ([Bench commands](#bench-commands)).

### One site per MCP process

Each process has one fixed target, selected by its startup environment: `FRAPPE_URL`, optional `FRAPPE_SITE` (the Host header for multi-site Bench routing), and one configured user's credentials. The tools do not accept a site selector, and the process does not switch targets between calls. `FRAPPE_SITE` selects which site that process addresses on a multi-site Bench; it does not make one process a multi-site client.

To work with several sites at once, configure a separate MCP server/process for each site in the host, with a distinct server name and the correct URL, optional `FRAPPE_SITE`, credentials, and—when file exports are needed—the matching Bench's `FRAPPE_APPS_PATH`. Before acting, call `get_site_info` on the explicitly selected connection and verify its user and app/version context. Keep results and writes attributed to that connection. Do not reuse one site's app checkout for another site's exports.

Give each process a `FRAPPE_SITE_LABEL`, such as `erp-dev` or `staging`. `get_site_info` returns it as `site_label`, and `mcp-frappe check` prints it, so an agent can check which connection it is using before it acts. The label is informational only: the server never uses it to choose a site, and a tool can't switch to another one. Without a label, `site_label` is `null` and the host's MCP server name is the only connection label; if two connections are easy to confuse, stop and resolve the mapping before any writes.

One process serves exactly one site, and that won't change: the login session, the metadata cache, the apps path, and the preview tokens all belong to that one site, and hosts already keep sites apart by running one server per site.

### Remote sites

Remote Frappe sites are reachable over HTTPS with API-key/secret or password authentication. A **remote target**, one whose `FRAPPE_URL` host isn't loopback (`localhost`, `127.0.0.1`, `::1`) or a `.localhost` name, starts **read-only**: only the `read` toolset is listed, and the startup log and `mcp-frappe check` say so. To write to a remote site, set `FRAPPE_MCP_READ_ONLY=NO`; the server then logs a warning at startup, and `check` prints one. Naming a writable toolset in `FRAPPE_MCP_TOOLSETS` without `FRAPPE_MCP_READ_ONLY=NO` stops the server at startup instead of dropping the toolset silently. Live validation against a remote site is still pending; see FR-2026-003 in [field feature requests](field-feedback/feature-requests.md).

## Install

Run the published package with [`uvx`](https://docs.astral.sh/uv/guides/tools/); it installs `mcp-frappe` into a cached, isolated environment on first use:

```bash
uvx mcp-frappe --version
```

`pipx run mcp-frappe` and `pip install mcp-frappe` also work. Pin a version with `uvx mcp-frappe@0.3.0`; [CHANGELOG.md](../CHANGELOG.md) lists what each release changes, breaking changes first.

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
| `mcp-frappe check` | Reads the settings below, logs in, and prints the user, the app versions, the selected toolsets, the size of each toolset's tool list (tools and characters, which the host sends to the model in every session), and whether `FRAPPE_APPS_PATH` is usable. With `FRAPPE_MCP_METHODS` set, it checks that file too and prints the number of entries and any not checked against the app source. It also prints `FRAPPE_SITE_LABEL` when set, and says when a remote target is read-only or has writes enabled. Exits 0 when ready, 1 otherwise. It never prints an API key, secret, or password, and it prints `FRAPPE_URL` without any credentials in it. |
| `mcp-frappe --version` | Prints the version. |

Run `check` with the same environment the host will use before adding the server to a host. It catches bad settings (an unknown toolset, an invalid `FRAPPE_TIMEOUT` or `FRAPPE_SITE_LABEL`), unreachable sites, rejected credentials, a Guest session, and an `apps` checkout that is missing or read-only. It reports developer mode as unknown, because Frappe has no API for it.

## Environment

| Variable | Required | Description |
|---|---:|---|
| `FRAPPE_URL` | Yes | Site base URL, e.g. `http://127.0.0.1:32783` |
| `FRAPPE_SITE` | No | Site host name sent as the HTTP `Host` header for multi-site Bench routing |
| `FRAPPE_SITE_LABEL` | No | A name for this connection, returned by `get_site_info` as `site_label` and printed by `check`; 1–64 letters, digits, spaces, `_`, `.`, or `-`, starting with a letter or digit. Never used for routing. The server refuses to start with any other value |
| `FRAPPE_API_KEY` | Token auth | API key; use together with `FRAPPE_API_SECRET` |
| `FRAPPE_API_SECRET` | Token auth | API secret |
| `FRAPPE_USERNAME` | Password auth | Login username; defaults to `Administrator` |
| `FRAPPE_PASSWORD` | Password auth | Password; never stored in this repository |
| `FRAPPE_APPS_PATH` | DocType creation and export | Local Bench `apps/` directory visible to the MCP process |
| `FRAPPE_TIMEOUT` | No | HTTP timeout in seconds; a positive number, defaults to `30`. Connecting gives up after 5 seconds (or `FRAPPE_TIMEOUT`, if smaller), so a site that is down fails fast. The server refuses to start with any other value |
| `FRAPPE_META_TTL` | No | Seconds to cache DocType metadata; zero or a positive number, defaults to `300`. `0` turns the cache off. The server refuses to start with any other value |
| `FRAPPE_ALLOW_INSECURE_HTTP` | No | Set to `YES` to allow plain HTTP to non-local hosts on a trusted development network; logs a warning at startup |
| `FRAPPE_MCP_TOOLSETS` | No | Comma-separated toolsets to expose: `read`, `data`, `customize`, `developer`, `bench`. Defaults to all five (`bench` has a tool only with `FRAPPE_BENCH_PATH`). The server refuses to start with an unknown name. See [tool-reference.md](tool-reference.md#toolsets-read-only-mode-and-annotations) |
| `FRAPPE_MCP_METHODS` | No | Path to a JSON file listing the only app methods the server may call (`mapper`, `read`, or `write` entries). Unset: the method tools aren't registered. The server refuses to start when the file has a problem, naming its line; see [allowlisted method calls](tool-reference.md#allowlisted-method-calls) and the [ERPNext example](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/examples/erpnext-methods.json) |
| `FRAPPE_BENCH_PATH` | No | The Bench folder (the one holding `apps/` and `sites/`, or the folder a container command runs from). Set: `run_bench` is registered, for a local `FRAPPE_URL` only. Unset: no Bench command can run. See [Bench commands](#bench-commands) |
| `FRAPPE_BENCH_SITE` | With `FRAPPE_BENCH_PATH` | The site name passed to `bench --site`; defaults to `FRAPPE_SITE` |
| `FRAPPE_BENCH_COMMAND` | No | A JSON list that runs `bench`, for a containerized Bench, such as `["docker", "compose", "exec", "-T", "backend", "bench"]`. Defaults to `["bench"]` from `PATH` |
| `FRAPPE_BENCH_TIMEOUT` | No | Seconds before a Bench command is stopped, 10–7200; defaults to `900` |
| `FRAPPE_MCP_JOURNAL_RECORDS` | No | `YES` also journals writes of business records (`create_document`, `create_documents`, `update_document`, deletes, submit, cancel, method calls) so that `undo_mcp_change` can reverse creations and updates. Off by default, because the journal then holds record names and old values. Schema and customization changes are always journaled. The journal is `FRAPPE_APPS_PATH/.mcp-frappe-journal/<site>.json`, or memory without `FRAPPE_APPS_PATH`; see [change journal](tool-reference.md#change-journal-and-undo) |
| `FRAPPE_MCP_READ_ONLY` | No | `YES` exposes only the `read` toolset, for checking a site that must not change. Unset: read-only for a remote target (see [Remote sites](#remote-sites)), no change for a local one. `NO` enables the write toolsets on a remote target too. The server refuses to start with any other value, or when read-only and `FRAPPE_MCP_TOOLSETS` names a non-`read` toolset |

### Aspire and Docker port mapping

When Codex, Claude Code, or `uvx mcp-frappe` runs on the host, `FRAPPE_URL` must use the host-published **HTTP** endpoint. In Aspire, run `aspire describe frappe` from the AppHost directory and copy the displayed Frappe desk URL. The default table output avoids printing the resource environment, which can contain credentials. Do not use the container target port unless the MCP itself runs inside the same container network.

Frappe's common container ports are HTTP `8000` and Socket.IO `9000`, but the host-published ports can be different. For example, use the host and port shown for Aspire’s `http` endpoint:

```bash
export FRAPPE_URL="<host-published-http-url>"
```

Do not point `FRAPPE_URL` at the `socketio` URL: the MCP uses the REST API over HTTP. If the HTTP endpoint URL uses `localhost` instead of the site hostname, set `FRAPPE_SITE` to the site's `.localhost` name so Frappe selects the right site. When using a Docker network from inside a container, use the Frappe service name and its internal HTTP port, and set `FRAPPE_SITE` when the Bench needs host-based site routing.

To separate network/port problems from authentication problems, the read-only `GET /api/method/ping` endpoint should answer at the selected HTTP URL. A successful ping only confirms that HTTP reaches Frappe; `mcp-frappe check` must also log in with the configured user's credentials. If `check` cannot connect at a common container port such as `8000` or `9000`, it prints a hint to inspect the current Aspire/Docker mapping; it does not assume a host-published port has a fixed role. It never scans other ports or changes the connection automatically.

The process location matters too: `.localhost`, `localhost`, and `127.0.0.1` resolve from the machine or network namespace running the MCP process. A local stdio MCP launched by Codex or Claude on the same workstation can use the local Aspire URL. A remote MCP service cannot reach the workstation's `.localhost` endpoint; use a network path reachable from that service with HTTPS and its normal credential safeguards, or run this MCP locally. Do not expose the development site's plain-HTTP endpoint publicly.

The environment file used to launch a Frappe container is not automatically the MCP process environment. Map only the needed site URL, site name, and credential to `FRAPPE_URL`, `FRAPPE_SITE`, and `FRAPPE_PASSWORD` in a local launcher or secret manager. Avoid exporting the entire container environment to the MCP host, and never place the password in a committed config or an MCP server command line. A local launcher can derive the URL from the current host-published HTTP endpoint.

Published host ports vary by AppHost configuration. Always read the current values from Aspire instead of copying a port from an example. With the site hostname in `FRAPPE_URL`, `FRAPPE_SITE` is unnecessary.

Use either both API token variables or a username/password. Token auth is preferred for remote sites. Password login uses Frappe's `/api/method/login`.

Both auth modes require HTTPS unless `FRAPPE_URL` points at a loopback host (`localhost`, `127.0.0.1`, `::1`) or a `.localhost` name. If the site is only reachable over plain HTTP by another name, for example a Docker service such as `http://frontend:8080`, set `FRAPPE_ALLOW_INSECURE_HTTP=YES`; do this only on a development network you trust. With password auth, a failed login is not cached, and an expired session is renewed automatically: the server logs in again and retries the rejected request once. A real permission denial is never retried. See [security.md](security.md#sessions). Every request runs as the configured Frappe user. This setup uses `Administrator`; Frappe grants that user all DocType permissions independently of Developer Mode. An Administrator password is highly privileged; use it only in a trusted development environment, and supply it through the MCP host's secret store/environment.

### Bench commands

`run_bench` runs `migrate`, `reload-doc`, `export-fixtures`, `clear-cache`, or `run-tests` on the site after a preview and confirmation ([tool reference](tool-reference.md#bench-commands)). It runs the app's own code, so it is off until you set `FRAPPE_BENCH_PATH`, and the server refuses to start with it when `FRAPPE_URL` isn't a loopback or `.localhost` address. At startup the server checks that the folder exists, that the site name is valid, that `sites/<site>/site_config.json` exists when the folder has a `sites/` directory, that the command is found on `PATH`, and the timeout; a bad value stops it with the reason. `mcp-frappe check` prints a `bench` row with the site, the command, the timeout, and whether the site allows tests, never the folder.

A Bench on this machine:

```bash
FRAPPE_URL=http://dev.localhost:8000
FRAPPE_SITE=dev.localhost
FRAPPE_BENCH_PATH=~/frappe-bench
```

A Bench in Docker Compose (the folder is where `docker compose` finds the project; with no `sites/` folder there, the site isn't checked locally and `run-tests` relies on the container's `allow_tests`):

```bash
FRAPPE_URL=http://dev.localhost:8080
FRAPPE_SITE=dev.localhost
FRAPPE_BENCH_PATH=~/projects/frappe-docker
FRAPPE_BENCH_COMMAND='["docker", "compose", "exec", "-T", "backend", "bench"]'
```

`-T` keeps Docker from allocating a terminal, which the server can't give it. `run-tests` needs `bench --site <site> set-config allow_tests true` once. The command gets a minimal environment (no `FRAPPE_*` credentials and no `CI`); set `DOCKER_HOST` or `DOCKER_CONTEXT` in the server's environment if your Docker setup needs them.

## Claude Code plugin

The repository is also a Claude Code plugin marketplace. Its `frappe-dev` plugin runs `uvx mcp-frappe` as the `frappe-dev` server and adds the `frappe-development` skill:

```bash
claude plugin marketplace add DannyFiallo030503/mcp-frappe
claude plugin install frappe-dev@mcp-frappe
```

Inside Claude Code, the same is `/plugin marketplace add DannyFiallo030503/mcp-frappe` and `/plugin install frappe-dev@mcp-frappe`. Claude Code asks for the plugin's options when it is enabled; `claude plugin configure frappe-dev@mcp-frappe` shows them and which are unset.

| Option | Variable | |
|---|---|---|
| Site URL | `FRAPPE_URL` | Required |
| Site name | `FRAPPE_SITE` | Multi-site Bench |
| API key, API secret | `FRAPPE_API_KEY`, `FRAPPE_API_SECRET` | Required; stored in the system's secure credential store, not in `settings.json` |
| Bench apps folder | `FRAPPE_APPS_PATH` | Standard DocTypes, drift checks, exports |
| Bench folder | `FRAPPE_BENCH_PATH` | Only to turn on [Bench commands](#bench-commands) |
| Toolsets | `FRAPPE_MCP_TOOLSETS` | Empty for all |

An option left empty is passed as an empty value, which the server treats as unset. The plugin supports API-key login only; for a password login or any other variable from the [Environment](#environment) table, add the server with `claude mcp add` instead and disable the plugin's server in `/mcp`, keeping the skill. Remove the plugin with `claude plugin uninstall frappe-dev@mcp-frappe`. The plugin's files are in [plugins/frappe-dev](../plugins/frappe-dev/README.md).

## Codex

Codex installs the repository's `frappe-development` skill through its plugin marketplace and runs the MCP server as a local stdio process. Install the marketplace from the repository root:

```bash
codex plugin marketplace add DannyFiallo030503/mcp-frappe --sparse .agents/plugins --sparse plugins/frappe-dev
```

Then open Codex, run `/plugins`, find **Frappe development**, and install it. Start a new Codex conversation after installation.

From the CLI, the same install is `codex plugin add frappe-dev@mcp-frappe`.

Register the server in `~/.codex/config.toml`:

```toml
[mcp_servers.frappe-dev]
command = "uvx"
args = ["mcp-frappe"]
env_vars = ["FRAPPE_URL", "FRAPPE_SITE", "FRAPPE_SITE_LABEL", "FRAPPE_API_KEY", "FRAPPE_API_SECRET", "FRAPPE_USERNAME", "FRAPPE_PASSWORD", "FRAPPE_APPS_PATH", "FRAPPE_TIMEOUT", "FRAPPE_META_TTL", "FRAPPE_ALLOW_INSECURE_HTTP", "FRAPPE_MCP_TOOLSETS", "FRAPPE_MCP_METHODS", "FRAPPE_BENCH_PATH", "FRAPPE_BENCH_SITE", "FRAPPE_BENCH_COMMAND", "FRAPPE_BENCH_TIMEOUT", "FRAPPE_MCP_JOURNAL_RECORDS", "FRAPPE_MCP_READ_ONLY"]
```

Provide those values in the environment used to launch Codex. Codex forwards the listed variables from its own environment to this MCP process; use your operating system's secret manager or a local shell environment manager to supply them. Don't put API keys or secrets in a shared project file or commit them. Set `FRAPPE_URL`, `FRAPPE_API_KEY`, and `FRAPPE_API_SECRET` at minimum. `FRAPPE_SITE` is needed for host-based multi-site routing; `FRAPPE_APPS_PATH` enables standard DocType file changes and exports; `FRAPPE_BENCH_PATH` opts in to the fixed Bench commands. `FRAPPE_MCP_TOOLSETS` can narrow what Codex sees. If Codex was launched from a desktop shortcut and doesn't inherit your shell environment, configure the variables in the environment from which that app launches or use Claude Code's plugin, which prompts for the site and stores API credentials in the system credential store.

Restart Codex after changing `config.toml` or the environment. Check the server is registered with `codex mcp list`, then run `uvx mcp-frappe check` from a terminal where the same Frappe settings are available. The check logs in, reports Frappe and app versions, and describes the version-specific MCP guidance. Once connected in Codex, call `get_site_info` and confirm the site, user, Frappe version, and installed apps before making changes. The repository supports Frappe 15 as its primary target and Frappe 16 as a supported target; tool behavior is selected from the detected site version where the APIs differ.

To remove the server, run `codex mcp remove frappe-dev`. To remove the marketplace, run `codex plugin marketplace remove mcp-frappe`.

## MCP host configuration

The host launches `uvx mcp-frappe` with the settings in its environment. The [README](../README.md#configure) has ready snippets for Claude Code (`claude mcp add frappe-dev -e FRAPPE_URL=... -- uvx mcp-frappe`), Codex (above), and Claude Desktop. The suggested server name is `frappe-dev`, because Frappe's built-in MCP server suggests `frappe`; to run both, see [using both servers](native-frappe-mcp.md). A generic local server declaration:

```json
{
  "mcpServers": {
    "frappe-dev": {
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

Remove the `frappe-dev` server entry (or whatever you named it) from the MCP host configuration (`claude mcp remove frappe-dev` in Claude Code, or `claude plugin uninstall frappe-dev@mcp-frappe` for the plugin). `uv cache clean mcp-frappe` drops the cached `uvx` environment; delete the checkout if you used one. No app uninstall, site migration, or Bench change is required.
