# Stage 08: Document lifecycle and workflow

**Depends on:** 07 · **Size:** large · **Fixes:** structural gap 4 (lifecycle) in [assessment.md](assessment.md)

## Why

ERPNext transactions mean nothing until they are submitted. Approvals run through workflows. Without submit, cancel, amend, and workflow actions, the server can only create drafts. This stage also makes previews worth reviewing: show what will change, not just which fields.

## Read first

- `standalone.py`: `create_document`, `update_document`, `delete_document`, `_expected_modified`
- Frappe:
  - `frappe/client.py`: `submit(doc)` rebuilds the document from the full dict, so `modified` is timestamp-checked; `cancel(doctype, name)` is not.
  - `frappe/model/workflow.py`: `get_transitions(doc)`, `apply_workflow(doc, action)`, and how an active workflow blocks direct docstatus changes.
  - `frappe/model/document.py`: `check_docstatus_transition`, amend handling.
  - `frappe/model/naming.py`: amended names (`-1`, `-2` suffixes).
- For ideas only (in-process API, not REST; don't import): `legacy/frappe_app/mcp_frappe/tools/documents.py` (`_change_docstatus`) and `legacy/frappe_app/mcp_frappe/tools/advanced.py` (workflow tools).

## Tasks

- [ ] **`submit_document(doctype, name, expected_modified=None, confirm=False)`.**
  - The preview shows `docstatus`, `modified`, and whether the DocType is submittable (from the stage 05 metadata). It also says whether an active Workflow exists for the DocType; if one does, refuse and point to the workflow tools.
  - On confirm, read the document, check `modified`, and POST `frappe.client.submit` with the full document dict.
- [ ] **`cancel_document(doctype, name, expected_modified=None, confirm=False)`.** The document must have `docstatus` 1. The preview warns that cancelling may reverse ledger or stock entries. On confirm, check `modified`, then call `frappe.client.cancel`.
- [ ] **`amend_document(doctype, name, confirm=False)`.** Frappe has no single endpoint for this. Do it the way Desk does:
  1. Read the cancelled document (`docstatus` 2).
  2. Copy it, dropping `name`, `docstatus`, `modified`, `creation`, `owner`, `modified_by`, and `amended_from`, plus child rows' `name`, `parent`, `parenttype`, and `parentfield`.
  3. Set `amended_from` to the original name, and insert the copy as a new draft.

  Confirm in `naming.py` that Frappe assigns the amended name. The preview shows the draft's fields.
- [ ] **`get_workflow_actions(doctype, name)`.** Return the current `workflow_state` and the allowed transitions (`action`, `next_state`, `allowed`) from `frappe.model.workflow.get_transitions`, called with the document JSON.
- [ ] **`apply_workflow_action(doctype, name, action, expected_modified=None, confirm=False)`.** The preview shows the transition. On confirm, call `apply_workflow`. Read the source to see whether passing the document with `modified` gives a server-side stale check. If it doesn't, keep the pre-check and document that a race is possible between the check and the action.
- [ ] **Diffs in update previews.** `update_document` previews return `changes: {field: {"from": old, "to": new}}`. For `Table` fields, warn when the new list leaves out existing row names: Frappe will **delete** those rows. Explain how to keep rows (include each existing row with its `name`).
- [ ] **Mandatory fields in create previews.** `create_document` previews list the mandatory fields that are missing (`reqd` with no `default`, from the metadata). The preview still succeeds, because Frappe and its controllers may fill some of them.
- [ ] **Annotations and toolsets.** All new tools join the `data` toolset. `cancel_document` gets `destructiveHint=True`.
- [ ] **Tests:**
  - submit refuses non-submittable DocTypes and DocTypes with an active workflow;
  - the submit payload contains `modified`;
  - cancel requires docstatus 1;
  - the amend payload strips system and child keys and sets `amended_from`;
  - workflow actions pass the document JSON;
  - the update diff and the child-row deletion warning;
  - missing mandatory fields in the create preview.

## Acceptance criteria

- The new tools are registered, annotated, and covered by the contract test.
- `docs/ai-agent-guide.md` gains two worked flows: draft → submit → cancel → amend, and "approve via workflow". `docs/tool-reference.md` is updated. `docs/frappe-capability-study.md` coverage table: mark submit, cancel, amend, and workflow as implemented but not site-verified.
- **Optional live check (only if the user asks, on a disposable site):** run a submittable test DocType through submit, cancel, and amend, and run one workflow transition. Clean up.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Mapped documents such as Sales Order → Sales Invoice (stage 10). Rename (backlog, unless it turns out trivial: `frappe.client.rename_doc`).

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Workflow stale-check finding:
- Deviations from this plan:
- Notes for the next stage:
