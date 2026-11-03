# Frappe MCP

A Model Context Protocol server for **Frappe developers**. Point Claude Code or Claude Desktop at a development site and let it create and change DocTypes, customize ERPNext forms, keep the site and your app's files in sync, and set up workflows, reports, and Workspaces. Every change is previewed before it happens.

It runs as a local stdio process and talks to the site only through Frappe's REST API. You don't install it as a Frappe app, add it to a Bench, or migrate anything. Frappe's own permissions and validation apply to every call, and there is no SQL, Python eval, or arbitrary method call.

## What you can ask

- "Create a submittable DocType **Lab Sample** in `lab_app`, module Lab, with a title, a customer link, a status select, and a child table of readings."
- "Add a required **Priority** field after the title on Lab Sample, show it in the list view, and drop the old `legacy_code` field."
- "On the ERPNext **Customer** form, add a Custom Field *Account Manager* linked to User, make *Territory* mandatory, then export the customizations to `lab_app`."
- "Has anything drifted between the site and `lab_app`? Show me what `bench migrate` would change for Lab Sample."
- "I prototyped **Lab Equipment** as a custom DocType on the site. Move it into `lab_app` so it ships with the app."
- "Add an approval workflow to Lab Sample: Draft → Pending → Approved, approved by Lab Manager."
- "Send a System Notification to Lab Managers when a Lab Sample is submitted."
- "Build a **Lab** Workspace with shortcuts to Lab Sample and an *Open Samples* report."
- "Create three test Lab Samples, submit one, and check the controller set its status."

The server answers by reading the schema first, returning a preview (fields added, moved, and removed; files Frappe will write; records affected), and acting only after you confirm.

## Install

```bash
uvx mcp-frappe --version
```

Until the first PyPI release, run it from GitHub: `uvx --from git+https://github.com/DannyFiallo030503/mcp-frappe mcp-frappe`. You need Python 3.10+ and an MCP host that runs local stdio servers.

## Configure

Create an API key for a user on your development site, export the settings, and check them:

```bash
export FRAPPE_URL=http://127.0.0.1:8000 FRAPPE_SITE=site1.localhost
export FRAPPE_API_KEY=... FRAPPE_API_SECRET=...
export FRAPPE_APPS_PATH=/absolute/path/to/frappe-bench/apps   # for standard DocTypes and exports
uvx mcp-frappe check
```

`check` logs in and prints the user, app versions, toolsets, and whether `FRAPPE_APPS_PATH` holds the site's apps. It never prints a secret. HTTPS is required except for loopback and `.localhost` hosts.

Then add the server to your host. **Claude Code:**

```bash
claude mcp add frappe \
  -e FRAPPE_URL=http://127.0.0.1:8000 -e FRAPPE_SITE=site1.localhost \
  -e FRAPPE_API_KEY="$FRAPPE_API_KEY" -e FRAPPE_API_SECRET="$FRAPPE_API_SECRET" \
  -e FRAPPE_APPS_PATH=/absolute/path/to/frappe-bench/apps \
  -- uvx mcp-frappe
```

**Claude Desktop** (`claude_desktop_config.json`):

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

Other hosts: a stdio server with command `uvx` and arguments `["mcp-frappe"]`. Password login, shared `.mcp.json` files, containers, and every setting are covered in [installation and configuration](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/installation-and-configuration.md).

Standard DocTypes, DocType edits, and file exports need **developer mode** on the site and `FRAPPE_APPS_PATH` pointing at the site's Bench `apps` folder. Site-custom DocTypes, Custom Fields, and records need neither.

## Toolsets

| Toolset | What it does |
|---|---|
| `read` | Site info, DocTypes and schemas, records, counts, search, reports, drift checks, workflows, notifications, Workspaces |
| `data` | Create, update, delete, submit, and cancel records |
| `customize` | Custom Fields, Property Setters, role permissions, export to app files, rollback |
| `developer` | Create, edit, delete, and promote DocTypes; Workflows, Notifications, Report Builder reports, Workspaces |

All four are on by default. `FRAPPE_MCP_TOOLSETS=read,customize` narrows them, and `FRAPPE_MCP_READ_ONLY=YES` exposes only `read`. Every tool carries MCP annotations, so a host can run reads freely and ask before writes.

## Safety

- Writes return a preview first. Updates and deletes are bound to the record's `modified` timestamp, and exports to a hash of the preview, so nothing changes if the site moved in between.
- File writes stay inside the configured app, refuse `frappe` and `erpnext`, back up what they overwrite, and can be rolled back.
- Fields Frappe evaluates as code (Python conditions, Query and Script Reports, custom HTML blocks) are refused.
- The server never runs `bench migrate`, restarts Bench, or deploys. Review and commit the generated files yourself.

Use an Administrator key only on a development site. See the [security model](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/security.md).

## Docs

- [Tool reference](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/tool-reference.md): every tool, its arguments, and its limits
- [AI agent guide](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/ai-agent-guide.md): the workflows an agent should follow
- [Compatibility and validation](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/compatibility-and-validation.md): what has been run on v15 and v16 sites
- [Architecture](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/architecture.md), [security](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/security.md), [development](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/development.md)
- [Frappe capability study](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/docs/frappe-capability-study.md): what Frappe offers and what the server covers
- [Changelog](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/CHANGELOG.md), [roadmap](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/ROADMAP.md), [contributing](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/CONTRIBUTING.md), [security policy](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/SECURITY.md), [LLM index](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/llms.txt)

## License

MIT. The archived code under `legacy/` keeps its upstream notices; see [UPSTREAM.md](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/UPSTREAM.md) and the [third-party notices](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/THIRD_PARTY_LICENSES-frappe-mcp.txt).
