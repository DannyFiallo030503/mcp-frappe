# Tool reference

All tools execute in the standalone MCP process and use the configured Frappe HTTP session. Native Frappe permissions apply to every remote call. With password auth, an expired session is renewed and the rejected call retried once without any action from the caller; a real permission denial is returned unchanged. List tools cap result sizes; mutation tools preview before acting unless noted.

Blocking Frappe HTTP, Bench, and app-file work runs in worker threads, so read calls can proceed while another call waits for a worker reload. Confirmed writes are serialized. Worker operations are not abandoned on host cancellation; if a client reports an interrupted write, inspect the site/app state before submitting it again.

Before a host lists any tool, `mcp-frappe check` (a command, not a tool) tests the same settings and the login from a terminal; see [installation and configuration](installation-and-configuration.md#command-line).

## Toolsets, read-only mode, and annotations

Tools are grouped into five toolsets. All five are on by default for a loopback or `.localhost` target (`bench` has a tool only when `FRAPPE_BENCH_PATH` is set); a remote target gets only `read` unless `FRAPPE_MCP_READ_ONLY=NO` (see below). `FRAPPE_MCP_TOOLSETS` (comma-separated) narrows the list, and the server removes the other tools at startup, so the host never sees them.

| Toolset | Tools |
|---|---|
| `read` | `get_site_info`, `list_installed_apps`, `list_doctypes`, `get_doctype_schema`, `list_documents`, `count_documents`, `search_records`, `get_document`, `list_reports`, `get_report_filters`, `run_report`, `diff_doctype`, `list_app_drift`, `list_pending_patches`, `get_recent_errors`, `list_app_methods`, `get_workflow`, `list_notifications`, `get_workspace`, `list_mcp_changes` |
| `data` | `create_document`, `create_documents`, `update_document`, `delete_document`, `submit_document`, `cancel_document`, `undo_mcp_change`; with `FRAPPE_MCP_METHODS` set, also `list_allowed_methods`, `make_mapped_document`, `call_allowed_method` ([allowlisted method calls](#allowlisted-method-calls)) |
| `customize` | `create_custom_field`, `set_field_property`, `set_role_permissions`, `customize_doctype`, `export_form_customizations`, `export_customizations`, `rollback_export` |
| `developer` | `create_doctype`, `update_doctype`, `delete_doctype`, `promote_custom_doctype`, `rename`, `upsert_workflow`, `upsert_notification`, `upsert_report`, `upsert_workspace` |
| `bench` | `run_bench`, only with `FRAPPE_BENCH_PATH` set and a local target ([Bench commands](#bench-commands)) |

`FRAPPE_MCP_READ_ONLY=YES` is shorthand for `FRAPPE_MCP_TOOLSETS=read`: use it to look at a site that must not change. When `FRAPPE_MCP_READ_ONLY` is unset, a remote target (a `FRAPPE_URL` host that isn't loopback or `.localhost`) is read-only too; `FRAPPE_MCP_READ_ONLY=NO` enables writes on it, and the startup log and `mcp-frappe check` report either case. The server refuses to start on an unknown toolset name, on a `FRAPPE_MCP_READ_ONLY` value other than `YES` or `NO`, and when it is read-only (by `YES` or as a remote target) and `FRAPPE_MCP_TOOLSETS` names a toolset other than `read`.

Every tool has a `title` (its display name) and MCP annotations; the annotations don't repeat the title. Tools in `read` have `readOnlyHint: true`, and so do `list_allowed_methods` and `make_mapped_document`, which save nothing; the others have `readOnlyHint: false` and say whether they are destructive and idempotent. `destructiveHint: true` is set on `delete_document`, `cancel_document`, `undo_mcp_change` (its inverse deletes what a change created), `delete_doctype`, `update_doctype` (it can remove fields and change field types), `set_role_permissions`, `rollback_export`, `call_allowed_method` (a listed write method can do anything), and `run_bench` (`migrate` runs patches that change data). `idempotentHint: true` is set where repeating the call with the same arguments leaves the same end state: `update_document`, `delete_document`, `delete_doctype`, `set_field_property`, `set_role_permissions`, and the four `upsert_*` tools. `openWorldHint` is `false` everywhere: the tools reach only the configured site and `FRAPPE_APPS_PATH`. Hosts use these hints to run read tools without asking and to ask a person before a write; see [security](security.md#human-approval).

The server also sends MCP `instructions` in its initialize result: a short workflow (start with `get_site_info`, read the schema first, which tool changes what, preview then confirm, a permission denial is final). The arguments shared by many tools (`filters`, `or_filters`, `fields`, `order_by`, `expected_modified`, `expected_preview`, `confirm`, `detail`, and the `run_report` filters) carry descriptions in each tool's input schema.

### Tool-list size

A host sends every listed tool's name, title, description, input schema, and annotations to the model in every session, so the list is kept small: each description says what the tool does, its key constraints, its return shape, and how to confirm, and this page holds the details. `tests/test_mcp_contract.py` keeps the whole list, serialized as a client receives it, at or below 40,000 characters (about 10,000 tokens); it was about 61,000 before stage 15, which set the cap at 32,000, stage 16 raised it to 34,000 for its three diagnostics tools, stage 19 to 37,000 for `customize_doctype` and `rename` (about 2,800 characters together), and stage 20 to 40,000 for `create_documents`, `list_mcp_changes`, and `undo_mcp_change` (about 2,350 together). The three method tools are listed only when `FRAPPE_MCP_METHODS` is set, so the 40,000 cap applies to the list without them; with them the cap is 42,500 (they add about 1,600 characters). `run_bench` is listed only when `FRAPPE_BENCH_PATH` is set: the cap with it is 41,500 (it adds about 1,200 characters), and 44,000 with both opt-in sets. `mcp-frappe check` prints the tools and characters per toolset, and `FRAPPE_MCP_TOOLSETS` turns off what you don't need.

The input schemas leave out what carries no meaning: pydantic's generated `title` keywords, `additionalProperties: true`, and the `null` branch and `default: null` of optional arguments (an optional argument is simply not `required`; an explicit `null` is still accepted). Tools have no output schema, because Frappe's results are open-ended JSON: a result is a JSON text block, and a list result (only `list_installed_apps`) is one text block per item.

## Errors

A Frappe error becomes a tool error whose text is readable, for example:

```text
Frappe HTTP 417 MandatoryError: Error: Value missing for Lab Sample: Title
Hint: Missing: Title. Required fields are empty; map the labels to fieldnames with get_doctype_schema and add them to the values.
```

When the site can't be reached, the error names the site and what to check instead:

```text
Cannot reach the Frappe site at http://127.0.0.1:8000: connection refused. Is the site running? Run `mcp-frappe check` to test the connection.
```

The failure is one of `connection refused`, `no connection within N s` (connecting gives up after 5 seconds, or `FRAPPE_TIMEOUT` if smaller), `no answer within N s` (`FRAPPE_TIMEOUT`), `the host name could not be resolved`, or `the connection was closed before Frappe answered`. The site is shown as `scheme://host[:port]`, without credentials, path, or query.

For a Frappe error, the text is `Frappe HTTP <status> <exception class>: <message>`. The message comes from Frappe's `_server_messages` (a JSON list of JSON objects): each `message` is decoded and its HTML tags are removed. When there are none, the last line of the exception is used without its module path. Frappe's traceback (`exc`) is never included. `PermissionError`, `DoesNotExistError`, `MandatoryError` (with the missing labels Frappe names), `LinkValidationError`, `DuplicateEntryError`, `TimestampMismatchError`, and `ValidationError` add a `Hint:` line saying what to do next; a `PermissionError` means asking the user, not retrying another way. The whole text is cut to 2,000 characters, keeping the hint.

## Discovery and records

| Tool | Purpose | Important arguments |
|---|---|---|
| `get_site_info` | Say who is connected and what the site runs: user, app titles and versions, Frappe major version, and which site apps have a checkout under `FRAPPE_APPS_PATH` | none |
| `list_installed_apps` | Group Module Def records by owning app | none |
| `list_doctypes` | List site DocTypes a page at a time; each row has `name`, `module`, `custom`, `issingle`, `istable`, `is_submittable` | `search` (part of the name), `app`, `module`, `include_child_tables`, `limit` (max 500, default 100), `offset` |
| `get_doctype_schema` | Read one DocType's metadata as Frappe's form sees it, Custom Fields and Property Setters applied | `doctype`, `detail` (`compact` or `full`) |
| `list_documents` | List records through Frappe REST, a page at a time | `doctype`, `filters` (object or list), `or_filters`, `fields` (max 40), `order_by`, `limit` (max 100), `offset` |
| `count_documents` | Count the records the user may read | `doctype`, `filters` |
| `search_records` | Find records the way a Link field's dropdown does (`frappe.desk.search.search_link`) | `doctype`, `text`, `filters`, `limit` (max 50) |
| `get_document` | Read a record through Frappe REST; always returns `doctype`, `name`, `modified`, and `docstatus` | `doctype`, `name`, `fields`, `child_row_limit` (max 200) |
| `list_reports` | List enabled reports a page at a time: `name`, `ref_doctype`, `report_type`, `module`, `is_standard` | `search` (part of the name), `ref_doctype`, `limit` (max 100, default 50), `offset` |
| `get_report_filters` | Read the filters a report takes: its Report Filter rows and its JavaScript, cut to about 8,000 characters | `report_name` |
| `run_report` | Run a Query, Script, or Custom report through Frappe's Query Report endpoint (it returns no rows for Report Builder reports; `upsert_report` returns a sample of those) | `report_name`, `filters`, `limit` (max 500), `offset`, `include_chart` |

The generic resource API is the common access path for standard and custom app DocTypes. A custom DocType or field that the connected user cannot read remains inaccessible.

### Pages

`list_documents` and `list_doctypes` return one page:

```json
{"rows": [{"name": "TODO-0001"}], "offset": 0, "limit": 20, "has_more": true}
```

The tool asks Frappe for `limit + 1` rows and returns `limit`; the extra row only sets `has_more`. To read the next page, call again with `offset` set to `offset + limit`. Records added or removed between calls can shift the pages; sort on a stable column (`creation`, `name`) when paging through a changing list.

### Sorting, OR filters, counts, and search

- `order_by` is `<fieldname> [asc|desc]`, up to three joined by commas, for example `"posting_date desc, name asc"`. Each field must be a stored field or standard column of the DocType (not a layout or Table field); the direction defaults to `asc`. The tool rebuilds the string it sends from those parts, so `name; drop table` and backquoted names are rejected. The default is `modified desc`.
- `or_filters` takes the same shapes and operators as `filters`. A record must match every `filters` condition and at least one `or_filters` condition.
- `count_documents` calls `frappe.client.get_count` with the same `filters` validation and returns `{"doctype", "count"}`. Frappe counts only records the user may read.
- `search_records` calls `frappe.desk.search.search_link` with the DocType's own search fields and title field, as a Link field does. It never sends a custom `query` method or `ignore_user_permissions`. `text` may be empty (up to 140 characters); `filters` uses the `list_documents` shapes. It returns `{"doctype", "results": [{"value", "label", "description"}]}`, where `value` is the record name and empty keys are left out.
- `list_doctypes(search=...)` matches part of the DocType name, case-insensitively (`["name", "like", "%text%"]`). The text may hold letters, digits, spaces, `_`, `.`, and `-`; `_` matches any one character.

### Reports

`list_reports` reads the `Report` list (`disabled = 0`, sorted by name) and returns the `list_documents` page shape. In Frappe v16 every Desk user may read `Report` (the `Desk User` role has read access); a website-only user is refused, so ask the user for the report name. The list does not check each report's own roles; `run_report` does, and refuses a report the user may not run.

`get_report_filters` calls `frappe.desk.query_report.get_script` and returns:

```json
{"report_name": "Open Tasks by Owner", "filters": [{"fieldname": "owner", "label": "Owner", "fieldtype": "Link", "options": "User", "mandatory": 1}], "script": "frappe.query_reports[\"Open Tasks by Owner\"] = {}", "script_truncated": false}
```

`filters` holds the Report Filter rows a Query Report keeps on its record (empty keys left out). Script Reports declare their filters in the JavaScript instead, in the `filters: [...]` array, and the model can read them there; `script` is cut to 8,000 characters (`script_truncated`). The report's print format (`html_format`) is never returned.

`run_report` passes `filters` as `{fieldname: value}`. Frappe computes the whole result, so `offset` and `limit` page through it locally: `result` holds that page, `total_rows` counts every row, `returned_rows` the page, and `has_more` (also `truncated`) says whether rows remain. The `chart` is dropped unless `include_chart` is true.

### Site information

`get_site_info` returns:

```json
{
  "site_label": "erp-dev",
  "user": "developer@example.localhost",
  "apps": [{"app": "frappe", "title": "Frappe Framework", "version": "16.33.1"}],
  "frappe_major_version": 16,
  "mcp_guidance": "Frappe v16+: if enabled on the site and configured in the MCP host, native Frappe MCP may handle data; keep standalone mcp-frappe for customize, developer, and bench tools.",
  "apps_path": {"configured": true, "site_apps_found": ["lab_app"], "site_apps_missing": ["frappe"]},
  "notes": ["Frappe exposes developer mode to Desk only ..."]
}
```

`site_label` is `FRAPPE_SITE_LABEL`, or `null` when it is unset: a name for checking which connection this is, never used to pick a site. `user` comes from `frappe.auth.get_logged_user` and `apps` from `frappe.utils.change_log.get_versions`, cut to `title` and `version` (branch and commit details are dropped). `frappe_major_version` is parsed from the Frappe app version. `mcp_guidance` recommends standalone tools for v15, native data tools plus standalone developer tools for v16+ when native MCP is enabled and configured, and standalone tools with the compatibility matrix when the version is unknown. It is guidance only: this process cannot configure or invoke another MCP server. `apps_path` is `{"configured": false}` without `FRAPPE_APPS_PATH`, `{"configured": true, "exists": false}` when the folder is missing, and otherwise lists the site's apps with and without a checkout there, using the same containment checks as the file tools. No local path is returned.

Developer mode is not reported. Frappe v16 puts it only in the boot data embedded in the Desk page, and no whitelisted method returns it. Tools that need it (`update_doctype`, standard `create_doctype`, standard reports and Workspaces) explain Frappe's error when it is off.

`list_installed_apps` stays: it lists each app's modules, which `create_doctype`, `upsert_report`, and `upsert_workspace` need, and which `get_versions` does not give.

### Metadata and who can use the record tools

The record tools (`list_documents`, `get_document`, `create_document`, `update_document`) and `get_doctype_schema` read DocType metadata from Frappe's form loader, `frappe.desk.form.load.getdoctype`. It returns `frappe.get_meta` for the DocType and its child tables, with Custom Fields, Property Setters, and Frappe's own field order already applied, to any logged-in user. The record tools never read the `DocType`, `Custom Field`, or `Property Setter` resources, which only System Manager can list. So they work for any user who has permission on the DocType; Frappe still checks that permission on every record read and write.

A live run as a user with only the Sales User role (Frappe 15.126 and 16.55, [evidence](compatibility-and-validation.md#matrix)) confirmed this: `get_site_info`, `list_installed_apps`, `get_doctype_schema`, `list_documents`, `count_documents`, `search_records`, `list_reports`, `get_report_filters`, `run_report`, and `get_workspace` work. Three developer reads are refused by Frappe with `PermissionError`, because they read records only System Manager (or the matching manager role) can list: `list_doctypes` (`DocType`), `get_workflow` (`Workflow`), and `list_notifications` (`Notification`). Use a developer account for those.

Metadata is cached in the MCP process for `FRAPPE_META_TTL` seconds (default 300; `0` turns the cache off). `create_doctype`, `update_doctype`, `delete_doctype`, `create_custom_field`, `set_field_property`, and `set_role_permissions` drop the affected DocType, and any DocType that uses it as a child table, from the cache. Deleting a `Custom Field`, `Property Setter`, or `Custom DocPerm` record with `delete_document` clears the whole cache; the record tools write no other schema records (see [record-tool limits](#record-tool-limits)). A change made outside the MCP (in Desk, say) shows up once its cache entry expires.

### Schema detail

`get_doctype_schema` returns the compact form unless `detail` is `full`:

- `compact` (default): the DocType flags `name`, `module`, `istable`, `issingle`, `is_submittable`, `is_tree`, `autoname`, `title_field`, and `custom`, then `fields` with the data fields only. Layout and display fields (Section Break, Column Break, Tab Break, HTML, Button, Fold, Heading, Image, Attachment Gallery) are left out. Each field has only `fieldname`, `label`, `fieldtype`, `options`, `reqd`, `read_only`, `hidden`, `default`, `unique`, and `permlevel`. Keys whose value is false, `0`, or empty are left out. `child_tables` maps each Table and Table MultiSelect field's DocType to its fields in the same form.
- Both forms say which Workflow is active on the DocType: `workflow` in the compact form (left out when there is none), and `"workflow": <name or null>` in the full form. Frappe's form loader returns it to any logged-in user.
- `full`: `{"doctype": <meta>, "children": {<child DocType>: <meta>}, "workflow": ...}` with every property Frappe returns (nulls already dropped by Frappe), including layout fields, permissions, links, and actions. Form assets such as client script source and print formats are left out.

```json
{"name": "ToDo", "module": "Desk", "fields": [{"fieldname": "status", "label": "Status", "fieldtype": "Select", "options": "Open\nClosed\nCancelled", "default": "Open"}]}
```

### Default `get_document` output

Without `fields`, `get_document` returns the DocType's data fields that have a value: `null`, `""`, and empty tables are left out, and `0` is kept. Child rows keep only their data fields with a value, plus `name` and `idx`. With `fields`, it returns exactly those fields, and child rows come back whole. Either way, Table and Table MultiSelect fields return at most `child_row_limit` rows, and `_truncated_child_tables` lists the cut fields.

### Readable fields

`fields` and filters accept the DocType's fields (including Custom Fields) plus these standard columns: `name`, `owner`, `creation`, `modified`, `modified_by`, `docstatus`, and `idx`. Filters on a child table may also use `parent`, `parenttype`, and `parentfield`. Any other name is rejected before the request is sent.

```json
{"doctype": "Sales Invoice", "fields": ["name", "modified", "docstatus"], "filters": {"docstatus": 1}}
```

### Filter shapes

`list_documents` accepts the filter shapes Frappe's REST API accepts:

| Shape | Example |
|---|---|
| Object, equality | `{"status": "Open"}` |
| Object, operator | `{"grand_total": [">", 100]}` |
| List of `[field, operator, value]` | `[["status", "=", "Open"], ["creation", ">=", "2026-10-01"]]` |
| List of `[doctype, field, operator, value]` | `[["Sales Invoice Item", "item_code", "=", "ITEM-001"]]` |

All conditions are combined with AND. In the four-element form, `doctype` must be the listed DocType itself or the `options` of one of its `Table` or `Table MultiSelect` fields, and the field must exist on that DocType; the filter then matches parents with at least one such child row.

### Filter operators

`=`, `!=`, `<`, `>`, `<=`, `>=`, `like`, `not like`, `in`, `not in`, `between`, `is`, `timespan`. Operators are matched case-insensitively; anything else is rejected with the allowed list.

| Operator | Example |
|---|---|
| `like` / `not like` | `["title", "like", "%invoice%"]` |
| `in` / `not in` | `["status", "in", ["Open", "Overdue"]]` |
| `between` | `["posting_date", "between", ["2026-10-01", "2026-10-31"]]` |
| `is` | `["due_date", "is", "set"]` (or `"not set"`) |
| `timespan` | `["creation", "timespan", "this week"]` (also `today`, `last month`, `this year`, ...) |

## Mutations

| Tool | Purpose | Confirmation |
|---|---|---|
| `create_document` | Create a record using native DocType validation; record data only (see [record-tool limits](#record-tool-limits)) | First call with `confirm: false`; inspect, then confirm |
| `create_documents` | Create 1-100 records of one DocType in one all-or-nothing request, for test and seed data ([batch inserts](#batch-inserts)) | `doctype`, `rows`; preview, then confirm with `preview_id` |
| `update_document` | Update a record using the native resource API; record data only | Confirm with `expected_modified` from the preview |
| `delete_document` | Delete a record | Confirm with `expected_modified` from the preview |
| `submit_document` | Submit a draft (docstatus 0) of a submittable DocType, to test its controller | Confirm with `expected_modified` from the preview |
| `cancel_document` | Cancel a submitted document (docstatus 1) | Confirm with `expected_modified` from the preview |
| `create_doctype` | Create a standard app DocType or a site-custom DocType through Frappe's native controller | `fields`, `is_submittable`, `is_child_table`, `is_single`, `is_tree`, `is_custom`, `properties`, and `permissions`; preview, inspect, then confirm |
| `update_doctype` | Change an existing DocType of your own app, or a site-custom DocType: add, change, remove, and move fields, and set DocType settings | `doctype`, `operations`; preview, then confirm with the same operations and `expected_modified` |
| `delete_doctype` | Delete a DocType created by mistake during development | Preview, then confirm with `expected_modified` |
| `rename` | Rename a record, a Custom Field's fieldname, a field of your app's DocType (with a generated patch), or your app's DocType, through Frappe's own rename code | `kind`, `doctype`, `old`, `new`, optional `merge`; preview, then confirm with `preview_id` |
| `promote_custom_doctype` | Turn a site-custom DocType into a standard DocType of an app module, so Frappe writes its files into the app | `doctype`, `app`, `module`; preview, then confirm with `expected_modified` |
| `set_role_permissions` | Add or update role permissions through Frappe's native Permission Manager | `doctype`, `role`, `permissions`, `permlevel`; preview, then confirm |
| `create_custom_field` | Add a Custom Field | `doctype`, `field`, optional `app`/`module`; preview, then confirm |
| `set_field_property` | Create or update the Property Setter for an allowlisted form property | `doctype`, `property`, `value`, optional `fieldname`/`property_type`/`app`/`module`; preview, then confirm (with `expected_modified` when the setter exists) |
| `customize_doctype` | Customize a Frappe or ERPNext DocType in one Customize Form save: properties, field order, new Custom Fields, related fieldtype changes | `doctype`, `operations`; preview, then confirm with `preview_id` |
| `export_form_customizations` | Preview then call Frappe's native Customize Form exporter for one DocType and its child tables | `app`, destination `module`, `doctype`, optional `sync_on_migrate`/`with_permissions`; preview, then confirm with the current `preview_id` |
| `export_customizations` | Export site customizations to Frappe's native `custom/` files (and Client Scripts and, with `with_workflows`, Workflows to app fixtures) so `bench migrate` applies them | `app`, `module`, `doctypes`, optional `with_permissions`/`apply_module_filter`/`sync_on_migrate`/`with_workflows`; confirm with the current `preview_id` |
| `rollback_export` | Restore or remove files from one prior export | Preview, then confirm with the current rollback `preview_id` |
| `undo_mcp_change` | Undo one change this server made, from its journal ([undo](#change-journal-and-undo)) | `entry_id`; preview, then confirm with `preview_id` |

Frappe still decides whether the user may perform the operation. A preview is not a substitute for checking permissions, and a confirmed mutation may trigger native hooks or side effects.

`create_document` and `update_document` accept only the DocType's own fields: standard columns (`name`, `owner`, `docstatus`, `modified`, child-table `parent` columns, ...) and read-only fields are rejected. The one exception is `name` on `create_document` when the DocType's `autoname` is `Prompt` (naming rule "Set by user"); it is sent to Frappe as `__newname`, the way Desk does. In `create_document` and `create_documents`, each child-table value must be a list of row objects whose keys are fields of the child DocType (plus an optional `doctype` naming it); standard columns such as `parent` or `idx` are refused there too. Read-only child fields stay allowed, because mapped drafts set them (an item's link back to its order).

### Batch inserts

`create_documents(doctype, rows, expected_preview, confirm)` creates 1 to 100 records of one DocType in a single request, for seeding a development site with test data. Each row is checked like `create_document`'s `values` (record-tool limits, writable fields, child rows, no `Code` fields, `name` only for Prompt naming) before anything is sent. The preview returns `count`, `fields` (every key used by a row), `problems` (`[{"row": n, "problem": ...}]`, rows counted from 1, duplicate prompted names included), and `preview_id`, a SHA-256 of the DocType and rows. While any row has a problem the preview has no `preview_id`, and confirming is refused.

Confirming with the same rows and `expected_preview` posts them to `frappe.client.insert_many`, which inserts each one with `frappe.get_doc(row).insert()`, so every DocType controller runs. Frappe commits a request only when it ends without an exception and rolls it back otherwise (`frappe.app.application`, v15 and v16), so a row that fails leaves nothing behind: the error starts "Nothing was created; Frappe rolled back the whole batch" and keeps Frappe's message and exception type. The exception is a controller or hook that commits on its own (`frappe.db.commit()`), or DDL on MariaDB, which commits implicitly; ordinary records do neither. `insert_many` allows 200 rows; the tool's limit is 100. The result is `{"created", "doctype", "names"}` in row order, plus `mcp_change` when record writes are journaled.

### Record-tool limits

The record tools are for record data. Their checks come before any request to the site:

| DocType | `create_document` / `update_document` | `delete_document` |
|---|---|---|
| `DocType` | refused: use `create_doctype` / `update_doctype` (or `create_custom_field` and `set_field_property` for Frappe and ERPNext DocTypes) | refused: use `delete_doctype` |
| `DocField` | refused: use `update_doctype`, or `create_custom_field` and `set_field_property` | refused, same tools |
| `DocPerm` | refused: use `set_role_permissions` | refused, same tool |
| `Custom Field` | refused: use `create_custom_field` / `set_field_property` | allowed |
| `Property Setter` | refused: use `set_field_property` | allowed |
| `Custom DocPerm` | refused: use `set_role_permissions` | allowed |
| `Customize Form` | refused: use `customize_doctype`, `create_custom_field`, `set_field_property`, or `set_role_permissions` | refused, same tools |
| `Workflow`, `Notification`, `Report`, `Workspace` | refused: use `upsert_workflow`, `upsert_notification`, `upsert_report`, `upsert_workspace` | allowed |
| Records that hold code: Address Template, Assignment Rule, Automation Flow, Client Script, Custom HTML Block, Email Template, Letter Head, Navbar Settings, Print Format, Print Format Field Template, Print Style, Scheduled Job Type, Server Script, System Console, Web Form, Web Page, Web Template, Webhook, Website Script, Website Theme | refused | refused |

The deletes that stay allowed have no other tool, and a delete writes no code. In any other DocType, `create_document` and `update_document` refuse a value for a field of type `Code`, in child-table rows too; the type is the one `getdoctype` returns, after Custom Fields and Property Setters. Code fields that hold plain data stay writable: Translation `source_text` and `translated_text`, ERPNext's `item_tax_rate` on Purchase Invoice, Purchase Order, Purchase Receipt, Quotation, Sales Order, and Supplier Quotation items, and Purchase Order `customer_contact_email`. Auto Repeat `subject` and `message`, which Frappe renders as Jinja, are refused. Set refused values in Desk or in your app's files.

`update_document` checks `expected_modified` before writing and also sends it in the update, so Frappe compares it with the stored value under a row lock. If the document changed in between, the update fails with "Document changed since preview"; request a fresh preview.

`submit_document` and `cancel_document` exist so a developer can test a submittable DocType's controller; amend and workflow actions on documents are not available. Both read the DocType's metadata fresh (not from the cache) and refuse:

- a DocType that is not submittable (`is_submittable` unset);
- a DocType with an active Workflow. Frappe does not block a direct submit or cancel there: `set_workflow_state_on_action` moves the document to the first workflow state with the new docstatus, skipping the workflow's transitions and roles. So the tools refuse, and the workflow stays in control;
- a document in the wrong state: submit needs docstatus 0, cancel needs docstatus 1.

The preview shows `docstatus`, `modified`, and warnings: submitting runs `validate`, `before_submit`, and `on_submit`; cancelling runs `before_cancel` and `on_cancel`, which may reverse ledger or stock entries, and Frappe refuses to cancel while submitted documents link to this one. On confirm, `submit_document` re-reads the document, checks `expected_modified`, and posts the whole document to `frappe.client.submit`, which rebuilds it from that dict, so Frappe's own timestamp check refuses it if it changed in between ("Document changed since preview"). `frappe.client.cancel` takes only the DocType and name, so `cancel_document` relies on the `expected_modified` check just before it. Both return `{"submitted"|"cancelled": true, "doctype", "name", "docstatus", "modified"}`.

`create_doctype` accepts native DocField objects in `fields`, so Frappe's DocType controller validates field types and properties. DocType-level values such as `autoname`, `track_changes`, `title_field`, `search_fields`, `sort_field`, and `sort_order` go in `properties`; the MCP restricts this object to recognized DocType settings. Role rules go in `permissions`.

By default, `is_custom: false` creates a standard DocType. It requires Developer Mode, and the app/module must exist on the site and under `FRAPPE_APPS_PATH`. Frappe writes the standard JSON and controller scaffolding; it also creates JavaScript/test scaffolds for non-child DocTypes. The MCP stages Frappe's own templates before the metadata request and reconciles a development-server restart against the site's DocType record. It refuses source writes to the protected `frappe` and `erpnext` apps, and existing DocType files. When the app already has the DocType's JSON file but the site has no such DocType (`list_app_drift` reports `missing_on_site`), the error says to run `bench migrate`, which loads it from the file.

Set `is_custom: true` to create a site-custom DocType. Frappe stores that definition in site metadata and does not generate app JSON/Python/JavaScript files; this path still requires an existing remote Module Def belonging to `app`, but does not require a local checkout, `FRAPPE_APPS_PATH`, or Developer Mode. Frappe rejects custom Virtual DocTypes. The live v15 matrix verified site-custom metadata and record CRUD; see the [manual report](../e2e/history/manual_functional_report.md).

### Editing DocTypes

`update_doctype` changes a DocType of your own app (a standard DocType, which needs Developer Mode) or a site-custom DocType (`custom = 1`). It refuses standard DocTypes of `frappe` and `erpnext`; customize those with [`customize_doctype`](#customize-form-saves), `create_custom_field`, and `set_field_property`. The tool reads the DocType record, applies `operations` in order to a copy, and saves the whole field list in one request. Fields that are not touched keep their row `name`, so Frappe updates them in place instead of recreating them.

| Operation | Shape | Notes |
|---|---|---|
| `add_field` | `{"op": "add_field", "field": {...}, "insert_after": "fieldname"}` | `field` needs `fieldname` (lowercase letters, digits, `_`, starting with a letter) and `fieldtype`. Without `insert_after` the field goes last; `null` puts it first. The fieldname may not exist on the DocType, Custom Fields included. |
| `update_field` | `{"op": "update_field", "fieldname": "...", "set": {...}}` | `fieldname` can't be changed here; use [`rename`](#renames) with `kind: "field"`. |
| `remove_field` | `{"op": "remove_field", "fieldname": "..."}` | See the data note below. |
| `move_field` | `{"op": "move_field", "fieldname": "...", "insert_after": "fieldname"}` | `insert_after: null` moves the field to the top. |
| `set_properties` | `{"op": "set_properties", "set": {...}}` | DocType settings from the same allowlist as `create_doctype`'s `properties`, except the tables `actions`, `links`, and `states`. |

```json
{
  "doctype": "Lab Sample",
  "operations": [
    {"op": "add_field", "insert_after": "title",
     "field": {"fieldname": "customer", "fieldtype": "Link", "options": "Customer",
               "label": "Customer", "reqd": 1}},
    {"op": "update_field", "fieldname": "status", "set": {"in_list_view": 1}},
    {"op": "move_field", "fieldname": "notes", "insert_after": "customer"},
    {"op": "set_properties", "set": {"track_changes": 1}}
  ]
}
```

Field values may be DocField properties such as `label`, `fieldtype`, `options`, `reqd`, `hidden`, `read_only`, `default`, `description`, `in_list_view`, `in_standard_filter`, `unique`, `fetch_from`, `precision`, `length`, `permlevel`, and the other DocField settings; values are strings, numbers, booleans (stored as `0`/`1`), or `null`. `depends_on`, `mandatory_depends_on`, and `read_only_depends_on` are accepted, as in `set_field_property`. `collapsible_depends_on`, `ignore_xss_filter`, and `options` on HTML and Button fields are refused, because Frappe evaluates or renders them as code. Every fieldtype needs to be one Frappe knows; Link, Select, Table, and Table MultiSelect need `options`; Link and Table targets must exist, and a Table or Table MultiSelect target must be a child table DocType.

The preview returns:

- `changes`: `added` (with `insert_after`), `removed`, `moved`, `changed` (per field, `{property: {"from": old, "to": new}}`), and `properties` (DocType settings, in the same form);
- `warnings`: removed fields and how many records have a value in them, fieldtype changes, `reqd` added to a field that records leave empty (with the count), and `unique` added;
- `modified`, which confirmation must send back as `expected_modified`;
- for standard DocTypes, `app_files` (the DocType JSON Frappe rewrites, resolved under `FRAPPE_APPS_PATH` when that is set) and `requires_developer_mode`.

Removing a field doesn't delete data: Frappe keeps the database column and its values until `bench trim-tables`, but forms, reports, and the API stop showing them. A fieldtype change between related types (the groups Customize Form allows, such as Data/Small Text/Text or Currency/Float/Percent) only warns. Any other change of a field that holds data is refused; clear the values or add a new field. Counts come from `frappe.client.get_count` and follow the user's permissions; Single and Virtual DocTypes have no counts.

Confirmation re-reads the DocType, refuses if `modified` moved, and sends `modified` in the save so Frappe checks it again under a row lock. If a developer-mode Bench reloads during the save, the tool waits for the site, re-reads the DocType, and reports `reconciled_after_server_reload: true` only when the saved DocType holds the change; it never sends the change twice. The DocType's metadata, and that of any child table whose Table field changed, is dropped from the cache.

`delete_doctype` deletes a DocType created by mistake. It refuses `frappe` and `erpnext` DocTypes and any DocType that another DocType uses through a Link, Table, or Table MultiSelect field, Custom Fields included (the error lists them). The preview gives the record count and says what Frappe removes (the DocType record with its fields and permissions, Table-type Custom Fields that point to it, its global search entries, and for a standard DocType in Developer Mode its whole folder in the app, controller code included) and what it keeps (the `tab<DocType>` database table with its records, and the Custom Fields and Property Setters defined on the DocType, counted in `customizations_left`). Only Administrator can delete a standard DocType. Confirm with `expected_modified`. If the connection drops and the DocType still exists, an app folder that Frappe removed is put back from a copy taken before the request; if Frappe left the folder partly changed, it is kept as it is and the copy goes to `<app>/.mcp-frappe-backups/doctype-recoveries/<id>/original`.

`create_custom_field` and `set_field_property` are database-backed site customizations. They need no local app checkout, `FRAPPE_APPS_PATH`, or Developer Mode. `app` and `module` are optional: `module` only tags the record for a later export. When `module` is given, its Module Def must exist on the site; when `app` is given too, the module must belong to it. `app` alone is rejected.

`create_custom_field` takes the Custom Field values in `field` (`fieldname`, `fieldtype`, `label`, `insert_after`, ...). The target DocType and module come only from the arguments: a `field` that contains `doctype`, `dt`, or `module` is rejected. A `fieldname` that already exists on the DocType is rejected. To change a Custom Field afterwards, use `set_field_property`; to remove it, `delete_document` on the `Custom Field` DocType, whose record name is `<DocType>-<fieldname>`.

`set_field_property` sets `property` on `fieldname`, or on the DocType itself when `fieldname` is omitted. The supported properties and their types follow Customize Form:

| Type | Properties | Accepted `value` |
|---|---|---|
| `Check` | `hidden`, `read_only`, `reqd`, `in_list_view`, `in_standard_filter`, `collapsible`, `bold` | `0`, `1`, `"0"`, `"1"`, or a boolean; stored as `"0"`/`"1"` |
| `Data` | `label`, `width`, `depends_on`, `mandatory_depends_on`, `read_only_depends_on` | a string |
| `Text` | `options`, `description`, `default` | a string |

`property_type` is inferred from this table; a value passed in must match it. The tool first looks for an existing Property Setter for the same DocType, field, and property:

- None exists: the preview has `mode: "create"` and confirmation creates one. A new DocType-level setter makes Frappe delete the field-level setters for the same property on that DocType; the preview lists those fields in `replaces_field_setters`.
- One exists: the preview has `mode: "update"`, the setter's `name`, `current_value`, the new `value`, and `modified`. Confirm with `expected_modified` set to that `modified`; the setter is updated in place with the same row-locked timestamp check as `update_document`.

`set_role_permissions` uses Frappe's System Manager-only Permission Manager methods. The connected account must have the System Manager role, and the target DocType and role must already exist. The tool does not create roles or assign roles to users.

The preview is not a client-side concurrency token: this tool accepts no expected `modified` value. On isolated Frappe 16.34.0, two independent MCP processes previewed and confirmed competing updates; Frappe accepted one and rejected the stale save with `TimestampMismatchError`, preserving the row identity and accepted flags ([runner](../e2e/run_standalone_role_permission_concurrency.py), [evidence](../e2e/evidence/2026-11-24-v16-role-permission-concurrent-update.json)). This live behavior is verified on v16 only. Keep permission writes isolated or pause other writers, and handle a stale-write error by rereading and previewing again. When the rule is absent, confirmation follows Frappe's native `add` path and creates permission configuration.

### Customize Form saves

`customize_doctype` customizes a standard DocType (Frappe's, ERPNext's, or another app's) the way Desk's Customize Form does, in one save. It loads the form with `fetch_to_customize` and saves it with `save_customization`, both through `/api/method/run_doc_method`, the endpoint Desk uses (checked in Frappe v15.126.0 and v16.55.1, where it takes the same `docs` and `method` arguments). The load is a `GET`, so Frappe keeps no writes from it; the save is a `POST` of the whole Customize Form document with the operations applied. Frappe then compares every value with the DocType's own, creates Property Setters only for the differences, deletes setters that a change makes redundant, keeps the field order, creates and updates Custom Fields, and validates the whole DocType together. Customize Form needs the System Manager role; Frappe checks it. It refuses site-custom DocTypes (use `update_doctype`), Single DocTypes, and Frappe's core DocTypes.

| Operation | Shape | Notes |
|---|---|---|
| `set_property` | `{"op": "set_property", "fieldname": "...", "property": "...", "value": ...}` | Without `fieldname` it sets a DocType setting. |
| `move_field` | `{"op": "move_field", "fieldname": "...", "insert_after": "fieldname"}` | `null` moves the field to the top. |
| `add_custom_field` | `{"op": "add_custom_field", "field": {"fieldname": "...", "fieldtype": "...", "label": "...", ...}, "insert_after": "..."}` | Frappe derives the saved name as `custom_` plus the label with spaces changed to `_`, characters other than letters, digits, and `_` removed, and lowercased. Letters outside ASCII are kept, as in Desk: the label `Código` gives `custom_código`. `fieldname` must match that result, have a letter or digit after `custom_`, and be at most 64 characters, or the preview refuses before writing. For a name of your own (an ASCII `custom_codigo` with the label `Código`, say), use `create_custom_field`. Without `insert_after` the field goes last. |
| `change_fieldtype` | `{"op": "change_fieldtype", "fieldname": "...", "fieldtype": "..."}` | Only within Customize Form's `ALLOWED_FIELDTYPE_CHANGE` groups: Currency/Float/Percent, Small Text/Data, Text/Data, Text/Text Editor/Code/Signature/HTML Editor, Data/Select, Text/Small Text/Long Text, Text/Data/Barcode, Code/Geolocation, Table/Table MultiSelect. |

Field properties are Customize Form's `docfield_properties` that `update_doctype` also allows: `label`, `options`, `description`, `default`, `hidden`, `read_only`, `reqd`, `bold`, `collapsible`, `in_list_view`, `in_standard_filter`, `in_global_search`, `in_preview`, `print_hide`, `print_hide_if_no_value`, `report_hide`, `no_copy`, `unique`, `permlevel`, `ignore_user_permissions`, `allow_on_submit`, `translatable`, `depends_on`, `mandatory_depends_on`, `read_only_depends_on`, `fetch_from`, `fetch_if_empty`, `precision`, `length`, `columns`, `width`, `print_width`, `placeholder`, `link_filters`, `sort_options`, `show_dashboard`, `non_negative`, `remember_last_selected_value`, `allow_bulk_edit`, `allow_in_quick_entry`, `hide_border`, `hide_days`, `hide_seconds`, and on v16 `alignment`, `button_color`, `mask`, and `set_only_once`. DocType settings: `title_field`, `search_fields`, `image_field`, `sort_field`, `sort_order`, `default_print_format`, `default_view`, `autoname`, `naming_rule`, `track_changes`, `track_views`, `allow_copy`, `allow_import`, `allow_auto_repeat`, `quick_entry`, `editable_grid`, `queue_in_background`, `max_attachments`, `make_attachments_public`, `protect_attached_files`, `show_name_in_global_search`, `show_preview_popup`, `show_title_field_in_link`, `email_append_to`, `subject_field`, `sender_field`, `default_email_template`, `is_calendar_and_gantt`, `force_re_route_to_default_view`, `translated_doctype`, `grid_page_length`, `rows_threshold_for_grid_search`, and on v16 `hide_toolbar`. Check values are `0`/`1` or booleans, Int values integers, the rest strings. `collapsible_depends_on`, `ignore_xss_filter`, and `options` on HTML and Button fields are refused, as in `update_doctype`.

Before sending anything, the tool refuses what Customize Form would skip with a message on a standard field: enabling Allow on Submit, making a mandatory field optional, making a read-only field editable, showing a no-value field in the list view, `translatable` on a type that can't be translated, and `options` on anything but Data and Read Only fields (Frappe does not let Customize Form change a standard Select field's options; `set_field_property` writes that setter without the check).

The preview simulates the save and returns:

- `property_setters`: each setter the save creates, updates, or removes (`{"action", "fieldname", "property", "from", "to"}`). A value equal to the DocType's own value removes the existing setter instead of writing a new one. A DocType setting replaces every setter of that property, field-level ones included. Whenever the new field order differs from the DocType's own order, Frappe stores it in a `field_order` setter; a DocType with Custom Fields always gets one on its first Customize Form save, as in Desk.
- `custom_fields`: Custom Fields to create (with `insert_after`) and existing ones to update (`set`, with `from`/`to` per property, `insert_after` included when a move changes it). Customize Form saves non-system Custom Fields directly, not through setters.
- `warnings`: fieldtype and length changes (Frappe refuses the save if stored values don't fit), `reqd` with the count of empty records, and `unique`.
- `preview_id`: a hash of the planned changes, the operations, the DocType's `modified`, and every Property Setter and Custom Field of the DocType. Confirmation recomputes it and refuses a mismatch.

The result lists the Property Setters that actually changed (read back after the save), the planned Custom Field changes, Frappe's messages, and a warning naming any previewed change Frappe didn't apply.

Use `set_field_property` for a single Property Setter with no Customize Form checks, and `create_custom_field` for one Custom Field with `module` (for a later `apply_module_filter` export). Use `customize_doctype` for several changes at once, for moves and fieldtype changes, and whenever the result must match a Customize Form save.

### Renames

`rename(kind, doctype, old, new, merge=False)` uses Frappe's own rename code, so references move with the name. Every kind previews first and confirms with `preview_id`, a hash of the preview (which includes the record's or DocType's `modified`).

| `kind` | What | Frappe call | Refused |
|---|---|---|---|
| `document` | Record `old` of `doctype` becomes `new`; `merge=true` merges it into an existing `new` | `frappe.model.rename_doc.update_document_title` (v15 and v16) | DocTypes without Allow Rename, Single and child DocTypes, schema DocTypes and records that hold code; `new` already taken (without `merge`) or missing (with `merge`) |
| `custom_field` | A Custom Field's fieldname on `doctype` | `frappe.custom.doctype.custom_field.custom_field.rename_fieldname` (System Manager; v15 and v16) | standard fields, system-generated Custom Fields, a taken fieldname |
| `field` | A field of your app's standard DocType | saves the DocType with the new fieldname, after writing a patch (below) | `frappe`/`erpnext` DocTypes, site-custom DocTypes (no app holds the patch), a taken fieldname, no checkout under `FRAPPE_APPS_PATH` |
| `doctype` | Your app's DocType `old` becomes `new` (pass `doctype: "DocType"`) | `update_document_title` on the DocType record | `frappe`/`erpnext` DocTypes, an existing `new`, `merge` |

The `document` preview counts, per Link field that points to the DocType (Custom Fields included, up to 50 fields), the records that hold `old`; `linked_records` is the total. Frappe also updates Dynamic Links, child rows, attachments, versions, and assignments, and may adjust the new name through naming rules or a `before_rename` hook; the result's `name` is the name Frappe kept.

The `custom_field` preview counts the records with a value and lists what Frappe's `rename_fieldname` does not update: Property Setters on the field, a `field_order` setter that names it, DocType settings such as `title_field`, and other fields' expressions. Frappe renames the column, updates other Custom Fields' `insert_after`, and keeps the Custom Field's record name (`<DocType>-<old fieldname>`).

A DocType save never renames a column: Frappe adds a column for the new fieldname and leaves the data in the old one. So `kind: "field"` also writes a patch that calls Frappe's `rename_field`, which copies the values and updates Property Setters, reports, and user settings. The patch comes from a fixed template; only the DocType, its module folder, and the two fieldnames are filled in, each checked first:

```python
# Generated by mcp-frappe: renames the Lab Sample field batch_no to lot_no.
import frappe
from frappe.model.utils.rename_field import rename_field


def execute():
	frappe.reload_doc('lab', "doctype", 'lab_sample')
	rename_field('Lab Sample', 'batch_no', 'lot_no')
```

It goes to `<app>/<app>/patches/rename_<doctype>_<old>_to_<new>.py`, with a `_2`, `_3`, ... suffix when the site's `Patch Log` already holds that name, because `bench migrate` skips a logged patch (a rename undone and done again). An empty `patches/__init__.py` is added if there is none, and the patch's dotted path goes to the end of the `[post_model_sync]` section of `patches.txt` (a new section if the file has none; the end of a file without sections; a new file with both sections). The preview shows each file's diff, the DocType settings that named the field and are renamed with it (`title_field`, `search_fields`, `sort_field`, ...), the record count, other fields' expressions that still name the old field, and lines in the DocType folder's code that do. Confirmation writes the files first, with a backup and a rollback manifest, then saves the DocType with its `modified`. A Frappe validation error restores the files. If the connection drops, the MCP waits and reads the DocType once: an unambiguous new-field/old-field readback is reconciled even if Frappe normalized other metadata. If the result cannot be confirmed, the tool returns an error and preserves the patch files; the error includes `rollback_id` and instructions to inspect the schema and, only when unchanged, preview `rollback_export`. Do not retry the rename or run `bench migrate` while the remote schema is uncertain. When confirmed, the result has the `rollback_id` (undo the file changes with `rollback_export`) and `next_step`: until `bench migrate` (or `run_bench(command="migrate")` when it is enabled) runs the patch, the field reads empty on this site.

The `doctype` preview gives the record count, the Link and Table fields of other DocTypes that use it (Frappe updates their options), the Custom Field and Property Setter counts (Frappe moves them), and for a standard DocType the folder Frappe moves (`app_files`) plus `code_mentions`: files elsewhere in the app that name the DocType, such as `hooks.py` or other DocTypes' JSON, which Frappe doesn't change. A standard DocType rename needs Developer Mode and the Administrator user; Frappe checks both. A reload during the rename is reconciled by checking that `new` exists and `old` doesn't. If the connection drops, the MCP reads both names back. When Frappe moved the app folder but kept the old name (a developer-mode reload can stop the request after the move and before the commit), it copies the original folder to `<app>/.mcp-frappe-backups/doctype-recoveries/<id>/original`, moves Frappe's renamed folder to `moved-doctype-folder` beside it, and puts the original folder back; the error names that folder. When the state can't be read or is ambiguous, it keeps the same copy and changes nothing else. Nothing removes these copies: delete the folder after checking the DocType and its files.

## Change journal and undo

Every confirmed write through this server records an entry in a change journal, and `undo_mcp_change` reverses the entries that have a safe inverse.

**Where.** The journal is `FRAPPE_APPS_PATH/.mcp-frappe-journal/<name>.json`, where `<name>` is `FRAPPE_SITE_LABEL`, else `FRAPPE_SITE`, else the `FRAPPE_URL` host (characters other than letters, digits, `.`, `_`, and `-` become `_`). The folder is created with mode 0700 and the file with 0600, through the same atomic write as exports; a symlinked folder or file is refused. Without `FRAPPE_APPS_PATH` the journal lives in memory and ends with the server process. It keeps the newest 200 entries.

**What goes in.** An entry has `id`, `at` (local time with offset), `tool`, `target` (names only: DocType, record or setter names, fieldname, property, app, module, command, and the `rollback_id` of an export or field rename so `rollback_export` can undo its files; never the values written), and `undo`, the inverse, or `no_undo`, the reason there is none. The inverse of an update holds the values the update replaced, so that undo can write them back. Writes of business records (the `data` toolset on any DocType other than schema and configuration ones, and `rename(kind="document")`) are journaled only when `FRAPPE_MCP_JOURNAL_RECORDS=YES`, because their names and old values can be customer data. Schema and configuration changes are always journaled. A confirmed result carries the entry's id as `mcp_change`. If the journal can't be written (for example a missing `FRAPPE_APPS_PATH`), the write still stands and the result says so in `journal_error`.

| Change | Inverse |
|---|---|
| `create_document`, `create_documents` (with `FRAPPE_MCP_JOURNAL_RECORDS=YES`) | delete the records, newest first, through `delete_document` |
| `update_document` (same opt-in) | write the replaced values back through `update_document`; child tables are written back as their old rows. A change to a `Password` field has no inverse (Frappe never returns the old value), nor does one whose old values exceed 50,000 characters |
| `create_custom_field` | delete the Custom Field through `delete_document` |
| `set_field_property`, new setter | delete the Property Setter through `delete_document`. A new DocType-level setter that replaced field-level ones has no inverse: deleting it would not bring them back |
| `set_field_property`, existing setter | set the old value again through `set_field_property` |
| `create_doctype` | delete the DocType through `delete_doctype`, with its checks (no other DocType links to it; Frappe keeps the table) |
| `update_doctype` | the inverse operations through `update_doctype`: remove added fields, re-add removed ones after their old neighbour, restore changed properties and settings, and move moved fields back. A DocType save never drops a column (only `bench trim-tables` does), so a re-added field gets its data back. Properties outside `update_doctype`'s allowlist are not re-added with a removed field |
| Deletes, submit, cancel, exports, `rollback_export`, `promote_custom_doctype`, `customize_doctype`, `rename`, `set_role_permissions`, the `upsert_*` tools, `run_bench`, method calls | none; the entry says what to use instead, such as `rollback_export` for exports |

`list_mcp_changes(limit=20)` returns the newest entries first (`limit` 1-50): `{"rows": [{"id", "at", "tool", "target", "undoable", "no_undo"|"undone_at"}], "total", "storage": "file"|"memory", "records_journaled"}`. A `names` list longer than 10 is cut, with `more_names` counting the rest.

`undo_mcp_change(entry_id, expected_preview, confirm)` refuses an unknown id, an entry already undone, an entry with no inverse, and an inverse whose tool isn't listed (its toolset isn't selected). Its preview runs the inverse tool's own preview and checks that the target is as the change left it: the journaled `modified` of every record, setter, or DocType must equal the current one, a record must still exist and be a draft (docstatus 0), and the setter must still be the same one. If someone changed the target afterwards, undo is refused, so that later change isn't lost. The preview returns `change` (the entry's listing row), `tool`, `inverse` (the inverse tool's preview), and `preview_id` over all of it; confirming recomputes it. The inverse then runs with the journal paused, so it adds no entry of its own, and the entry gets `undone_at`. If undoing a batch stops partway (Frappe refuses one delete), the entry keeps the records not yet deleted, and a fresh preview continues from there.

## Site and app sync

A developer has two copies of a schema: the site's database and the app's files. These tools compare them. They never write anything, and they read local files only below `FRAPPE_APPS_PATH`, with the same path checks as the export tools (traversal and escaping symlinks are refused).

| Tool | Purpose | Arguments |
|---|---|---|
| `diff_doctype` | Compare one DocType with `<app>/<app>/<module>/doctype/<doctype>/<doctype>.json`, and its Custom Fields and Property Setters with the app's `<module>/custom/<doctype>.json` files | `doctype`, optional `app` (default: the app that owns the DocType's module) |
| `list_app_drift` | One verdict per DocType for every DocType folder of the app and every site DocType in the app's modules | `app`, optional `module`, `limit` (1-200, default 50), `offset` |

Verdicts:

| Verdict | Meaning | What to do |
|---|---|---|
| `in_sync` | The site and the file hold the same DocType | Nothing |
| `site_ahead` | They differ and the site was saved after the file's `modified` | Export the site's version: save the DocType once with developer mode on (`update_doctype` or Desk), and Frappe rewrites the file |
| `app_ahead` | They differ and the file is newer (or was edited by hand since the last migrate) | `bench migrate` (`run_bench(command="migrate")` when [Bench commands](#bench-commands) are on) |
| `diverged` | Both changed since the last migrate, or the timestamps can't tell | Reconcile by hand; `bench migrate` would replace the site's changes |
| `missing_in_app` | The site has a standard DocType (or customizations) the app has no file for | Export it, or delete it if it was a mistake |
| `missing_on_site` | The app has a DocType folder the site lacks (`list_app_drift` only) | `bench migrate`, or for one DocType `run_bench(command="reload-doc", app, module, doctype)` |
| `site_custom` | A site-custom DocType (`custom=1`), which has no app files by design | `promote_custom_doctype` if it should ship with the app |

How the verdict is decided: both sides are normalized first. System keys (`name`, `owner`, `creation`, `modified`, `modified_by`, `docstatus`, `idx`, `migration_hash`, child-row `parent`/`parenttype`/`parentfield`), `_` columns, and blank values (`null`, `""`, `0`, `[]`) are dropped, because Frappe's export drops them. Field order comes from the file's `field_order`, and a moved field is still reported. If anything else differs, `modified` decides which side is newer. `bench migrate` re-imports a DocType's JSON whenever the file's MD5 differs from the site's `DocType.migration_hash` (or none is stored), whatever the timestamps say, so a site that is newer while the file has also changed since the last migrate is `diverged`. When the stored `migration_hash` matches the file and both `modified` values are equal, the site holds exactly what the last import read, so the verdict is `in_sync` even if values differ: those are defaults Frappe filled in on import that a hand-written JSON leaves out (`engine`, `row_format`, `sort_field`, `sort_order`), and `doctype_file.filled_on_import` is `true` with them still listed under `changes`. `doctype_file.migrate_imports_file` says whether migrate would re-import the file; when it would and the site is ahead, the result warns that migrate will replace the site's changes.

`diff_doctype` returns `verdict`, a one-line `summary`, and `doctype_file` with `path`, `site_modified`, `app_modified`, `newer`, `migrate_imports_file`, and `changes`. `changes` has the shape of `update_doctype`'s preview, read from the site (`from`) to the app file (`to`), which is the change `bench migrate` would make: `added` (fields only in the file, with `insert_after`), `removed` (only on the site), `moved`, `changed`, `properties`, and `tables` (other child tables such as `permissions`, as `only_on_site` / `only_in_app` rows). `customizations` has one entry per custom file found, with `only_on_site`, `only_in_app`, and `changed` for `custom_fields` (keyed by fieldname) and `property_setters` (keyed by `field:property`), and a verdict. Site rows whose `module` belongs to another app are left out unless the file holds them. With no file, site customizations of this app (or with no module) are reported as `missing_in_app`. Lists, objects, and long strings are capped; `truncated` says so.

`list_app_drift` reads the site's DocType list once. A DocType whose file MD5 matches its `migration_hash` and whose `modified` matches the file is `in_sync` without another request; every other pair costs one DocType read. It returns `items` (`doctype`, `module`, `verdict`), `counts` for the page, `total`, `next_offset`, and `modules_missing_locally`. When the page has a `missing_on_site` DocType, `hint` says how to sync it: with `run_bench` when it is registered, else `bench migrate`.

`promote_custom_doctype` ships a DocType prototyped on the site with an app. It saves the DocType with `custom: 0` and the target `module`; in developer mode Frappe's `on_update` then writes the DocType JSON and controller boilerplate into `<app>/<app>/<module>/doctype/<doctype>/`. It refuses `frappe` and `erpnext`, a module that belongs to another app or is a custom module (Frappe exports those into the site folder), a DocType that is already standard, one with a `default_print_format` (Frappe refuses that on a standard DocType), and an existing target folder. The preview lists the files Frappe will create and warns about Link and Table fields that point to other site-custom DocTypes, and about Custom Fields and Property Setters that stay site customizations (export them with `export_customizations`). Confirmation, like standard `create_doctype`, first writes Frappe's own controller templates (so `FRAPPE_APPS_PATH` must include the Frappe source and the app's `hooks.py` must define `app_publisher`), waits for the Bench to settle, then sends the save with `modified`. If the Bench reloads mid-save, the tool re-reads the DocType and reports `reconciled_after_server_reload: true` only when it is no longer custom; if the DocType is still custom, the staged files are removed. The result has `file_status` and `native_files_complete`.

## Developer diagnostics

Three read tools answer what an agent keeps asking during development. They combine the site with the app checkout under `FRAPPE_APPS_PATH` (except `get_recent_errors`, which reads only the site), work on Frappe v15 and v16, and never import or run anything from the app.

| Tool | Purpose | Arguments |
|---|---|---|
| `list_pending_patches` | List the app's `patches.txt` entries that `Patch Log` doesn't show as run | `app` |
| `get_recent_errors` | Read `Error Log` newest first, with the traceback trimmed to its tail | `since` (ISO datetime in the site's time zone, such as `2026-11-20T09:30`), `method` (part of the method), `reference_doctype`, `limit` (1-50, default 20), `offset` |
| `list_app_methods` | List the methods the app checkout whitelists with `frappe.whitelist`, and their arguments | `app`, `search` (part of the dotted path, any case), `limit` (1-200, default 100), `offset`, `as_config` (return suggested `FRAPPE_MCP_METHODS` entries instead) |

### `list_pending_patches`

The tool reads `<app>/<app>/patches.txt` with the same path checks as `diff_doctype` (it fails the same way without `FRAPPE_APPS_PATH` or the checkout) and parses it as `frappe.modules.patch_handler` does: when a `[section]` header comes before the first entry, the file is INI-like with `[pre_model_sync]` and `[post_model_sync]` sections and `#` or `;` comment lines; otherwise every line not starting with `#` is a pre-model-sync patch. The whole line is the patch name, so a re-run marker such as `my_app.patches.fix #2` is part of it, as it is in `Patch Log`. A `finally:` prefix is dropped for the comparison, because Frappe logs those patches without it. `execute:` lines are returned as text and never run.

It then reads every `Patch Log` row (`patch`, `skipped`) a thousand at a time, up to 50,000 rows. A row with `skipped` set (left by `bench migrate --skip-failing`) doesn't count as run, as in Frappe; such an entry is pending with `skipped: true`. `Patch Log` is readable by System Manager.

It returns `format` (`sections`, `plain`, or `missing` when the app has no `patches.txt`), `entries`, `applied_count`, `pending` in file order (`patch`, `section`, `line`), `pending_count`, `unknown_in_log` (sorted log rows whose dotted path starts with `<app>.` but which no line in the file names, such as a removed patch or a line whose `#` marker changed), `unknown_in_log_count`, and `warnings` (sections Frappe ignores, a patch listed twice). The two lists hold at most 200 entries each; the counts are exact. With pending patches, `hint` names what runs them: `run_bench(command='migrate')` when it is registered, else `bench migrate`.

### `get_recent_errors`

Reads `Error Log` (System Manager only) ordered by `creation desc` and returns a page (`rows`, `offset`, `limit`, `has_more`). Each row has `name`, `creation`, `method`, `reference_doctype`, `reference_name`, `exception` (the traceback's last non-empty line, at most 500 characters), `traceback` (the last 40 lines, cut to the last 4,000 characters), and `traceback_truncated`. `since` must have no time zone, because Frappe stores naive times in the site's zone. The fields it reads are the same on v15 and v16 (v16 adds `fingerprint`, `metadata`, and `fingerprint_stats`, which are not returned). Tracebacks can hold record values, so the description tells the agent to show the user only what is needed.

### `list_app_methods`

Walks `<app>/<app>/` and parses each `.py` file with Python's `ast` module; nothing is imported. It skips `tests`, `test`, `node_modules`, `__pycache__`, and hidden folders, does not descend into symlinked folders, and skips (and reports) a file that resolves outside the app, a file larger than 1,000,000 bytes, and a file that doesn't parse. It reads at most 5,000 files (`files_truncated` says when it stopped) and parses only files that contain the word `whitelist`.

A function counts when a decorator is `@frappe.whitelist` or `@frappe.whitelist(...)` (also through `import frappe as f`), or a bare `@whitelist` or alias imported with `from frappe import whitelist`. Module-level functions and methods of module-level classes are listed; nested functions are not. Each row has `method` (the dotted path from the app package; a class method includes the class), `kind` (`module`, or `document` for a class method, with `doctype` read from the controller's `<doctype>.json` when the file is `<module>/doctype/<doctype>/<doctype>.py`, else `null`), `args` (`name`, and `annotation` and `default` as source text; `*args` and `**kwargs` keep their stars; `self` is dropped), `doc` (the docstring's first paragraph, at most 300 characters), `allow_guest`, `methods` (Frappe's default `GET`, `POST`, `PUT`, `DELETE` when the decorator doesn't say), and `file` (`<app>/<path>.py:<line of def>`). A decorator argument that isn't a literal is returned as source text. The page also has `files_scanned`, `files_truncated`, and `skipped` (`file`, `reason`; at most 50). A document method runs through `frappe.client.run_doc_method`, not `/api/method/<path>`. The list is what the checkout declares, which may differ from what the site runs until the Bench restarts.

With `as_config: true`, the page holds suggested [`FRAPPE_MCP_METHODS`](#allowlisted-method-calls) entries instead of rows, for module functions only (a controller method can't be called by its dotted path), and a `note`. The kind is a guess: `mapper` for a `make_*` function whose first argument is `source_name` (with `source_doctype` when the file is a DocType controller), `read` when the name starts with `get_`, `list_`, `fetch_`, `search_`, `count_`, `is_`, or `has_` and the decorator allows `GET` (or doesn't allow `POST`), else `write`. `params` lists every argument except `*args`, `**kwargs`, `cmd`, and `ignore_permissions`, with `type` from a plain annotation (`str`, `int`, `float`, `int | float`, `bool`, `dict`, `list`, optionally `| None`) and `required: true` when the argument has no default. Review each entry before adding it; the server never writes the file.

## Allowlisted method calls

These tools call app-specific whitelisted methods, such as ERPNext's Sales Order to Sales Invoice mapper, or a custom app's API while you develop it. They call only the methods listed in a JSON file that `FRAPPE_MCP_METHODS` names; without the variable they aren't registered at all, and no code path calls an unlisted method. They are in the `data` toolset, so a read-only server doesn't list them.

| Tool | Purpose | Arguments |
|---|---|---|
| `list_allowed_methods` | The allowlist: `method`, `kind`, `description`, `source_doctype`, `target_doctype`, `params`, and `checked_against_source` per entry, plus `count` | none |
| `make_mapped_document` | Run a `mapper` entry through `frappe.model.mapper.make_mapped_doc` and return the unsaved draft | `method`, `source_name` |
| `call_allowed_method` | Run a `read` entry (`GET`) or preview, then run, a `write` entry (`POST`) | `method`, `args` (object), `limit` (1-500, default 100), `offset`, `confirm` |

### The `FRAPPE_MCP_METHODS` file

A JSON list of 1 to 200 entries ([example for ERPNext](https://github.com/DannyFiallo030503/mcp-frappe/blob/main/examples/erpnext-methods.json)):

```json
[
  {
    "method": "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
    "kind": "mapper",
    "source_doctype": "Sales Order",
    "target_doctype": "Sales Invoice",
    "description": "Draft a Sales Invoice for the unbilled items of a submitted Sales Order"
  },
  {
    "method": "my_app.api.get_price",
    "kind": "read",
    "params": {
      "item": {"type": "string", "required": true},
      "qty": {"type": "integer"},
      "mode": {"enum": ["net", "gross"]}
    }
  }
]
```

| Key | Rule |
|---|---|
| `method` | Required. A dotted Python path of at least two parts (`app.module.function`), letters, digits, and `_` only, at most 200 characters. No wildcards; each method is listed once. `frappe.client.*`, `frappe.model.mapper.make_mapped_doc`, `frappe.model.mapper.map_docs`, `frappe.handler.run_doc_method`, `frappe.desk.form.run_method.runserverobj`, and `frappe.model.document.execute_action` are refused: they run other methods or write any record, which would make the allowlist unrestricted (use the record tools for records). |
| `kind` | Required. `mapper` (called through `make_mapped_doc`; returns a draft and never saves), `read` (a `GET` with arguments), or `write` (a `POST`, previewed first). |
| `description` | Optional text, at most 300 characters; shown in the list and in a write preview. |
| `source_doctype`, `target_doctype` | Optional DocType names. A `mapper` may have both; a `read` entry may have `target_doctype` (its result is then shaped as a draft, like a mapper's); a `write` entry has neither. When `target_doctype` is set, a mapper that returns another DocType is an error. |
| `params` | Optional, `read` and `write` only: an object of argument name to `{type, required, enum, description}`, at most 50. `type` is `string`, `integer`, `number`, `boolean`, `object`, or `array`; without a `type`, only a scalar is accepted. `cmd` and `ignore_permissions` are never allowed. |

The server checks the file at startup and refuses to start, naming the file, the line, and the method of each problem (up to 20 at once). Unknown keys and invalid values are problems. `mcp-frappe check` reports the same, plus the number of entries.

**Checked against the app source.** When `FRAPPE_APPS_PATH` holds a checkout of the method's app (the first part of the path), the loader finds the function in `<app>/<app>/<module path>.py` (or the package's `__init__.py`) with the same parser as `list_app_methods` and checks that it is a module-level function decorated with `frappe.whitelist`, that the decorator's `methods` allow `GET` for a `read` entry and `POST` for a `write` entry, that a mapper takes an argument, and that every name in `params` is an argument (unless the function takes `**kwargs`). A mismatch stops startup. The function's argument names are kept, so an entry without `params` still refuses an argument the function doesn't take. Without a checkout of that app, startup logs a warning per app and goes on; the entry's `checked_against_source` is `false`. The check reads the checkout, not the running site: an `override_whitelisted_methods` hook on the site is not followed.

### Calling

`call_allowed_method` refuses a method that isn't listed and a `mapper` entry (and `make_mapped_document` refuses `read` and `write` entries), naming the right tool. It checks `args` before any request: every name must be in `params` (or, without `params`, a scalar value and, when the source was checked, an argument of the function); types and `enum` must fit (`true` is not an integer); `required` ones must be present; `null` values are left out; one value can be at most 10,000 characters as JSON; `cmd` and `ignore_permissions` are refused. A `read` entry is sent as `GET /api/method/<method>` with the arguments as query parameters (objects and arrays as JSON text). A `write` entry returns a preview (`method`, `kind`, `description`, the checked `args`, `requires_confirmation`) and is sent as `POST /api/method/<method>` with a JSON body only with `confirm: true`.

The result comes back as `{method, kind, result, truncated}`. A list result is paged like `run_report`: `result` holds items `offset` to `offset + limit`, with `total_rows`, `offset`, `limit`, `returned_rows`, and `has_more`. Inside the result, lists and objects are cut to `limit` items, strings to 2,000 characters, and nesting to 8 levels; if the result is still over 100,000 characters as JSON, `result` is `null` and `result_text` holds the first 100,000 characters. `truncated` says whether anything was cut.

Frappe rolls back what a `GET` request writes unless the method commits on its own, so a `read` entry should be a method that only reads; list anything that changes data as `write`.

### `make_mapped_document`

Sends `GET /api/method/frappe.model.mapper.make_mapped_doc` with `method` and `source_name`. Frappe resolves the method, checks it is whitelisted, and calls it with `source_name`; the mapper checks the source's permissions and builds an unsaved document. Because the request is a `GET`, Frappe rolls back anything the mapper wrote; for example, ERPNext's Quotation to Sales Order mapper creates a Customer from a Lead when the Quotation is for a Lead, and that Customer is not kept, so create the Customer first.

The result has `method`, `source_doctype`, `source_name`, `doctype` (the target), `values`, and `read_only`. `values` holds what `create_document` accepts: the target's fields that are not read-only and not empty, with child-table rows whole except for `__`-prefixed keys, local names, `parent*`, `idx`, `docstatus`, and empty values. Child rows keep their other fields, read-only and hidden ones included (such as ERPNext's `so_detail`), because they tie the new document to its source. `read_only` holds the top-level read-only fields (names fetched from links, totals), which Frappe sets again on save. A child table over 200 rows is cut, named in `_truncated_child_tables`, and a `note` says the draft can't be created as it is. Save the draft with `create_document(doctype, values)`, then `submit_document` if needed.

## Bench commands

`run_bench` runs one of five Bench commands on the configured site, so the development loop (change, export, migrate, test, check) stays in one session. It exists only when the user sets `FRAPPE_BENCH_PATH` ([configuration](installation-and-configuration.md#bench-commands)), and only for a local target: with `FRAPPE_BENCH_PATH` set and a `FRAPPE_URL` that isn't loopback or `.localhost`, the server refuses to start. It is the only tool of the `bench` toolset, so read-only mode leaves it out.

| `command` | Arguments | Runs |
|---|---|---|
| `migrate` | optional `app` (its drift and pending patches go into the preview) | `bench --site <site> migrate` |
| `reload-doc` | `app`, `module`, `doctype` | `bench --site <site> reload-doc <module> doctype <doctype>`, with the names scrubbed, as `frappe.reload_doctype` calls it |
| `export-fixtures` | `app` (not `frappe` or `erpnext`) | `bench --site <site> export-fixtures --app <app>` |
| `clear-cache` | none | `bench --site <site> clear-cache` |
| `run-tests` | `app` and exactly one of `module` (a Module Def), `doctype`, or `test_module` (a dotted module path inside the app) | `bench --site <site> run-tests --app <app>` with `--module-def`, `--doctype`, or `--module` |

`<site>` is `FRAPPE_BENCH_SITE` (or `FRAPPE_SITE`), and `bench` is the configured command (`FRAPPE_BENCH_COMMAND`, for example a `docker compose exec` prefix). Nothing else reaches the command line: no flag, no free text, and no shell. Before the preview the tool checks:

- every command gets exactly its arguments, and each name matches the safe-name pattern (letters, digits, space, `_`, `.`, `-`, starting with a letter or digit, so never a flag);
- the app has modules on the site, and `module` belongs to `app`;
- for `reload-doc`, the checkout under `FRAPPE_APPS_PATH` has `<module>/doctype/<doctype>/<doctype>.json` and the file holds that DocType;
- for `run-tests` with `doctype`, the DocType's module belongs to `app`; and when the Bench's `sites/` folder is readable, the site config (site over `common_site_config.json`) has `allow_tests`, because without it `run-tests` prints a message and exits 0.

**Preview.** Without `confirm`, the result has `command`, `site`, `argv`, a `warning` for `migrate`, `reload-doc`, `export-fixtures`, and `run-tests`, and `preflight`:

- `drift` for `migrate` with `app` and for `reload-doc` (scoped to the module): `total`, `counts`, `items` that aren't `in_sync` (at most 50), and `partial` when the app has more than 200 DocTypes;
- `pending_patches` for `migrate` with `app`: `pending_count` and the first 20;
- `fixture_files` for `export-fixtures`: the app's `fixtures/*.json` with their SHA-256 (at most 200).

A part that can't be read (no checkout, no access) holds `unavailable` and the reason. `preview_id` is a SHA-256 over all of it. Confirmation takes `confirm: true` and `expected_preview`; the tool reads everything again first, so if drift, patches, or fixture files changed since the preview, it refuses and you preview again.

**Run.** The command runs with `FRAPPE_BENCH_PATH` as working directory, stdin closed, stdout and stderr together, and a timeout of `FRAPPE_BENCH_TIMEOUT` seconds (default 900). Its environment holds only `PATH`, `HOME`, the user, locale, time zone, temporary-folder, shell, and terminal variables, `PYENV_ROOT`/`NVM_*`, the Docker and Podman host and config variables, TLS certificate paths, and `PYTHONUNBUFFERED=1`: no `FRAPPE_*` credential, and no `CI` (which skips Frappe's `allow_tests` check). The call blocks until the command ends.

**Result.** `command`, `site`, `argv`, `exit_code` (`null` after a timeout), `timed_out`, `seconds`, `ok`, and:

- `output`: the last 200 lines, each at most 500 characters and 20,000 in all, with terminal colors removed, configured secrets replaced by `***`, and the Bench folder by `<bench>`; `output_lines` and `output_truncated`;
- `server_ready`: whether the site answered again within 45 seconds (migrate and reload-doc can restart the web worker);
- `after`: the preflight read again, after the metadata cache is cleared, so you see what changed; it runs after a failed command and a timeout too;
- `changed_files` for `export-fixtures`: `added`, `changed`, and `removed` fixture files;
- `tests` for `run-tests`: `ran`, `status` (`OK`, `FAILED`, `disabled`, or `unknown`), and `detail` such as `failures=1`. Frappe v15 exits 0 when tests fail, so `ok` is false for `FAILED` and `disabled` whatever the exit code.

After a timeout the process is killed, and `note` says so; a command started inside a container through `docker exec` may keep running there. Bench runs the app's own code: `migrate` runs patches and hooks, and `run-tests` runs test code that writes test records.

## Workflows and Notifications

Approval flows and alerts are configuration records, not code. These tools take a small, checked shape and build the record, so the model never sends raw child rows. Fields Frappe evaluates as code are refused (see [security](security.md#code-fields-and-templates)).

| Tool | Purpose | Arguments |
|---|---|---|
| `get_workflow` | The DocType's active Workflow (states, transitions, state field), or `workflow: null`; `inactive` names its other Workflows | `doctype` |
| `upsert_workflow` | Create or replace an approval Workflow | `doctype`, `name`, `states`, `transitions`, optional `is_active` (true), `send_email_alert` (false), `workflow_state_field` (`workflow_state`); preview, then confirm (with `expected_modified` when it exists) |
| `list_notifications` | Notification rules: `name`, `document_type`, `event`, `channel`, `enabled`, `is_standard`, `module` | optional `document_type`, `limit` (1-200, default 50); `truncated` says when there are more |
| `upsert_notification` | Create or replace an Email or System Notification rule | `name`, `document_type`, `event`, `channel`, `recipients`, `subject`, `message`, and the options below; preview, then confirm (with `expected_modified` when it exists) |

### `upsert_workflow`

```json
{
  "doctype": "Leave Request",
  "name": "Leave Approval",
  "states": [
    {"state": "Draft", "doc_status": 0, "allow_edit": "Employee"},
    {"state": "Pending", "doc_status": 0, "allow_edit": "Leave Approver"},
    {"state": "Approved", "doc_status": 1, "allow_edit": "Leave Approver",
     "update_field": "status", "update_value": "Approved"},
    {"state": "Rejected", "doc_status": 0, "allow_edit": "Leave Approver"}
  ],
  "transitions": [
    {"state": "Draft", "action": "Submit for Approval", "next_state": "Pending", "allowed": "Employee"},
    {"state": "Pending", "action": "Approve", "next_state": "Approved", "allowed": "Leave Approver",
     "allow_self_approval": false},
    {"state": "Pending", "action": "Reject", "next_state": "Rejected", "allowed": "Leave Approver"},
    {"state": "Rejected", "action": "Submit for Approval", "next_state": "Pending", "allowed": "Employee"}
  ]
}
```

- **States**, in order: `state` (a Workflow State name), `doc_status` (`0` draft, `1` submitted, `2` cancelled; `1` and `2` only on a submittable DocType), `allow_edit` (the Role that may edit a document in that state), and optionally `update_field` (a field of the DocType) with `update_value` (a literal value). The first state is where new documents start, so it must have `doc_status` 0. `evaluate_as_expression` is refused.
- **Transitions**: `state`, `action` (a Workflow Action Master name), `next_state`, `allowed` (a Role), optional `allow_self_approval` (default true). `condition` is refused. The same docstatus rules as Frappe's `Workflow.validate_docstatus` apply before anything is saved: a cancelled state cannot change, a submitted document cannot go back to draft, and a draft cannot be cancelled.
- **Checks**: every role must exist; every state must be reachable from the first one (otherwise the call is refused); a draft state with no transition out is a warning. A Workflow name used by another DocType is refused. The state field, if the DocType has it, must be a Link, Data, or Select field.
- **Preview**: `states` and `transitions` as they will be saved, `creates` (the Workflow State and Workflow Action Master records that don't exist yet), `state_field` (with the hidden `custom_field` Frappe's `on_update` adds when the DocType has no such field), `deactivates` (other active Workflows of the DocType, which Frappe's `set_active` turns off), `warnings` (including how many existing records have no state yet; Frappe sets each to the first state with the record's docstatus), and, when the Workflow exists, `modified` and `changes`: `properties`, `states` (`added`, `removed`, `changed` per state, `order_changed`), and `transitions` (`added`, `removed`, `changed`, keyed by state, action, and role).
- **Confirm** creates the missing masters, then saves the Workflow with the preview's `modified`. Rows that already exist keep their `name` and the columns this tool doesn't manage (a state's message, a transition condition set in Desk), so Frappe updates them in place; the preview warns about each condition kept. The DocType's metadata cache is cleared, because Frappe may have added the state field.

### `upsert_notification`

| Argument | Rule |
|---|---|
| `event` | `New`, `Save`, `Submit`, `Cancel` (these two need a submittable DocType), `Value Change` with `value_changed` (a field of the DocType), `Days Before` / `Days After` with `date_changed` (a Date or Datetime field) and `days_in_advance` (a positive integer). `Custom`, `Method`, and minute-based events are refused. A child-table DocType takes only the day events, as in Frappe. |
| `channel` | `Email` or `System Notification` (Desk's bell). Slack and SMS are refused. For System Notification the message is also sent as `notification_message`, the alert's text. |
| `recipients` | 1-20 entries, each with any of `receiver_by_document_field` (`owner`, a Data field with options Email, or a Link to User), `receiver_by_role` (an existing Role), and, for Email only, `cc` / `bcc` (plain addresses separated by commas or newlines). `condition` is refused. |
| `subject`, `message` | The template rule below. `subject` is at most 140 characters. |
| `filters` | Optional. Send only when the document matches, in the `list_documents` filter shapes, on the DocType's own fields: `{"status": "Approved"}` or `[["grand_total", ">", 1000]]`. Saved with `condition_type: Filters`; Frappe evaluates them as data. Without filters, the Python `condition` is cleared. |
| `enabled` | Default true. |
| `is_standard`, `module` | A standard Notification ships with the app: in developer mode Frappe writes `<module>/notification/<name>/<name>.json`, the message as `<name>.md`, a `get_context` stub `<name>.py`, and `__init__.py` files. `module` must be a non-custom Module Def of an app other than `frappe` and `erpnext`. The preview lists the files (`app_files`) and notes that developer mode is needed. |

**Template rule.** Frappe renders `subject` and `message` (and `cc`/`bcc` when they contain `{`) as Jinja on the server. They may hold plain text, Markdown, or HTML, plus placeholders of exactly the form `{{ doc.<fieldname> }}`, where the field is a value field of the DocType (not a layout field or a table) or a standard column such as `name`, `owner`, or `modified`. A placeholder may add filters from this list, with literal arguments only: `upper`, `lower`, `title`, `capitalize`, `trim`, `striptags`, `escape` (`e`), `int`, `str`, `flt(precision)`, `round(precision, "common"|"ceil"|"floor")`, `truncate(length)`, `default("text")`. Everything else is refused: `{% %}` statements, `{# #}` comments, whitespace control (`{{-`), other variables (`frappe`, `nowdate`), attribute chains, indexing, calls, operators, and other filters. Example: `Leave {{ doc.name }} for {{ doc.employee_name | title }} is {{ doc.total_days | round(1) }} days`.

The preview returns `record` (what will be saved), `warnings`, and, when the rule exists, `modified` and `changes` (`{field: {from, to}}`, bounded, plus `recipients` when they change). Replacing a rule removes a Python condition and recipient conditions set outside this server; the preview warns about both.

Both upsert tools save through `_save_config_record`: an update sends `modified`, so Frappe refuses a record that changed since the preview, and if a developer-mode Bench reloads while Frappe writes a standard Notification's files, the record is re-read and the save counts only if it holds the change.

## Reports and Workspaces

Saved reports and Workspaces finish a module: they give users a list to work from and a sidebar page with shortcuts. Only Report Builder reports are supported; Query and Script Reports run SQL or Python, and custom HTML blocks carry HTML and JavaScript, so they are refused (see [security](security.md#code-fields-and-templates)).

| Tool | Purpose | Arguments |
|---|---|---|
| `upsert_report` | Create or replace a Report Builder report | `name`, `ref_doctype`, `columns`, optional `filters`, `order_by`, `group_by`, `add_totals_row`, `is_standard`, `module`; preview, then confirm (with `expected_modified` when it exists) |
| `get_workspace` | A Workspace's module, visibility, shortcuts, link cards, number cards, and block `layout` in display order; `unmanaged_blocks` names blocks `upsert_workspace` cannot rebuild | `name` |
| `upsert_workspace` | Create or replace a public, standard Workspace of an app module | `name`, `module`, `shortcuts`, optional `link_cards`, `number_cards`; preview, then confirm (with `expected_modified` when it exists) |

### `upsert_report`

```json
{
  "name": "Open Samples",
  "ref_doctype": "Lab Sample",
  "columns": ["name", "title", "status", "Lab Reading.value"],
  "filters": {"status": "Open"},
  "order_by": "modified desc"
}
```

| Argument | Rule |
|---|---|
| `ref_doctype` | A DocType that is neither a child table nor a Single. |
| `columns` | 1-50 distinct columns, in order: a stored field of `ref_doctype` or a standard column (`name`, `owner`, `creation`, `modified`, `modified_by`, `docstatus`, `idx`), or `<child DocType>.<fieldname>` for one of its child tables. Layout and Table fields are not columns. |
| `filters` | The `list_documents` shapes and operators, up to 50; saved as `[doctype, field, operator, value]` rows. |
| `order_by` | `<fieldname> [asc|desc]`, up to 3 joined by commas, on fields of `ref_doctype`. The tool rebuilds Frappe's quoted sort string; no other text passes. |
| `group_by` | `{"field", "aggregate_function": "count"|"sum"|"avg", "aggregate_on"}` (`aggregate_on`, a numeric field, for sum and avg only). With it, `columns` must be exactly `[field]` and `order_by` is refused: Frappe shows the group field plus the aggregate and sorts by it. |
| `is_standard`, `module` | A standard report ships with the app: in developer mode Frappe writes `<module>/report/<name>/<name>.json` and `__init__.py` files. Frappe lets only Administrator save one. `module` must be a non-custom Module Def of an app other than `frappe` and `erpnext`. The preview lists `app_files`. Without `is_standard` the report is saved with `is_standard: No` explicitly, because Frappe would otherwise make an Administrator's report standard in developer mode and write it into the DocType's module. |

The report's settings go to the Report `json` field with the keys Frappe's Report Builder saves: `fields` (`[fieldname, doctype]` pairs), `filters`, `order_by`, `add_totals_row`, `page_length`, and `group_by` (`group_by`, `aggregate_function`, `aggregate_on`, as backquoted columns). Other keys already in the record, such as `column_widths` and `chart_args`, are kept. An existing report must be a Report Builder report (its type never changes), and a standard report cannot be made site-only.

The preview returns `record`, `settings`, and, when the report exists, `modified` and `changes` (`{key: {from, to}}`, bounded). After a confirmed save, `sample` holds up to 5 rows read with the report's columns, filters, and sort through `frappe.desk.reportview.get_list` (the call the Report Builder makes; `run_report`'s query-report endpoint returns nothing for Report Builder reports), with `truncated`; a failed sample run is returned as `sample.error` and does not undo the save.

### `upsert_workspace`

```json
{
  "name": "Lab",
  "module": "Lab",
  "shortcuts": [
    {"label": "Samples", "type": "DocType", "link_to": "Lab Sample", "doc_view": "List", "color": "Blue"},
    {"label": "Open Samples", "type": "Report", "link_to": "Open Samples"}
  ],
  "link_cards": [
    {"label": "Masters", "links": [{"label": "Lab Sample", "type": "DocType", "link_to": "Lab Sample"}]}
  ],
  "number_cards": ["Open Samples Count"]
}
```

- **Targets.** Shortcut and link `type` is `DocType`, `Report`, or `Page`, and `link_to` must exist (one list read per type). A DocType target can't be a child table. `URL`, `Dashboard`, and other shortcut types are refused. `doc_view` (`List`, `Report Builder`, `Dashboard`, `Tree`, `New`, `Calendar`, `Image`) applies to DocType shortcuts; `Kanban` needs a Kanban Board and is refused. `color` is a color name or a `#hex` value. Labels are unique among shortcuts and among cards. `number_cards` name existing Number Card records; creating them is out of scope. Up to 50 shortcuts, 50 cards with 1-50 links each, and 50 number cards.
- **Layout.** The tool writes `content`, the JSON list of editor blocks Desk renders: number cards (`number_card` blocks), then shortcuts (`shortcut`), then link cards (`card`), separated by spacers. Each block names its row by label (`number_card_name`, `shortcut_name`, `card_name`), so the layout always matches the rows. Block ids are derived from the label, so the same input gives the same `content`.
- **Rows.** Link cards are saved as Frappe's `links` table: a `Card Break` row with `link_count`, then one `Link` row per link; a link to a Query, Script, or Custom report gets `is_query_report: 1` so Desk opens it as a query report. On replacement, columns this tool doesn't set (shortcut `icon`, `stats_filter`, `format`; link `description`, `dependencies`, `onboard`, ...) are carried over from the row with the same label.
- **Module.** `module` must be a non-custom Module Def of an app other than `frappe` and `erpnext`; when replacing, so must the Workspace's current module. The Workspace is saved `public` with no `for_user`, and with `standard: 1` where the Workspace DocType has that field (Frappe v16; v15 has none). In developer mode Frappe then writes `<module>/workspace/<name>/<name>.json` (`app_files` in the preview): v15 exports every public Workspace, v16 only standard ones. Saving a public Workspace needs the Workspace Manager role.
- **Refused on replacement:** a private Workspace, and one whose layout has chart, quick list, custom HTML block, or onboarding blocks (edit those in Desk). Header and paragraph blocks are dropped from the layout with a warning.
- **Preview** returns `record`, `content`, `layout`, `warnings`, `app_files`, and, when the Workspace exists, `modified` and `changes`: for `shortcuts`, `link_cards`, and `number_cards`, the labels `added`, `removed`, and `moved` (kept, but in a different order), plus `changed` for cards whose links differ.
- A name whose Desk route is taken by a Page, DocType, or another Workspace (Frappe's `validate_route_conflict`, which compares names lower-cased with spaces as hyphens) is refused by Frappe; the error says so.

## File export details

`FRAPPE_APPS_PATH` must point to a local Bench `apps/` directory. The requested app and module must exist below it, and the corresponding remote Module Def must name that app as owner. Path traversal and escaping symlinks are rejected. Source export refuses the protected `frappe` and `erpnext` apps. The export preview reports destination paths, bounded diffs, and content hashes. Existing files are backed up under the selected app's `.mcp-frappe-backups/` directory; inspect and commit the resulting files with Git. A successful rollback consumes its manifest and backup files.

`export_customizations` writes Frappe's native module customization file, the one Customize Form's "Export Customizations" writes, to `<app>/<app>/<module>/custom/<doctype>.json` (the DocType name lower-cased, spaces and hyphens replaced by `_`). The file has the keys `custom_fields`, `property_setters`, `custom_perms`, `links`, `doctype`, and `sync_on_migrate`, with full rows as Frappe exports them, and is serialized like `frappe.as_json`. Every child-table DocType the DocType uses gets its own file. A DocType with no Custom Fields, Property Setters, or (when exported) Custom DocPerms gets no file and is listed under `empty`; when nothing at all is left the preview returns `nothing_to_export: true` and no `preview_id`.

- `with_permissions` (default false) adds the DocType's Custom DocPerm rows. On migrate, Frappe deletes the site's Custom DocPerms for that DocType and inserts the exported ones.
- `apply_module_filter` (default false) keeps only Custom Fields and Property Setters whose `module` is the selected module, like Frappe's "Apply Module Export Filter".
- `sync_on_migrate` (default true; Frappe's own default is false) makes `bench migrate` import the file through `sync_customizations`.

The export may target any DocType, including one owned by another app such as `Customer`; `module` only chooses where the file goes. Client Scripts for the exported DocTypes whose `module` is the selected module go to the app-level `<app>/<app>/fixtures/client_script.json`, in the row format of `bench export-fixtures`. Rows already in that file for other DocTypes or modules are kept. Only then does the exporter add or update a clearly marked block in the app's `hooks.py` that registers a `Client Script` fixture filter; text outside the block is preserved, and fixture entries for other DocTypes left there by older exports are dropped from it. Without Client Scripts or Workflows, `hooks.py` is not touched.

`with_workflows` (default false) also exports the Workflows whose `document_type` is one of `doctypes`. A Workflow has no module, so Frappe has no module folder to write it to; it travels as a fixture, in the row format of `bench export-fixtures` (no `owner`, `creation`, `modified_by`, or `idx`; child rows without their `name` and parent columns): `<app>/<app>/fixtures/workflow.json`, plus `workflow_state.json` and `workflow_action_master.json` with the masters those Workflows name. Rows for other Workflows and masters stay in the files; a Workflow of one of these DocTypes that no longer exists on the site is removed from `workflow.json`. The managed block lists `Workflow`, `Workflow State`, and `Workflow Action Master` with `name` filters, merged with names earlier exports put there. `bench migrate` imports every fixture file on every run (with link checks off and `force`), so the app's copy overwrites edits made on the site; Frappe's `on_update` then runs again (state field, deactivating other Workflows). If the app's own `fixtures` hook already lists `Workflow`, `bench export-fixtures` writes the same `workflow.json`; keep one of the two.

DocType JSON is never written: in developer mode Frappe writes standard DocType files itself, and `create_doctype` and `promote_custom_doctype` rely on that. Folder names follow Frappe's `scrub` (lower case, each space and hyphen replaced by `_`).

The exporter creates local backups and a rollback manifest, and `rollback_export` refuses if an exported file changed after export. Rollback restores overwritten files, deletes created files, and removes the directories the export created once they are empty. Manifests from older exports, which wrote to `<module>/fixtures/`, still roll back. Exports never deploy files, run migrations, or roll back remote database changes. For fixtures the app's own `fixtures` hook lists (which `export_customizations` doesn't write), use `run_bench(command="export-fixtures", app=...)` when [Bench commands](#bench-commands) are on.

## Deliberate exclusions

There is no arbitrary Python execution, SQL execution, or unrestricted whitelisted-method dispatcher: method calls go only to the methods the user lists in `FRAPPE_MCP_METHODS`. `run_bench` runs only its five commands; `bench build`, `install-app`, `get-app`, `backup`, `restore`, `drop-site`, `console`, `execute`, `--force`, and any non-local site are out of scope. Query Reports, Script Reports, custom HTML blocks, Dashboard Charts, and private Workspaces are not written. Other Frappe features such as applying workflow actions to documents, Notification conditions, Slack and SMS channels, file downloads, translations, print rendering, and app-specific methods that aren't in the `FRAPPE_MCP_METHODS` allowlist require explicit wrappers and are not exposed by this standalone release yet. Do not infer a tool exists from the retained legacy source tree.
