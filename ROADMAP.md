# Roadmap

Frappe MCP is a standalone stdio server for Frappe developers. This page lists what is implemented and what is planned. The staged plan that built it, with each stage's handoff notes, is in [docs/work-plan](docs/work-plan/README.md); what has been run against live sites is in the [compatibility matrix](docs/compatibility-and-validation.md#matrix).

## Implemented

1. **Host integration and setup**: `uvx mcp-frappe`, the `mcp-frappe check` setup check, API-key or password login with session renewal, `FRAPPE_SITE` Host routing to select one site on a multi-site Bench per process, PyPI releases (the first was 0.3.0), and a manual workflow that lists each release in the MCP registry.
2. **Discovery and reads**: site info, apps and modules, paged DocType lists, compact or full schemas from Frappe's form metadata (Custom Fields and Property Setters merged, cached), paged record lists with sorting and OR filters, counts, Link-style search, and bounded document reads.
3. **Records**: create, update, and delete with previews and `modified`-bound confirmation; batches of up to 100 records in one all-or-nothing insert, for test data; submit and cancel for testing a submittable DocType. The record tools refuse schema, configuration, and code records, and `Code` fields, and name the tool to use instead.
4. **DocTypes**: create standard and site-custom DocTypes (every variant and field type), edit them with add, change, move, and remove operations, delete DocTypes created by mistake, and promote a site-custom DocType into an app.
5. **Customization**: Custom Fields, allowlisted Property Setters, and role permissions.
6. **Site and app sync**: `diff_doctype` and `list_app_drift` verdicts based on `modified` and `migration_hash`, and native `custom/` exports with Client Script and Workflow fixtures, backups, and guarded rollback.
7. **Configuration records**: Workflows, Email and System Notifications, Report Builder reports, and public Workspaces, with code fields refused.
8. **Reports**: list reports, read their filters, and run Query and Script Reports with paging.
9. **Developer diagnostics and app methods**: pending patches, recent errors, and the app's whitelisted methods read from source; calls to app methods only from the user's `FRAPPE_MCP_METHODS` allowlist (mapped drafts such as Sales Order to Sales Invoice, previewed writes, bounded reads), with entries checked against the app source at startup.
10. **Opt-in Bench commands**: `run_bench` runs `migrate`, `reload-doc`, `export-fixtures`, `clear-cache`, and `run-tests` on a local site when the user sets `FRAPPE_BENCH_PATH`, previewed with drift and pending patches and confirmed with a preview id; the argv is fixed, with no shell and no credentials in its environment.
11. **Customize Form saves and renames**: `customize_doctype` customizes a standard DocType in one previewed Customize Form save, as Desk does; `rename` renames records, Custom Fields, app DocType fields (with a generated `rename_field` patch), and app DocTypes through Frappe's own rename code.
12. **Change journal and undo**: every confirmed write is journaled (business records only when the user opts in), and `undo_mcp_change` reverses created records, Custom Fields, Property Setters, and DocTypes, updates, and DocType edits after a preview, refusing when the target changed since.
13. **Claude Code plugin and evaluations**: the `frappe-dev` plugin (the server plus a `frappe-development` skill) installs from this repository's marketplace; `evals/` runs twelve developer tasks with this server, Frappe's built-in MCP, and both, and checks the site afterwards ([results](docs/evaluations.md), not run yet).
14. **Agent ergonomics**: readable Frappe errors with hints, a clear error naming the site when it can't be reached, MCP instructions and annotations, toolsets with a read-only mode (the default for remote targets), and an optional site label.
15. **Validation**: unit tests against a fake site built from Frappe's source, an in-memory MCP contract test, and opt-in live runners, including the end-to-end developer flow. Every runner has passed on Frappe 15.126 and 16.55 development sites with ERPNext, and the read tools were checked as a Sales User ([matrix](docs/compatibility-and-validation.md#matrix)).

## Next

- **Close the remaining validation gaps**: concurrent `set_role_permissions` updates (the native update path has no compare-and-set) ([list](docs/compatibility-and-validation.md#not-yet-checked-on-a-site)).
- **Repeat the developer-task evaluations** ([first results](docs/evaluations.md)) several times per cell to turn single passes into rates, and on v15 with `--config dev`.

## Backlog

Detailed plans for the first two are in [docs/work-plan/backlog](docs/work-plan/backlog/):

- **[Document lifecycle and workflow actions](docs/work-plan/backlog/lifecycle-workflow.md)**: amend, workflow transitions on documents, update diffs, and mandatory-field previews.
- **[Collaboration and files](docs/work-plan/backlog/collaboration-files.md)**: document activity, comments, assignments, and attachments.
- **Async HTTP**: move to `httpx.AsyncClient` so Bench reload waits stop blocking the MCP event loop.
- **Streamable HTTP transport** with per-user authentication, for teams sharing one server. Needs its own security design.
- **Print formats and PDF**: render to text or HTML, and optionally save a PDF to a confined path. Print Format templates are Jinja, so they count as code.
- **MCP resources and prompts**: DocType schemas as resources, and prompts such as "add a field to this DocType".

## Security boundaries

- No arbitrary SQL, Python, or unrestricted method dispatch: app methods only from the user's own allowlist.
- Preserve the connected user's Frappe permissions.
- Preview and explicitly confirm mutations; bind changes to existing records to fresh state.
- Keep filesystem writes inside the configured app, with backups and rollback.
- Refuse fields Frappe evaluates as code unless a stage explicitly designs and approves them.
- Treat Administrator credentials as full access and keep them out of source control.
