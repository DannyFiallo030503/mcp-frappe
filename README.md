# Frappe MCP

Frappe MCP is a **standalone Model Context Protocol server**. An MCP host launches it as a local process over `stdio`; it connects to a Frappe site over HTTP using that site's regular API. You do not install this project as a Frappe app, add it to a Bench, or migrate the target site.

It discovers installed apps and DocTypes at runtime, so the same server can work with Frappe Framework, ERPNext, HRMS, official apps, and custom apps. Permissions and validation remain enforced by Frappe's standard REST APIs. Schema exports are written locally into an explicitly configured app directory.

> **Status:** The standalone transport and core tools are implemented. The previous Frappe app implementation is archived under `legacy/` and is not included in the installable package. See [validation](docs/compatibility-and-validation.md) for completed real-site checks and known limits.

## Capabilities

- Discover apps, modules, DocTypes, fields, schemas, and records from the connected site.
- Create, update, and delete documents through Frappe's standard resource API, with previews and stale-document checks.
- Create standard DocTypes with native field definitions, DocType settings, role permissions, and Frappe-generated app files; create Custom Fields and Property Setters through the REST API.
- Export site customizations into a configured local app tree as Frappe's native `custom/` files, which `bench migrate` applies, with a preview hash, backups, and rollback.
- Find reports and their filters, and run permitted Query and Script Reports through Frappe's report API.
- Group tools into toolsets (`read`, `data`, `customize`, `developer`) with `FRAPPE_MCP_TOOLSETS`, or expose only reads with `FRAPPE_MCP_READ_ONLY=YES`. Every tool carries MCP annotations, so a host can run reads freely and ask before writes.
- Connect to custom apps without maintaining app-specific catalogs.

Frappe remains the source of truth for roles, permissions, validation, workflows, and document lifecycle rules. Tools never execute arbitrary Python or SQL. See the [tool reference](docs/tool-reference.md) for exact operations and limitations.

## Requirements

- Python 3.10 or newer.
- An MCP-capable host that supports a local `stdio` server.
- Network access from the MCP process to the target Frappe site.
- API key credentials, or password login with automatic session renewal. Both require HTTPS except for loopback and `.localhost` hosts, unless `FRAPPE_ALLOW_INSECURE_HTTP=YES` is set for a trusted development network.
- For file export only: read/write access to the intended local Bench `apps/` directory, configured through `FRAPPE_APPS_PATH`.

The account's Frappe permissions still apply. An Administrator account can do anything that account is allowed to do in Desk, so use it only in a controlled development environment. The MCP does not assume or embed a password.

## Install

