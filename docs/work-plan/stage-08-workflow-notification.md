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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Template rule chosen (and who decided):
- Workflow `on_update` findings (state field, deactivation):
- Standard Notification files finding:
- Deviations from this plan:
- Notes for the next stage:
