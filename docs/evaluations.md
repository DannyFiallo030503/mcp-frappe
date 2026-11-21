# Developer-task evaluations

Does this server make Claude better at Frappe development than Frappe's built-in MCP server? The [evaluation suite](../evals/README.md) answers with twelve development tasks, each run by Claude Code headless in three configurations (this server with its skill, the native server, and both) and checked against the site and the app checkout afterwards, not against the agent's own report.

## Results

**Not run yet.** The suite was built in stage 21 and checked with unit tests only (`tests/test_evals.py`): running it costs API usage and needs a v16 site with the native MCP server enabled. Every cell below is empty until a run fills it; this page will then give the date, the Frappe, ERPNext, mcp-frappe, and Claude Code versions, the model, and the evidence file, and say plainly where the native server did as well or better.

| Task | What the check reads | `dev` | `native` | `both` |
|---|---|---|---|---|
| `child-table-export` | Child table field and child DocType in the schema; both DocTypes `in_sync`; the app JSON has the field | not run | not run | not run |
| `custom-field-export` | Custom Field and bold `status` in ToDo's form metadata; both in the app's `custom/todo.json` | not run | not run | not run |
| `workflow-three-states` | Active Workflow with the three states and both transitions | not run | not run | not run |
| `rename-field-keep-data` (Bench) | New fieldname in the schema, old one gone; the three records' values under the new field | not run | not run | not run |
| `missing-doctype` (Bench) | A DocType planted in the app files is `in_sync` afterwards | not run | not run | not run |
| `seed-and-undo` | None of the 20 ToDos left; 20 `Deleted Document` rows for them | not run | not run | not run |
| `pending-patches` | The answer names the patch line the harness appended to `patches.txt` | not run | not run | not run |
| `add-field-after` | Required Select field right after `title`, in the list view; `in_sync` | not run | not run | not run |
| `notification-on-submit` | Enabled System Notification on Submit for the DocType | not run | not run | not run |
| `report-builder` | Report Builder report on ToDo; its columns and the `Open` filter | not run | not run | not run |
| `promote-prototype` | Site-custom DocType now `in_sync` with a folder in the app | not run | not run | not run |
| `drift-report` | The answer names the DocType whose app JSON the harness changed; the site is unchanged | not run | not run | not run |

## What to expect, before any run

These are hypotheses for the run to confirm or refute, not results:

- The native server has no access to the app checkout and can't create DocTypes in an app's files, export customizations, or run Bench commands. In the `native` configuration the agent can still read the app's files (`Read`, `Glob`, `Grep`), and it can write DocType, Custom Field, and Workflow records directly, which in developer mode can make Frappe write the files too. Tasks such as `child-table-export`, `workflow-three-states`, and `seed-and-undo` may well pass there.
- Tasks that need Bench (`rename-field-keep-data`, `missing-doctype`) can't pass with the native server alone: the agent can't run commands in this setup.
- `both` should match `dev` on development tasks and use the native server for `seed-and-undo`, which then has no journal to undo from.

## How the comparison is kept fair

- Every configuration gets the same prompt, model, built-in tools (`Read`, `Glob`, `Grep`, `Skill`, `ToolSearch`), and per-run budget, in a fresh folder with no user settings, hooks, or plugins.
- The skill is part of the `dev` and `both` configurations, because the plugin installs it with the server; `--no-skill` measures the server alone.
- A run where an MCP server failed to connect is recorded as `invalid`, not as a failure of that configuration.
- Each task starts from the same state: the runner removes what the task creates, before and after, and resets the test app's checkout.
