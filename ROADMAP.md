# Roadmap

Frappe MCP is a standalone stdio server for Frappe developers. This page lists what is implemented and what is planned. The staged plan that built it, with each stage's handoff notes, is in [docs/work-plan](docs/work-plan/README.md); what has been run against live sites is in the [compatibility matrix](docs/compatibility-and-validation.md#matrix).

## Implemented

1. **Host integration and setup**: `uvx mcp-frappe`, the `mcp-frappe check` setup check, API-key or password login with session renewal, multi-site `Host` routing, and a PyPI release workflow.
2. **Discovery and reads**: site info, apps and modules, paged DocType lists, compact or full schemas from Frappe's form metadata (Custom Fields and Property Setters merged, cached), paged record lists with sorting and OR filters, counts, Link-style search, and bounded document reads.
3. **Records**: create, update, and delete with previews and `modified`-bound confirmation; submit and cancel for testing a submittable DocType.
4. **DocTypes**: create standard and site-custom DocTypes (every variant and field type), edit them with add, change, move, and remove operations, delete DocTypes created by mistake, and promote a site-custom DocType into an app.
5. **Customization**: Custom Fields, allowlisted Property Setters, and role permissions.
6. **Site and app sync**: `diff_doctype` and `list_app_drift` verdicts based on `modified` and `migration_hash`, and native `custom/` exports with Client Script and Workflow fixtures, backups, and guarded rollback.
7. **Configuration records**: Workflows, Email and System Notifications, Report Builder reports, and public Workspaces, with code fields refused.
8. **Reports**: list reports, read their filters, and run Query and Script Reports with paging.
9. **Agent ergonomics**: readable Frappe errors with hints, MCP instructions and annotations, and toolsets with a read-only mode.
10. **Validation**: unit tests against a fake site built from Frappe's source, an in-memory MCP contract test, and opt-in live runners, including the end-to-end developer flow. Every runner has passed on Frappe 15.126 and 16.55 development sites with ERPNext, and the read tools were checked as a Sales User ([matrix](docs/compatibility-and-validation.md#matrix)).

## Next

- **First PyPI release** of 0.3.0 through the manual release workflow.
- **Close the remaining validation gaps**: `set_role_permissions` on a live site, `bench migrate` re-importing an exported Custom Field, and record writes as a user without System Manager ([list](docs/compatibility-and-validation.md#not-yet-checked-on-a-site)).

## Backlog

Detailed plans for the first three are in [docs/work-plan/backlog](docs/work-plan/backlog/):

- **[Document lifecycle and workflow actions](docs/work-plan/backlog/lifecycle-workflow.md)**: amend, workflow transitions on documents, update diffs, and mandatory-field previews.
- **[Collaboration and files](docs/work-plan/backlog/collaboration-files.md)**: document activity, comments, assignments, and attachments.
- **[Allowlisted method adapters](docs/work-plan/backlog/method-allowlist.md)**: mapped documents and app-specific methods through a per-method allowlist.
- **Async HTTP**: move to `httpx.AsyncClient` so Bench reload waits stop blocking the MCP event loop.
- **Streamable HTTP transport** with per-user authentication, for teams sharing one server. Needs its own security design.
- **Print formats and PDF**: render to text or HTML, and optionally save a PDF to a confined path. Print Format templates are Jinja, so they count as code.
- **Rename DocTypes and fields** with data migration (`frappe.rename_doc`, `rename_field`).
- **MCP resources and prompts**: DocType schemas as resources, and prompts such as "add a field to this DocType".

## Security boundaries

- No arbitrary SQL, Python, or unrestricted method dispatch.
- Preserve the connected user's Frappe permissions.
- Preview and explicitly confirm mutations; bind changes to existing records to fresh state.
- Keep filesystem writes inside the configured app, with backups and rollback.
- Refuse fields Frappe evaluates as code unless a stage explicitly designs and approves them.
- Treat Administrator credentials as full access and keep them out of source control.
