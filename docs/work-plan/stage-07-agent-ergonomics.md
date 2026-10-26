# Stage 07: Agent ergonomics (errors, instructions, annotations, toolsets, reports)

**Depends on:** 06 · **Size:** large · **Fixes:** structural gaps 3 (reports), 5, and part of 6 in [assessment.md](assessment.md)

## Why

How well an agent does depends on what it sees:

- Clean error text lets it correct itself.
- Server instructions tell it the workflow.
- Tool annotations let the host auto-approve reads and ask a human before writes. That's real human approval, unlike `confirm=true`, which the model sets itself.
- Toolsets keep schema-editing tools away from an agent doing data work.
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
  - start with `get_site_info`;
  - find DocTypes and records with search;
  - read the compact schema before writing;
  - keep limits small;
  - every write is preview, then confirm with tokens;
  - a permission denial is final.
- [ ] **Per-argument descriptions.** Use `Annotated[..., Field(description=...)]` (pydantic) for at least: `filters` (shapes and operators, with an example), `fields`, `order_by`, `expected_modified`, `confirm`, `preview_id`/`expected_preview`, and `detail`. Check that the descriptions appear in `list_tools` (contract test).
- [ ] **Tool annotations.** Every tool gets `ToolAnnotations`:
  - read tools: `readOnlyHint=True`;
  - create and update tools: `readOnlyHint=False`;
  - `delete_document`, `rollback_export`, and `set_role_permissions`: `destructiveHint=True`;
  - `idempotentHint` where it's true;
  - a human-readable `title` for each.

  Assert in the contract test that every tool has annotations.

### Toolsets and read-only mode

- [ ] **Toolsets.** Group the tools:
  - `read`: discovery, schema, list, get, count, search, reports, site info;
  - `data`: create, update, delete, plus the stage 08–10 verbs later;
  - `customize`: custom field, property setter, role permissions, export, rollback;
  - `developer`: `create_doctype`.

  Select them with `FRAPPE_MCP_TOOLSETS` (comma-separated). `FRAPPE_MCP_READ_ONLY=YES` is shorthand for `read` only. Remove the tools that aren't selected at startup with `server.remove_tool`, so the host never sees them.
- [ ] **Default toolsets: ask the user first.** Recommend `read,data` as the default, with `customize` and `developer` opt-in. This changes what existing users see. If the user hasn't decided, default to all four and record that in Handoff.
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

Lifecycle verbs (stage 08). MCP prompts and resources (backlog).

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Default toolsets chosen (and who decided):
- Report permission finding:
- Deviations from this plan:
- Notes for the next stage:
