# Stage 00: Baseline (CI, test harness, cleanup)

**Depends on:** none · **Size:** small · **Behavior changes:** none

## Why

Every later stage changes behavior. Before that starts, the repository needs automatic checks, a reusable way to fake a Frappe site in tests, and docs that don't contradict the code.

## Read first

- `pyproject.toml`, `tests/test_standalone.py`
- `UPSTREAM.md`, `docs/compatibility-and-validation.md`, `docs/development.md`, `e2e/standalone_real_http_report.md`
- [assessment.md](assessment.md), "Structural gaps" items 6 and 7

## Tasks

- [x] **CI.** Add `.github/workflows/ci.yml`. On push and pull request, it should run `uv sync --extra dev`, `uv run pytest -q`, and `uv run ruff check frappe_mcp tests` on Python 3.10 and 3.12. Don't lint `e2e/` or `legacy/` in CI yet.
- [x] **Lint clean.** Fix the existing `ruff check frappe_mcp tests` errors (import order, E501 in `standalone.py`) without changing behavior.
- [x] **Fake-site fixture.** Add `tests/conftest.py` with a `fake_site` fixture. It should:
  - register responses by `(method, path)`, with an optional matcher on params or JSON;
  - record every call so tests can assert on request counts and payloads;
  - raise a clear `AssertionError` when a request has no registered response;
  - patch `standalone._request`, and reset any module-level caches (stage 05 adds one).

  Move one or two existing tests onto it to prove it works. Don't rewrite them all.
- [x] **MCP contract test.** Add `tests/test_mcp_contract.py`. It should connect a real MCP client to `standalone.server._mcp_server` using `mcp.shared.memory.create_connected_server_and_client_session` (available in the installed `mcp` 1.30). It should:
  - call `list_tools` and assert that every tool has a non-empty description and an object input schema;
  - call one read tool end to end with `fake_site` active, and assert the result decodes to JSON.
- [x] **Remove duplicate packaging.** Delete `setup.py` and `requirements.txt` once `grep -rn "setup.py\|requirements.txt"` shows nothing depends on them. `pyproject.toml` with hatchling is the single source.
- [x] **Stale docs:**
  - `UPSTREAM.md`: the vendored transport now lives under `legacy/` and isn't packaged.
  - `docs/compatibility-and-validation.md`: the defects paragraph still lists "`get_document` omitted `docstatus`" and "export previews allowed core-app paths". Both are fixed; mark them as fixed.
  - `e2e/standalone_real_http_report.md`: add a one-line note at the top that it is a historical run (14 tools, 8 tests at the time).
  - `docs/development.md`: make the lint command match CLAUDE.md.
- [x] **Generic names.** Replace `cubazone.localhost` with `site1.localhost` in README and the docs, and the `CubaZone` module in tests with `Example Module`. Leave `e2e/*report*.md` and evidence JSON alone; they are historical records.

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

- Status: done.
- Branch / commits: `stage-00-baseline`, one commit on top of `main` (`da3de8d`).
- What changed:
  - CI: the existing `.github/workflows/tests.yml` (pytest only, from PR #6) became `ci.yml`, which now also runs `uv run ruff check frappe_mcp tests`. It keeps `uv sync --extra dev --locked` and the 3.10/3.12 matrix.
  - Lint: `ruff check frappe_mcp tests` is clean. `standalone.py` got only import sorting and wrapped long lines.
  - `tests/conftest.py`: the `fake_site` fixture (see notes below). `tests/test_fake_site.py` covers the fixture itself. `test_get_document_always_returns_docstatus` and `test_delete_reconciles_a_worker_reload_after_document_is_gone` now use it.
  - `tests/test_mcp_contract.py`: connects a real client to `standalone.server._mcp_server` with `create_connected_server_and_client_session`, checks every tool has a description and an object input schema, and calls `list_installed_apps` end to end against `fake_site`.
  - Removed `setup.py` and `requirements.txt` (nothing referenced them).
  - Docs: `UPSTREAM.md` points at `legacy/frappe_mcp_server/`; the compatibility doc marks the `docstatus` and core-app export defects as fixed; the historical real-HTTP report has a note at the top; `docs/development.md` lint command matches CLAUDE.md and describes CI; `cubazone.localhost` → `site1.localhost` in README and the installation doc; `CubaZone` → `Example Module` in unit tests. CLAUDE.md now mentions CI, `fake_site`, and the contract test.
- Deviations from this plan:
  - CI already existed as `tests.yml`; it was renamed and extended instead of adding a second, overlapping workflow. The job is now called `checks`.
  - The e2e runners (`e2e/run_standalone_*.py`) still use `erp_app` / `CubaZone` as their target app and module. They run against a real site, so renaming them would break that site's runs; they need an env var for the target app and module instead (left for stage 13 or whoever next touches the runners).
  - `docs/frappe-capability-study.md` named CubaZone as a negative example; it now says "a site-specific app or module".
- Notes for the next stage (how to use `fake_site`):
  - Take `fake_site` as a test argument. It replaces `standalone._request` and makes `standalone._client()` raise, so a test can't reach real HTTP by accident.
  - `fake_site.add(method, path, response, params=..., json=...)`. `path` is the exact string the code passes to `_request` (build it with `standalone._resource()` when names need quoting, e.g. `/api/resource/Module%20Def`). `params`/`json` match when they are a subset of the request's value, or pass a predicate. The last matching registration wins. `response` is deep-copied; an exception instance is raised (e.g. `httpx.ConnectError` to simulate a Bench reload); a callable gets the `Call` and returns the response.
  - `fake_site.add_doctype(name, fields, custom_fields=..., property_setters=..., **props)` registers the three reads `_doctype()` makes.
  - An unregistered request raises `AssertionError` listing the registered routes. Assert on traffic with `fake_site.calls` or `fake_site.calls_to("POST", path)`; each `Call` has `.params` and `.json`.
  - Caches: the fixture calls `cache_clear()` on any `functools` cache in `standalone` before and after each test. Stage 05's TTL cache is likely a plain dict, so add its reset to `_reset_module_caches()` in `tests/conftest.py`.
  - FastMCP returns a list result as one `TextContent` block per item; the contract test decodes each block.
