# Stage 19: Full Customize Form saves, and renames

**Depends on:** 15 (the rename parts that write a patch also use stage 18 when it is enabled) · **Size:** large · **Source:** [native-process-audit.md](../native-process-audit.md) (item 6), the "Rename DocTypes and fields" backlog item, [native MCP comparison](native-mcp-comparison.md)

## Why

Two common developer operations are still missing, and the native MCP has neither:

- **Customizing a standard DocType the way Desk does.** `set_field_property` writes one Property Setter at a time. The audit (item 6) says it isn't the full Customize Form save: that save also compares with the base value, removes redundant setters, keeps the field order, handles links, actions, and states, and validates the whole form together. Field use needed a field-type change on a standard DocType; the path that was tried skipped these checks, and Frappe rejected the change.
- **Renaming.** `update_doctype` refuses fieldname changes (stage 06). A rename done by hand loses data, or leaves the database column and the code out of step.

## Read first

- `standalone.py`: `set_field_property`, `create_custom_field`, `_FIELDTYPE_CHANGE_GROUPS`, `update_doctype` and `_plan_doctype_update()` (the operation-list pattern to reuse), `_editable_doctype`
- Frappe source (v15 and v16):
  - `frappe/custom/doctype/customize_form/customize_form.py`: `fetch_to_customize`, `save_customization`, `ALLOWED_FIELDTYPE_CHANGE`, and how Desk calls them (`customize_form.js`);
  - the generic document-method endpoint Desk uses (`run_doc_method`), and whether it checks permissions on the Customize Form doc;
  - `frappe/model/rename_doc.py`: `rename_doc` and the whitelisted entry point for renaming a document (its name differs between versions);
  - `frappe/custom/doctype/custom_field/custom_field.py`: is there a whitelisted fieldname rename (`rename_fieldname`)?
  - `frappe/model/utils/rename_field.py`: `rename_field`, which patches use.

## Tasks

### Customize Form

- [ ] **Investigate the save path over HTTP:** load with `fetch_to_customize`, then call `save_customization` through the same endpoint Desk uses. Check it on v15 and v16, and record the endpoint and payload in the Handoff. This calls one fixed method, so it isn't unrestricted dispatch.
- [ ] **New customize tool `customize_doctype(doctype, operations, confirm=False, expected_preview=None)`.** The operations are like `update_doctype`'s:
  - `set_property` (DocType or field level; the same allowlist as `set_field_property`);
  - `move_field`;
  - `add_custom_field`;
  - `change_fieldtype` (only within `ALLOWED_FIELDTYPE_CHANGE`, read from the source).
- [ ] **The preview** lists the Property Setters and Custom Fields that will be created, changed, or removed (including redundant setters Frappe will delete), plus the warnings. `preview_id` binds the current customization state.
- [ ] **Keep `set_field_property` and `create_custom_field`** for single changes. Document when to use each.

### Renames

- [ ] **`rename_document(doctype, old_name, new_name, merge=False)`,** with preview and confirmation. The preview counts the Link fields that point to the record. It uses Frappe's own rename, so links are updated, and it refuses DocTypes that don't allow renaming.
- [ ] **Rename a Custom Field's fieldname**, if Frappe has a whitelisted method for it. Otherwise document that it isn't possible.
- [ ] **Rename a field in an app DocType:**
  - change the fieldname in the DocType;
  - write a patch file from a fixed template that calls `rename_field(doctype, old, new)`, with only validated identifiers filled in;
  - append it to `patches.txt`.

  The model writes no Python. When stage 18 is enabled, the preview offers `migrate` as the next step. Ask the user whether template-generated patch files are acceptable under the "no Python" rule before building this.
- [ ] **Rename a DocType** of the user's app (not frappe or erpnext): the record and the app folder, handled by Frappe in developer mode. Reconcile after a reload, like the other DocType tools.

### Tests

- [ ] Customize Form: each operation, fieldtype changes allowed and refused, a redundant setter in the preview, and a stale preview.
- [ ] Renames: preview counts, refusals, and the generated patch file: its exact content, containment, and the `patches.txt` append.

## Acceptance criteria

- A standard DocType can be customized in one previewed save that matches Desk: the same Property Setters as a Customize Form save of the same change, checked on a site.
- Fields and documents can be renamed without data loss through Frappe's own code paths.
- Docs:
  - `docs/tool-reference.md`;
  - `docs/ai-agent-guide.md` (which tool for which customization);
  - `docs/security.md` (the patch template, if built);
  - CHANGELOG.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Renaming DocTypes of frappe or erpnext. Merging DocTypes. Client Scripts and Server Scripts: they are code.

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Customize Form endpoint and payload (v15 / v16):
- Rename entry points found (v15 / v16):
- User decision on template patch files:
- Deviations from this plan:
- Notes for the next stage:
