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

### Added

- Seven developer-task evaluations: `full-feature`, `run-app-tests`, `sync-teammate-change`, `workflow-fixture-export`, `export-rollback`, `undo-schema-change`, and `diagnose-error`. Tasks that ask for a change, `promote-prototype` included, tell the agent not to stop for confirmation.
- `evals/run.py --repeat N` runs each task and configuration N times and adds a per-configuration `summary` of passes.
- The change journal keeps the `rollback_id` of an export or field rename in the entry's `target`, so an agent can find the export to undo with `rollback_export`.
- `get_site_info` and `mcp-frappe check` report version-based MCP routing guidance. Frappe v15 uses this server; v16+ may use the native MCP for data when it is enabled and configured; unknown versions do not assume native availability. The local server cannot configure another MCP host.

### Fixed

- `customize_doctype`'s `add_custom_field` accepts the name Frappe derives from a non-ASCII label, as Desk does (`Código` → `custom_código`), instead of refusing it as not lowercase ASCII and then asking for that same name. `order_by` in `list_documents` and `upsert_report`, and `{{ doc.<field> }}` placeholders in Notification templates, accept such field names too.
- The DocType recovery copies kept when a standard DocType rename or deletion loses the connection now go to `<app>/.mcp-frappe-backups/doctype-recoveries/` inside `FRAPPE_APPS_PATH`, like export backups, instead of `~/.local/state`, and Frappe's moved folder is kept there instead of in the module's `doctype/` folder. One recovery keeps one copy, and its error message is punctuated correctly.
- `mcp-frappe check` and connection errors describe local ports generically: 8000 as `bench start`'s web port (or a container's internal port) and 9000 as Socket.IO, without assuming a particular container tool.
- `docs/security.md` no longer claims a default `admin` password; password login always needs `FRAPPE_PASSWORD`.
- `rename(kind="field")` no longer removes its generated patch files when a transport error leaves the remote DocType state uncertain. It reconciles an unambiguous field rename despite Frappe metadata normalization; otherwise it returns the rollback ID and a conditional recovery path without retrying the write.

## [0.4.0] - 2026-11-21

This release adds the rest of the development loop that Frappe's built-in MCP server can't
cover: Bench commands, pending patches and recent errors, renames with data-preserving patches,
full Customize Form saves, allowlisted app methods, batch inserts, and undo for MCP changes.
It also ships a Claude Code plugin and the first developer-task evaluations. It covers
work-plan stages 16–21. Every change to the tool contract is an addition: no tool or argument
was removed, and no argument became required.

### Added

- A Claude Code plugin, `frappe-dev`, in `plugins/frappe-dev`, listed by the repository's
  marketplace (`claude plugin marketplace add DannyFiallo030503/mcp-frappe`, then
  `claude plugin install frappe-dev@mcp-frappe`). It runs `uvx mcp-frappe` with the site URL,
  API key and secret (kept in the credential store), apps folder, Bench folder, and toolsets
  from its options, and adds the `frappe-development` skill.
- Developer-task evaluations: `evals/run.py` runs the twelve tasks in `evals/tasks/` with
  `claude -p` against this server, Frappe's built-in MCP, or both, and checks the site and the
  app checkout afterwards. It needs `FRAPPE_ALLOW_EVALS=YES` and a local site. Results of
  the first run (v16) are in `docs/evaluations.md`. PyYAML joins the `dev` extra.
- `list_pending_patches(app)`: the app's `patches.txt` entries that `Patch Log` doesn't show
  as run, in file order with section and line, plus logged patches no longer in the file.
- `get_recent_errors(since, method, reference_doctype, limit, offset)`: `Error Log` newest
  first, with the exception line and the last 40 traceback lines.
- `list_app_methods(app, search, limit, offset)`: the app checkout's `frappe.whitelist`
  methods with their arguments, found by parsing the files with `ast` (nothing is imported).
- The tool-list budget test allows 34,000 characters (was 32,000).
- Allowlisted method calls: `FRAPPE_MCP_METHODS` names a JSON file listing the only app
  methods the server may call, each as a `mapper`, `read`, or `write` entry with optional
  `params`. With it set, the `data` toolset adds `list_allowed_methods()`,
  `make_mapped_document(method, source_name)` (an unsaved draft shaped for
  `create_document`), and `call_allowed_method(method, args, limit, offset, confirm)` (reads
  run at once, writes are previewed). The file is checked at startup, against the app
  checkout when `FRAPPE_APPS_PATH` has it; a problem stops the server with its line.
  `mcp-frappe check` reports it. Example: `examples/erpnext-methods.json`.
- `list_app_methods(..., as_config=True)` suggests `FRAPPE_MCP_METHODS` entries.
- `mcp-frappe check` and the startup log report the method allowlist.
- Opt-in Bench commands: with `FRAPPE_BENCH_PATH` set (local sites only), the new `bench`
  toolset adds `run_bench(command, app, module, doctype, test_module, confirm,
  expected_preview)` for `migrate`, `reload-doc`, `export-fixtures`, `clear-cache`, and
  `run-tests`. Each run is previewed (argv, drift, pending patches, fixture files) and
  confirmed with a `preview_id`; the argv is built from checked values with no shell, the
  child process gets no credentials, and the output is redacted and capped. Settings:
  `FRAPPE_BENCH_SITE`, `FRAPPE_BENCH_COMMAND` (a JSON list, for Docker), and
  `FRAPPE_BENCH_TIMEOUT`. `mcp-frappe check` prints a `bench` row.
- `list_app_drift` and `list_pending_patches` return a `hint` naming what syncs a
  `missing_on_site` DocType or runs pending patches.
- `customize_doctype(doctype, operations, expected_preview, confirm)`: customize a standard
  DocType in one Customize Form save (`set_property`, `move_field`, `add_custom_field`,
  `change_fieldtype` within Customize Form's related-type groups). The preview lists the
  Property Setters and Custom Fields the save creates, updates, or removes, redundant setters
  included; confirm with `preview_id`.
- `rename(kind, doctype, old, new, merge, expected_preview, confirm)`: rename a record, a
  Custom Field's fieldname, a field of your app's DocType, or your app's DocType through
  Frappe's own rename code. A field rename also writes a `rename_field` patch from a fixed
  template and lists it in `patches.txt`; `rollback_export` undoes those files.
- The tool-list budget test allows 37,000 characters (was 34,000), and the opt-in caps grow
  by the same 3,000.
- `FRAPPE_MCP_TOOLSETS` accepts `bench`; the default selection includes it, and it has no
  tool unless `FRAPPE_BENCH_PATH` is set. `mcp-frappe check` lists only toolsets with tools.

- `create_documents(doctype, rows, expected_preview, confirm)`: create 1-100 records of one
  DocType in one all-or-nothing request (`frappe.client.insert_many`). Every row is checked
  like `create_document`; the preview lists per-row `problems` and confirms with `preview_id`.
- A change journal: every confirmed write is recorded in
  `FRAPPE_APPS_PATH/.mcp-frappe-journal/<site>.json` (in memory without `FRAPPE_APPS_PATH`),
  newest 200 entries, and confirmed results carry the entry id as `mcp_change`. Writes of
  business records are journaled only with the new `FRAPPE_MCP_JOURNAL_RECORDS=YES`.
- `list_mcp_changes(limit)` lists the journal, and `undo_mcp_change(entry_id,
  expected_preview, confirm)` undoes created records, Custom Fields, Property Setters, and
  DocTypes, updates, and DocType edits, after a preview, and only while the target is
  unchanged since.
- `create_document` checks child-table rows: each must be an object whose keys are fields of
  the child DocType (standard columns such as `parent` are refused).
- The tool-list budget test allows 40,000 characters (was 37,000), and the opt-in caps grow
  by the same 3,000.
- `e2e/run_standalone_batch_undo.py`: batch inserts and undo on ToDos, opt-in with
  `FRAPPE_ALLOW_MUTATION_TEST=YES`.
- `evals/run.py --transcripts DIR` saves each run's raw stream-json (mode 0600) for debugging.

### Fixed

- `rollback_export` no longer reports an exported empty file as changed since the export.
- `rename(kind="field")` no longer reuses a patch name the site's `Patch Log` already holds.
  A field renamed, undone, and renamed again got a patch that `bench migrate` skipped, so its
  values never reached the new column; the patch now gets a `_2`, `_3`, ... suffix. Found by
  the stage 21 evaluations.
- `diff_doctype` reports `in_sync`, not `diverged`, when the site's `migration_hash` and
  `modified` match the file: what still differs is what Frappe filled in on import (`engine`,
  `row_format`, `sort_field`, `sort_order` for a hand-written JSON), now flagged
  `filled_on_import`. Found by the stage 21 evaluations.

### Validated

- The developer-task evaluations ran on a Frappe 16.55.1 development site with ERPNext
  16.53.0: this server passed 11 of 12 tasks, Frappe's built-in MCP server 9, and both
  together 11. The evaluation agents also used `run_bench` (`reload-doc`, `migrate`),
  `rename(kind="field")` with its patch, `list_pending_patches`, `create_documents`, and
  `undo_mcp_change` on that site. See `docs/evaluations.md`. The other tools added in this
  release are unit-tested; v15 was not run (`docs/compatibility-and-validation.md`).

## [0.3.0] - 2026-11-20

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

[Unreleased]: https://github.com/DannyFiallo030503/mcp-frappe/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/DannyFiallo030503/mcp-frappe/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/DannyFiallo030503/mcp-frappe/compare/5ceaff6...v0.3.0
[0.2.0]: https://github.com/DannyFiallo030503/mcp-frappe/commit/5ceaff6