The main path is [`uvx`](https://docs.astral.sh/uv/guides/tools/), which runs the published package in its own cached environment, with no clone and no virtualenv to manage:

```bash
uvx mcp-frappe --version
```

Until the first PyPI release is out, run it from GitHub instead: `uvx --from git+https://github.com/DannyFiallo030503/mcp-frappe mcp-frappe`. `pipx run mcp-frappe` and `pip install mcp-frappe` work too.

To work on the server itself, use a checkout:

```bash
git clone https://github.com/DannyFiallo030503/mcp-frappe.git
cd mcp-frappe
uv sync --extra dev
uv run mcp-frappe --version
```

## Check the setup first

Export the settings in your shell (keep secrets out of shell history and shared files), then run `check`. It reads the settings, logs in, and prints the user, the app versions, the selected toolsets, and whether `FRAPPE_APPS_PATH` is usable. It exits 0 when ready, 1 otherwise, and never prints a key, secret, or password.

```bash
export FRAPPE_URL=http://127.0.0.1:8000 FRAPPE_SITE=site1.localhost
export FRAPPE_API_KEY=... FRAPPE_API_SECRET=...
uvx mcp-frappe check
```

```text
mcp-frappe 0.3.0
  ok    settings          http://127.0.0.1:8000, site site1.localhost, API key auth
  ok    connection        logged in as Administrator
  ok    versions          erpnext 16.2.0, frappe 16.33.1, my_app 0.0.1
  info  developer mode    not reported: Frappe has no API for it
  ok    toolsets          read, data, customize, developer
  ok    FRAPPE_APPS_PATH  not set; standard DocType creation and file exports are unavailable
Ready.
```

## Configure an MCP host

The host launches `uvx mcp-frappe` and passes the settings as environment variables. With no arguments, `mcp-frappe` serves MCP over stdio; it writes nothing else to stdout and logs to stderr.

**Claude Code:**

```bash
claude mcp add frappe \
  -e FRAPPE_URL=http://127.0.0.1:8000 \
  -e FRAPPE_SITE=site1.localhost \
  -e FRAPPE_API_KEY="$FRAPPE_API_KEY" \
  -e FRAPPE_API_SECRET="$FRAPPE_API_SECRET" \
  -e FRAPPE_APPS_PATH=/absolute/path/to/frappe-bench/apps \
  -- uvx mcp-frappe
```

This stores the server for the current project in your user configuration (scope `local`). For a shared `.mcp.json` (`-s project`), write `"FRAPPE_API_SECRET": "${FRAPPE_API_SECRET}"` there instead of the value; Claude Code expands it from the environment at launch. Run `/mcp` in Claude Code to see the server's status.

**Claude Desktop** (`claude_desktop_config.json`, opened from Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "frappe": {
      "command": "uvx",
      "args": ["mcp-frappe"],
      "env": {
        "FRAPPE_URL": "http://127.0.0.1:8000",
        "FRAPPE_SITE": "site1.localhost",
        "FRAPPE_API_KEY": "<api key>",
        "FRAPPE_API_SECRET": "<api secret>"
      }
    }
  }
}
```

Claude Desktop does not expand variables, so the values live in that file; keep it private. If it cannot find `uvx`, use the absolute path that `which uvx` prints.

**Other MCP hosts:** declare a stdio server with command `uvx`, arguments `["mcp-frappe"]`, and the `FRAPPE_*` variables in its environment. From a development checkout, use command `uv` with `["--directory", "/absolute/path/to/mcp-frappe", "run", "mcp-frappe"]`.

`FRAPPE_SITE` is optional and is used as the HTTP `Host` header for a multi-site Bench. For non-local targets, use HTTPS and preferably an API key/secret pair.

For file export, set `FRAPPE_APPS_PATH` to the local Bench `apps` directory visible to the MCP process. Only app/module paths that exist below that directory are writable. When the MCP host runs in a container, mount only the intended app directory and set the path to that mount; the MCP does not need to run inside the Frappe container.

All settings are described in [installation and configuration](docs/installation-and-configuration.md).

## First-use workflow

1. Call `get_site_info`, then `list_doctypes(search=...)`, to discover the target site.
2. Call `get_doctype_schema` before constructing filters or write payloads.
3. Read with `list_documents` (paged with `offset` and `has_more`), `count_documents`, `search_records`, or `get_document`, requesting only needed data.
4. For a mutation, call the tool with `confirm: false`, review the preview, then confirm with its current `modified` token when required.
5. For schema changes, preview the operation first and verify the configured app/module ownership.
6. Call `export_customizations` to preview filesystem changes. Confirm with the returned `preview_id`, then inspect and commit the app files through normal source control.

The MCP does not run migrations, restart Bench, create sites, or deploy exported code.

## Docs

- [Frappe capability study and MCP coverage](docs/frappe-capability-study.md) — framework/app capability model, current coverage, visual-equivalence criteria, and test roadmap.
- [Manual functional verification report](e2e/manual_functional_report.md) — Administrator-driven live checks of DocTypes, field settings, generated files, database state, customizations, and known failures.
- [Installation and configuration](docs/installation-and-configuration.md)
- [Changelog](CHANGELOG.md) — releases, breaking changes, and the versioning policy
- [Tool reference](docs/tool-reference.md)
- [AI agent guide](docs/ai-agent-guide.md)
- [Security model](docs/security.md)
- [Architecture](docs/architecture.md)
- [Compatibility and validation](docs/compatibility-and-validation.md)
- [Development guide](docs/development.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [LLM documentation index](llms.txt)
- [Roadmap](ROADMAP.md)

## License

MIT. Archived vendored transport code retains its upstream notices; see [UPSTREAM.md](UPSTREAM.md) and [third-party notices](THIRD_PARTY_LICENSES-frappe-mcp.txt).
