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
- [ ] **Update the runners.** Bring `e2e/run_standalone_smoke.py` and the mutation, customization, and DocType-matrix runners up to the current tool contract, if earlier stages didn't already.
- [ ] **Add `e2e/run_standalone_developer_flow.py`.** This is opt-in (`FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES`), refuses non-loopback targets, and needs `FRAPPE_APPS_PATH` with a disposable test app. The flow:
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

- [ ] **`docs/compatibility-and-validation.md`.** Rewrite it around a matrix table (rows: tool areas; columns: v15 and v16, plus a limited-user column for read tools if that run happened), with links to the evidence files. Remove claims that are out of date.
- [ ] **Move historical material.** Move `e2e/REAL_HTTP_REPORT.md`, `e2e/manual_functional_report.md`, and the old evidence files into `e2e/history/`, and fix the links.
- [ ] **README.** Rewrite it around use cases. Lead with "What you can ask", with 6–10 example developer prompts (create a DocType, add a field, customize an ERPNext form, check drift, add a workflow, build a Workspace), then install (`uvx`), configuration, toolsets, and links. Keep it under about 120 lines.
- [ ] **Capability study.** Update the coverage table in `docs/frappe-capability-study.md` to "implemented / site-verified (version, role)" from the new evidence.
- [ ] **Small docs.** Update `llms.txt` and `ROADMAP.md`: move finished items to Implemented, and copy the remaining backlog from this folder.
- [ ] **Check every link.** Make sure every relative link in `README.md`, `llms.txt`, and `docs/` resolves (a small script is fine; don't commit it unless it's useful in CI).

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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- Sites used (versions only, no hostnames or credentials):
- Matrix summary:
- Deviations from this plan:
- Suggested next backlog items:
