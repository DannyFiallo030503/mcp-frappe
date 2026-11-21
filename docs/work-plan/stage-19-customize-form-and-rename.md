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

- Status: done (unit tests only; no live run).
- Branch / commits: `stage-19-customize-form-and-rename`, one commit on `main` at `5464b81`.
- What changed:
  - `customize_doctype(doctype, operations, expected_preview, confirm)` (customize toolset). Operations: `set_property` (field, or DocType when `fieldname` is omitted), `move_field`, `add_custom_field`, `change_fieldtype` (within `_FIELDTYPE_CHANGE_GROUPS`, Customize Form's `ALLOWED_FIELDTYPE_CHANGE`). The preview simulates `save_customization` against the raw DocType record, the DocType's Property Setters, and its Custom Fields. It lists setters to create, update, or remove (redundant ones included, plus the `field_order` setter), Custom Fields to create or update (`insert_after` included), and warnings, and it refuses what `allow_property_change` would skip with a message. The `preview_id` binds the operations, the planned changes, the DocType's `modified`, and every setter and Custom Field. After the save, the result reads the setters back and names any previewed change Frappe didn't apply, with Frappe's messages.
  - `rename(kind, doctype, old, new, merge, expected_preview, confirm)` (developer toolset), `kind` being `document`, `custom_field`, `field`, or `doctype`. Every kind confirms with a `preview_id` that includes `modified`.
  - `kind="field"` writes `<app>/patches/rename_<doctype>_<old>_to_<new>.py` from the constant `_RENAME_FIELD_PATCH`, plus `patches/__init__.py` when missing, and adds the patch to `patches.txt` under `[post_model_sync]` (`_append_patch()`). The files are written through the new `_write_app_files()`, which is also export's writer, so `rollback_export` undoes them. Then the DocType is saved with the field renamed and the DocType settings that named it (`title_field`, `search_fields`, ...). A Frappe refusal restores the files; a reload is reconciled with `_doctype_matches()`.
  - `update_doctype`'s fieldname refusal, `_editable_doctype()`, the record-tool redirects, and the server instructions now point to the new tools.
  - Tool-list caps raised by 3,000 (37,000 / 39,500 / 38,500 / 41,000), as the user chose; the two tools add about 2,760 characters.
  - Fixed: `rollback_export` treated an exported empty file as changed (its hash of `""` was `None`).
  - Docs: tool-reference (new sections "Customize Form saves" and "Renames"), ai-agent-guide (which customization tool when, renames), security (run_doc_method use, "Generated patch files"), architecture, compatibility, capability study, ROADMAP, CHANGELOG, and CLAUDE.md.
- Customize Form endpoint and payload (v15 / v16): the same in both. `GET /api/method/frappe.handler.run_doc_method?method=fetch_to_customize&docs={"doctype":"Customize Form","name":"Customize Form","doc_type":<DocType>}` returns `{"docs": [form]}`. `POST` the same path with `{"method": "save_customization", "docs": "<the form as JSON>"}`. `run_doc_method` checks read permission on the doc (System Manager for Customize Form) and `check_if_latest`. The short path `run_doc_method` works but v16 marks it deprecated for v17. A `GET` isn't committed, except that `fetch_to_customize` may create the `auto_repeat` Custom Field when Allow Auto Repeat is on (DDL commits on MariaDB), as Desk's load does. v15 calls `set_property_setters()` before `update_custom_fields()`, and v16 after; the outcome is the same. v15's lists lack `hide_toolbar`, `allow_bulk_edit` (DocType), `alignment`, `in_import_template`, `button_color`, `mask`, and `set_only_once`; the tool allows those in the v16 set that `update_doctype` allows, and on v15 they show up as "not applied".
- Rename entry points found (v15 / v16): records go through `frappe.model.rename_doc.update_document_title(doctype, docname, name, merge, enqueue)` (POST, keyword-only; the same in both, and `rename_doc` itself isn't whitelisted). Custom Fields go through `frappe.custom.doctype.custom_field.custom_field.rename_fieldname(custom_field, fieldname)` (POST, System Manager; identical in both). It renames the column and other Custom Fields' `insert_after`, but not Property Setters, `field_order`, or expressions. Patches use `frappe.model.utils.rename_field.rename_field(doctype, old, new)`, which copies values and updates property setters, reports, and user settings. A DocType save never renames a column (`updatedb` only adds the new one). A DocType rename runs `DocType.before_rename` (Administrator for standard DocTypes, plus developer mode) and `after_rename` (renames the table and moves the folder and controller names).
- User decision on template patch files: approved (2026-11-20). The template is fixed, only checked identifiers are filled in as `repr` literals, and the preview shows the diff. Also approved: two tools (`customize_doctype`, one `rename` with `kind`) and a 3k budget raise.
- Deviations from this plan:
  - The `set_property` allowlist is wider than `set_field_property`'s 15 properties. It is Customize Form's own lists intersected with `update_doctype`'s vetted `_DOCFIELD_PROPERTIES`/`_DOCTYPE_PROPERTIES`, which excludes `istable`, `is_virtual`, `collapsible_depends_on`, and `ignore_xss_filter`. DocType-level `label` (a translation in Customize Form) isn't offered.
  - `options` on standard Select/Link fields is refused because Customize Form itself skips it (`ALLOWED_OPTIONS_CHANGE`).
  - Renames are one tool with `kind`, not separate tools.
  - The acceptance check against Desk on a site wasn't run: the user chose unit tests only.
- Notes for the next stage: a live runner for both tools is the main gap. It should save the same change through Desk's Customize Form and through `customize_doctype` and compare the Property Setters, rename a record with links, a Custom Field, a test-app field (then `bench migrate`), and a test DocType. Stage 20's undo could reuse `_write_app_files()` and `rollback_export` for file changes.
