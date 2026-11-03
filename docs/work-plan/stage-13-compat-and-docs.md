# Stage 13: Compatibility matrix and docs rewrite

**Depends on:** 01–12 · **Size:** medium (plus live-site time) · **Fixes:** structural gap 7 in [assessment.md](assessment.md), and the "v15 only" evidence gap

## Why

All evidence so far comes from one v15 site. Before developers rely on the server, it needs repeatable runs of the developer workflow on v15 **and** v16 development Benches with developer mode on. The docs should lead with what you can do, not with long validation narratives.

## Read first

- `e2e/run_standalone_*.py`, `docs/development.md`, `docs/compatibility-and-validation.md`, `docs/frappe-capability-study.md`
- Every stage's Handoff section in this folder

## Tasks

### Live validation: only with the user's explicit approval, on disposable sites

- [ ] **Get the sites.** Ask the user which disposable sites to use. A local v16 Bench may already exist (for example a `frappe_docker` development setup); a v15 site is also needed. Both should be in developer mode, with Frappe, ERPNext (so the customize tools have standard DocTypes to work on), and a disposable test app under `FRAPPE_APPS_PATH`. Never point the runners at a site the user hasn't named as disposable.
- [x] **Update the runners.** Bring `e2e/run_standalone_smoke.py` and the mutation, customization, and DocType-matrix runners up to the current tool contract, if earlier stages didn't already.
- [x] **Add `e2e/run_standalone_developer_flow.py`.** This is opt-in (`FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES`), refuses non-loopback targets, and needs `FRAPPE_APPS_PATH` with a disposable test app. The flow:
  1. Create a standard DocType and a child table in the test app; check `diff_doctype` reports `in_sync`.
  2. Edit it with `update_doctype` (add, move, change, and remove a field); check the app JSON and `diff_doctype` again.
  3. Add a Custom Field to a `frappe` DocType and export it; check the `custom/` file.
  4. Create a site-custom DocType and promote it into the test app.
  5. Add a Workflow, a Notification, a Report Builder report, and a Workspace for the new DocType.
  6. Create, submit, and cancel a test document if the DocType is submittable.
  7. Remove everything, restore the test app's files, and check `list_app_drift` is clean.

  Print aggregate results only.
- [ ] **Limited-user smoke run (optional).** If the user wants it, create a temporary user with only `Sales User`, run the read-only smoke runner as that user, then delete the user. Record which read tools work.
- [ ] **Record the results** as redacted evidence JSON under `e2e/`, with the MCP commit, versions, developer mode, and pass/fail per area.

### Docs

- [x] **`docs/compatibility-and-validation.md`.** Rewrite it around a matrix table (rows: tool areas; columns: v15 and v16, plus a limited-user column for read tools if that run happened), with links to the evidence files. Remove claims that are out of date.
- [x] **Move historical material.** Move `e2e/REAL_HTTP_REPORT.md`, `e2e/manual_functional_report.md`, and the old evidence files into `e2e/history/`, and fix the links.
- [x] **README.** Rewrite it around use cases. Lead with "What you can ask", with 6–10 example developer prompts (create a DocType, add a field, customize an ERPNext form, check drift, add a workflow, build a Workspace), then install (`uvx`), configuration, toolsets, and links. Keep it under about 120 lines.
- [x] **Capability study.** Update the coverage table in `docs/frappe-capability-study.md` to "implemented / site-verified (version, role)" from the new evidence.
- [x] **Small docs.** Update `llms.txt` and `ROADMAP.md`: move finished items to Implemented, and copy the remaining backlog from this folder.
- [x] **Check every link.** Make sure every relative link in `README.md`, `llms.txt`, and `docs/` resolves (a small script is fine; don't commit it unless it's useful in CI).

## Acceptance criteria

- The compatibility matrix exists, and every cell links to evidence or says "not tested".
- The README is under about 120 lines and starts with use cases.
- No broken relative links.
- The evidence files contain no credentials, hostnames other than placeholders or `.localhost`, or record values.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
# live runners only with user approval, e.g.:
# FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES uv run python e2e/run_standalone_developer_flow.py
```

## Out of scope

New tools. Anything in the backlog.

## Handoff

- Status: partial. Everything that doesn't need a live site is done; the live runs are left (the user chose to build now and run later, since no disposable site was available in this session).
- Branch / commits: `stage-13-compat-and-docs`, based on `main` at `89ce7da` (stages 00-12 merged).
- Sites used (versions only, no hostnames or credentials): none. The matrix's v15 cells cite the 2026-10-16/19 runs (Frappe 15.96.0, ERPNext 15.96.1, server 0.2.0, Administrator), now in `e2e/history/`.
- What changed:
  - New `e2e/run_standalone_developer_flow.py` (`FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES`, loopback `.localhost` only, needs `FRAPPE_APPS_PATH`, `FRAPPE_TEST_APP`, `FRAPPE_TEST_MODULE`; optional `FRAPPE_TEST_CUSTOMIZE_DOCTYPE`, default `ToDo`). It runs the stage's seven steps as nine areas, each `pass` / `fail: <exception class>` / `skipped` (the steps after a failure are skipped). Lifecycle runs before the Workflow, because submit and cancel refuse DocTypes with an active Workflow. The parent DocType has its own `workflow_state` field, so the Workflow adds no Custom Field. States and the action get unique names, so the "create masters" path runs and cleanup deletes exactly what the preview listed in `creates`. Cleanup is a LIFO stack run in `finally`. It then removes leftover folders named with the run's suffix, and new folders holding only `__init__.py`, from the test module only. It compares the module's file list, the app's `git status --porcelain` count, and `list_app_drift` counts with their state before the run. Output: MCP version and commit, site app versions, Frappe major, identity (`Administrator` or `other user`), developer mode as shown by Frappe writing the standard files, areas, details (verdicts, checks), and cleanup. Exit code 1 on any failure.
  - Existing runners: `FRAPPE_TEST_APP` / `FRAPPE_TEST_MODULE` replace the hardcoded `erp_app` / `CubaZone` (the writing runners refuse to run without them, or with `frappe`/`erpnext`; the smoke runner skips its app-preview part). The customization runner now deletes its Property Setter on the success path too; before, it was deleted only in the failure cleanup and left behind otherwise. It reports `temporary_property_setter_deleted`. Docstrings list the required variables. No new ruff errors (27 pre-existing E501; the one I001 is gone).
  - `e2e/history/`: the six old reports and evidence files, moved with `git mv`; links fixed (including `REAL_HTTP_REPORT.md` to `legacy/e2e/run_real_http.py`).
  - Docs: `compatibility-and-validation.md` rewritten around the matrix (area rows, v15 and v16 columns), a runner-to-row table, how to save evidence (`e2e/evidence/<date>-v<major>-<runner>.json`), the 0.2.0 known issues with what changed since, and the source-checked behaviors a live run must confirm. README rewritten (106 lines): nine example prompts, then install, configure, toolsets, safety, docs. Its links are absolute GitHub URLs because it is the PyPI description. Capability study: the coverage table now has "Implemented" and "Site-verified (version, role)" columns. `llms.txt` and `ROADMAP.md` were rewritten: finished work is under Implemented, and the backlog is copied from this folder. Also `development.md` (runner setup, DocType matrix and developer flow sections), `architecture.md` (`e2e/` row), `tool-reference.md` (moved link), `installation-and-configuration.md` (README anchor), `legacy/README.md`, and `CLAUDE.md`.
  - `tests/test_docs_links.py`: every relative link in `README.md`, `llms.txt`, and every non-legacy `*.md` must resolve, including `#heading` anchors (GitHub's slug rules), and links into this repo's `github.com/.../blob/main/` tree are mapped to local files. README links must be absolute. It skips itself when `docs/` is missing (the sdist ships `tests/` without `docs/`). It runs in CI with the other tests.
- Matrix summary: v15 has 0.2.0-era evidence (Administrator) for discovery and schema, record reads and writes, `run_report`, DocType creation, Custom Fields and Property Setters, role permissions, and the old fixture-format export. Everything else on v15, all of v16, and the limited-user column (left out until that run happens) are "not tested".
- Deviations from this plan:
  - No live runs, no new evidence, no limited-user run: the user deferred them. The developer flow was checked only for lint, imports, and its opt-in and loopback guards. It has not run against Frappe, so expect fixes on the first live run (likely places: the JSON key names it reads from the app files, workflow master cleanup order, and Workspace file paths).
  - The limited-user column is left out of the matrix instead of filled with "not tested"; the page says so.
  - The link check is committed as a unit test (it is useful in CI) rather than as a throwaway script.
- Remaining to finish the stage (needs disposable v15 and v16 developer-mode sites with ERPNext and a test app):
  1. Run, in order, the smoke, mutation, customization, DocType-matrix, and developer-flow runners on each site; fix what the developer flow trips on.
  2. Save each output under `e2e/evidence/`, read it for hostnames, credentials, and values, and update the matrix cells and the capability study's "Site-verified" column.
  3. Optional: the limited-user (`Sales User`) smoke run, then add its column.
  4. Re-run `mcp-frappe check` on both sites (stage 12's note).
  5. Mark this stage `done`.
- Suggested next backlog items: async HTTP first (the developer flow's many reloads make the blocking waits obvious), then document lifecycle and workflow actions, then MCP resources and prompts.
