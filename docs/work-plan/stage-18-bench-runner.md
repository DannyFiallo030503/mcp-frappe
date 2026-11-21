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

- Status: done (unit tests only; the live runner hasn't been run).
- Branch / commits: `stage-18-bench-runner`, one commit on top of `main` (`a3a4549`).
- User approval: the project owner approved the boundary change "as designed" on 2026-11-20, in the session, before any code was written: all five commands, the local-only rule, the AGENTS.md exception, and the `docs/security.md` section.
- What changed:
  - **Settings** (`_load_bench_config()`, called from `main()` through `_bench_settings()` after `_method_settings()`): `FRAPPE_BENCH_PATH` (must exist; a non-local `FRAPPE_URL` stops startup), `FRAPPE_BENCH_SITE` falling back to `FRAPPE_SITE` (`_SAFE_NAME`, no `..` or spaces; `sites/<site>/site_config.json` must exist when the folder has `sites/`), `FRAPPE_BENCH_COMMAND` (JSON list of 1-20 non-empty strings, its program found with `shutil.which`; default `["bench"]`), `FRAPPE_BENCH_TIMEOUT` (10-7200, default 900). Only then is `run_bench` registered, in a new `bench` toolset (`_register_bench_tool()`, `destructiveHint: true`).
  - **`run_bench(command, app, module, doctype, test_module, confirm, expected_preview)`**: `_bench_argv_tail()` builds `migrate`, `reload-doc <scrub(module)> doctype <scrub(doctype)>` (the call `frappe.reload_doctype` makes; needs the DocType's JSON in the checkout naming it), `export-fixtures --app` (not frappe/erpnext), `clear-cache`, and `run-tests --app` with exactly one of `--module-def`, `--doctype` (its module must belong to the app), `--module` (a dotted path inside the app). Apps must have modules on the site. Preview: `argv`, `site`, `warning`, `preflight` (`_bench_state()`: drift, pending patches, fixture-file hashes; an unreadable part says `unavailable`), and `preview_id` over all of it, recomputed on confirm. Run: `subprocess.run` with a list argv, `cwd` the Bench folder, `stdin` closed, the timeout, and `_bench_env()` (allowlisted variables; no `FRAPPE_*`, no `CI`). Result: exit code, `ok`, `seconds`, the redacted and capped output tail (`_bench_output()`), `server_ready`, `after` (the preflight again, after `_forget_meta()`; also after a failure or timeout), `changed_files` for export-fixtures, and `tests` for run-tests (`_test_summary()`).
  - **Hints**: `list_app_drift` returns `hint` when the page has `missing_on_site`, and `list_pending_patches` when patches are pending; both name `run_bench` when it is registered, else `bench migrate`.
  - **`mcp-frappe check`**: a `bench` row (site, command, timeout, whether run-tests is allowed; never the folder). Toolsets without tools are no longer printed in the toolsets row and the tool-list summary, so the default output is unchanged.
  - **Tests**: `tests/test_bench.py` (76): no tool without the variable, registration and annotations, remote targets refused at startup and per call, every bad setting, the container command prefix, the argv of each command, 16 refused argument combinations (unknown command, flags, shell text, `--help` as an app, wrong argument sets, protected apps, a foreign `test_module`), reload-doc file checks, `allow_tests` (site over common), preview contents and no Bench folder in it, stale and foreign `preview_id`s, the child environment (no `FRAPPE_*`, `CI`, or secret), the timeout setting, output redaction and caps, timeout and non-zero exit with the post-check, start failures, cache clearing, the missing_on_site → in_sync flow, run-tests summaries (v15 exit 0 on failure, tests disabled), export-fixtures file changes, hints, `main()`, read-only mode, and `check`. `test_mcp_contract.py`: budgets with `run_bench` (35,500) and with every opt-in tool (38,000), and an MCP-session round trip. Pinned default toolset tests now include `bench`.
  - **Live runner**: `e2e/run_standalone_bench_flow.py` (`FRAPPE_ALLOW_BENCH_TEST=YES`, `FRAPPE_BENCH_PATH`, the test app and module).
  - **Docs**: AGENTS.md (the approved exception), `docs/security.md` (new "Bench commands" section), `docs/tool-reference.md`, `docs/ai-agent-guide.md` (the loop: change, export, sync, test, check), `docs/installation-and-configuration.md` (variables, local and Docker Compose examples), `docs/compatibility-and-validation.md` (runner row; not yet run), `docs/architecture.md`, README, `llms.txt`, ROADMAP, CHANGELOG, CLAUDE.md. FR-2026-004 is resolved.
- Bench version and command arguments checked: Frappe v15.126.0 and v16.55.1 sources in the local Benches (`frappe/commands/site.py` for `migrate` and `reload-doc`, `utils.py` for `clear-cache` and `export-fixtures`, v15 `utils.py` and v16 `testing.py` for `run-tests`), with bench CLI 5.31.0. Nothing was run against a site. Findings: `reload-doc` takes `module doctype docname`, and `frappe.reload_doctype` passes them scrubbed. `run-tests` without `allow_tests` prints "Testing is disabled for the site!" and exits 0. v15 `run-tests` exits 0 on failures unless `CI` is set, and `CI` also skips the `allow_tests` check. That is why `CI` is stripped, `allow_tests` is checked first, and the unittest summary decides `ok`.
- Deviations from this plan:
  - `migrate` takes an optional `app` to scope its preflight (drift and patches are per app); without it the preflight is empty.
  - Without a local `sites/` folder (a containerized Bench run from a compose folder), the site and `allow_tests` aren't checked locally; `run-tests` then reports `tests.status: disabled` from the output instead of refusing up front.
  - `FRAPPE_BENCH_PATH` with a remote `FRAPPE_URL` stops startup instead of silently leaving the tool out, since the user asked for both.
  - The default toolset selection includes `bench` (empty without the variable). `check` hides toolsets without tools, so its output is unchanged for existing setups.
  - The MCP `instructions` don't mention `run_bench`: they are capped at 1,500 characters, and the tool's description carries it when it is listed.
  - `export_customizations` points to `export-fixtures` in its docs, not its docstring (tool-list budget).
- Notes for the next stage: the tools are synchronous, so a long `migrate` blocks the server until it ends (the backlog's async item). A timeout kills the local process; with `docker exec`, the command may keep running in the container. Run `e2e/run_standalone_bench_flow.py` on the v15 and v16 Benches and add the results to the matrix. Stage 19 can use `run_bench(command="reload-doc")` or `migrate` after a rename if it needs them.
