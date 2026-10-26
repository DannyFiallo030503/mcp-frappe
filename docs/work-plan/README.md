# Work plan

This folder splits the work to make Frappe MCP useful into **stages**. Each stage is sized for one Claude session. The stages run in order: each one assumes the earlier ones are done.

- [assessment.md](assessment.md): the investigation behind this plan. It covers what exists, confirmed bugs, and structural gaps. Read it once for background.
- `stage-NN-*.md`: one file per stage. Each file has the context, tasks, acceptance criteria, verification commands, and a **Handoff** section that the session fills in when it finishes.

## Goal

The main target user is **someone using Claude Desktop or Claude Code against an ERPNext site with their own, non-admin account**. They ask questions about business data and run everyday transactions. A Frappe developer who needs live schema, data, and reports is the second target. Their needs mostly overlap with the first user's read path.

## How to run a stage with Claude

Start a new session in the repository and paste:

```text
Read CLAUDE.md, AGENTS.md, and docs/work-plan/README.md.
Then execute docs/work-plan/stage-NN-<name>.md. Work only on that stage.
When finished, fill in the stage's Handoff section and update the status table in docs/work-plan/README.md.
```

If a stage turns out larger than one session, stop at a clean point (tests passing). Record what is left in the Handoff section and mark the stage `partial`. The next session continues from there.

## Rules for every stage

1. **One stage per session, one branch per stage.** Name the branch `stage-NN-<slug>` and base it on `main`, or on the previous stage's branch if that one isn't merged yet. Commit, push, or open a PR only when the user asks.
2. **AGENTS.md is binding.** That means: no SQL, no Python eval, no unrestricted method dispatch; Frappe permissions are preserved; outputs are bounded; mutations are previewed and confirmed; filesystem writes stay confined; no secrets or customer data appear in tests or evidence.
3. **Tests first.** Every behavior change gets a focused unit test in `tests/`. Unit tests never need a live site; they monkeypatch `standalone._request` and related helpers. Stage 00 adds a shared fake-site fixture for this.
4. **Keep the tool contract accurate.** Type hints and docstrings become the MCP schema. When the tool set changes, update `tests/test_standalone.py::test_server_registers_expected_tools`.
5. **Keep the e2e runners working.** If a tool's signature or return shape changes, update `e2e/run_standalone_*.py` to match. Don't run them unless the user asks, and then only against a disposable local site.
6. **Update the docs in the same stage:** `docs/tool-reference.md` and `docs/ai-agent-guide.md` always, plus `docs/architecture.md`, `docs/security.md`, and `docs/compatibility-and-validation.md` when relevant.
7. **Verify before claiming done:**
   ```bash
   uv run pytest -q
   uv run ruff check frappe_mcp tests
   ```
   From stage 00 on, both must be clean.
8. **Line numbers drift.** References such as `standalone.py:339` are as of commit `e8fbd0c`. Find code by symbol name, not by line.
9. **Check Frappe behavior in the source, not from memory.** Frappe references such as `frappe/client.py` were checked against Frappe **v16.33.1**. If a local Bench checkout is available, read the source there. Behavior can differ in v15; note any difference you find in the Handoff section.

## Stages

| # | Stage | Depends on | Status |
|---|---|---|---|
| 00 | [Baseline: CI, test harness, cleanup](stage-00-baseline.md) | none | todo |
| 01 | [Authentication and transport fixes](stage-01-auth-transport.md) | 00 | todo |
| 02 | [Read validation and atomic updates](stage-02-read-validation.md) | 00 | todo |
| 03 | [Customization tool fixes](stage-03-customization-fixes.md) | 00 | todo |
| 04 | [Native customization export](stage-04-native-export.md) | 03 | todo |
| 05 | [Metadata layer: non-admin access, cache, compact schema](stage-05-metadata-layer.md) | 02 | todo |
| 06 | [Read primitives: pagination, count, search, site info](stage-06-read-primitives.md) | 05 | todo |
| 07 | [Agent ergonomics: errors, instructions, annotations, toolsets, reports](stage-07-agent-ergonomics.md) | 06 | todo |
| 08 | [Document lifecycle and workflow](stage-08-lifecycle-workflow.md) | 07 | todo |
| 09 | [Collaboration and files](stage-09-collaboration-files.md) | 08 | todo |
| 10 | [Allowlisted method adapters](stage-10-method-allowlist.md) | 08 | todo |
| 11 | [Distribution and releases](stage-11-distribution.md) | 07 | todo |
| 12 | [Compatibility matrix and docs rewrite](stage-12-compat-and-docs.md) | 01–11 | todo |

Status values: `todo`, `in progress`, `partial`, `done`.

Stages 01, 02, and 03 depend only on 00 and can run in any order. Everything from 05 onward is sequential. Stage 11 can run any time after 07 if you want early releases.

## Backlog (not scheduled)

Pick these up after stage 12, or when a real user needs them:

- **Streamable HTTP transport** with per-user authentication, for teams sharing one server. This needs its own security design.
- **Print formats and PDF**: render to text or HTML for the model, and optionally save a PDF to a confined path.
- **Async HTTP.** Tools are synchronous, so the reload waits in `_wait_for_doctype` and similar helpers (up to about 60 s of `time.sleep`) block the MCP event loop. Move them to `httpx.AsyncClient` with async tools.
- **Portable site-custom DocTypes**: export `DocType` records with `custom=1` as app-level fixtures.
- **MCP resources and prompts**: DocType schemas as resources, and prompts such as "explore this site".
- **Developer toolset scope.** Keep `create_doctype` in the opt-in developer toolset (added in stage 07) and stop growing its test matrix. A coding agent with file access can scaffold DocTypes in the app repo directly.
