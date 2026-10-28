# Security model

## Identity and authorization

The standalone process authenticates to Frappe with either an API token or the standard login endpoint. Requests execute as that user, and Frappe's resource/method APIs enforce its permissions and validation. API tokens are preferred outside local development.

## Transport

One rule covers both auth modes: `FRAPPE_URL` must use HTTPS unless the host is a loopback address (`localhost`, `127.0.0.1`, `::1`) or ends in `.localhost`. An API token is a long-lived credential sent on every request, so plain HTTP to any other host is refused before any request goes out, for token auth as well as password login.

Development networks sometimes reach the site by a container service name such as `http://frontend:8080`. For those, set `FRAPPE_ALLOW_INSECURE_HTTP=YES` (exactly that value). The server then allows plain HTTP to any host and logs one warning to stderr at startup. Use it only on a network you trust; credentials and data travel unencrypted.

## Sessions

A failed password login is never cached: the next tool call tries to log in again instead of continuing as Guest.

When a password session expires, Frappe treats requests as Guest and rejects them with HTTP 401 or 403 before running them. On such a response the server asks `/api/method/frappe.auth.get_logged_user` who it is. If that call fails or answers `Guest`, the session has expired: the server logs in again and retries the original request once. If the user is still logged in, the response is a real permission denial and is returned unchanged, with no retry and no other endpoint tried. Token auth never re-logs in, because a token does not expire with a session.

Administrator is a full-privilege account. If used for local development, supply its password through the MCP host's secret/environment facility. The project has no embedded default password and does not write credentials into files, tests, logs, or evidence. Do not use a known default password on a network reachable site.

## Human approval

`confirm=true` is not a human checkpoint: the model sets it itself. The preview tokens (`expected_modified`, `preview_id`) guard against stale state, so a change applies only to the record or files the preview showed; they do not stop a model that decides to confirm.

The human checkpoint is the MCP host. Every tool declares MCP annotations: the `read` toolset has `readOnlyHint: true`, every other tool `readOnlyHint: false`, and tools that delete or can remove data (`delete_document`, `cancel_document`, `delete_doctype`, `update_doctype`, `set_role_permissions`, `rollback_export`) `destructiveHint: true`. A host can run read tools without asking and ask a person before any write, which is what the annotations are for. Annotations are hints a host chooses to follow; configure the host to require approval for non-read tools.

To take writes away entirely, start the server with `FRAPPE_MCP_READ_ONLY=YES` (or narrow `FRAPPE_MCP_TOOLSETS`): the unselected tools are removed at startup and never listed. The connected Frappe user's permissions still bound what any remaining tool can do.

Error text returned to the model is decoded from Frappe's messages and never includes the server traceback (`exc`), which can hold file paths and code; it is capped at 2,000 characters.

## Tool boundaries

- There is no arbitrary Python, SQL, or unrestricted dotted-method tool.
- Lists and report output have explicit caps.
- Document changes preview first; update/delete confirmations require the current `modified` token. Updates also send that token to Frappe, which re-checks it under a row lock (`check_if_latest`), so a change between the check and the write is refused.
- Field names in reads, filters, and writes are checked against the DocType's metadata (Custom Fields and Property Setters included); filter operators are allowlisted, and four-element filters may only name the DocType's own child tables. Writes refuse standard columns and read-only fields.
- Metadata comes from Frappe's form loader (`frappe.desk.form.load.getdoctype`), which Frappe serves to any logged-in user, as it does for Desk. The record tools never read the System Manager-only `DocType`, `Custom Field`, or `Property Setter` resources, so they work for any user with permission on the DocType, and Frappe checks that permission on every record read and write. Metadata is cached for `FRAPPE_META_TTL` seconds (default 300); a schema change made outside the MCP is seen once its entry expires, and Frappe's own validation still applies to every write. Set `FRAPPE_META_TTL=0` to always read fresh metadata.
- Frappe's API validates writes and applies the connected user's roles and permissions.
- Schema creation uses native `DocType`, `Custom Field`, and `Property Setter` resources and remains subject to server permissions. DocType field types and validation are handled by Frappe's controller.
- `update_doctype` and `delete_doctype` refuse standard DocTypes of `frappe` and `erpnext`. `update_doctype` allowlists DocField properties and DocType settings; it accepts `depends_on`, `mandatory_depends_on`, and `read_only_depends_on` (as `set_field_property` does) but refuses `collapsible_depends_on`, `ignore_xss_filter`, and `options` on HTML and Button fields, which Frappe evaluates or renders as code. It refuses fieldname changes and fieldtype changes that can lose data. `delete_doctype` refuses DocTypes that other DocTypes link to. Neither writes local files: Frappe writes or removes a standard DocType's app files itself in developer mode.
- `diff_doctype` and `list_app_drift` are read-only. They read the `DocType`, `Module Def`, `Custom Field`, and `Property Setter` resources (System Manager, as for the other schema tools) and local files only below `FRAPPE_APPS_PATH`.
- `promote_custom_doctype` refuses `frappe` and `erpnext`, modules of other apps, custom modules, and an existing DocType folder. It changes only the DocType's `custom` flag and `module`, with the preview's `modified`.
- `set_role_permissions` calls Frappe's System Manager-only Permission Manager endpoints; it requires an existing role and DocType.
- `upsert_workflow` and `upsert_notification` build Workflow and Notification records from a checked shape: roles must exist, states and fields must belong to the DocType, and the fields Frappe evaluates as code are refused (next section). `get_workflow` and `list_notifications` are read-only. Both upserts preview first and need the current `modified` to replace a record.
- `upsert_report` writes only Report Builder reports. Columns, filters, `order_by`, and `group_by` are checked against the DocType's metadata, and `order_by` is rebuilt from fieldnames, so no SQL text reaches the saved `json`. Its post-save sample uses `frappe.desk.reportview.get_list`, which applies the user's permissions, and returns at most 5 rows.
- `upsert_workspace` saves public Workspaces of non-custom modules of apps other than `frappe` and `erpnext`. Every shortcut, link, and number card must point to an existing DocType, Report, Page, or Number Card; `URL` shortcuts are refused. `get_workspace` is read-only.
- Report execution uses Frappe's report API; report permissions still apply. `run_report` drops the `chart` unless asked and pages the rows. `list_reports` reads the `Report` list (Frappe v16 grants read to Desk users) and `get_report_filters` calls `frappe.desk.query_report.get_script`, which checks the report's roles; it returns the Report Filter rows and the report's JavaScript (cut to 8,000 characters), never its HTML print format.
- `list_documents` rebuilds `order_by` from checked fieldnames and `asc`/`desc`, so no SQL text reaches Frappe; `or_filters` gets the same checks as `filters`. Pages are capped at 100 rows (500 for `list_doctypes`).
- `count_documents` uses `frappe.client.get_count`, which counts only records the user may read. `search_records` calls `frappe.desk.search.search_link` with the DocType's own search settings; it never sends a `query` method (which would dispatch to an arbitrary whitelisted function) or `ignore_user_permissions`, and returns at most 50 results.
- `get_site_info` returns the user, app titles and versions (branch and commit details dropped), and which site apps have a checkout under `FRAPPE_APPS_PATH`; it returns no local paths.
- `submit_document` and `cancel_document` need the current `modified`, refuse DocTypes that aren't submittable, and refuse DocTypes with an active Workflow: a direct submit or cancel there would skip the workflow's transitions and roles, because Frappe only moves the document to the first state with the new docstatus.

## Code fields and templates

AGENTS.md forbids adding Python evaluation, and a field Frappe evaluates is Python evaluation by another route, so the configuration tools never write one:

- Workflow Transition `condition`, Notification `condition`, and Notification Recipient `condition` are Python expressions Frappe runs with `frappe.safe_eval`. They are refused; Notifications take `filters` instead (`condition_type: Filters`), a JSON filter list Frappe compares as data, validated like `list_documents` filters. A Workflow State's `evaluate_as_expression` (which evaluates `update_value`) is refused too. Conditions set by a person in Desk are not read back as code: `get_workflow` reports only `has_condition`. Replacing a Workflow keeps an existing transition's condition (the preview warns); replacing a Notification clears its conditions (the preview warns).
- Notification `subject` and `message` are Jinja templates Frappe renders on the server with the document and a `frappe` namespace in context. The rule (chosen by the project owner in work-plan stage 08) allows plain text plus `{{ doc.<fieldname> }}` placeholders for fields of the DocType or standard columns, each optionally followed by allowlisted filters with literal arguments: `upper`, `lower`, `title`, `capitalize`, `trim`, `striptags`, `escape`/`e`, `int`, `str`, `flt`, `round`, `truncate`, `default`. Statements, comments, whitespace control, other variables, attribute access beyond the field, indexing, calls, operators, and other filters are refused, so a template can print values of the document and nothing else. `cc` and `bcc` are rendered when they contain `{`, so they must be plain addresses.
- `Custom` and `Method` events (which name a server method) and the Slack and SMS channels are out of scope.
- Query Reports hold SQL (`query`) and Script Reports hold Python (`report_script`, or a module file for a standard one); both are code, so `upsert_report` only writes `report_type: Report Builder`, whose `json` is a list of columns and filters Frappe runs through `frappe.get_list`, and it refuses to replace a report of another type. A Report's `javascript` is never written.
- A Workspace's Custom HTML Blocks hold HTML, CSS, and JavaScript that Desk runs in the browser. `upsert_workspace` has no parameter for them, never sends the `custom_blocks` table, and refuses to replace a Workspace whose layout uses one (or a chart, quick list, or onboarding block it can't rebuild). Workspace link `description` is HTML; the tool never sets it, only carries over what is already there.

## Filesystem writes

Filesystem output is optional and local to the MCP process. `FRAPPE_APPS_PATH` is the only writable root. The app and module must exist beneath it, and the selected remote Module Def must belong to that app. Resolved paths are checked against traversal and symlink escapes. Export previews expose paths and content hashes; overwritten files receive a backup before replacement. Exports write only the module's `custom/` folder, the app's `fixtures/client_script.json` (and, with `with_workflows`, `fixtures/workflow.json`, `workflow_state.json`, and `workflow_action_master.json`), and the managed block of `hooks.py`. The configuration tools write no local files; for a standard Notification, Report, or Workspace, Frappe itself writes the module's `notification/`, `report/`, or `workspace/` folder in developer mode. `promote_custom_doctype` writes Frappe's controller templates only into a new `<module>/doctype/<doctype>/` folder and removes them again if the DocType is still custom after a failed save. The comparison tools only read: DocType JSON and `custom/` files, resolved with the same traversal and symlink checks, each at most 5 MB.

Mount only the intended app tree when running the MCP host in a container. Review exported source and fixtures through Git before migration or deployment. The MCP does not run Bench commands, migrations, or deployments.

## Data handling

The MCP host may retain tool inputs and outputs in its own logs. Request only the records and fields needed. Do not put customer data or credentials in public issue reports or evidence. Binary/file browsing and arbitrary whitelisted methods are not exposed by the standalone release.
