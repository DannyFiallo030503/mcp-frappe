# Stage 03: Customization tool fixes

**Depends on:** 00 · **Size:** medium · **Fixes:** bugs 5, 6, and 7 in [assessment.md](assessment.md)

## Why

"Add a field to Customer" is the most common customization request. Today it fails against any remote site because the tool demands a local app checkout. A caller can also redirect the field to another DocType by passing `dt` inside `field`. And a second change to the same form property fails as a duplicate.

## Read first

- `standalone.py`: `create_custom_field`, `set_field_property`, `_assert_app_module`, `_assert_remote_module`
- Frappe: `frappe/custom/doctype/property_setter/property_setter.py` (`autoname`), `frappe/custom/doctype/custom_field/custom_field.py`
- `docs/installation-and-configuration.md` (the paragraph that says these tools work without `FRAPPE_APPS_PATH`)

## Tasks

- [ ] **Stop caller overrides (bug 5).** In `create_custom_field`, reject a `field` that contains `dt`, `module`, or `doctype`. Build the payload as `{**field, "doctype": "Custom Field", "dt": doctype, "module": module}` so the fixed keys always win.
- [ ] **No local checkout needed (bug 6).** Make `app` and `module` optional keyword arguments on `create_custom_field` and `set_field_property`. If `module` is given, check it remotely with `_assert_remote_module`; if `app` is also given, check the module belongs to it. Never call `_app_path`. A module only matters later, for export. Note that the argument order changes; update the e2e runners (`e2e/run_standalone_customization_acceptance.py`, `e2e/run_standalone_doctype_matrix.py`) to match.
- [ ] **Update an existing Property Setter (bug 7).** Before creating one, query `Property Setter` with the filters `doc_type`, `field_name` (or none for DocType level), and `property`.
  - If it exists: the preview shows the current value, the new value, and the setter's `modified`. Confirmation requires `expected_modified`, then sends a PUT.
  - If it doesn't exist: create it as today.
- [ ] **Infer `property_type`.** Make `property_type` optional and derive it from a fixed map: `Check` for `hidden`, `read_only`, `reqd`, `in_list_view`, `in_standard_filter`, `collapsible`, and `bold`; `Data` for `label` and `width`; `Text` for `options`, `description`, and `default`; `Code` for the three `*depends_on` properties. Check the expected types against Customize Form in the Frappe source. If the caller passes a value, it must match the map.
- [ ] **Validate the value against the type.** A `Check` value must be `0`/`1` (accept booleans and normalize them). Reject anything else before preview.
- [ ] **Tests:**
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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Notes for the next stage:
