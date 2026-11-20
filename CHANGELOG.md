# Changelog

All notable changes to this project are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) with the 0.x rule below.

## Versioning policy

- The package version lives in one place, `frappe_mcp/__init__.py` (`__version__`). The
  release tag is `v<version>`.
- The **tool contract** is the set of MCP tools, their arguments, and their return shapes,
  plus the environment variables the server reads.
- While the version is 0.x, a **minor** release (0.3 → 0.4) may break the tool contract, and a
  **patch** release (0.3.0 → 0.3.1) never does. Every breaking change is listed under
  **Breaking** in its release, with what to change.
- From 1.0 on, only a major release breaks the tool contract.

## [Unreleased]

## [0.3.0] - 2026-11-19

This release refocuses the server on Frappe developers working against a development site:
editing DocTypes, customizing standard DocTypes, keeping the site and the app files in sync,
and setting up configuration records. It covers work-plan stages 00–15.

### Breaking

- `list_documents` and `list_doctypes` return a page,
  `{"rows", "offset", "limit", "has_more"}`, instead of a bare list. Pass `offset + limit` as
  the next `offset` while `has_more` is true.
- `export_customizations` writes Frappe's native
  `<app>/<app>/<module>/custom/<doctype>.json` files, which `bench migrate` applies, instead of
  DocType JSON. Client Scripts go to `<app>/<app>/fixtures/client_script.json`. New arguments:
  `with_permissions`, `apply_module_filter`, `sync_on_migrate`, `with_workflows`.
- `get_doctype_schema` returns a compact schema by default; pass `detail="full"` for every
  DocField property. `get_document` without `fields` returns only fields that have a value.
- `create_custom_field(doctype, field, app=None, module=None, confirm=False)`: `app` and
  `module` are optional and checked against the site only, and `field` may not carry
  `doctype`, `dt`, or `module`.
- `set_field_property` checks `property_type` against the property, updates an existing
  Property Setter (preview `mode: "update"`, confirm with `expected_modified`), and returns a
  flat preview instead of nesting the payload under `data`.
- `create_document` accepts `name` only when the DocType's `autoname` is `prompt`.
- Reads reject unknown fields, and filters accept only the allowlisted operators.
- Error messages have one format, `Frappe HTTP <status> <ExcType>: <message>`, decoded from
  Frappe's server messages with HTML stripped and no traceback.
- Password login needs HTTPS too, unless `FRAPPE_URL` is loopback or `.localhost` or
  `FRAPPE_ALLOW_INSECURE_HTTP=YES` is set.
- `run_report` returns `total_rows`, `offset`, `limit`, and `has_more`, and leaves the chart
  out unless `include_chart=True`.
- **Remote targets start read-only.** When `FRAPPE_URL`'s host isn't loopback or `.localhost`
  and `FRAPPE_MCP_READ_ONLY` is unset, only the `read` toolset is listed. To keep writing to a
  remote site, set `FRAPPE_MCP_READ_ONLY=NO`. A remote target that names a writable toolset
  in `FRAPPE_MCP_TOOLSETS` without it no longer starts.
- **The record tools keep to record data.** `create_document` and `update_document` refuse
  schema and configuration DocTypes and name the tool to use instead: `DocType`, `DocField`,
  `DocPerm`, `Custom Field`, `Property Setter`, `Custom DocPerm`, `Customize Form`,
  `Workflow`, `Notification`, `Report`, `Workspace`. Edit a Custom Field with
  `set_field_property` instead of `update_document`. `delete_document` sends DocType deletes
  to `delete_doctype` and DocField/DocPerm rows to their DocType's tools, and still deletes
  Custom Fields, Property Setters, Custom DocPerms, Workflows, Notifications, Reports, and
  Workspaces.
- All three record tools refuse records that hold code (Server Script, Client Script, Print
  Format, Webhook, Email Template, Web Form, and the others listed in `docs/security.md`), and
  `create_document`/`update_document` refuse values for `Code` fields in any DocType, child
  rows included, apart from a short allowlist of fields that hold plain data.
- **Tools have no output schema, so results have no `structuredContent`.** The JSON is in the
  text content as before: one text block, or one block per item for `list_installed_apps`. A
  client that read `structuredContent["result"]` should decode the text blocks instead. Tool
  annotations no longer repeat the `title`; read the tool's own `title`.

### Added

- **Command line:** `mcp-frappe --version`, and `mcp-frappe check`, which reads the settings,
  logs in, and prints the user, app versions, selected toolsets, and whether
  `FRAPPE_APPS_PATH` is usable, without printing secrets.
- **Packaging:** PyPI metadata, one version source, an install path through
  `uvx mcp-frappe`, a CI job that installs the built wheel in a clean environment, and a
  release workflow with PyPI trusted publishing that only runs when triggered by hand.
- **DocType editing:** `update_doctype` (add, update, remove, and move fields, set DocType
  properties) with data-safety checks, and `delete_doctype`.
- **Site and app sync:** `diff_doctype`, `list_app_drift`, and `promote_custom_doctype`.
- **Configuration records:** `get_workflow`, `upsert_workflow`, `list_notifications`,
  `upsert_notification`, `upsert_report` (Report Builder), `get_workspace`, and
  `upsert_workspace`. Workflows can be exported as app fixtures.
- **Reads:** `count_documents`, `search_records`, `get_site_info`, `list_reports`,
  `get_report_filters`; `list_documents` gained `offset`, `order_by`, and `or_filters`.
- **Document lifecycle:** `submit_document` and `cancel_document`.
- **Agent ergonomics:** server instructions, argument descriptions, tool titles and MCP
  annotations (read-only, destructive, idempotent), and toolsets chosen with
  `FRAPPE_MCP_TOOLSETS` or `FRAPPE_MCP_READ_ONLY=YES`.
- **Settings:** `FRAPPE_META_TTL` (metadata cache), `FRAPPE_ALLOW_INSECURE_HTTP`,
  `FRAPPE_MCP_TOOLSETS`, `FRAPPE_MCP_READ_ONLY`, and `FRAPPE_SITE_LABEL`, a name for the
  connection that `get_site_info` returns as `site_label` and `check` prints (FR-2026-001). Invalid values stop the server at startup
  with a one-line message.
- `mcp-frappe check` prints a `tool list` row: the tools and characters each toolset adds to
  the host's context, and the total for the selected toolsets.
- `server.json` for the MCP registry, and
  [using both servers](docs/native-frappe-mcp.md): how to run this server as `frappe-dev` next
  to Frappe's built-in MCP server.

### Changed

- Metadata comes from Frappe's `getdoctype` (Custom Fields and Property Setters already
  applied) and is cached per process, so most tools need fewer requests.
- Updates send `modified`, so Frappe refuses a save over a newer version of the document.
- Expired password sessions are renewed once automatically; a real permission denial is
  never retried.
- Write paths that can make a developer-mode Bench reload its web worker check the site's
  state after a dropped connection instead of reporting a failure.
- When the site can't be reached, a tool error names the site (`scheme://host[:port]`), the
  failure (connection refused, timed out, host name not resolved), and says to run
  `mcp-frappe check`, instead of a bare `[Errno 111] Connection refused`. Connecting gives up
  after 5 seconds, or `FRAPPE_TIMEOUT` if smaller; reads keep the full timeout.
- `create_doctype` says to run `bench migrate` when the app already has the DocType's file but
  the site doesn't have the DocType.
- **The tool list is half the size:** about 31,900 characters instead of 61,000 for the 35
  tools. Descriptions are shorter (details are in `docs/tool-reference.md`), shared argument
  descriptions are shorter, and input schemas drop generated titles and the `null` branch of
  optional arguments. A test keeps the list within 32,000 characters.
- The server instructions tell the model to use another connected Frappe MCP server for record
  data and method calls, and this one for DocTypes, customizations, and app files. The docs
  suggest the server name `frappe-dev`.
- The README (also the PyPI page) starts with example developer prompts and uses absolute
  links; the compatibility page is a v15/v16 matrix that links each cell to its evidence.

### Fixed

- A failed login is no longer kept as the session.
- `create_custom_field` and `set_field_property` no longer require a local app checkout.
- Exports recurse into child tables of the merged schema, and rollback removes the
  directories the export created.
- `upsert_workspace` saves the Workspace as `standard` on Frappe v16, which writes a Workspace
  to its module's files only when that flag is set. Before, on v16 the Workspace was saved on
  the site but never reached the app.

### Validated

- Every e2e runner, including the end-to-end developer flow, passed on fresh Frappe 15.126 and
  16.55 development sites with ERPNext, as Administrator, and the read tools were checked as a
  Sales User. See the compatibility matrix in `docs/compatibility-and-validation.md`.

## [0.2.0] - 2026-10-19

### Added

- Standalone MCP server over stdio that talks to a Frappe site through its HTTP API, replacing
  the earlier implementation that ran as an installed Frappe app (now under `legacy/`).

[Unreleased]: https://github.com/DannyFiallo030503/mcp-frappe/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/DannyFiallo030503/mcp-frappe/compare/5ceaff6...v0.3.0
[0.2.0]: https://github.com/DannyFiallo030503/mcp-frappe/commit/5ceaff6
