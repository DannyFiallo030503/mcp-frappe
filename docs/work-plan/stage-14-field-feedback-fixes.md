# Stage 14: Fixes from field use

**Depends on:** 13 · **Size:** medium · **Source:** [field feedback](../field-feedback/README.md) and the 2026-11-19 review ([comparison](native-mcp-comparison.md))

## Why

The first real use of the server (14 tasks on one development site) found five things to fix:

- **When the site is down, tool errors give no context.** Three tasks hit this. An agent sees only `[Errno 111] Connection refused`, or waits for the 30-second timeout. It isn't told which site, or what to check.
- **The record tools write any DocType.** `create_document`, `update_document`, and `delete_document` accept `Custom Field`, `Property Setter`, `DocType`, `Workflow`, `Notification`, `Report`, `Server Script`, and `Client Script` records. That skips the checks the dedicated tools run, and it breaks the rule against writing fields Frappe evaluates as code (ROADMAP, Security boundaries). One field task changed a Custom Field's type through `update_document`. The preview didn't warn, and Frappe then rejected the change.
- **There is no site label (FR-2026-001).** When several connections are configured, an agent can't check which site it is talking to.
- **Remote sites get write tools by default (FR-2026-003).**
- **The field-feedback docs need cleanup.** `docs/ai-agent-guide.md` has a project-specific instruction. One gap has no feature request. The usage log can't show which problems the MCP caused.

## Read first

- `standalone.py`: `_tool`, `_client`, `_request`, `_timeout`, `_display_url`, `create_document`, `update_document`, `delete_document`, `_SCHEMA_DOCTYPES`, `_selected_toolsets`, `_setup_check`, `get_site_info`
- The reload-tolerance helpers that catch `httpx.TransportError`: `_wait_for_doctype`, `_wait_for_server_ready`, `_doctype_matches`, `_wait_for_document_deleted`
- `docs/field-feedback/` (all files) and `docs/ai-agent-guide.md`
- Frappe source: which core DocTypes have `Code` fields, or fields evaluated as Python, Jinja, or JavaScript. Check `frappe/core/doctype/server_script`, `frappe/custom/doctype/client_script`, `frappe/email/doctype/notification`, `frappe/workflow/doctype/workflow_transition`, `frappe/core/doctype/report`, `frappe/printing/doctype/print_format`, `frappe/integrations/doctype/webhook`, `frappe/automation/doctype/assignment_rule`, `frappe/website/doctype/web_form`, and `frappe/desk/doctype/custom_html_block`.

## Decisions to ask the user about first

1. **Remote write default.** Should a target that isn't loopback or `.localhost` start in read-only mode unless `FRAPPE_MCP_READ_ONLY=NO` is set? This breaks existing remote setups: it needs a **Breaking** entry in CHANGELOG and a minor version bump. If the user says no, keep the current default and only make `check` and the startup log warn.
2. **Internal names in public docs.** May the field-feedback files keep the project's internal names (the consuming project, its custom app, its services)? If not, rename `docs/field-feedback/cubazone/` to `docs/field-feedback/reports/`, and replace the alias and names with generic ones.

## Tasks

### Clear errors when the site can't be reached

- [x] **Wrap transport errors where each tool returns, not in `_request`.** The reload helpers need the raw `httpx.TransportError`. In the wrapper that `_tool()` registers, catch the `httpx.TransportError` that escapes a tool and raise `RuntimeError`. The message names:
  - the site as `_display_url(FRAPPE_URL)`;
  - the kind of failure (connection refused, timed out after N s, name not resolved);
  - the next step: "is the site running? Run `mcp-frappe check`."

  Keep the function signature visible to FastMCP (`functools.wraps`), and check in the contract test that argument descriptions still appear.
- [x] **Fail fast on connect.** Build the client with `httpx.Timeout(timeout, connect=min(timeout, 5.0))`, so a site that is down fails in seconds. Reads keep the full timeout.

### Keep the record tools to record data

- [x] **Redirect schema DocTypes.** `create_document`, `update_document`, and `delete_document` refuse `_SCHEMA_DOCTYPES`, and the error names the right tool: `create_doctype`, `update_doctype`, `create_custom_field`, `set_field_property`, or `set_role_permissions`. For each DocType, decide whether a dedicated tool covers deletion; if not, say so in the error.
- [x] **Redirect configuration DocTypes.** `Workflow`, `Notification`, `Report`, and `Workspace` point to their `upsert_*` tool.
- [x] **Refuse code DocTypes.** Add a `_CODE_DOCTYPES` denylist, built from the Frappe source you read: at least Server Script, Client Script, System Console, Print Format, Webhook, Assignment Rule, Web Form, Custom HTML Block, and Email Template. Record the list and the Frappe version you checked in the Handoff.
- [x] **Refuse writes to `Code` fields in any DocType.** A denylist can't cover custom apps, so the record tools also refuse values for fields whose `fieldtype` is `Code`. Check whether any common record DocType stores plain data in a `Code` field. If one does, add a narrow allowlist with a reason for each entry.
- [x] **Update the e2e runners and unit tests** that wrote such records through the record tools, if any.

### Site label (FR-2026-001)

- [x] **Add an optional `FRAPPE_SITE_LABEL`:** 1–64 characters matching `_SAFE_NAME`, validated at startup. `get_site_info` returns it as `site_label` (`null` when unset), and `check` prints it. The label is never used for routing.

### Remote read-only default (FR-2026-003, if the user approves)

- [x] In `_selected_toolsets()`: when `FRAPPE_URL` isn't a local target and `FRAPPE_MCP_READ_ONLY` is unset, select `read` only. `NO` enables writes explicitly. Log the decision to stderr at startup, and print it in `check` ("read-only: remote target").

### Field-feedback docs

- [x] **Move the project-specific bullet** out of `docs/ai-agent-guide.md` (the instruction for the consuming project) and into that project's own agent instructions; ask the user where they are. Replace it with a generic line: report a missing capability or a defect as a GitHub issue.
- [x] **Add FR-2026-005** for calling an app's whitelisted method, from the `product-lines-null-central-2026-11-10` task. Link stages 16 and 17.
- [x] **Close FR-2026-002** (several sites in one process) as won't do. The rationale: the global session, metadata cache, apps path, and preview tokens all assume one site, and hosts already isolate sites by running one server each.
- [x] **Link FR-2026-004** (sync a DocType that exists in the app but not on the site) to stage 18. Until then, `list_app_drift` already says to run `bench migrate`; make `create_doctype`'s refusal say the same when the files exist.
- [x] **Separate requirements from observations.** FR-2026-001 to 003 came from a usage policy, not from a task. Add a **Source:** line (`field` or `requirement`) to the template and to each entry.
- [x] **Add columns to the usage log:** `MCP version`, and `Blocked by MCP` (`no`, or the FR or BUG id). Fix the inconsistent rows:
  - `selector-follow-up-2026-11-10` has no report of its own;
  - the `image-sync-*` rows list "read-only Item queries" instead of exact tool names;
  - the `product-lines-null-central` row leaves out `count_documents`.
- [x] **Keep separate task reports only for tasks with a write, a failure, or a gap.** Fold the others into their log row. Write this rule in `docs/field-feedback/README.md`.
- [x] Apply decision 2 (internal names).

### Tests

- [x] A refused connection, a connect timeout, and a DNS failure each give the new message, with no credentials and no path in it. The reload helpers still see the raw `httpx.TransportError`.
- [x] Each redirected or refused DocType gives the expected message. A `Code` field write is refused on an ordinary DocType.
- [x] `FRAPPE_SITE_LABEL`: valid, invalid, and unset values; `get_site_info` and `check` output.
- [x] Remote default, if approved: each combination of local or remote target and unset, `YES`, or `NO` gives the expected toolsets.

## Acceptance criteria

- With the site stopped, the first tool call fails within about 5 seconds, with a message that names the site and the next step.
- The record tools can't write schema, configuration, or code records, or any `Code` field.
- Docs:
  - `docs/tool-reference.md`: the record-tool limits and `site_label`;
  - `docs/ai-agent-guide.md`: the generic feedback line;
  - `docs/security.md`: one site per process, the label is informational, the code-record refusal, and the remote default;
  - `docs/installation-and-configuration.md`: the new variables;
  - CHANGELOG;
  - `docs/field-feedback/`: updated as above.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Calling methods (stage 17). Running Bench commands (stage 18). Several sites in one process (closed).

## Handoff

- Status: done.
- Branch / commits: `stage-14-field-feedback-fixes`, based on `main` at `ee743fb`; one commit, "Stage 14: fixes from field use".
- What changed:
  - **Unreachable site.** `_tool()` registers a `functools.wraps` wrapper with FastMCP (FastMCP reads the signature with `inspect.signature(..., eval_str=True)`, which follows `__wrapped__`) and returns the original function. The wrapper turns an escaping `httpx.TransportError` into a `RuntimeError` from `_unreachable_message()`: the site as `_display_url(FRAPPE_URL)`, the failure (connection refused, `no connection within N s`, `no answer within N s`, host name not resolved, connection closed), and "run `mcp-frappe check`". It never includes the exception text, which can echo the URL. Internal callers and the reload helpers still get the raw error. `check` uses the same message without the "run check" hint. The client is built with `httpx.Timeout(timeout, connect=min(timeout, 5))`. Checked on real sockets (no site involved): refused 0.4 s, non-routable address 5.2 s, DNS failure 0.1 s.
  - **Record tools.** `_assert_record_doctype()` runs before any request: `_RECORD_TOOL_REDIRECTS` names the tool to use for `DocType`, `DocField`, `DocPerm`, `Custom DocPerm`, `Custom Field`, `Property Setter`, `Customize Form`, `Workflow`, `Notification`, `Report`, and `Workspace`; `_CODE_DOCTYPES` are refused by all three tools. `_assert_no_code_fields()` refuses values for `Code` fields (child rows included) on create and update, plus `_TEMPLATE_FIELDS`. The DocType-matrix e2e runner now deletes its temporary DocTypes with `delete_doctype`.
  - **`FRAPPE_SITE_LABEL`**, validated at startup and in `check` (1–64 characters, `_SAFE_NAME`); `get_site_info` returns `site_label`, and `check` prints `label <name>` in the settings row.
  - **Remote read-only default** in `_selected_toolsets()` via `_read_only_reason()`; startup logs `_remote_mode_notice()` as a warning, and `check` shows `read (read-only: remote target; ...)` or a `warn` row when `FRAPPE_MCP_READ_ONLY=NO`.
  - `create_doctype` says to run `bench migrate` when the DocType's JSON file already exists in the app (FR-2026-004).
  - Docs: tool reference (record-tool limits table, `site_label`, unreachable errors, remote default), AI agent guide (generic feedback line, `site_label` check, unreachable-site handling, Custom Field edits through `set_field_property`), security (one site per process, label, remote default, code-record refusal), installation (new variables, connect timeout), architecture, CHANGELOG (0.3.0 is unreleased, so the entries went into its Breaking, Added, and Changed sections, with no further version bump), README, ROADMAP, CLAUDE.md, compatibility (the remote check is a gap).
  - Field feedback: `cubazone/` renamed to `reports/`; five reports kept (a write, a failure, or a gap), eight folded into their log rows; FR-2026-005 added; FR-2026-001 and 003 resolved; FR-2026-002 closed as won't do; FR-2026-004 linked to stage 18 and the new refusal; `Source` lines; `MCP version` and `Blocked by MCP` columns.
