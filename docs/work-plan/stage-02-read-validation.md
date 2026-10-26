# Stage 02: Read validation and atomic updates

**Depends on:** 00 · **Size:** medium · **Fixes:** bugs 1, 2, and 10 in [assessment.md](assessment.md)

## Why

Field validation was written for writes, and reads reuse it. As a result, "list submitted invoices" (`docstatus = 1`) and "records created this week" (`creation`) are rejected, and so are list-form filters. Separately, the update stale check runs before the write instead of being enforced by Frappe.

## Read first

- `standalone.py`: `_RESERVED_FIELDS`, `_assert_fields`, `list_documents`, `get_document`, `update_document`, `_expected_modified`
- Frappe: `frappe/model/document.py` (`check_if_latest`, `set_user_and_timestamp`), `frappe/api/v1.py` (`update_doc`), `frappe/model/naming.py` (`set_new_name`)

## Tasks

- [ ] **Split validation into read and write.** Replace `_assert_fields(..., writing=...)` with two clear functions, for example `_assert_readable_fields` and `_assert_writable_fields`.
  - Reads and filters may use DocType fields plus the standard columns `name`, `owner`, `creation`, `modified`, `modified_by`, `docstatus`, and `idx`. Child DocTypes may also use `parent`, `parenttype`, and `parentfield`.
  - Writes keep today's rules: no reserved fields, no `read_only` fields.
- [ ] **Accept every Frappe filter shape** in `list_documents` (change the type hint to `dict | list | None`):
  - dict: `{"status": "Open"}` and `{"grand_total": [">", 100]}`;
  - list of three-element lists: `[["status", "=", "Open"]]`;
  - list of four-element lists with a child DocType: `[["Sales Invoice Item", "item_code", "=", "X"]]`. The child DocType must be the `options` of a `Table` or `Table MultiSelect` field on the parent, and the field must exist on that child.
- [ ] **Operator allowlist:** `=`, `!=`, `<`, `>`, `<=`, `>=`, `like`, `not like`, `in`, `not in`, `between`, `is`, `timespan`. Reject anything else with a message that lists the allowed operators.
- [ ] **Explicit field lists.** `fields` in `list_documents` and `get_document` accept the same readable set, so `modified` and `docstatus` can be requested.
- [ ] **Atomic stale check on update.** In `update_document`, keep the friendly pre-check and also send `"modified": expected_modified` in the PUT body. Frappe then compares it under a row lock and raises `TimestampMismatchError` if the document changed (`check_if_latest`). Turn that error into the same "Document changed since preview" message. Add a comment explaining why `modified` is sent even though callers may not write it.
- [ ] **Names for prompt-named DocTypes (investigate first).** Read `frappe/model/naming.py` (`set_new_name`, prompt autoname). If the DocType's `autoname` is `prompt`, or its naming rule is "Set by user", allow `name` on `create_document` only, and pass it the way Frappe expects (`name` or `__newname`, whichever the source shows). Otherwise keep rejecting it. If this takes too long, record the finding in Handoff and move it to the backlog.
- [ ] **Tests:**
  - filtering by `docstatus` and `creation` is accepted;
  - selecting `modified` works;
  - three-element and four-element list filters work;
  - an unknown child DocType is rejected;
  - an unknown operator is rejected;
  - writes still reject `docstatus` and `name`;
  - the update PUT body contains `modified`;
  - `TimestampMismatchError` maps to the stale message.

## Acceptance criteria

- The probe calls from the assessment (`filters={"docstatus": 1}`, `filters={"creation": [">", ...]}`, `fields=["name","modified"]`, list-form filters) all succeed against `fake_site`.
- `docs/tool-reference.md` documents the filter shapes, the operators, and the readable standard columns, with one example each. `docs/ai-agent-guide.md` shows a "submitted documents in a date range" example.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Pagination, `order_by`, and counts (stage 06). Fetching metadata differently (stage 05). For now, keep calling `_doctype()` the way the code does today.

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Prompt-naming finding:
- Notes for the next stage:
