# Frappe MCP

Frappe MCP is a **standalone Model Context Protocol server**. An MCP host launches it as a local process over `stdio`; it connects to a Frappe site over HTTP using that site's regular API. You do not install this project as a Frappe app, add it to a Bench, or migrate the target site.

It discovers installed apps and DocTypes at runtime, so the same server can work with Frappe Framework, ERPNext, HRMS, official apps, and custom apps. Permissions and validation remain enforced by Frappe's standard REST APIs. Schema exports are written locally into an explicitly configured app directory.

> **Status:** The standalone transport and core tools are implemented. The previous Frappe app implementation is archived under `legacy/` and is not included in the installable package. See [validation](docs/compatibility-and-validation.md) for completed real-site checks and known limits.

## Capabilities

- Discover apps, modules, DocTypes, fields, schemas, and records from the connected site.
- Create, update, and delete documents through Frappe's standard resource API, with previews and stale-document checks.
- Create standard DocTypes with native field definitions, DocType settings, role permissions, and Frappe-generated app files; create Custom Fields and Property Setters through the REST API.
- Export standard DocType JSON and customization fixtures into a configured local app tree, with a preview hash, managed `hooks.py` block, backups, and rollback.
- Run permitted Query and Script Reports through Frappe's report API.
- Connect to custom apps without maintaining app-specific catalogs.

Frappe remains the source of truth for roles, permissions, validation, workflows, and document lifecycle rules. Tools never execute arbitrary Python or SQL. See the [tool reference](docs/tool-reference.md) for exact operations and limitations.

## Requirements

- Python 3.10 or newer.
- An MCP-capable host that supports a local `stdio` server.
- Network access from the MCP process to the target Frappe site.
- API key credentials, or password login over HTTPS. Password login over HTTP is restricted to loopback and `.localhost` hosts.
- For file export only: read/write access to the intended local Bench `apps/` directory, configured through `FRAPPE_APPS_PATH`.

The account's Frappe permissions still apply. An Administrator account can do anything that account is allowed to do in Desk, so use it only in a controlled development environment. The MCP does not assume or embed a password.

## Install

Install the public package from a checkout:

```bash
git clone https://github.com/DannyFiallo030503/mcp-frappe.git
cd mcp-frappe
uv sync
```

The project entry point is `mcp-frappe`. For a direct Python invocation, use `uv run python -m frappe_mcp.standalone`.

## Configure an MCP host

The server reads its connection settings from environment variables. Example shape for a host that launches local stdio servers:

```json
{
  "mcpServers": {
    "frappe": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/mcp-frappe", "run", "mcp-frappe"],
      "env": {
        "FRAPPE_URL": "http://127.0.0.1:32783",
        "FRAPPE_SITE": "cubazone.localhost",
        "FRAPPE_USERNAME": "Administrator",
        "FRAPPE_PASSWORD": "${FRAPPE_PASSWORD}"
      }
    }
  }
}
```

Host-specific environment substitution varies. Supply secrets using the host's secret store or process environment; do not replace the placeholder with a password in a shared config file. `FRAPPE_SITE` is optional and is used as the HTTP `Host` header for a multi-site Bench. For non-local targets, use HTTPS and preferably an API key/secret pair.

For file export, set `FRAPPE_APPS_PATH` to the local Bench `apps` directory visible to the MCP process. Only app/module paths that exist below that directory are writable. When the MCP host runs in a container, mount only the intended app directory and set the path to that mount; the MCP does not need to run inside the Frappe container.

All settings are described in [installation and configuration](docs/installation-and-configuration.md).

## First-use workflow

1. Call `list_installed_apps` and `list_doctypes` to discover the target site.
2. Call `get_doctype_schema` before constructing filters or write payloads.
3. Read with `list_documents` or `get_document`, requesting only needed data.
4. For a mutation, call the tool with `confirm: false`, review the preview, then confirm with its current `modified` token when required.
5. For schema changes, preview the operation first and verify the configured app/module ownership.
6. After creating or changing fields in Customize Form, call `export_form_customizations` for the DocType and destination app module. Keep `sync_on_migrate: true`, review the native `custom/<doctype>.json` diff, then confirm with its current `preview_id`.
7. Use `export_customizations` when a separate fixture export is needed. Confirm with its returned `preview_id`, then inspect and commit the app files through normal source control.

The MCP does not run migrations, restart Bench, create sites, or deploy exported code.

## Docs

- [Frappe capability study and MCP coverage](docs/frappe-capability-study.md) — framework/app capability model, current coverage, visual-equivalence criteria, and test roadmap.
- [Manual functional verification report](e2e/manual_functional_report.md) — Administrator-driven live checks of DocTypes, field settings, generated files, database state, customizations, and known failures.
- [Installation and configuration](docs/installation-and-configuration.md)
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
