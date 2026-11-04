# Stage 13: Compatibility matrix and docs rewrite

**Depends on:** 01–12 · **Size:** medium (plus live-site time) · **Fixes:** structural gap 7 in [assessment.md](assessment.md), and the "v15 only" evidence gap

## Why

All evidence so far comes from one v15 site. Before developers rely on the server, it needs repeatable runs of the developer workflow on v15 **and** v16 development Benches with developer mode on. The docs should lead with what you can do, not with long validation narratives.

## Read first

- `e2e/run_standalone_*.py`, `docs/development.md`, `docs/compatibility-and-validation.md`, `docs/frappe-capability-study.md`
- Every stage's Handoff section in this folder

## Tasks

### Live validation: only with the user's explicit approval, on disposable sites

- [x] **Get the sites.** Ask the user which disposable sites to use. A local v16 Bench may already exist (for example a `frappe_docker` development setup); a v15 site is also needed. Both should be in developer mode, with Frappe, ERPNext (so the customize tools have standard DocTypes to work on), and a disposable test app under `FRAPPE_APPS_PATH`. Never point the runners at a site the user hasn't named as disposable.
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
- [x] **Limited-user smoke run (optional).** If the user wants it, create a temporary user with only `Sales User`, run the read-only smoke runner as that user, then delete the user. Record which read tools work.
- [x] **Record the results** as redacted evidence JSON under `e2e/`, with the MCP commit, versions, developer mode, and pass/fail per area.

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

- Status: done. The docs and runner work landed in PR #21 (first session, live runs deferred); the live runs, the fixes they led to, and the evidence are on `stage-13-live-runs` (second session, 2026-11-03).
- Sites used (versions only, no hostnames or credentials): two fresh local Benches built for this stage, each with one `.localhost` site in developer mode, ERPNext, and a disposable `mcp_test_app` (module `MCP Test App`, one fixture DocType, clean Git checkout). v15: Frappe 15.126.0, ERPNext 15.124.4, Python 3.12. v16: Frappe 16.55.1, ERPNext 16.53.0, Python 3.14. MariaDB 11.4 in Docker. The two Benches ran side by side on separate web and Redis ports.
- Results: all five runners passed on both sites as Administrator, the developer flow included (all nine areas, clean cleanup, module files and drift counts unchanged). `mcp-frappe check` printed `Ready.` on both. The limited-user run (a temporary Sales User, deleted afterwards) passed 10 of the 13 read tools on both versions; `list_doctypes`, `get_workflow`, and `list_notifications` are refused by Frappe's permissions. Evidence is in `e2e/evidence/2026-11-03-v{15,16}-*.json`, checked for hostnames, paths, and credentials.
- What changed in the second session:
  - **Bug fix:** Frappe v16 exports a Workspace to its module only when its new `standard` flag is set (v15 exports every public one). `upsert_workspace` now sends `standard: 1` when the Workspace DocType has that field (`_meta_bundle("Workspace")`), and the preview note says so. Two unit tests; the `ws_site` fixture and the MCP contract test register Workspace metadata.
  - Developer flow: fails when the Workspace file is not written (before, it only recorded it).
  - DocType matrix runner: progress lines go to stderr, so stdout is pure JSON. The v16 matrix evidence was produced before that change; its 12 progress lines were removed by hand, the JSON is untouched.
  - Smoke runner: `probe_read_tools()` calls each read tool once and records `pass` / `denied (<status> <exception>)` / `fail`. It runs as part of the Administrator smoke run (`read_tools`), and alone with `FRAPPE_SMOKE_LIMITED_USER=YES`.
  - Docs: the compatibility matrix filled in (v15, v16, and a Sales User column), the open source-review questions marked confirmed, a "Found by the live runs" section, and a "Not yet checked on a site" list. The capability study's Site-verified column, the tool reference (who can use which read tool; Workspace export on v15 vs v16), the AI agent guide (limited users), the development guide (limited-user mode), CHANGELOG (Fixed and Validated), and ROADMAP (Next) were updated.
- Deviations from this plan:
  - The developer flow needed no fixes beyond the Workspace check; the handoff's guesses (JSON key names, workflow master cleanup order, Workspace paths) were all right.
  - No Bench reload interrupted a request in these runs, so `reconciled_after_server_reload` stayed false and that path is still covered by unit tests only.
  - `set_role_permissions` is not called by any runner, so its cells say "not tested" for 0.3.0.
- Left for later (in the compatibility page's "Not yet checked on a site"): `set_role_permissions` live, `bench migrate` re-importing an exported Custom Field, Workflow `doc_status`/`allow_self_approval` read-back normalization, record writes as a non-System-Manager user.
- Suggested next: the first PyPI release of 0.3.0 (stage 12's checklist), then async HTTP, document lifecycle and workflow actions, and MCP resources and prompts from the backlog.
