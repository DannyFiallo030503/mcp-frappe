# Stage 00: Baseline (CI, test harness, cleanup)

**Depends on:** none · **Size:** small · **Behavior changes:** none

## Why

Every later stage changes behavior. Before that starts, the repository needs automatic checks, a reusable way to fake a Frappe site in tests, and docs that don't contradict the code.

## Read first

- `pyproject.toml`, `tests/test_standalone.py`
- `UPSTREAM.md`, `docs/compatibility-and-validation.md`, `docs/development.md`, `e2e/standalone_real_http_report.md`
- [assessment.md](assessment.md), "Structural gaps" items 6 and 7

## Tasks

- [ ] **CI.** Add `.github/workflows/ci.yml`. On push and pull request, it should run `uv sync --extra dev`, `uv run pytest -q`, and `uv run ruff check frappe_mcp tests` on Python 3.10 and 3.12. Don't lint `e2e/` or `legacy/` in CI yet.
- [ ] **Lint clean.** Fix the existing `ruff check frappe_mcp tests` errors (import order, E501 in `standalone.py`) without changing behavior.
- [ ] **Fake-site fixture.** Add `tests/conftest.py` with a `fake_site` fixture. It should:
  - register responses by `(method, path)`, with an optional matcher on params or JSON;
  - record every call so tests can assert on request counts and payloads;
  - raise a clear `AssertionError` when a request has no registered response;
  - patch `standalone._request`, and reset any module-level caches (stage 05 adds one).

  Move one or two existing tests onto it to prove it works. Don't rewrite them all.
- [ ] **MCP contract test.** Add `tests/test_mcp_contract.py`. It should connect a real MCP client to `standalone.server._mcp_server` using `mcp.shared.memory.create_connected_server_and_client_session` (available in the installed `mcp` 1.30). It should:
  - call `list_tools` and assert that every tool has a non-empty description and an object input schema;
  - call one read tool end to end with `fake_site` active, and assert the result decodes to JSON.
- [ ] **Remove duplicate packaging.** Delete `setup.py` and `requirements.txt` once `grep -rn "setup.py\|requirements.txt"` shows nothing depends on them. `pyproject.toml` with hatchling is the single source.
- [ ] **Stale docs:**
  - `UPSTREAM.md`: the vendored transport now lives under `legacy/` and isn't packaged.
  - `docs/compatibility-and-validation.md`: the defects paragraph still lists "`get_document` omitted `docstatus`" and "export previews allowed core-app paths". Both are fixed; mark them as fixed.
  - `e2e/standalone_real_http_report.md`: add a one-line note at the top that it is a historical run (14 tools, 8 tests at the time).
  - `docs/development.md`: make the lint command match CLAUDE.md.
- [ ] **Generic names.** Replace `cubazone.localhost` with `site1.localhost` in README and the docs, and the `CubaZone` module in tests with `Example Module`. Leave `e2e/*report*.md` and evidence JSON alone; they are historical records.

## Acceptance criteria

- `uv run pytest -q` passes, and the new contract test is included.
- `uv run ruff check frappe_mcp tests` reports no errors.
- The CI workflow file is valid YAML and runs both commands. If the user pushes, confirm the run is green.
- No tool behavior changed: `git diff` on `frappe_mcp/` shows only lint fixes.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))" 2>/dev/null || echo "check YAML manually"
```

## Out of scope

Any bug fix or new tool. Leave lint errors in `e2e/` alone.

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Notes for the next stage (for example, how to use `fake_site`):
