# Frappe MCP

**A permission-aware Model Context Protocol (MCP) server for Frappe Framework sites.** It discovers apps, DocTypes, fields, records, reports, workflows, workspaces, print formats, files, and translations at runtime. It also supports guarded document operations and portable app customizations.

The server is installed as a Frappe app and serves MCP over authenticated Streamable HTTP. Its tools operate through the active Frappe session and native Frappe APIs. It is designed for Frappe Framework, ERPNext, HRMS, and other installed apps without maintaining app-specific DocType catalogs.

> **Status:** The current release has been exercised against Frappe 15.96.0, ERPNext 15.96.1, and HRMS 15.60.0. See [compatibility and validation](docs/compatibility-and-validation.md). Support for other versions should be verified in a dedicated test site.

## What it can do

- Discover installed apps, modules, permission-visible DocTypes, and readable schemas.
- Read documents and child tables under Frappe document and field permissions.
- Create, update, delete, submit, cancel, or apply workflows with preview and explicit confirmation.
- Create standard DocTypes, Custom Fields, Property Setters, Client Scripts, and role permission rules using native Frappe controllers.
- Export selected standard DocTypes and customization records to an installed app as JSON and fixtures, with a preview, backup manifest, and rollback operation.
- Discover and run permitted reports; inspect workspaces, print formats, files, and translations.
- Invoke explicitly named Frappe-whitelisted methods after preview and confirmation.

There is no arbitrary SQL or Python execution tool. Authorization remains the responsibility of Frappe roles, document permissions, field permissions, report roles, and the authenticated user.

## Requirements

- A Frappe Bench and site with a supported Frappe installation.
- Python 3.10 or later for the package metadata. Use the Python runtime managed by the target Bench when installing the app.
- Network access from the MCP client to the Frappe site.
- A dedicated Frappe user with only the roles needed for the intended tools. Schema customization and export tools require `System Manager`.

## Install in a Bench

Run these commands from the Bench directory. Use a development or staging site first; do not install an unreviewed integration on a production site.

```bash
bench get-app https://github.com/<OWNER>/mcp-frappe.git
bench --site <site-name> install-app mcp_frappe
bench restart
```

The repository URL above is a placeholder until the project is published. If you already have a local checkout, make it available as `apps/mcp_frappe` in the Bench and install it on the target site.

The MCP endpoint is:

```text
https://<frappe-host>/api/method/mcp_frappe.mcp.handle_mcp
```

Frappe token authentication uses this HTTP header:

```text
Authorization: token <api_key>:<api_secret>
```

Do not enable Guest access for this endpoint. Use HTTPS outside a trusted local environment. Store credentials in the MCP client's secret store or environment, never in a checked-in config file.

## Connect an MCP client

Configure an MCP client that supports **Streamable HTTP** and custom HTTP headers. For clients with a JSON configuration format, the conceptual configuration is:

```json
{
  "mcpServers": {
    "frappe": {
      "url": "https://<frappe-host>/api/method/mcp_frappe.mcp.handle_mcp",
      "headers": {
        "Authorization": "token ${FRAPPE_API_KEY}:${FRAPPE_API_SECRET}"
      }
    }
  }
}
```

Client configuration formats and environment-variable expansion vary. Confirm that your client supports Streamable HTTP and custom headers; do not assume the example's variable interpolation works in every client. Create an API key for a dedicated Frappe user and make the secret available to the client using its documented secret mechanism.

## First-use workflow

An AI agent should discover the site instead of assuming installed apps or DocTypes:

1. Call `list_installed_apps`.
2. Call `list_doctypes` for the relevant app/module.
3. Call `get_doctype_schema` before constructing filters or write payloads.
4. Read records with `list_documents` or `get_document`; request only the fields needed.
5. For writes, call the tool with `confirm: false` (the default), inspect the returned preview, then repeat with `confirm: true`.
6. For update/delete/submit/cancel/workflow actions, pass the exact `expected_modified` returned by the latest preview.
7. Verify the result with a fresh read.

See [AI agent guide](docs/ai-agent-guide.md) for detailed instructions and examples, and [tool reference](docs/tool-reference.md) for every tool and argument.

## Documentation

- [Installation and configuration](docs/installation-and-configuration.md)
- [Tool reference](docs/tool-reference.md)
- [AI agent guide](docs/ai-agent-guide.md)
- [Security model](docs/security.md)
- [Architecture and customization export](docs/architecture.md)
- [Compatibility and validation evidence](docs/compatibility-and-validation.md)
- [Development guide](docs/development.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [AI coding agent instructions](AGENTS.md)
- [LLM documentation index](llms.txt)
- [Roadmap](ROADMAP.md)
- [Vendored upstream code](UPSTREAM.md)

## Development and validation

Read [development guide](docs/development.md) before contributing. It describes the package layout, local Bench workflow, and automated test commands. Real HTTP acceptance evidence from the isolated `prueba.localhost` site is summarized in [the validation report](docs/compatibility-and-validation.md); its raw JSONL evidence and runner are under [`e2e/`](e2e/).

## License

This project is released under the MIT License. The vendored transport retains its upstream copyright and license notices; see [UPSTREAM.md](UPSTREAM.md) and [THIRD_PARTY_LICENSES-frappe-mcp.txt](THIRD_PARTY_LICENSES-frappe-mcp.txt).
