# Stage 03: Customization tool fixes

**Depends on:** 00 · **Size:** medium · **Fixes:** bugs 5, 6, and 7 in [assessment.md](assessment.md)

## Why

"Add a field to Customer" is the most common customization request. Today it fails against any remote site because the tool demands a local app checkout. A caller can also redirect the field to another DocType by passing `dt` inside `field`. And a second change to the same form property fails as a duplicate.

## Read first

- `standalone.py`: `create_custom_field`, `set_field_property`, `_assert_app_module`, `_assert_remote_module`
- Frappe: `frappe/custom/doctype/property_setter/property_setter.py` (`autoname`), `frappe/custom/doctype/custom_field/custom_field.py`
- `docs/installation-and-configuration.md` (the paragraph that says these tools work without `FRAPPE_APPS_PATH`)

## Tasks

- [x] **Stop caller overrides (bug 5).** In `create_custom_field`, reject a `field` that contains `dt`, `module`, or `doctype`. Build the payload as `{**field, "doctype": "Custom Field", "dt": doctype, "module": module}` so the fixed keys always win.
- [x] **No local checkout needed (bug 6).** Make `app` and `module` optional keyword arguments on `create_custom_field` and `set_field_property`. If `module` is given, check it remotely with `_assert_remote_module`; if `app` is also given, check the module belongs to it. Never call `_app_path`. A module only matters later, for export. Note that the argument order changes; update the e2e runners (`e2e/run_standalone_customization_acceptance.py`, `e2e/run_standalone_doctype_matrix.py`) to match.
- [x] **Update an existing Property Setter (bug 7).** Before creating one, query `Property Setter` with the filters `doc_type`, `field_name` (or none for DocType level), and `property`.
  - If it exists: the preview shows the current value, the new value, and the setter's `modified`. Confirmation requires `expected_modified`, then sends a PUT.
  - If it doesn't exist: create it as today.
- [x] **Infer `property_type`.** Make `property_type` optional and derive it from a fixed map: `Check` for `hidden`, `read_only`, `reqd`, `in_list_view`, `in_standard_filter`, `collapsible`, and `bold`; `Data` for `label` and `width`; `Text` for `options`, `description`, and `default`; `Code` for the three `*depends_on` properties. Check the expected types against Customize Form in the Frappe source. If the caller passes a value, it must match the map.
- [x] **Validate the value against the type.** A `Check` value must be `0`/`1` (accept booleans and normalize them). Reject anything else before preview.
- [x] **Tests:**
  - a `dt` override is rejected;
  - custom field and property setter work with `FRAPPE_APPS_PATH` unset;
  - a module/app mismatch is rejected when both are given;
  - a second `set_field_property` on the same property sends a PUT, not a POST;
  - the stale `expected_modified` check works on a property setter update;
  - `property_type` is inferred correctly.

## Acceptance criteria

- With `FRAPPE_APPS_PATH` unset, `create_custom_field(doctype="Customer", field={...})` previews and confirms against `fake_site`.
- `docs/installation-and-configuration.md` is now accurate. `docs/tool-reference.md` documents optional `app`/`module`, update-in-place, and the inferred type.
- The recipe for removing or editing a Custom Field is documented in `docs/ai-agent-guide.md`: use `update_document` or `delete_document` on the `Custom Field` DocType.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Export behavior (stage 04). Growing the Property Setter allowlist.

## Handoff

- Status: done
- Branch / commits: `stage-03-customization-fixes`, one commit on top of `eecabe6`.
- What changed:
  - `create_custom_field(doctype, field, app=None, module=None, confirm=False)`: rejects `doctype`, `dt`, or `module` inside `field`; builds `{**field, "doctype": "Custom Field", "dt": doctype}` and adds `module` only when given. No `_app_path` call.
  - `set_field_property(doctype, property, value, fieldname=None, property_type=None, app=None, module=None, expected_modified=None, confirm=False)`: `property_type` comes from `_PROPERTY_TYPES` (a passed value must match); Check values accept 0/1, "0"/"1", or booleans and are stored as "0"/"1"; other values must be strings. Before writing it reads `Property Setter` by `doc_type` + `property` and matches `field_name` (and an empty `row_name`) in Python. An existing setter gives a `mode: "update"` preview with `name`, `current_value`, `modified`, and confirmation PUTs with `expected_modified` (same `TimestampMismatchError` handling as `update_document`). Otherwise `mode: "create"` and a POST as before. The preview is now flat (`doctype`, `fieldname`, `property`, `value`, `property_type`, ...) instead of nesting the payload under `data`.
  - `_assert_remote_module(app, module)` accepts `app=None` (module existence only) and reports an unknown module separately; new `_assert_optional_module()` wraps it and rejects `app` without `module`.
  - Tests: `tests/test_customization.py` (31 cases). Docs: tool reference, AI agent guide (Custom Field edit/remove recipe), installation, architecture. `e2e/run_standalone_doctype_matrix.py` no longer passes `property_type` (its `Int`, `Small Text`, and `Code` values would now be rejected).
- Deviations from this plan:
  - The `*depends_on` properties are `Data`, not `Code`: that's what Customize Form's `docfield_properties` uses in Frappe v16.33.1 (`label`, `width` Data; `options`, `description`, `default` Text; the seven flags Check, all as planned).
  - Not planned: a new DocType-level setter lists `replaces_field_setters`. `PropertySetter.validate` calls `delete_property_setter(doc_type, property, field_name=None)` on insert, which deletes every field-level setter for that property on the DocType. The preview now says so; the tool doesn't prevent it.
  - The e2e runners call tools with keyword arguments over MCP, so the argument-order change needed no runner edits beyond the `property_type` values.
- Notes for the next stage:
  - Frappe v16 `PropertySetter.validate` already deletes a same-key setter before inserting a new one, so bug 7 may not reproduce as a duplicate error on v16; the update-in-place path is still what binds the change to `modified`. Not checked against v15.
  - `depends_on`, `mandatory_depends_on`, and `read_only_depends_on` hold `eval:` expressions that the browser evaluates. They were on the allowlist before this stage, and server-side model code doesn't evaluate them. Rule 2 may still apply if a later stage revisits the allowlist.
  - Custom Fields and Property Setters created without `module` are not tagged for any app. Stage 04's export has to decide how to select them (by DocType, or require `module`).
