# Stage 12: Distribution and releases

**Depends on:** 11 (can run any time after it) · **Size:** small to medium · **Fixes:** structural gap 6 in [assessment.md](assessment.md)

## Why

Today, installing means cloning the repo and running `uv`. A one-line install (`uvx mcp-frappe`), a setup check, and versioned releases make the difference between a project people try and one they keep using.

## Read first

- `pyproject.toml`, `frappe_mcp/__init__.py` (`__version__`), `standalone.py` (`main`)
- `README.md`, `docs/installation-and-configuration.md`

## Tasks

- [x] **Package metadata.** Fill in `readme`, `license`, `authors`, `classifiers`, `keywords`, and `project.urls`. Keep a single version source: use hatch dynamic versioning from `frappe_mcp/__init__.py`, or the reverse, but not two literals.
- [x] **CLI around `main()`,** using argparse:
  - `mcp-frappe` (no arguments): run the stdio server, as today;
  - `mcp-frappe --version`;
  - `mcp-frappe check`: read the environment, connect, print the user, Frappe and app versions, developer mode (if stage 10 found a way to read it), the selected toolsets, and whether `FRAPPE_APPS_PATH` is usable, then exit 0 or 1. Never print secrets.

  **Never write to stdout in server mode,** because stdout is the MCP channel. Send logs to stderr.
- [x] **Build check.** `uv build`, then install the wheel into a clean environment (`uvx --from dist/<wheel> mcp-frappe --version`). Add this to CI.
- [x] **Release workflow.** Add `.github/workflows/release.yml`: on a `v*` tag, build and publish to PyPI with trusted publishing. **Before enabling it, ask the user.** Publishing is public and can't be undone, and the PyPI project and trusted publisher must be set up by the user.
- [x] **CHANGELOG.md.** Use Keep a Changelog format, filled in from the stage Handoff sections so far. Add a short versioning policy: 0.x minor releases may break the tool contract, and breaking changes are listed explicitly.
- [x] **README install.** Make the main path `uvx mcp-frappe`, keep the git checkout as the development path, and add configuration snippets:
  - Claude Desktop JSON;
  - Claude Code: `claude mcp add frappe --env FRAPPE_URL=... -- uvx mcp-frappe` (check the current `claude mcp add` syntax);
  - a generic MCP host.

  Recommend running `mcp-frappe check` first.
- [x] **Tests:** `--version` output; `check` with `fake_site`, including the success case, the failure case, and secrets never appearing in output; the server mode writes nothing to stdout before the protocol starts.

## Acceptance criteria

- `uv build` produces a wheel that installs and runs `--version` and `check` in a clean environment (in CI).
- The release workflow exists. Whether it's enabled is recorded in Handoff.
- `README.md` and `docs/installation-and-configuration.md` lead with the `uvx` install and `check`.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
uv build && ls dist/
```

## Out of scope

Docker images, the HTTP transport (backlog), and publishing without the user's approval.

## Handoff

- Status: done.
- Branch / commits: `stage-12-distribution`, one commit on top of `main` (`9dd35c5`).
- What changed:
  - Package metadata: `readme`, `license = "MIT"` with `license-files` (PEP 639, so `hatchling>=1.27`), `authors` (Dennis Fiallo, Danny Fiallo; names only), `keywords`, `classifiers`, `project.urls`. The version is dynamic, read from `frappe_mcp/__init__.py` by `[tool.hatch.version]`, and bumped to `0.3.0`; `uv.lock` now records the project version as dynamic. The sdist uses `only-include` (`frappe_mcp`, `tests`, README, CHANGELOG, LICENSE): plain `include` patterns match names anywhere and pulled in `docs/` and `legacy/`.
  - CLI: `main(argv=None)` uses argparse. With no command it serves stdio as before (settings validated by `_startup_settings()`); `--version` prints `mcp-frappe <version>`; `check` runs `_setup_check()` and `_run_setup_check()` (status rows for settings, transport warning, connection, versions, developer mode, toolsets, and `FRAPPE_APPS_PATH`, then `Ready.` or `N problem(s) found.`, exit 0/1). It reuses `get_site_info()`, prints `FRAPPE_URL` as `scheme://host[:port]`, never prints the apps path, checks each found app package is writable (`_writable()`), and runs the output through `_redact()` (secrets of 8+ characters; shorter values like `admin` would mangle words such as `Administrator`). Unknown commands exit 2 through argparse.
  - CI: a `package` job builds, installs the wheel with `uvx --isolated`, checks `--version` matches `__version__`, checks that `check` fails without a site with a readable message, checks that the server with stdin at EOF writes nothing to stdout, and checks that `legacy/`, `e2e/`, `docs/` (and `tests/` in the wheel) stay out of the archives.
  - `.github/workflows/release.yml`: `workflow_dispatch` only; the build job runs only on a `v*` tag ref whose name matches `v<__version__>`, runs the tests, builds, runs the wheel, and uploads `dist/`; the publish job uses the `pypi` environment, `id-token: write`, and `pypa/gh-action-pypi-publish@release/v1`. The `push: tags` trigger is commented out; the header lists the one-time PyPI and GitHub setup.
  - `CHANGELOG.md` (Keep a Changelog): versioning policy, `0.3.0 - Unreleased` from the stage 00–12 handoffs with a **Breaking** section first, and `0.2.0 - 2026-10-19`.
  - Tests: `tests/test_cli.py` (18 cases): `--version`, the single version source, an unknown command, `check` success / read-only toolsets / rejected credentials / unreachable site / Guest / no URL / bad settings without any request / credentials in the URL and an echoed password / insecure-HTTP warning / apps path found, missing, read-only, nonexistent / short secrets; server mode prints nothing to stdout, and a subprocess sends `initialize` and gets only JSON-RPC lines on stdout while the startup warning goes to stderr. Existing tests call `main([])` instead of `main()`, because argparse would otherwise parse pytest's argv.
  - Docs: README and `installation-and-configuration.md` lead with `uvx mcp-frappe` and `check`, with Claude Code (`claude mcp add frappe -e ... -- uvx mcp-frappe`, syntax checked against the installed CLI), Claude Desktop, and generic host snippets, and a command-line table; `development.md` (build and release steps, CI package job); `architecture.md` (command line); `security.md` (setup check); `tool-reference.md` and `ai-agent-guide.md` (send the user to `mcp-frappe check`, never ask for secrets in chat); `llms.txt`; `CLAUDE.md`.
- PyPI publishing enabled? (who approved): no. The user chose "manual trigger only" at the start of the session; nothing has been published. To publish: create the PyPI trusted publisher (owner `DannyFiallo030503`, repo `mcp-frappe`, workflow `release.yml`, environment `pypi`) and the `pypi` GitHub environment, tag `v0.3.0`, and run the workflow on that tag. The name `mcp-frappe` was free on PyPI when checked (2026-11-03).
- Deviations from this plan:
  - `check` cannot report developer mode (stage 10 found no API for it); it prints an `info` row saying so.
  - The docs lead with `uvx mcp-frappe`, which only works after the first PyPI release; until then they give `uvx --from git+https://github.com/DannyFiallo030503/mcp-frappe mcp-frappe`.
  - The version went from 0.2.0 to 0.3.0 in this stage (the user chose it), so the changelog has a 0.3.0 section rather than only `Unreleased`.
- Notes for the next stage:
  - The README is the PyPI long description, and its relative links (`docs/...`) will not resolve on PyPI. Stage 13's docs rewrite should make them absolute GitHub links, or move to a PyPI-specific readme.
  - After the first release, drop the `--from git+...` fallback from README and the installation doc.
  - Re-run `mcp-frappe check` on the v15 site in the compatibility matrix; `get_versions` is the only endpoint it adds beyond `get_logged_user`.
