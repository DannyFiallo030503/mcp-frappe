# Stage 09: Report Builder reports and Workspaces

**Depends on:** 08 · **Size:** medium · **Fixes:** structural gap 10 in [assessment.md](assessment.md) (second half)

## Why

The last step of building a module is making it usable: a few saved reports, and a Workspace with shortcuts to the DocTypes and reports. Both are configuration records. A standard one, saved in developer mode, is written to the app module so it ships with the app. Built by hand they take many clicks, and generic `create_document` can't check that a report's columns exist or that a shortcut points to something real.

## Read first

- `standalone.py`: `_save_config_record` (stage 08), the filter validation from stage 02, `run_report`, `_assert_remote_module`, `_assert_writable_app`
- Frappe:
  - `frappe/core/doctype/report/report.py`: the `json` field of a Report Builder report (columns, filters, `order_by`, `group_by`, `add_totals_row`; check the exact keys a saved report uses), and what `on_update` writes for `is_standard="Yes"` in developer mode;
  - `frappe/desk/doctype/workspace/workspace.py`: the `content` field (a JSON list of blocks that refer to shortcuts and cards by label), the child tables (`shortcuts`, `links`, `charts`, `number_cards`, `quick_lists`, `custom_blocks`), how `public`, `for_user`, and `module` interact, and what is written for a standard Workspace in developer mode;
  - `Custom HTML Block`, to confirm it holds HTML, CSS, and JavaScript.

## Design

- **Report Builder only.** Query Reports run SQL and Script Reports run Python, so AGENTS.md rules them out. `report_type` is always `Report Builder`.
- **Workspaces without code.** `custom_blocks` (Custom HTML Block) carries HTML and JavaScript, so refuse it (rule 2 of the README). Shortcut and link types are limited to `DocType`, `Report`, and `Page`, and each target must exist. `URL` shortcuts are refused.
- **The tool builds `content`.** The caller lists shortcuts and link cards in order; the tool generates the `content` JSON that places them, so the two can't disagree.
- **Public Workspaces only.** Private per-user Workspaces (`for_user`) are out of scope.

## Tasks

### Reports

- [ ] **`upsert_report(name, ref_doctype, columns, filters=None, order_by=None, group_by=None, is_standard=False, module=None, confirm=False, expected_modified=None)`.**
  - Columns are fieldnames of `ref_doctype`, or `<child_doctype>.<fieldname>` for one of its child tables. Validate them against the stage 05 metadata.
  - Validate `filters` and `order_by` with the same helpers as `list_documents`.
  - With `is_standard=True`, `module` must belong to a writable app; the preview lists the files Frappe will write.
  - Save with `_save_config_record`.
- [ ] **Run it back.** After a confirmed save, the result includes the first few rows from `run_report` (bounded), so the developer sees the report works.

### Workspaces

- [ ] **`get_workspace(name)`.** Read-only: label, module, public, shortcuts, link cards, and the order they appear in.
- [ ] **`upsert_workspace(name, module, shortcuts, link_cards=None, number_cards=None, confirm=False, expected_modified=None)`.**
  - `shortcuts`: `[{"label", "type": "DocType"|"Report"|"Page", "link_to", "color"?, "doc_view"?}]`; `link_to` must exist.
  - `link_cards`: `[{"label", "links": [{"label", "type", "link_to"}]}]`, with the same checks.
  - `number_cards`: names of existing Number Card records only; creating them is out of scope.
  - The module must belong to a writable app. The Workspace is public and standard, so Frappe writes it to the module in developer mode; the preview lists the files.
  - On update, the preview shows which shortcuts and cards are added, removed, or moved.

### Tests

- [ ] Report: unknown columns and child-table columns of a non-child table are refused; filters reuse the stage 02 validation; `report_type` can't be changed; the post-save run is bounded.
- [ ] Workspace: unknown targets, `URL` shortcuts, and `custom_blocks` are refused; the generated `content` refers to exactly the given shortcuts and cards, in order; protected-app modules are refused.

## Acceptance criteria

- The new tools are registered and in the contract test.
- `docs/ai-agent-guide.md` has a "finish the module" recipe: a report plus a Workspace with shortcuts to the new DocTypes. `docs/tool-reference.md` and `docs/security.md` (why Query and Script Reports and custom blocks are refused) are updated.
- **Optional live check (only if the user asks, on a disposable site):** create a standard report and a Workspace in a test app, check the files, and clean up.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Query and Script Reports. Dashboard Charts and creating Number Cards. Private Workspaces. Print Formats (backlog).

## Handoff

- Status: done.
- Branch / commits: `stage-09-reports-workspaces`, based on `main` at `56b0b17` (stages 05-08 merged).
- What changed:
  - `upsert_report(name, ref_doctype, columns, filters, order_by, group_by, add_totals_row, is_standard, module, expected_modified, confirm)`. Columns: stored fields or standard columns of `ref_doctype`, or `<child DocType>.<fieldname>` of one of its child tables (layout and Table fields refused; 1-50, no duplicates). Filters: `_assert_filters`, saved as 4-element rows. `order_by`: `<fieldname> [asc|desc]`, up to 3, rebuilt as Frappe's backquoted string. `group_by`: `{field, aggregate_function: count|sum|avg, aggregate_on (numeric, sum/avg)}`; columns must be `[field]` and `order_by` is refused. Refuses child/Single `ref_doctype`, replacing a non-Report-Builder report, and making a standard report site-only. Unmanaged `json` keys (`column_widths`, `chart_args`) are kept. Confirm saves with `_save_config_record`, then `sample` (at most 5 rows, `truncated`, or `error`) from `frappe.desk.reportview.get_list`.
  - `get_workspace(name)`: label, title, module, app, public/private/hidden, shortcuts, link cards (from the `links` table), number cards, `layout` (blocks in order, spacers left out), `unmanaged_blocks`; bounded.
  - `upsert_workspace(name, module, shortcuts, link_cards, number_cards, expected_modified, confirm)`. Targets checked in one list read per type (DocType not a child table, Report, Page, Number Card); `URL` and other shortcut types, `Kanban` doc_view, unknown keys, and duplicate labels refused. Builds `content` (number cards, shortcuts, link cards, with spacers; ids hashed from the label) and the `shortcuts`/`links`/`number_cards` rows; `is_query_report` set for non-Report-Builder report links; unmanaged row columns carried over by label. Refuses private Workspaces and replacing one with chart/quick list/custom block/onboarding blocks, or whose current module is in a protected app; warns about dropped header/paragraph blocks and module moves. Preview `changes`: added/removed/moved per kind, `changed` cards. Frappe's route-conflict `NameError` is explained.
  - Shared helpers: `_standard_module()` (now also used by `upsert_notification`) and `_record_files()` (`_notification_files` is a wrapper).
  - Tests: `tests/test_reports_workspaces.py` (78 cases), an MCP round-trip for `upsert_workspace` (no `custom_blocks` parameter; a stray one never reaches Frappe), tool-set test.
  - Docs: tool-reference ("Reports and Workspaces", `run_report` caveat), ai-agent-guide ("Finish the module" recipe), security (Report Builder only, custom blocks), architecture, compatibility, capability study, `CLAUDE.md`.
- Report `json` keys found (v16.33.1, `report_view.js` `save_report`, `Report.run_standard_report`): `filters` (`[doctype, field, operator, value]`; the UI may add a fifth flag), `fields` (`[fieldname, doctype]`; `columns` is an older alternative), `order_by` (SQL string like `` `tabX`.`f` desc ``, from `sort_selector.get_sql_string`), `add_totals_row`, `page_length`, `column_widths`, `group_by` (`{group_by, aggregate_function, aggregate_on}` as backquoted columns, with `["_aggregate_column", doctype]` appended to `fields` and `order_by` forced to `_aggregate_column desc`), `chart_args`. `reportview.save_report` validates `group_by` with `_validate_group_by_args`; the REST save used here does not, so the tool validates first.
- Workspace `content` format found: a JSON list of Editor.js blocks `{"id", "type", "data"}`; `shortcut` / `card` / `number_card` / `chart` / `quick_list` / `custom_block` / `onboarding` blocks name their row by label in `data.<type>_name` (Desk matches the row's `label`), plus `col` (width out of 12; Frappe's own files use 3 for shortcuts, 4 for cards); `spacer`, `header`, and `paragraph` blocks carry layout or HTML text. Link cards are the `links` table: a `Card Break` row (`label`, `link_count`) followed by its `Link` rows (`link_type`, `link_to`, `is_query_report`).
- Other Frappe findings: `query_report.run` returns no rows for Report Builder reports (`get_report_result` only runs Query, Script, and Custom reports), so the sample uses `reportview.get_list`. `Report.validate` sets `is_standard="Yes"` when it is empty and the user is Administrator in developer mode, so the tool always sends it. A standard report needs Administrator and developer mode; `on_update` writes `<module>/report/<name>/<name>.json` and `__init__.py` files. `Workspace.validate` needs Workspace Manager for public Workspaces, fills `report_ref_doctype` and `app`, and runs `validate_route_conflict` (Page, Workspace, and DocType names compared as lower-case slugs); `on_update` writes `<module>/workspace/<name>/<name>.json` (no `__init__.py`) for a public Workspace with a module in developer mode and deletes the old folder when module or title changes. Workspace has v16 `type` (`Workspace`/`Link`/`URL`) and `link_type`/`link_to`/`external_link` fields; the tool always sends `type: Workspace`.
- Deviations from this plan:
  - `upsert_report` takes `add_totals_row` (listed among the `json` keys in the plan, not in the signature).
  - The post-save run uses `frappe.desk.reportview.get_list`, not `run_report`, because `run_report`'s endpoint returns nothing for Report Builder reports.
  - `group_by` is limited to own fields of `ref_doctype` and takes the shape above; `order_by` takes own fields only.
  - Replacing a Workspace with chart, quick list, custom block, or onboarding blocks is refused rather than dropping them.
  - The e2e runners were not changed: no existing signature or return shape changed and none exercises the new tools.
- Notes for the next stage:
  - Not run live (the user chose unit tests only): a standard report and Workspace in a test app. Still open: whether Frappe's REST save of a Workspace from a non-Administrator Workspace Manager writes files, and whether `Page` list reads (target checks) need System Manager.
  - Stage 11's annotations should mark `get_workspace` read-only and `upsert_report`/`upsert_workspace` as mutations with previews.
  - v15 was not compared.
