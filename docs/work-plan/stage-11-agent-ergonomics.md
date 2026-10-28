# Stage 11: Agent ergonomics (errors, instructions, annotations, toolsets, reports)

**Depends on:** 09, 10 · **Size:** large · **Fixes:** structural gaps 3 (reports), 5, and part of 6 in [assessment.md](assessment.md)

## Why

How well an agent does depends on what it sees:

- Clean error text lets it correct itself.
- Server instructions tell it the workflow.
- Tool annotations let the host auto-approve reads and ask a human before writes. That's real human approval, unlike `confirm=true`, which the model sets itself.
- Toolsets let a developer hide what they don't need, and give a read-only mode for checking a site that shouldn't change.
- Report discovery stops it guessing report names and filters.

## Read first

- `standalone.py`: `_check`, `server = FastMCP("frappe")`, every `@server.tool()`
- mcp SDK (installed version): `FastMCP.__init__(instructions=...)`, `FastMCP.tool(annotations=...)`, `FastMCP.remove_tool`, `mcp.types.ToolAnnotations`
- Frappe `frappe/desk/query_report.py` (`get_script` returns `script`, `filters`, `html_format`, ...; `run`)

## Tasks

### Errors

- [ ] **A `FrappeError(RuntimeError)` with readable text.** It should carry `status`, `exc_type`, and a plain-text `message`.
  - `_server_messages` is a JSON string holding a list of JSON strings, each with an HTML `message`. Decode both levels and strip the HTML tags.
  - Include `exc_type` when present.
  - **Never** include `exc` (the traceback).
- [ ] **Hints for common exception types.** Add a short hint for each of these:
  - `PermissionError`: report the missing access; don't retry another way.
  - `DoesNotExistError`;
  - `MandatoryError`: list the missing fields if the message names them;
  - `LinkValidationError`;
  - `DuplicateEntryError`;
  - `TimestampMismatchError`: get a fresh preview;
  - `ValidationError`.
- [ ] **Bound the message** to about 2,000 characters.

### Agent guidance

- [ ] **Server instructions.** Pass `instructions=` to `FastMCP(...)` (under about 1,500 characters). Cover:
  - start with `get_site_info` (developer mode, apps under `FRAPPE_APPS_PATH`);
  - find DocTypes with `list_doctypes(search=...)` and read the compact schema before changing anything;
  - which tool for which change: `create_doctype` and `update_doctype` for DocTypes of your own app, `create_custom_field` and `set_field_property` for `frappe`/`erpnext` DocTypes, the configuration tools for Workflows, Notifications, reports, and Workspaces;
  - check drift with `diff_doctype` before and after editing a standard DocType;
  - keep limits small;
  - every write is preview, then confirm with tokens;
  - a permission denial is final.
- [ ] **Per-argument descriptions.** Use `Annotated[..., Field(description=...)]` (pydantic) for at least: `filters` (shapes and operators, with an example), `fields`, `order_by`, `expected_modified`, `confirm`, `preview_id`/`expected_preview`, and `detail`. Check that the descriptions appear in `list_tools` (contract test).
- [ ] **Tool annotations.** Every tool gets `ToolAnnotations`:
  - read tools: `readOnlyHint=True`;
  - create and update tools: `readOnlyHint=False`;
  - `delete_document`, `delete_doctype`, `cancel_document`, `rollback_export`, and `set_role_permissions`: `destructiveHint=True`;
  - `idempotentHint` where it's true;
  - a human-readable `title` for each.

  Assert in the contract test that every tool has annotations.

### Toolsets and read-only mode

- [ ] **Toolsets.** Group the tools:
  - `read`: discovery, schema, list, get, count, search, reports, site info, `diff_doctype`, `list_app_drift`, and the read-only `get_*`/`list_*` tools of stages 08–09;
  - `data`: create, update, delete, submit, cancel;
  - `customize`: custom field, property setter, role permissions, export, rollback;
  - `developer`: `create_doctype`, `update_doctype`, `delete_doctype`, `promote_custom_doctype`, and the configuration tools of stages 08–09 (`upsert_workflow`, `upsert_notification`, `upsert_report`, `upsert_workspace`).

  Select them with `FRAPPE_MCP_TOOLSETS` (comma-separated). `FRAPPE_MCP_READ_ONLY=YES` is shorthand for `read` only. Remove the tools that aren't selected at startup with `server.remove_tool`, so the host never sees them.
- [ ] **Default toolsets: all four.** The target user is a developer (see the README goal), so every toolset is on by default and `FRAPPE_MCP_TOOLSETS` narrows it. Don't change this default without asking the user.
- [ ] **Startup failures.** Fail at startup with a clear message for an unknown toolset name.

### Reports

- [ ] **New tool `list_reports(search=None, ref_doctype=None, limit=50)`.** List enabled reports with `name`, `ref_doctype`, `report_type`, and `module`. Investigate permissions: check whether an ordinary user can list `Report`. If not, document it, and tell the agent to ask the user for the report name.
- [ ] **New tool `get_report_filters(report_name)`.** Call `frappe.desk.query_report.get_script` and return `filters` (the child rows for Query Reports) plus the `script` text truncated to about 8,000 characters. Script reports define their filters in that JavaScript, and the model can read it. Never return `html_format`.
- [ ] **`run_report` gets `offset`**, and reports `total_rows` before truncating. Drop large fields that aren't needed, such as `chart`, unless `include_chart=True`.

### Tests

- [ ] Error decoding: a nested `_server_messages` value gives plain text, and tracebacks never leak.
- [ ] Annotations and descriptions are present on every tool.
- [ ] Toolset filtering: read-only exposes no mutation tools, and an unknown toolset fails.
- [ ] `get_report_filters` truncates the script.

## Acceptance criteria

- `list_tools` shows annotations and argument descriptions on every tool, and the instructions appear in the initialize result.
- `FRAPPE_MCP_READ_ONLY=YES` exposes no mutation tools (contract test).
- Docs:
  - `docs/tool-reference.md` covers toolsets, read-only mode, and the report tools;
  - `docs/security.md` explains that annotations plus host approval are the human checkpoint, and that the preview tokens guard against stale state, not against the model itself;
  - `docs/installation-and-configuration.md` lists the new environment variables.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Amend and workflow actions on documents (backlog). MCP prompts and resources (backlog).

## Handoff

- Status: done
- Branch / commits: `stage-11-agent-ergonomics`, based on `main` at `22cba6e` (stage 10 merged).
- What changed:
  - Errors: `_check()` builds `FrappeError` through `_frappe_error()`. It keeps `status`, `exc_type`, and `message`; decodes both JSON levels of `_server_messages` and strips HTML; falls back to `_error_message`, the `exception` line (module path dropped), or `message`; never uses `exc`. Text is `Frappe HTTP <status> <ExcType>: <text>` plus a `Hint:` line from `_ERROR_HINTS` for the seven listed types (`MandatoryError` also names the missing labels), capped at 2,000 characters with the hint kept. The constructor stays `FrappeError(message, exc_type=None, *, status=None)`, so existing callers and tests that branch on `exc_type` are unchanged.
  - Agent guidance: `FastMCP("frappe", instructions=_INSTRUCTIONS)` (about 1,250 characters). Shared arguments use `Annotated[..., Field(description=...)]` aliases: `Filters`, `OrFilters`, `Fields`, `OrderBy`, `ExpectedModified`, `ExpectedPreview`, `Confirm`, `Detail`, `ReportFilters`; `create_doctype.fields` has its own description. `pydantic` is now a declared dependency (it was already installed through `mcp`).
  - Annotations and toolsets: every tool registers through `_tool(toolset, title, destructive=, idempotent=)`, which fills `_TOOLSETS` and passes a `title` and `ToolAnnotations` (`openWorldHint=False` everywhere). `main()` calls `_apply_toolsets(_selected_toolsets())`, which reads `FRAPPE_MCP_TOOLSETS` and `FRAPPE_MCP_READ_ONLY` and removes the unselected tools before serving. All four toolsets are on by default.
  - Reports: new `list_reports(search, ref_doctype, limit=50, offset=0)` (page shape of `list_documents`) and `get_report_filters(report_name)` (Report Filter rows plus the script cut to 8,000 characters; `html_format` dropped). `run_report` gained `offset` and `include_chart`, returns `total_rows`, `offset`, `limit`, `has_more`, and still `returned_rows` and `truncated` (the smoke runner reads them), and drops `chart` by default.
  - Tests: `tests/test_ergonomics.py` (errors, instructions, annotations, descriptions, toolsets, startup failures, report tools); the expected-tools test and one transport assertion updated for the new tools and message format.
- Toolset membership changes, if any: none beyond the plan; the two new report tools are in `read`. `list_installed_apps` is in `read`.
- Report permission finding: Frappe v16.33.1's `Report` DocType gives read to `Administrator`, `System Manager`, `Report Manager`, and `Desk User`, so every Desk user can list reports; website-only users cannot, and the tool docstring and AI guide tell the agent to ask for the report name then. Listing does not apply each report's `roles`; `get_script` and `run` do (`get_report_doc` → `is_permitted()`). Not checked on v15.
- Deviations from this plan:
  - `update_doctype` is also marked `destructiveHint: true`, because its `remove_field` operation and field-type changes can drop data; the plan's list named five tools.
  - `idempotentHint: true` on `update_document`, `delete_document`, `delete_doctype`, `set_field_property`, `set_role_permissions`, and the four `upsert_*` tools (same arguments leave the same end state); `false` on the other write tools.
  - `list_reports` also takes `offset`, like the other list tools.
  - `FRAPPE_MCP_READ_ONLY` accepts only `YES`/`NO`/unset (a typo such as `true` must not silently keep writes on), and `YES` together with a non-`read` toolset in `FRAPPE_MCP_TOOLSETS` fails at startup.
  - The error text puts the exception class after the status (`Frappe HTTP 403 PermissionError: ...`) rather than in a separate field of the message.
  - The plan's `preview_id` argument is named `expected_preview` in the tools; that argument got the description.
- Notes for the next stage: tool registration is `@_tool(...)`, not `@server.tool()`; a new tool needs a toolset and title, and `test_every_tool_has_annotations_and_a_toolset` fails otherwise. Stage 12 can document `FRAPPE_MCP_TOOLSETS`/`FRAPPE_MCP_READ_ONLY` in packaged host configs. The e2e runners were not changed (the smoke runner's `run_report` call still works) and were not run.
