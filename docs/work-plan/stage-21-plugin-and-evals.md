# Stage 21: Claude Code plugin and developer-task evaluations

**Depends on:** 15 (plugin); 16–20 (evaluations, which measure the finished tools) · **Size:** medium to large · **Source:** [native MCP comparison](native-mcp-comparison.md)

## Why

- **Setup should be one step.** `frappectl assistant` starts a coding agent with a Frappe system prompt. The equivalent here is a Claude Code plugin: it installs the server configuration and a skill for the developer workflow (which tool for which DocType, and the change, export, migrate, check loop).
- **Better at development has to be shown, not claimed.** A repeatable set of developer tasks, run with this server, with the native server, and with both, gives evidence for the README. It also works as a regression check for the agent experience, which the unit tests don't cover.

## Read first

- The current Claude Code plugin and skill format: plugin manifest, bundled `.mcp.json`, `skills/<name>/SKILL.md`, and marketplace files. Check the current docs (the `claude-code-guide` agent can help); don't rely on memory.
- `docs/ai-agent-guide.md`, `docs/native-frappe-mcp.md` (stage 15), `_INSTRUCTIONS`
- The e2e runners and `FRAPPE_TEST_APP` / `FRAPPE_TEST_MODULE` (the disposable app used by the writing runners)

## Part 1: plugin

- [ ] **A plugin in the repository** (folder per the current format):
  - the manifest;
  - an MCP server entry named `frappe-dev` that runs `uvx mcp-frappe`, with the `FRAPPE_*` variables left for the user to fill in (no values, no secrets);
  - one skill.
- [ ] **The skill `frappe-development`,** short, with the details in references:
  - start with `get_site_info`;
  - standard DocTypes (frappe, erpnext) → customize tools; DocTypes of your own app → developer tools;
  - the loop: change → `diff_doctype` → export → migrate (stage 18, if enabled) → pending patches and recent errors (stage 16) → verify;
  - test data with `create_documents`, and undo with `undo_mcp_change` (stage 20);
  - when the native server is connected, use it for data and method calls.
- [ ] **A marketplace entry**, so users can install the plugin from this repository. Document the install in the README (absolute link) and in `docs/installation-and-configuration.md`.
- [ ] **Check the plugin installs** in a clean Claude Code configuration, without opening any GUI. Record the Claude Code version.

## Part 2: evaluations

- [ ] **A task suite in `evals/tasks/*.yaml`** (10–15 tasks). Each has a prompt and a deterministic check run against the site and the app checkout after the agent finishes. For example:
  - add a child table to a DocType of the test app and export it;
  - add a Custom Field and a Property Setter to a standard DocType, then export the customizations;
  - create a Workflow with three states;
  - rename a field without losing data;
  - find and fix a DocType that is missing from the site;
  - seed 20 test records, then undo them;
  - report which patches are pending.

  The checks use the server's own read tools or file comparisons, never the agent's own report.
- [ ] **A runner, `evals/run.py`.** It sets up a fresh state for each task with the disposable test app, runs Claude Code headless (`claude -p`) with one of three configurations (this server, native, both), and records:
  - pass or fail;
  - tool calls;
  - tokens, when the CLI reports them;
  - wall time.

  It refuses targets that aren't loopback and needs `FRAPPE_ALLOW_EVALS=YES`, like the other writing runners.
- [ ] **Running the suite costs API usage and needs a v16 site with native MCP enabled.** Run it only when the user asks. Save results under `e2e/evidence/`, with versions only and no hostnames.
- [ ] **Publish a summary** in `docs/compatibility-and-validation.md` (or a new `docs/evaluations.md`): per task and configuration, with the date, versions, and model. If the native server wins a task, say so.

## Acceptance criteria

- The plugin installs and gives a working `frappe-dev` server plus the skill.
- The evaluation suite runs end to end on a disposable site, and the results are recorded honestly.
- README, `llms.txt`, and the installation doc link to the plugin and the results.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
uv run pytest -q tests/test_docs_links.py
```

## Out of scope

Hosted or shared evaluation infrastructure. Comparing with third-party Frappe MCP servers (add them later if they matter to users).

## Handoff

- Status: done. The first session built and unit-tested everything; the second (2026-11-21, branch `stage-21-eval-run`) ran the suite, fixed the two bugs it found, ran it again, and published the results.
- Branch / commits: `stage-21-plugin-and-evals`, one commit on top of `main` (`8c8eef7`).
- What changed:
  - **Plugin** `plugins/frappe-dev/`: `.claude-plugin/plugin.json` (version = `__version__`, seven `userConfig` options: site URL and API key/secret required, the key and secret `sensitive`; site name, apps folder, Bench folder, toolsets default to empty), `.mcp.json` (server `frappe-dev`, `uvx mcp-frappe`, every `FRAPPE_*` value from `${user_config.*}`, nothing literal), the `frappe-development` skill (`SKILL.md`, 50 lines, plus `references/loop.md` and `references/data-and-undo.md`), and a README. `.claude-plugin/marketplace.json` at the root lists it as `frappe-dev@mcp-frappe`.
  - **Evaluations** `evals/`: `run.py`, twelve tasks in `evals/tasks/*.yaml`, and a README. Tasks: `child-table-export`, `custom-field-export`, `workflow-three-states`, `rename-field-keep-data` (Bench), `missing-doctype` (Bench), `seed-and-undo`, `pending-patches`, `add-field-after`, `notification-on-submit`, `report-builder`, `promote-prototype`, `drift-report`. Steps are declarative (`call`/`write`/`delete_matching`/file edits for setup; `call` with `expect`, `file`, `answer` for checks); the harness is an MCP stdio session to this server with every toolset and `FRAPPE_META_TTL=0`. The agent runs as `claude -p` with `--strict-mcp-config`, `--setting-sources project`, `--permission-mode dontAsk`, built-in tools `Read,Glob,Grep,Skill,ToolSearch`, `--add-dir` the test app, and `--no-session-persistence`. The skill is copied into the run folder's `.claude/skills/` for `dev` and `both` (not loaded through the plugin, whose options would be unset there). Records: status, failed checks, tool calls by name, MCP calls, turns, tokens, cost, wall time, leftover drift. Refuses without `FRAPPE_ALLOW_EVALS=YES`, a non-local URL or native URL, a non-`.localhost` site, `frappe`/`erpnext` as the test app, or a dirty test-app checkout.
  - Tests: `tests/test_plugin.py` (versions, option-to-variable mapping, the skill's tool names, frontmatter) and `tests/test_evals.py` (task loading and placeholders, paths and operators, the safety checks, configs and argv without secrets, stream-json parsing, the harness steps, `--dry-run`). PyYAML joins the `dev` extra. CI lints `evals/` too.
  - Docs: README (plugin install, evaluations link), installation (plugin section and options table), native-frappe-mcp (plugin toolsets, evaluations), new `docs/evaluations.md`, compatibility ("not yet checked"), development (plugin validation, evaluations), llms.txt, ROADMAP, CHANGELOG, CLAUDE.md.
- Plugin format checked against (Claude Code version): Claude Code 2.1.329, with the plugins and marketplace reference pages at code.claude.com (2026-11-21). `claude plugin validate --strict` passes for the plugin and the marketplace; its only note is the missing icon, which matters only for Anthropic's plugin directory. In an empty `CLAUDE_CONFIG_DIR`: `claude plugin marketplace add <checkout>`, `claude plugin install frappe-dev@mcp-frappe`, `claude plugin details` (one skill, one MCP server, ~120 tokens always on), and, with placeholder option values (secrets kept in the throwaway config, not the desktop keyring), `claude mcp list` showed `plugin:frappe-dev:frappe-dev: uvx mcp-frappe - ✔ Connected`. Empty optional options did not stop the server (an unknown toolset would have).
- Evaluation run (date, Frappe version, model, who approved the cost): 2026-11-21, Frappe 16.55.1 and ERPNext 16.53.0 on the local disposable v16 Bench, Claude Code 2.1.329, `claude-sonnet-5-5`, `--max-budget-usd 2` per run; the user approved running it. Claude Code signed in with a Claude Pro subscription, so the reported cost counts against plan limits, not a bill. Two full runs of 36 sessions ($2.56 on `05da805`, $2.47 with the fixes) plus about $0.70 of single-task probes and re-runs.
- Results summary: `dev` 11/12, native 9/12, `both` 11/12 ([evaluations](../evaluations.md); evidence `e2e/evidence/2026-11-21-v16-evals.json`, and the run before the fixes in `2026-11-21-v16-evals-before-fixes.json`). The native server matched this one on the development tasks that write records (developer mode makes Frappe write the files); it can't do `rename-field-keep-data` or `missing-doctype` (no Bench). `seed-and-undo` took 6 MCP calls here against 42 with the native server, and `both` used the native one for it. `promote-prototype` stopped at the preview to ask in `dev` and `both` (it passed in `dev` in the first run): the skill's "show the user each preview before you confirm" has no answer headless. Not changed: the confirm rule is an AGENTS.md safety rule; changing the prompt or the skill is the user's call.
- Second session changes:
  - Fixed `rename(kind="field")`: `_unused_patch_name()` skips patch names already in `Patch Log` (filtered `like` read). Before, a rename undone and redone reused the name, `migrate` skipped the patch, and the values stayed in the old column.
  - Fixed `diff_doctype`: `_doctype_file_drift()` returns `in_sync` with `filled_on_import: true` when the stored `migration_hash` matches the file and `modified` is equal; the differences left are defaults Frappe filled in on import (`engine`, `row_format`, `sort_field`, `sort_order`).
  - `evals/run.py --transcripts DIR` keeps each run's stream-json (0600; local debugging only, never evidence).
  - Docs: `evaluations.md` results, compatibility matrix row and not-yet-checked notes, tool reference, architecture, agent guide, native-frappe-mcp, evals README, ROADMAP, CHANGELOG.
  - Site setup done on the dev Bench (not part of the code): Administrator API keys generated, `enable_mcp` set in System Settings. The site had no `encryption_key`; generating the keys added one to its `site_config.json`, and the web workers needed a restart to read it.
- Deviations from this plan:
  - The plugin supports API-key login only (secrets go to the credential store); password login and the other variables stay with `claude mcp add`.
  - Question tasks (`pending-patches`, `drift-report`) check the final answer against what the harness itself planted, plus a site read where one applies. The plan said checks never use the agent's report; these check its correctness, not its claim of success.
  - The agent can't run shell commands or edit files in the runs, so every change goes through an MCP server; `--tools` widens that.
  - `claude plugin eval` exists in this Claude Code version but runs model-graded cases against a plugin; it doesn't fit three server configurations checked against a site, so the runner is our own.
  - The runner forwards the connection settings to the agent's `frappe-dev` server inside the 0600 `--mcp-config` file, because whether Claude Code passes its environment to stdio servers isn't documented.
- Verified by the run (were open): the native endpoint `/api/v2/mcp` with `Authorization: token`, the stream-json fields, the `Deleted Document.data` filter, `ToDo-status-bold`, the Report Builder `Open` filter. `--setting-sources project` loaded no user plugins or skills, and no transcript shows the user's `~/.claude/CLAUDE.md`. The agent can't write files: the native agent's `Write` attempts were refused.
- Notes from the first session: run the suite (`uv run python evals/run.py --dry-run` first) on a v16 site with developer mode, Administrator keys, a clean disposable test app, and `FRAPPE_BENCH_PATH`; save the JSON as `e2e/evidence/<date>-v16-evals.json` and fill `docs/evaluations.md`. Unverified until that run: the native endpoint URL and its `Authorization: token` header in `--mcp-config`, whether `--setting-sources project` keeps `~/.claude/CLAUDE.md` out, the stream-json field names (`mcp_servers` status, `usage`, `total_cost_usd`), the `Deleted Document.data` filter, Frappe's Property Setter name `ToDo-status-bold`, and the Report Builder `json` holding the `Open` filter. Bump the plugin's `version` with each release (a test enforces it).
