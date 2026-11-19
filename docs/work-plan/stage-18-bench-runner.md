# Stage 18: Opt-in Bench commands for the development loop

**Depends on:** 16 · **Size:** large · **Source:** FR-2026-004 in [field feedback](../field-feedback/feature-requests.md), [native-process-audit.md](../native-process-audit.md) (the fixture-export gap), [native MCP comparison](native-mcp-comparison.md)

> **Ask the user first.** This stage changes the project's security boundary. Today the server only talks HTTP to the site and writes to the app checkout. Running `bench` executes the app's own code (patches, tests, hooks). Don't start until the user has approved it explicitly, and approved the AGENTS.md and `docs/security.md` changes that come with it. Record the approval in the Handoff.

## Why

Several steps of the development loop happen only through Bench, and field use kept hitting them:

- **A DocType missing from the site** (`missing_on_site`): two tasks needed `bench migrate`, which was run outside the MCP. `create_doctype` can't help, and `update_doctype` needs the DocType on the site (FR-2026-004).
- **Pending patches:** stage 16 lists them, but only `bench migrate` runs them.
- **Fixtures:** `bench export-fixtures` isn't a whitelisted HTTP method, so stage 04 removed the MCP's own fixture serializer (native-process-audit.md, item 5).
- **Tests and caches:** `bench run-tests` and `bench clear-cache` are part of every change.

Neither the native MCP nor `frappectl` can run these. A narrow, opt-in runner closes the loop: change a DocType, export, migrate, test, and check drift, without leaving the session.

## Read first

- `AGENTS.md`, `docs/security.md`, `ROADMAP.md` (Security boundaries)
- `standalone.py`: `_is_local_target`, `_wait_for_server_ready`, `_forget_meta`, `_redact`, `list_app_drift`, the stage 16 tools, `_selected_toolsets`, and the preview-id pattern of `export_customizations`
- Bench CLI help for the target Bench version: `bench --site <site> migrate`, `reload-doc`, `export-fixtures`, `clear-cache`, `run-tests`. Check each command's arguments and exit codes; don't rely on memory.

## Design

- **Opt in with configuration.** The tools exist only when `FRAPPE_BENCH_PATH` is set: no variable, no tools, like the method config in stage 17. They are in a new `bench` toolset.
- **Local sites only.** Refuse when `FRAPPE_URL` isn't a local target.
- **The site name** comes from `FRAPPE_BENCH_SITE`, falling back to `FRAPPE_SITE`. It must match `_SAFE_NAME`, and when the Bench is on this filesystem, `sites/<site>/site_config.json` must exist.
- **The command** is `bench` on `PATH`, or `FRAPPE_BENCH_COMMAND`: a JSON list for containerized Benches, for example `["docker", "compose", "exec", "-T", "backend", "bench"]`. Only the user sets it; no tool argument changes it.
- **A fixed command list.** One tool, `run_bench(command, app=None, module=None, doctype=None, test_module=None, confirm=False, expected_preview=None)`, where `command` is one of:
  - `migrate`;
  - `reload-doc` (needs `app`, `module`, and `doctype`; for a DocType missing from the site);
  - `export-fixtures` (needs `app`);
  - `clear-cache`;
  - `run-tests` (needs `app`, plus `module` or `doctype` or `test_module`; refused unless the site has `allow_tests`).

  The server builds the argv from validated values. Nothing comes from free text, and there is no shell.
- **Preview.** It returns:
  - the exact argv, site, and command;
  - the preflight state: `list_app_drift` verdicts, pending patches (stage 16), and for `export-fixtures` the files that will change;
  - a `preview_id` over all of that.

  Confirmation needs the same `preview_id`, recomputed just before running.
- **Run.**
  - `subprocess.run` with a timeout (`FRAPPE_BENCH_TIMEOUT`, default 900 s).
  - A minimal environment: no `FRAPPE_API_*` or `FRAPPE_PASSWORD`.
  - It captures the exit code and the last ~200 lines of output, redacted and capped.
  - Afterwards it calls `_wait_for_server_ready()` and `_forget_meta()`, then a post-check: drift and pending patches again, so the result shows what changed.
- **Annotations.** `destructiveHint=True` for `migrate` and `reload-doc`, because migrate runs patches that can change data. The preview says so.

## Tasks

- [ ] **Present the boundary change to the user:** the command list, what each command runs, and the local-only rule. Update AGENTS.md (an exception to "Do not ... migrate" for this opt-in toolset) and `docs/security.md` only after approval.
- [ ] **Load and validate the configuration at startup**, with clear errors: path, site, command JSON, timeout.
- [ ] **Implement `run_bench`** as designed. Each command has its own argument rules, and unknown combinations are refused.
- [ ] **Hook it into the other tools:**
  - `list_app_drift`'s `missing_on_site` message names `run_bench(command="reload-doc", ...)` or `migrate` when the tool is available;
  - stage 16's `list_pending_patches` points to `migrate`;
  - `export_customizations` documents `export-fixtures` for fixtures it doesn't cover.
- [ ] **Resolve FR-2026-004** through `reload-doc` or `migrate`, and update the feature request.
- [ ] **Unit tests:** monkeypatch `subprocess.run`, and check:
  - the argv for each command and refused argument combinations;
  - a remote target refused, and no tools without the variable;
  - the environment has no secrets, and the output is redacted and capped;
  - a stale `preview_id` is refused, and a timeout is handled;
  - the post-check runs after a non-zero exit too.
- [ ] **Live runner.** Add `e2e/run_standalone_bench_flow.py`, opt-in with `FRAPPE_ALLOW_BENCH_TEST=YES`, against the disposable test app. The flow: create a DocType file without the site record, `reload-doc`, confirm drift is clean, `run-tests` for that DocType, `clear-cache`. Run it only when the user asks.

## Acceptance criteria

- With `FRAPPE_BENCH_PATH` unset, the tool list is unchanged.
- No tool argument can add a command, a flag, or shell text.
- A `missing_on_site` DocType can be synced in one preview and confirmation, and drift then shows it in sync.
- Docs:
  - `docs/tool-reference.md`;
  - `docs/ai-agent-guide.md` (the complete loop: change, export, migrate, test, check);
  - `docs/security.md`;
  - `docs/installation-and-configuration.md` (the variables, and container examples);
  - `docs/compatibility-and-validation.md` (live runner rows);
  - AGENTS.md (as approved).

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

`bench build`, `install-app`, `get-app`, `backup`, `restore`, `drop-site`, `console`, `execute`, `--force` flags, and anything on a site that isn't local.

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- User approval (who, date, what was approved):
- What changed:
- Bench version and command arguments checked:
- Deviations from this plan:
- Notes for the next stage:
