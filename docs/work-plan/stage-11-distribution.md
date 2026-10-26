# Stage 11: Distribution and releases

**Depends on:** 07 (can run any time after it) · **Size:** small to medium · **Fixes:** structural gap 6 in [assessment.md](assessment.md)

## Why

Today, installing means cloning the repo and running `uv`. A one-line install (`uvx mcp-frappe`), a setup check, and versioned releases make the difference between a project people try and one they keep using.

## Read first

- `pyproject.toml`, `frappe_mcp/__init__.py` (`__version__`), `standalone.py` (`main`)
- `README.md`, `docs/installation-and-configuration.md`

## Tasks

- [ ] **Package metadata.** Fill in `readme`, `license`, `authors`, `classifiers`, `keywords`, and `project.urls`. Keep a single version source: use hatch dynamic versioning from `frappe_mcp/__init__.py`, or the reverse, but not two literals.
- [ ] **CLI around `main()`,** using argparse:
  - `mcp-frappe` (no arguments): run the stdio server, as today;
  - `mcp-frappe --version`;
  - `mcp-frappe check`: read the environment, connect, print the user, Frappe and app versions, the selected toolsets, and whether `FRAPPE_APPS_PATH` is usable, then exit 0 or 1. Never print secrets.

  **Never write to stdout in server mode,** because stdout is the MCP channel. Send logs to stderr.
- [ ] **Build check.** `uv build`, then install the wheel into a clean environment (`uvx --from dist/<wheel> mcp-frappe --version`). Add this to CI.
- [ ] **Release workflow.** Add `.github/workflows/release.yml`: on a `v*` tag, build and publish to PyPI with trusted publishing. **Before enabling it, ask the user.** Publishing is public and can't be undone, and the PyPI project and trusted publisher must be set up by the user.
- [ ] **CHANGELOG.md.** Use Keep a Changelog format, filled in from the stage Handoff sections so far. Add a short versioning policy: 0.x minor releases may break the tool contract, and breaking changes are listed explicitly.
- [ ] **README install.** Make the main path `uvx mcp-frappe`, keep the git checkout as the development path, and add configuration snippets:
  - Claude Desktop JSON;
  - Claude Code: `claude mcp add frappe --env FRAPPE_URL=... -- uvx mcp-frappe` (check the current `claude mcp add` syntax);
  - a generic MCP host.

  Recommend running `mcp-frappe check` first.
- [ ] **Tests:** `--version` output; `check` with `fake_site`, including the success case, the failure case, and secrets never appearing in output; the server mode writes nothing to stdout before the protocol starts.

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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- PyPI publishing enabled? (who approved):
- Deviations from this plan:
- Notes for the next stage:
