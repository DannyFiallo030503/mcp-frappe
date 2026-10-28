# Development guide

## Repository structure

- `frappe_mcp/standalone.py`: MCP stdio server, Frappe HTTP client, tools, and confined app exports.
- `tests/`: isolated tests for standalone validation and path safety. `tests/conftest.py` provides a `fake_site` fixture that stands in for the Frappe HTTP API, and `tests/test_mcp_contract.py` drives the server through a real MCP client session.
- `e2e/`: MCP stdio acceptance runners and redacted evidence.
- `legacy/`: previous Frappe-hosted app and transport, excluded from the installable standalone package.

## Local setup

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check frappe_mcp tests e2e/run_standalone_*.py
```

CI (`.github/workflows/ci.yml`) runs `uv run pytest -q` and `uv run ruff check frappe_mcp tests` on Python 3.10 and 3.12. The `e2e/` runners are not lint-clean yet, so CI does not lint them; make sure your changes add no new errors there.

The normal test configuration runs only standalone tests; the historical Frappe app tests require their Bench environment and are not part of this package test run.

## Read-only MCP acceptance

Set `FRAPPE_URL`, optional `FRAPPE_SITE`, and either API key/secret variables or a password in your local environment. For app export previews, also set `FRAPPE_APPS_PATH` to the local Bench `apps/` directory. Then run:

```bash
uv run python e2e/run_standalone_smoke.py
```

This launches a real MCP stdio client/server session. It reads app and DocType metadata, a single DocType record, and one report row; customization actions remain previews. It prints aggregate results only.

## Temporary document acceptance

This creates a uniquely marked ToDo, verifies preview/confirmation, tests stale-update rejection, reads it back, deletes it, and verifies absence. It refuses non-loopback targets:

```bash
FRAPPE_ALLOW_MUTATION_TEST=YES uv run python e2e/run_standalone_mutation_acceptance.py
```

Run only on a development site. The runner cleans up in a `finally` block and prints no record contents.

## Customization and export acceptance

This temporarily creates a Custom Field and Property Setter, verifies the merged schema, exports the native `custom/` customization file, previews and confirms rollback, then removes the temporary records. It refuses non-loopback targets:

```bash
FRAPPE_ALLOW_CUSTOMIZATION_TEST=YES uv run python e2e/run_standalone_customization_acceptance.py
```

Because Bench development mode watches app files, a confirmed export or rollback may reload the Frappe web worker. The standalone client should tolerate a short transient connection failure after those operations. Inspect the target app's Git status after the run; successful rollback consumes its own backup manifest and files.

## Secrets and evidence

Credentials must come from a local secret store or environment and are never committed. Evidence files contain aggregate counts/booleans only. Do not store customer records, API keys, cookies, passwords, report rows, or generated app contents in test output.
