# Stage 08: Workflows and Notifications

**Depends on:** 07 · **Size:** large · **Fixes:** structural gap 10 in [assessment.md](assessment.md) (first half)

## Why

After a developer builds a DocType, the next requests are usually "add an approval flow" and "email the manager when it's submitted". Both are configuration records, not code, but they are tedious to set up by hand: a Workflow needs Workflow State and Workflow Action Master records to exist first, and a Notification has a dozen interacting fields. Generic `create_document` can write them, but it can't check states against roles, fields against the DocType, or keep the model away from the fields Frappe evaluates as code.

## Read first

- `standalone.py`: `create_document`, `update_document`, `_expected_modified`, `_assert_remote_module`, `_wait_for_server_ready`, the stage 06 `update_doctype` (diff and reload reconciliation), `export_customizations` (fixtures path after stage 04)
- Frappe:
  - `frappe/workflow/doctype/workflow/workflow.py`: what `on_update` does (it should create the `workflow_state` Custom Field and deactivate other workflows for the same DocType; check both), and the validation of states and transitions;
  - the child DocTypes `Workflow Document State` and `Workflow Transition`, and the masters `Workflow State` and `Workflow Action Master`;
  - `frappe/model/workflow.py`: where a transition's `condition` is evaluated;
  - `frappe/email/doctype/notification/notification.py`: events, channels, recipients, where `condition` is evaluated, how `subject` and `message` are rendered, and what happens on save when `is_standard=1` in developer mode (it should write files under `<module>/notification/`; check).

## Design

- **Shared helper for configuration records.** Add `_save_config_record(doctype, values, name=None, expected_modified=None)`: create or update one record with its child tables, send `modified` in the body on updates, invalidate the metadata cache, and reconcile a Bench reload the way stage 06 does (standard Notifications write files). Stage 09 reuses it for Reports and Workspaces.
- **Structured input, not raw records.** The tools take a small, validated shape and build the record. The model never sends the raw child rows.
- **Code fields (rule 2 of the README).**
  - Workflow Transition `condition` and Notification `condition` are Python expressions Frappe evaluates. Refuse them. AGENTS.md forbids adding Python evaluation, and these would add it indirectly.
  - Notification `subject` and `message` are Jinja templates Frappe renders. **Ask the user at the start of the stage** which rule to use. Recommended: accept plain text plus `{{ doc.<fieldname> }}` placeholders, check each fieldname against the DocType, and reject `{%`, filters, and any other expression. Record the decision in Handoff.
  - Notification events `Custom` and `Method`, and channels other than `Email` and `System Notification`, are out of scope.

## Tasks

### Workflows

- [ ] **`get_workflow(doctype)`.** Return the active Workflow for a DocType (states, transitions, `workflow_state_field`, `is_active`), or none. Read-only.
- [ ] **`upsert_workflow(doctype, name, states, transitions, is_active=True, send_email_alert=False, confirm=False, expected_modified=None)`.**
  - `states`: `[{"state", "doc_status", "allow_edit", "update_field"?, "update_value"?}]`. `doc_status` is `0`, `1`, or `2`, and only for a submittable DocType can it be `1` or `2`. `allow_edit` must be an existing Role. `update_field` must be a field of the DocType.
  - `transitions`: `[{"state", "action", "next_state", "allowed", "allow_self_approval"?}]`. `state` and `next_state` must be listed in `states`; `allowed` must be an existing Role. No `condition` key.
  - Check that every state is reachable from the first one, and warn about states with no way out that aren't final (`doc_status` 1 or 2).
  - The preview lists the Workflow State and Workflow Action Master records that will be created because they don't exist, the Custom Field Frappe will add for the state field, and, if `is_active`, the workflow it will deactivate. On update, it shows a states and transitions diff and the `modified` to send back.
  - On confirm, create the missing masters, then save the Workflow with `_save_config_record`.

### Notifications

- [ ] **`upsert_notification(name, document_type, event, channel, recipients, subject, message, confirm=False, expected_modified=None, ...)`.** Supported events: `New`, `Save`, `Submit`, `Cancel`, `Value Change` (with `value_changed` checked against the DocType), and `Days Before`/`Days After` (with `date_changed` a Date or Datetime field and `days_in_advance` a positive integer). Recipients: `receiver_by_document_field` (a field holding an email or a User link), `receiver_by_role` (an existing Role), and `cc`/`bcc` as plain addresses. `is_standard` and `module` are optional; with `is_standard=True` the module must belong to a writable app.
  - Validate `subject` and `message` with the template rule chosen above.
  - The preview shows the record and, for a standard Notification, the files Frappe will write.
- [ ] **`list_notifications(document_type=None, limit=50)`.** Read-only: name, DocType, event, channel, enabled.

### Export

- [ ] **Workflows travel as fixtures.** A Workflow has no module, so Frappe can't write it to a module folder. Check whether `export_customizations` (after stage 04) can register `Workflow`, `Workflow State`, and `Workflow Action Master` fixtures for one DocType, filtered by name. If it can't, extend the existing fixtures path, keeping its preview, backups, and rollback.

### Tests

- [ ] Workflow: unknown roles, states not in the list, `doc_status` 1 on a non-submittable DocType, a `condition` key, and an unreachable state are all refused or warned; missing masters appear in the preview; an update shows a diff and needs `expected_modified`.
- [ ] Notification: each supported event validates its fields; unsupported events and channels are refused; the template rule accepts `{{ doc.field }}` for a real field and rejects `{%`, unknown fields, and other expressions; `condition` is refused.
- [ ] `_save_config_record`: `modified` in the body on updates, cache invalidation, reload reconciliation.

## Acceptance criteria

- The new tools are registered and in the contract test.
- `docs/security.md` explains why conditions are refused and documents the template rule. `docs/ai-agent-guide.md` has an "add an approval workflow" recipe and a "notify on submit" recipe. `docs/tool-reference.md` is updated.
- **Optional live check (only if the user asks, on a disposable site):** add a two-step workflow and a System Notification to a test DocType, then remove them.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Applying workflow actions to documents ([backlog](backlog/lifecycle-workflow.md)). Transition conditions, Jinja beyond the chosen rule, Slack and SMS channels, `Custom` and `Method` events.

## Handoff

- Status: done.
- Branch / commits: `stage-08-workflow-notification`, based on `main` at `5b27eed` (stages 05-07 merged).
- What changed:
  - `_save_config_record(doctype, values, name, expected_modified=None, *, forget=())`: creates with `POST` (no `expected_modified`) or updates with `PUT` + `modified`; maps `TimestampMismatchError` to the stale-preview error; on `httpx.TransportError` waits with `_wait_for_server_ready`, re-reads, and succeeds only if the record holds the sent values (`_record_holds`, child rows in order) and, on update, `modified` moved; never resends. Clears the metadata cache for `doctype` and `forget` in `finally`. Returns `(record, reconciled)`.
  - `get_workflow(doctype)`: the active Workflow (states, transitions with `has_condition` instead of the condition text, `workflow_state_field`), or `workflow: null`, plus `inactive` names.
  - `upsert_workflow(doctype, name, states, transitions, is_active=True, send_email_alert=False, workflow_state_field="workflow_state", expected_modified=None, confirm=False)`. Refuses: child/Single DocTypes, unknown keys, `condition`, `evaluate_as_expression`, unknown roles (one `Role` read), states not in the list, duplicate states or (state, action, role) transitions, `doc_status` 1/2 on a non-submittable DocType, a first state that isn't draft, Frappe's `validate_docstatus` violations, an `update_field` that isn't a value field, a state field that isn't Link/Data/Select, a Workflow name used by another DocType, and any state unreachable from the first. Warns: draft states with no way out, kept Desk conditions, records with no state that `on_update` will set (counted with `get_count`). Preview: `states`, `transitions`, `creates` (missing Workflow State / Workflow Action Master), `state_field` (with the Custom Field Frappe adds), `deactivates`, and on replacement `modified` + `changes` (properties; states added/removed/changed/order_changed; transitions added/removed/changed). Confirm creates the masters, then saves; existing child rows keep their `name` and unmanaged columns (`_merged_rows`), because `update_child_table` saves an omitted column as its default.
  - `list_notifications(document_type=None, limit=50)`: name, DocType, event, channel, enabled, is_standard, module; `truncated`.
  - `upsert_notification(name, document_type, event, channel, recipients, subject, message, value_changed, date_changed, days_in_advance, filters, enabled, is_standard, module, expected_modified, confirm)`. Events and channels are `Literal`s in the schema and checked again in code. Value Change / Days events check their fields; Submit/Cancel need a submittable DocType; child tables take only day events; Email Queue is refused (Frappe's list). Recipients: `owner`, a Data/Email or Link/User field, an existing role, and plain `cc`/`bcc` addresses (Email only); `condition` refused. `filters` (user's choice, see below) are checked with `_assert_filters`, own fields only, and saved as `condition_type: Filters`; `condition` is always sent as null. System Notification also sends `notification_message` (Frappe takes the alert text only from it). Standard rules need a non-custom Module Def of a writable app; the preview lists `app_files`. Replacing a rule warns when it clears a Desk condition or recipient conditions.
  - `_assert_template()` implements the template rule below; `_template_fields()` lists the fields a placeholder may print.
  - Export: `export_customizations(..., with_workflows=False)` writes the Workflows of `doctypes` and their masters to `fixtures/workflow.json`, `workflow_state.json`, `workflow_action_master.json` in `export_json`'s row format (`_fixture_row`), keeping other rows and dropping vanished Workflows of these DocTypes (`_fixture_file`, now shared with Client Scripts). `_fixture_plan(app_path, entries)` now takes `{dt: {field: values}}` and keeps every `_MANAGED_FIXTURES` entry (Client Script, Workflow, Workflow State, Workflow Action Master) written by earlier exports; backups and rollback are unchanged.
  - `_forget_meta_after_write` also clears the cache after a generic `create/update/delete_document` on `Workflow` (it can add a Custom Field).
  - Tests: `tests/test_config_records.py` (86 cases), four export tests, an MCP round-trip for `upsert_notification` (enum schema, and a stray `condition` argument never reaches Frappe), tool-set test, and the two `_fixture_plan` callers updated.
  - Docs: tool-reference ("Workflows and Notifications", export option), ai-agent-guide ("Add an approval workflow" and "Notify on submit" recipes), security ("Code fields and templates"), architecture ("Configuration records"), compatibility, capability study, `CLAUDE.md`.
- Template rule chosen (and who decided): the user chose "placeholders + safe filters" at the start of the session. Plain text (Markdown/HTML) plus `{{ doc.<fieldname> }}` where the field is a value field of the DocType or a standard column; optional filters `upper`, `lower`, `title`, `capitalize`, `trim`, `striptags`, `escape`/`e`, `int`, `str`, `flt(int)`, `round(int, "common"|"ceil"|"floor")`, `truncate(int)`, `default(str)`, literal arguments only (parsed with `ast.literal_eval`, no braces in strings). Refused: `{%`, `{#`, `{{-`, other variables, attribute chains, indexing, calls, operators, other filters, unclosed `{{`. The user also chose to accept Notification `filters` (JSON, evaluated as data) as the safe replacement for conditions.
- Workflow `on_update` findings (state field, deactivation): v16.33.1, local Bench. `validate` runs `set_active` (an active Workflow sets `is_active=0` on every other Workflow of the DocType) and `validate_docstatus` (2 -> anything, 1 -> 0, and 0 -> 2 are refused). `on_update` runs `create_custom_field_for_workflow_state` (a hidden Link to Workflow State with `allow_on_submit` and `no_copy`, only when `get_meta` has no such field) and `update_default_workflow_status` (records with an empty state get the first state of their docstatus, by direct query, on every save). Workflow States also have `evaluate_as_expression`, which evaluates `update_value`; refused.
- Standard Notification files finding: `on_update` calls `export_module_json`, which, when `is_standard`, developer mode is on, and not importing, runs `export_to_files` (creating `__init__.py` in the module's `notification/` folder and the record folder) and writes `<module>/notification/<scrub(name)>/<scrub(name)>.json`; then `on_update` writes the message to `<name>.md` (`.html`/`.txt` by `message_type`) and a `<name>.py` `get_context` stub if missing. A standard rule's message is later read from that file, and the `.py` module's `get_context` runs before rendering (Frappe's stub; this server never writes it). `validate_standard` refuses saving an enabled standard rule without developer mode; deleting one needs developer mode too.
- Other Frappe findings: `validate_condition` runs `safe_eval` on save; `get_list_of_recipients` runs each recipient `condition` with `safe_eval` and renders `cc`/`bcc` as Jinja when they contain `{`; `create_system_notification` uses `subject` (or `notification_title`) as the title and only `notification_message` as the text. Notification's `autoname` is Prompt and its controller falls back to `subject` when no name is set; a REST `POST` with `name` keeps it. Fixtures: `import_fixtures` imports every `fixtures/*.json` in name order through `import_file_by_path(force=True)` with `ignore_links`, so masters importing after `workflow.json` is fine, and migrate overwrites site edits on every run. Frappe's Jinja filters beyond the built-ins are `json`, `len`, `int` (cint), `str` (cstr), `flt`; frappe's `hooks.py` adds globals, not filters.
- Deviations from this plan:
  - `_save_config_record(doctype, values, name, expected_modified=None, *, forget=())`: `name` is always required (reconciling a create needs it) and `expected_modified` selects update; `forget` names the DocTypes whose metadata the save changes.
  - An unreachable state is refused rather than warned about; dead-end draft states are warned about.
  - Added `workflow_state_field` to `upsert_workflow`, `enabled` and `filters` to `upsert_notification` (the latter by the user's choice).
  - Replacing a Workflow keeps an existing transition's Desk condition (with a warning), because dropping it would silently widen who can act; replacing a Notification clears conditions (with a warning), because the tool's `filters` replace them.
  - `export_customizations` exports Workflows only with the new `with_workflows=True`, so existing exports are unchanged.
  - The e2e runners were not changed: no existing signature or return shape changed (the export option defaults to off) and none exercises the new tools yet.
- Notes for the next stage:
  - Stage 09 should reuse `_save_config_record` (pass `forget` only when the save changes a DocType's metadata) and `_assert_template` if Report or Workspace fields hold Jinja. Report `query`/`javascript`, and Script Reports, are code (rule 2).
  - `_fixture_plan` and `_fixture_file` handle any fixture DocType; add new ones to `_MANAGED_FIXTURES` so later exports keep their hook entries.
  - Not run live (not requested yet at the time of writing): a two-step workflow and a System Notification on a test DocType. Still open: how Frappe normalizes `doc_status` and `allow_self_approval` on read, which `_record_holds` reconciliation and the diff assume (`"0"` strings and `0`/`1`).
  - v15 was not compared.
