# Stage 02: Read validation and atomic updates

**Depends on:** 00 · **Size:** medium · **Fixes:** bugs 1, 2, and 10 in [assessment.md](assessment.md)

## Why

Field validation was written for writes, and reads reuse it. As a result, "list submitted invoices" (`docstatus = 1`) and "records created this week" (`creation`) are rejected, and so are list-form filters. Separately, the update stale check runs before the write instead of being enforced by Frappe.

## Read first

- `standalone.py`: `_RESERVED_FIELDS`, `_assert_fields`, `list_documents`, `get_document`, `update_document`, `_expected_modified`
- Frappe: `frappe/model/document.py` (`check_if_latest`, `set_user_and_timestamp`), `frappe/api/v1.py` (`update_doc`), `frappe/model/naming.py` (`set_new_name`)

## Tasks

- [x] **Split validation into read and write.** Replace `_assert_fields(..., writing=...)` with two clear functions, for example `_assert_readable_fields` and `_assert_writable_fields`.
  - Reads and filters may use DocType fields plus the standard columns `name`, `owner`, `creation`, `modified`, `modified_by`, `docstatus`, and `idx`. Child DocTypes may also use `parent`, `parenttype`, and `parentfield`.
  - Writes keep today's rules: no reserved fields, no `read_only` fields.
- [x] **Accept every Frappe filter shape** in `list_documents` (change the type hint to `dict | list | None`):
  - dict: `{"status": "Open"}` and `{"grand_total": [">", 100]}`;
  - list of three-element lists: `[["status", "=", "Open"]]`;
  - list of four-element lists with a child DocType: `[["Sales Invoice Item", "item_code", "=", "X"]]`. The child DocType must be the `options` of a `Table` or `Table MultiSelect` field on the parent, and the field must exist on that child.
- [x] **Operator allowlist:** `=`, `!=`, `<`, `>`, `<=`, `>=`, `like`, `not like`, `in`, `not in`, `between`, `is`, `timespan`. Reject anything else with a message that lists the allowed operators.
- [x] **Explicit field lists.** `fields` in `list_documents` and `get_document` accept the same readable set, so `modified` and `docstatus` can be requested.
- [x] **Atomic stale check on update.** In `update_document`, keep the friendly pre-check and also send `"modified": expected_modified` in the PUT body. Frappe then compares it under a row lock and raises `TimestampMismatchError` if the document changed (`check_if_latest`). Turn that error into the same "Document changed since preview" message. Add a comment explaining why `modified` is sent even though callers may not write it.
- [x] **Names for prompt-named DocTypes (investigate first).** Read `frappe/model/naming.py` (`set_new_name`, prompt autoname). If the DocType's `autoname` is `prompt`, or its naming rule is "Set by user", allow `name` on `create_document` only, and pass it the way Frappe expects (`name` or `__newname`, whichever the source shows). Otherwise keep rejecting it. If this takes too long, record the finding in Handoff and move it to the backlog.
- [x] **Tests:**
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

Pagination, `order_by`, and counts (stage 10). Fetching metadata differently (stage 05). For now, keep calling `_doctype()` the way the code does today.

## Handoff

- Status: done
- Branch / commits: `stage-02-read-validation`, one commit on top of `6325a87` (stage 01 merge).
- What changed:
  - `_assert_fields` is replaced by `_assert_readable_fields` (schema fields + `_STANDARD_COLUMNS`, plus `_CHILD_COLUMNS` on child tables), `_assert_filters`, and `_assert_writable_fields` (no reserved or read-only fields). Each takes the merged schema, so a tool fetches `_doctype()` once.
  - `list_documents` takes `filters: dict | list | None` and accepts `{field: value}`, `{field: [op, value]}`, `[field, op, value]`, and `[doctype, field, op, value]`. Operators come from `_FILTER_OPERATORS` (matched case-insensitively); anything else is rejected with the allowed list.
  - `_check` now raises `FrappeError(RuntimeError)` carrying Frappe's `exc_type`. `update_document` sends `"modified": expected_modified` in the PUT body and maps `TimestampMismatchError` to the "Document changed since preview" message (`_STALE_DOCUMENT`).
  - `create_document` accepts `name` only when the merged schema's `autoname` is `prompt` (case-insensitive) and sends it as `__newname`; otherwise it refuses `name` with a message saying the DocType names its records itself.
  - Tests: new `tests/test_documents.py` (18 tests, 27 cases with parametrization) plus a `_check` `exc_type` test in `tests/test_transport.py`; the two old `_assert_fields` tests were removed (covered by the new file).
  - Docs: `tool-reference.md` (readable fields, filter shapes, operators, write rules, atomic update), `ai-agent-guide.md` (submitted-in-a-date-range example, stale handling), `security.md`, and the Validation bullet in `CLAUDE.md`.
- Deviations from this plan:
  - A four-element filter may also name the listed DocType itself (`["Sales Invoice", "modified", ">", ...]`); Frappe accepts it and it is unambiguous.
  - Malformed filters (dict values that are not a value or `[op, value]`, list conditions that are not 3 or 4 elements, a bare string) are rejected up front. Frappe's two-element `[field, value]` list form is not accepted, because the stage lists 3- and 4-element forms only.
  - Tree operators (`descendants of`, `ancestors of`, ...) and `previous`/`next` exist in Frappe's `get_filter` but are not in the allowlist, per the stage.
- Prompt-naming finding (Frappe v16.33.1, local Bench source): `Document.set_new_name` uses `__newname` only when `meta.autoname.lower() == "prompt"`; `naming.set_new_name` also keeps a passed `name` for `prompt` and `UUID` and clears it otherwise. The naming rule "Set by user" is a Desk-side label that sets `autoname = "prompt"` (`public/js/frappe/doctype/index.js`), so the server-side gate is `autoname` alone and the MCP checks the same thing. `api/v1.create_doc` passes the body to `frappe.new_doc(**data)`, which copies `__newname` onto the document. UUID-named DocTypes still refuse `name`.
- Other Frappe checks: `api/v1.update_doc` loads the row `for_update=True`, `doc.update(data)` sets `modified`, `set_user_and_timestamp` stores it as `_original_modified`, and `check_if_latest` compares `cstr(previous.modified)` with it. Both sides use `str(datetime)` (REST serializes with `json_handler` → `str`), so the preview token round-trips. `TimestampMismatchError` is a `ValidationError` (HTTP 417) and v1 error responses include `exc_type`. v15 was not checked.
- Notes for the next stage: stage 05 can cache `_doctype()`; the validators already take a schema dict, so they need no change. A four-element child filter costs one extra `_doctype(child)` fetch per distinct child DocType. Stage 11 (errors) can build on `FrappeError.exc_type` instead of parsing messages.
