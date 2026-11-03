# Development guide

## Repository structure

- `frappe_mcp/standalone.py`: MCP stdio server, Frappe HTTP client, tools, and confined app exports.
- `tests/`: isolated tests for standalone validation and path safety. `tests/conftest.py` provides a `fake_site` fixture that stands in for the Frappe HTTP API, and `tests/test_mcp_contract.py` drives the server through a real MCP client session.
- `e2e/`: MCP stdio acceptance runners. `e2e/history/` keeps the reports and evidence of earlier runs; new evidence goes in `e2e/evidence/` (see [compatibility and validation](compatibility-and-validation.md#filling-in-a-cell)).
- `legacy/`: previous Frappe-hosted app and transport, excluded from the installable standalone package.

## Local setup

```bash
uv sync --extra dev
uv run pytest -q
uv run ruff check frappe_mcp tests e2e/run_standalone_*.py
```

CI (`.github/workflows/ci.yml`) runs `uv run pytest -q` and `uv run ruff check frappe_mcp tests` on Python 3.10 and 3.12. Its `package` job runs `uv build`, installs the wheel into a clean environment with `uvx --isolated`, runs `mcp-frappe --version`, `mcp-frappe check` (which must fail without a site), and the server with stdin at EOF (stdout must stay empty), and checks that `legacy/`, `e2e/`, and `docs/` stay out of the archives. The `e2e/` runners are not lint-clean yet, so CI does not lint them; make sure your changes add no new errors there.

The normal test configuration runs only standalone tests; the historical Frappe app tests require their Bench environment and are not part of this package test run.

## Build and release

```bash
uv build                                                  # dist/mcp_frappe-<version>.tar.gz and .whl
uvx --isolated --from dist/mcp_frappe-*.whl mcp-frappe --version
```

The version is `__version__` in `frappe_mcp/__init__.py`; `pyproject.toml` reads it, so change it only there. To release:

1. Move the `Unreleased` entries in `CHANGELOG.md` under the new version and date, listing breaking changes first (see its versioning policy).
2. Bump `__version__`, merge to `main`, and push a tag `v<version>` on that commit.
3. Run the **Release** workflow (`.github/workflows/release.yml`) on that tag from the Actions tab. It refuses a ref that is not a `v*` tag or does not match `__version__`, runs the tests, builds, checks the wheel, and publishes to PyPI through trusted publishing in the `pypi` environment.

The workflow has only a manual trigger for now. The file's header lists the one-time PyPI and GitHub setup, and how to switch to publishing on every pushed tag. A PyPI release is public and permanent; a version number cannot be reused.

## Live-site runners

The runners below launch a real MCP stdio client and server session against the site in your environment: `FRAPPE_URL`, optional `FRAPPE_SITE`, and an API key and secret or a password. Every runner except the smoke runner writes temporary data, needs its own opt-in variable, and refuses anything but a loopback `.localhost` site, so use a disposable development site. The writing runners also need:

- `FRAPPE_APPS_PATH`: the site's Bench `apps/` directory;
- `FRAPPE_TEST_APP` and `FRAPPE_TEST_MODULE`: a disposable test app installed on the site and one of its modules. The runners refuse `frappe` and `erpnext`.

Each runner prints aggregate JSON only (counts, booleans, versions; never record values or credentials). The [compatibility page](compatibility-and-validation.md#filling-in-a-cell) says which matrix rows each runner fills and how to keep its output as evidence.

## Read-only MCP acceptance

```bash
uv run python e2e/run_standalone_smoke.py
```

It reads site info, app and DocType metadata, the ERPNext `Customer` schema, a single DocType record, and one report row. With `FRAPPE_APPS_PATH`, `FRAPPE_TEST_APP`, and `FRAPPE_TEST_MODULE` set, it also previews (never confirms) DocType creation, a Custom Field, a Property Setter, and an export in the test app.

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

## DocType matrix

This creates DocTypes of every variant (regular, child, Single, Tree, submittable, Virtual, site-custom) with every field type in the test app, plus Custom Fields and Property Setters on them, then deletes them. Frappe keeps the physical tables of deleted DocTypes; drop only verified-empty `tabMCP Matrix ...` tables yourself.

```bash
FRAPPE_ALLOW_DOCTYPE_MATRIX=YES uv run python e2e/run_standalone_doctype_matrix.py
```

## Developer flow

This runs the whole developer workflow once: create a child table and a submittable DocType and check `diff_doctype`, edit it with `update_doctype` and check the app JSON, export a Custom Field on `ToDo` and roll it back, promote a site-custom DocType, submit and cancel a document, then add a Workflow, a Notification, a Report Builder report, and a Workspace. It removes everything afterwards and checks that the test module's files and `list_app_drift` are as they were. It needs developer mode and Administrator.

```bash
FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES uv run python e2e/run_standalone_developer_flow.py
```

`FRAPPE_TEST_CUSTOMIZE_DOCTYPE` picks a `frappe` DocType other than `ToDo` to customize. Every object it creates carries a random suffix; after cleanup it removes leftover folders with that suffix, and empty scaffold folders the run created, from the test module only. The exit code is non-zero when any area failed.

## Secrets and evidence

Credentials must come from a local secret store or environment and are never committed. Evidence files contain aggregate counts/booleans only. Do not store customer records, API keys, cookies, passwords, report rows, or generated app contents in test output.
