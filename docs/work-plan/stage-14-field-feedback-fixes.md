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

- [ ] **Wrap transport errors where each tool returns, not in `_request`.** The reload helpers need the raw `httpx.TransportError`. In the wrapper that `_tool()` registers, catch the `httpx.TransportError` that escapes a tool and raise `RuntimeError`. The message names:
  - the site as `_display_url(FRAPPE_URL)`;
  - the kind of failure (connection refused, timed out after N s, name not resolved);
  - the next step: "is the site running? Run `mcp-frappe check`."

  Keep the function signature visible to FastMCP (`functools.wraps`), and check in the contract test that argument descriptions still appear.
- [ ] **Fail fast on connect.** Build the client with `httpx.Timeout(timeout, connect=min(timeout, 5.0))`, so a site that is down fails in seconds. Reads keep the full timeout.

### Keep the record tools to record data

- [ ] **Redirect schema DocTypes.** `create_document`, `update_document`, and `delete_document` refuse `_SCHEMA_DOCTYPES`, and the error names the right tool: `create_doctype`, `update_doctype`, `create_custom_field`, `set_field_property`, or `set_role_permissions`. For each DocType, decide whether a dedicated tool covers deletion; if not, say so in the error.
- [ ] **Redirect configuration DocTypes.** `Workflow`, `Notification`, `Report`, and `Workspace` point to their `upsert_*` tool.
- [ ] **Refuse code DocTypes.** Add a `_CODE_DOCTYPES` denylist, built from the Frappe source you read: at least Server Script, Client Script, System Console, Print Format, Webhook, Assignment Rule, Web Form, Custom HTML Block, and Email Template. Record the list and the Frappe version you checked in the Handoff.
- [ ] **Refuse writes to `Code` fields in any DocType.** A denylist can't cover custom apps, so the record tools also refuse values for fields whose `fieldtype` is `Code`. Check whether any common record DocType stores plain data in a `Code` field. If one does, add a narrow allowlist with a reason for each entry.
- [ ] **Update the e2e runners and unit tests** that wrote such records through the record tools, if any.

### Site label (FR-2026-001)

- [ ] **Add an optional `FRAPPE_SITE_LABEL`:** 1–64 characters matching `_SAFE_NAME`, validated at startup. `get_site_info` returns it as `site_label` (`null` when unset), and `check` prints it. The label is never used for routing.

### Remote read-only default (FR-2026-003, if the user approves)

- [ ] In `_selected_toolsets()`: when `FRAPPE_URL` isn't a local target and `FRAPPE_MCP_READ_ONLY` is unset, select `read` only. `NO` enables writes explicitly. Log the decision to stderr at startup, and print it in `check` ("read-only: remote target").

### Field-feedback docs

- [ ] **Move the project-specific bullet** out of `docs/ai-agent-guide.md` (the instruction for the consuming project) and into that project's own agent instructions; ask the user where they are. Replace it with a generic line: report a missing capability or a defect as a GitHub issue.
- [ ] **Add FR-2026-005** for calling an app's whitelisted method, from the `product-lines-null-central-2026-11-10` task. Link stages 16 and 17.
- [ ] **Close FR-2026-002** (several sites in one process) as won't do. The rationale: the global session, metadata cache, apps path, and preview tokens all assume one site, and hosts already isolate sites by running one server each.
- [ ] **Link FR-2026-004** (sync a DocType that exists in the app but not on the site) to stage 18. Until then, `list_app_drift` already says to run `bench migrate`; make `create_doctype`'s refusal say the same when the files exist.
- [ ] **Separate requirements from observations.** FR-2026-001 to 003 came from a usage policy, not from a task. Add a **Source:** line (`field` or `requirement`) to the template and to each entry.
- [ ] **Add columns to the usage log:** `MCP version`, and `Blocked by MCP` (`no`, or the FR or BUG id). Fix the inconsistent rows:
  - `selector-follow-up-2026-11-10` has no report of its own;
  - the `image-sync-*` rows list "read-only Item queries" instead of exact tool names;
  - the `product-lines-null-central` row leaves out `count_documents`.
- [ ] **Keep separate task reports only for tasks with a write, a failure, or a gap.** Fold the others into their log row. Write this rule in `docs/field-feedback/README.md`.
- [ ] Apply decision 2 (internal names).

### Tests

- [ ] A refused connection, a connect timeout, and a DNS failure each give the new message, with no credentials and no path in it. The reload helpers still see the raw `httpx.TransportError`.
- [ ] Each redirected or refused DocType gives the expected message. A `Code` field write is refused on an ordinary DocType.
- [ ] `FRAPPE_SITE_LABEL`: valid, invalid, and unset values; `get_site_info` and `check` output.
- [ ] Remote default, if approved: each combination of local or remote target and unset, `YES`, or `NO` gives the expected toolsets.

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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- User decisions (remote default, internal names):
- Code DocTypes and `Code`-field exceptions (Frappe version checked):
- Deviations from this plan:
- Notes for the next stage:
