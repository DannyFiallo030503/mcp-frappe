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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Plugin format checked against (Claude Code version):
- Evaluation run (date, Frappe version, model, who approved the cost):
- Results summary:
- Deviations from this plan:
- Notes for the next stage:
