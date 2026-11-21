# Developer-task evaluations

Does this server make Claude better at Frappe development than Frappe's built-in MCP server? The [evaluation suite](../evals/README.md) answers with twelve development tasks, each run by Claude Code headless in three configurations (this server with its skill, the native server, and both) and checked against the site and the app checkout afterwards, not against the agent's own report.

## Results

First run: **2026-11-21**, Frappe 16.55.1 and ERPNext 16.53.0 on a disposable local development site (developer mode, Administrator API keys, built-in MCP server enabled, `FRAPPE_BENCH_PATH` set), mcp-frappe 0.3.0 at `05da805` plus the two fixes below, Claude Code 2.1.329, model `claude-sonnet-5-5`, skill on, `--max-budget-usd 2` per run. Evidence: [`2026-11-21-v16-evals.json`](../e2e/evidence/2026-11-21-v16-evals.json). The run on `05da805` before the fixes is [`2026-11-21-v16-evals-before-fixes.json`](../e2e/evidence/2026-11-21-v16-evals-before-fixes.json).

| Configuration | Passed | Cost (all 12 tasks) |
|---|---|---|
| `dev` | 11 of 12 | $0.79 |
| `native` | 9 of 12 | $0.83 |
| `both` | 11 of 12 | $0.84 |

Costs are what Claude Code reports per session (`total_cost_usd`); on a Claude subscription they count against the plan's usage limits instead of being billed. One run per cell: a single pass or fail is not a rate.

| Task | What the check reads | `dev` | `native` | `both` |
|---|---|---|---|---|
| `child-table-export` | Child table field and child DocType in the schema; both DocTypes `in_sync`; the app JSON has the field | pass (7 MCP calls) | pass (3) | pass (7) |
| `custom-field-export` | Custom Field and bold `status` in ToDo's form metadata; both in the app's `custom/todo.json` | pass (9) | pass (5) | pass (8) |
| `workflow-three-states` | Active Workflow with the three states and both transitions | pass (4) | pass (5) | pass (4) |
| `rename-field-keep-data` (Bench) | New fieldname in the schema, old one gone; the three records' values under the new field | pass (8) | fail: can't run Bench | pass (7) |
| `missing-doctype` (Bench) | A DocType planted in the app files is `in_sync` afterwards | pass (4) | fail: can't run Bench | pass (3) |
| `seed-and-undo` | None of the 20 ToDos left; 20 `Deleted Document` rows for them | pass (6) | pass (42) | pass (42) |
| `pending-patches` | The answer names the patch line the harness appended to `patches.txt` | pass (1) | pass (1) | pass (1) |
| `add-field-after` | Required Select field right after `title`, in the list view; `in_sync` | pass (3) | pass (2) | pass (4) |
| `notification-on-submit` | Enabled System Notification on Submit for the DocType | pass (5) | pass (5) | pass (4) |
| `report-builder` | Report Builder report on ToDo; its columns and the `Open` filter | pass (3) | pass (3) | pass (2) |
| `promote-prototype` | Site-custom DocType now `in_sync` with a folder in the app | fail: stopped at the preview to ask | fail: can't write app files | fail: stopped at the preview to ask |
| `drift-report` | The answer names the DocType whose app JSON the harness changed; the site is unchanged | pass (1) | pass (4) | pass (1) |

What the run shows:

- **The native server did as well on most development tasks.** In developer mode, writing DocType, Custom Field, Workflow, Notification, and Report records through it makes Frappe write the app files too, so `child-table-export`, `custom-field-export`, `workflow-three-states`, `add-field-after`, `notification-on-submit`, and `report-builder` passed in all three configurations, at similar cost. It used fewer MCP calls on three of them.
- **It can't do what needs the Bench or app files.** `rename-field-keep-data` and `missing-doctype` failed with the native server alone. Its agent correctly explained that a rename through the API would leave the values in the old column, and that syncing a DocType from its files needs `bench migrate` or `reload-doc`.
- **Bulk data and drift questions take far fewer calls here.** `seed-and-undo` took 6 MCP calls with `create_documents` and `undo_mcp_change`, against 42 one-at-a-time inserts and deletes with the native server. In `both`, the agent used the native server for that task, as the skill suggests for data, and paid the same 42 calls. `drift-report` took one `list_app_drift` call, against four reads plus file reading.
- **`promote-prototype` stopped at the preview in `dev` and `both`.** The agent showed the preview and asked "Should I apply it?", which is what the skill tells it to do before every confirmation; a headless run has nobody to answer. In the run before the fixes, `dev` confirmed and passed. This is a measurement of the confirmation habit, not a tool failure. The native agent tried the `Write` tool, which the run doesn't offer, and stopped.

**Bugs the first run found, fixed before the evidence run:**

- `rename(kind="field")` reused a patch name the site had already logged in `Patch Log`, after an earlier rename of the same field was undone. `bench migrate` skipped the patch and the values never reached the new column (`rename-field-keep-data` failed in `both`). Patch names now get a `_2`, `_3`, ... suffix.
- `diff_doctype` reported `diverged` right after `reload-doc` imported a hand-written JSON, because Frappe filled in `engine`, `row_format`, `sort_field`, and `sort_order` (`missing-doctype` failed in `dev` and `both`). A matching `migration_hash` with equal `modified` values is now `in_sync`.

Also confirmed by the run: the native endpoint `/api/v2/mcp` with an `Authorization: token` header, the stream-json fields the runner reads (`mcp_servers`, `usage`, `total_cost_usd`), the `Deleted Document` check, the Property Setter `ToDo-status-bold`, and the Report Builder `json` filter. The sessions load no user plugins, and only the built-in tools named above.

## What we expected, before the first run

These were the hypotheses; the run confirmed all three:

- The native server has no access to the app checkout and can't create DocTypes in an app's files, export customizations, or run Bench commands. In the `native` configuration the agent can still read the app's files (`Read`, `Glob`, `Grep`), and it can write DocType, Custom Field, and Workflow records directly, which in developer mode can make Frappe write the files too. Tasks such as `child-table-export`, `workflow-three-states`, and `seed-and-undo` may well pass there.
- Tasks that need Bench (`rename-field-keep-data`, `missing-doctype`) can't pass with the native server alone: the agent can't run commands in this setup.
- `both` should match `dev` on development tasks and use the native server for `seed-and-undo`, which then has no journal to undo from.

## How the comparison is kept fair

- Every configuration gets the same prompt, model, built-in tools (`Read`, `Glob`, `Grep`, `Skill`, `ToolSearch`), and per-run budget, in a fresh folder with no user settings, hooks, or plugins.
- The skill is part of the `dev` and `both` configurations, because the plugin installs it with the server; `--no-skill` measures the server alone.
- A run where an MCP server failed to connect is recorded as `invalid`, not as a failure of that configuration.
- Each task starts from the same state: the runner removes what the task creates, before and after, and resets the test app's checkout.