- User decisions (remote default, internal names): remote targets are read-only unless `FRAPPE_MCP_READ_ONLY=NO` (Breaking). Internal names genericized: alias `dev-local`, "the consuming project", "a downstream service" and "a downstream projection", "a custom app". The project-specific bullet in `docs/ai-agent-guide.md` was removed, not moved; the user adds it to the consuming project's own agent instructions. A third decision, asked during the stage: `delete_document` still deletes Custom Field, Property Setter, Custom DocPerm, Workflow, Notification, Report, and Workspace records, because no other tool deletes them and the e2e cleanup depends on it.
- Code DocTypes and `Code`-field exceptions (Frappe version checked): checked against Frappe **v16.55.1** and ERPNext v16 in the local Bench, by listing every DocType JSON with a `Code` field and every `safe_eval`/`render_template` call site. `_CODE_DOCTYPES`: Address Template, Assignment Rule, Automation Flow (v16 only), Client Script, Custom HTML Block, Email Template, Letter Head, Navbar Settings, Print Format, Print Format Field Template, Print Style, Scheduled Job Type, Server Script, System Console, Web Form, Web Page, Web Template, Webhook, Website Script, Website Theme. `_PLAIN_CODE_FIELDS`: Translation `source_text` and `translated_text` (plain text for `frappe._`); `item_tax_rate` on Purchase Invoice/Purchase Order/Purchase Receipt/Quotation/Sales Order/Supplier Quotation Item (JSON ERPNext reads with `json.loads`; agents copy it with item rows); Purchase Order `customer_contact_email` (Code with Email options). `_TEMPLATE_FIELDS`: Auto Repeat `subject` and `message` (Data/Text rendered as Jinja). Many other `Code` fields hold JSON filters or logs (Number Card, Dashboard Chart, Kanban Board, Error Log, ...); they are refused, since some, such as `dynamic_filters_json`, are evaluated as JavaScript, and none is common test data. v15 was not re-checked field by field.
- Deviations from this plan:
  - Deletes of schema and configuration records that no tool covers stay allowed (user decision above), instead of "say so in the error".
  - A remote target that names a writable toolset in `FRAPPE_MCP_TOOLSETS` without `FRAPPE_MCP_READ_ONLY=NO` stops at startup, like `YES` does, rather than having the toolset dropped silently.
  - `_TEMPLATE_FIELDS` (Auto Repeat) goes beyond `Code` fields, per rule 2 of the work plan.
  - The two `image-sync-*` rows say "Item reads (tool names not recorded)": the sanitized reports never recorded the exact tools, and guessing them would break the "observations, not assumptions" rule. The `MCP version` of every past row is `unknown` for the same reason.
  - Task slugs (`product-lines-null-central-2026-11-10`) were kept as references to the original task records, although they contain a service name.
- Notes for the next stage:
  - Internal names remain outside `docs/field-feedback/`: `tests/test_standalone.py` (`erp_app`, `CubaZone` module), `docs/native-process-audit.md`, and `e2e/history/`. Decision 2 covered only the field-feedback files.
  - `update_doctype` and `set_field_property` still accept `depends_on`, `mandatory_depends_on`, and `read_only_depends_on`, which Desk evaluates as JavaScript (`eval:`); `docs/security.md` documents it. Revisit it if the code rule is tightened.
  - The remote default and the record-tool refusals are unit-tested only; no e2e runner covers them yet (listed in `docs/compatibility-and-validation.md`).
  - Stage 15 (tool-list cost): the new docstrings on the three record tools add a few lines each.
