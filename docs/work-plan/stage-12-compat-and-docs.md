# Stage 12: Compatibility matrix and docs rewrite

**Depends on:** 01–11 · **Size:** medium (plus live-site time) · **Fixes:** structural gap 7 in [assessment.md](assessment.md), and the "v15 Administrator only" evidence gap

## Why

All evidence so far comes from one v15 site, tested as Administrator. Before anyone relies on the server, it needs repeatable runs on v15 **and** v16, with an ordinary user as well as an admin. The docs should lead with what you can do, not with long validation narratives.

## Read first

- `e2e/run_standalone_*.py`, `docs/development.md`, `docs/compatibility-and-validation.md`, `docs/frappe-capability-study.md`
- Every stage's Handoff section in this folder

## Tasks

### Live validation: only with the user's explicit approval, on disposable sites

- [ ] **Get the sites.** Ask the user which disposable sites to use. A local v16 Bench may already exist (for example a `frappe_docker` development setup); a v15 site is also needed. Both should have Frappe and ERPNext. Never point the runners at a site the user hasn't named as disposable.
- [ ] **Update the runners.** Bring `e2e/run_standalone_smoke.py` and the mutation, customization, and DocType-matrix runners up to the current tool contract, if earlier stages didn't already.
- [ ] **Add `e2e/run_standalone_business_flow.py`.** This is opt-in (`FRAPPE_ALLOW_BUSINESS_FLOW_TEST=YES`), and refuses non-loopback targets. It uses an allowlist config from stage 10. The flow:
  1. Create a test Customer and Item.
  2. Create a Quotation, then map it to a Sales Order and submit it.
  3. Map that to a Sales Invoice and submit it.
  4. Check the outstanding amount through a report or `get_document`.
  5. Cancel everything and clean up.

  Print aggregate results only.
- [ ] **Non-admin run.** On the disposable site, create a temporary user with only `Sales User`. Run the smoke runner and the read parts of the business flow as that user, then delete the user. Record which tools work and which are denied.
- [ ] **Record the results** as redacted evidence JSON under `e2e/`, with the MCP commit, versions, role profile, and pass/fail per area.

### Docs

- [ ] **`docs/compatibility-and-validation.md`.** Rewrite it around a matrix table (rows: tool areas; columns: v15 admin, v15 limited user, v16 admin, v16 limited user), with links to the evidence files. Remove claims that are out of date.
- [ ] **Move historical material.** Move `e2e/REAL_HTTP_REPORT.md`, `e2e/manual_functional_report.md`, and the old evidence files into `e2e/history/`, and fix the links.
- [ ] **README.** Rewrite it around use cases. Lead with "What you can ask", with 6–10 example prompts (business questions, a transaction flow, a customization), then install (`uvx`), configuration, toolsets, and links. Keep it under about 120 lines.
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
# FRAPPE_ALLOW_MUTATION_TEST=YES uv run python e2e/run_standalone_mutation_acceptance.py
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
