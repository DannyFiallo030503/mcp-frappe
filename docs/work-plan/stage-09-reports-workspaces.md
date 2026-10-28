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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Report `json` keys found:
- Workspace `content` format found:
- Deviations from this plan:
- Notes for the next stage:
