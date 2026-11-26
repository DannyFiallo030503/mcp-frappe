# Developer-task evaluations

A repeatable set of Frappe development tasks, run with Claude Code headless (`claude -p`) in three configurations, and checked against the site and the app checkout afterwards. The results are in [docs/evaluations.md](../docs/evaluations.md).

| Configuration | MCP servers | Skill |
|---|---|---|
| `dev` | this server, all toolsets, as `frappe-dev` | `frappe-development` |
| `native` | Frappe's built-in MCP server, as `frappe` | none |
| `both` | both; this one with `read,customize,developer` (plus `bench` when set), as [using both servers](../docs/native-frappe-mcp.md) recommends | `frappe-development` |

## What a run does

For each task and configuration, `evals/run.py`:

1. opens its own MCP session to this server (the **harness**: every toolset, no metadata cache) and runs the task's `cleanup` steps, then restores the test app's checkout with Git, then runs the task's `setup` steps;
2. runs `claude -p "<prompt>"` in a fresh temporary folder, with `--strict-mcp-config` and only that configuration's servers, `--setting-sources project` (no user settings, hooks, or plugins), `--permission-mode dontAsk`, and the built-in tools `Read,Glob,Grep,Skill,ToolSearch`, with read access to the test app (`--add-dir`). The agent can read the app's files but can't run commands or edit files, so every change goes through an MCP server;
3. runs the task's `check` steps through the harness: the server's read tools (`get_doctype_schema`, `diff_doctype`, `count_documents`, ...) and file comparisons. A task that asks a question (which patches are pending, which DocTypes drifted) is checked against what the harness itself planted or read, never against the agent's claim that it succeeded;
4. runs `cleanup` again, restores the test app with `git checkout` and `git clean`, and records DocTypes that drifted during the run (`leftover_drift`).

It records, per run: `status` (`pass`, `fail`, `setup_failed`, `invalid` when an MCP server didn't connect, or `skipped` when the site lacks what the task needs), the failed checks, tool calls by name, MCP tool calls, turns, tokens and cost as the CLI reports them, and wall time. The output names versions only: no URL, host name, site name, or credential.

## Tasks

`evals/tasks/<id>.yaml`. Each has a `prompt`, optional `needs` (`bench`, `erpnext`), and `setup`, `check`, and `cleanup` step lists. Placeholders: `${app}`, `${module}`, `${module_dir}` (the module's folder name), `${run}` (8 hex characters, new for every run).

| Step | Does |
|---|---|
| `call: <tool>`, `args`, `expect` | One tool call; each `expect` entry checks a value of the result |
| `write: <tool>`, `args` | The tool's preview, then the same call confirmed with the token it returned |
| `delete_matching: <DocType>`, `filters` | Deletes up to 100 matching records, each previewed and confirmed |
| `write_file`, `append_file`, `replace_in_file` | Changes a file inside the test app's checkout (setup only) |
| `file: <path>`, `contains` or `exists: false` | Checks a file inside the test app's checkout |
| `answer: [text, ...]` | The agent's final answer must mention each text (question tasks only) |

An `expect` entry is `{path: ..., <op>: value}`. A path is dotted, `fields[fieldname=title].label` picks a list item, and a key applied to a list maps over it (`fields.fieldname`). Operators: `equals`, `not_equals`, `contains`, `not_contains`, `count`, `in`, `adjacent` (two values next to each other in a list). Add `ignore_errors: true` to a step that may fail.

## Run it

It writes to the site and to the test app, and every run costs API usage (19 tasks × 3 configurations = 57 Claude Code sessions, times `--repeat`). Use a disposable development site only.

Needs:

- a Frappe **v16** site with the built-in MCP server enabled (System Settings > AI Agents > Enable MCP) for `native` and `both`; on v15, run `--config dev` only;
- developer mode, Administrator's API key and secret (deleting standard DocTypes needs Administrator), and a loopback or `.localhost` URL;
- `FRAPPE_APPS_PATH`, and a disposable test app (`FRAPPE_TEST_APP`, `FRAPPE_TEST_MODULE`) whose Git checkout is clean: each run resets it to its last commit;
- `FRAPPE_BENCH_PATH` for the five tasks that need Bench commands (`rename-field-keep-data`, `missing-doctype`, `sync-teammate-change`, `run-app-tests`, `diagnose-error`); without it they are `skipped`. `run-app-tests` and `diagnose-error` also need `allow_tests` in the site config, because their setup runs a planted test (Error Log fields are read-only through the API, so `diagnose-error` logs its error from a test);
- no `<module>/custom/todo.json` committed in the test app, because `export-rollback` checks that the rollback removes the file;
- Claude Code, signed in.

```bash
uv sync --extra dev
uv run python evals/run.py --dry-run                       # list tasks and runs; touches nothing
FRAPPE_ALLOW_EVALS=YES uv run python evals/run.py \
  --model <model> --max-budget-usd 2 --out e2e/evidence/<date>-v16-evals.json
```

Options: `--config dev|native|both` and `--task <id>` (repeat either), `--repeat N` (1-10 runs of each task and configuration, so a cell becomes a rate; the output's `summary` counts passes per configuration and each row has its `attempt`), `--no-skill`, `--tools`, `--claude <path>`, and `--transcripts <dir>`, which saves each run's raw stream-json (tool arguments and results, the final answer) as `<task>-<config>.jsonl` with mode 0600. Transcripts hold site data and local paths: use them to debug a failure, never as evidence. The native endpoint defaults to `<scheme>://<FRAPPE_SITE or host>:<port>/api/v2/mcp`; set `FRAPPE_NATIVE_MCP_URL` to override it (it must be loopback or `.localhost` too).

The runner refuses to start without `FRAPPE_ALLOW_EVALS=YES`, with a URL that isn't loopback or `.localhost`, with `frappe` or `erpnext` as the test app, or with uncommitted changes in the test app. It writes the `--mcp-config` file, which holds the API key for both servers, with mode 0600 in the run's temporary folder and deletes the folder after the run; Claude Code itself gets no `FRAPPE_*` variable. Your `~/.claude/CLAUDE.md` may still be loaded; note it with the results if it says anything about Frappe.

Read the output before saving it as evidence: it should hold only versions, task ids, and counts.
