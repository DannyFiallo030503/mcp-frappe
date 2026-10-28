# Work plan

This folder splits the work to make Frappe MCP useful into **stages**. Each stage is sized for one Claude session. The stages run in order: each one assumes the earlier ones are done.

- [assessment.md](assessment.md): the investigation behind this plan. It covers what exists, confirmed bugs, and structural gaps. Read it once for background.
- `stage-NN-*.md`: one file per stage. Each file has the context, tasks, acceptance criteria, verification commands, and a **Handoff** section that the session fills in when it finishes.
- [backlog/](backlog/): detailed plans for work that is not scheduled. They were stages before the plan was refocused (see below).

## Goal

The target user is a **Frappe developer** who uses Claude Code or Claude Desktop against a development site. The server should make everyday development work faster and safer:

- create DocTypes and **change existing ones** (fields, child tables, settings, permissions);
- customize standard DocTypes with Custom Fields and Property Setters;
- keep the site and the app's files in sync, and see what drifted;
- set up configuration records such as Workflows, Notifications, Report Builder reports, and Workspaces;
- read schema, data, and reports to check the result, and create test data.

Reading business data as an ordinary user still works where Frappe permissions allow it, but it isn't the focus. ERPNext transaction verbs (amend, workflow actions, comments, files, mapped documents) are in the backlog.

**Plan revision (2026-10-27).** The first version of this plan targeted a non-admin ERPNext business user. It was refocused on developers: stages 06–09 are new, the old read-primitives, ergonomics, distribution, and compatibility stages were renumbered and reoriented, and the old lifecycle, collaboration, and method-allowlist stages moved to [backlog/](backlog/).

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
2. **AGENTS.md is binding.** That means: no SQL, no Python eval, no unrestricted method dispatch; Frappe permissions are preserved; outputs are bounded; mutations are previewed and confirmed; filesystem writes stay confined; no secrets or customer data appear in tests or evidence. Fields that Frappe evaluates as code (Python conditions, Jinja templates, client or server scripts) count as code: a stage that would let the model write them must ask the user first.
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
9. **Check Frappe behavior in the source, not from memory.** Frappe references such as `frappe/client.py` were checked against Frappe **v16.33.1**. The new developer stages (06–09) name Frappe files and behaviors to check, but those weren't verified when the plan was written; confirm each one before relying on it. If a local Bench checkout is available, read the source there. Behavior can differ in v15; note any difference you find in the Handoff section.

## Stages

| # | Stage | Depends on | Status |
|---|---|---|---|
| 00 | [Baseline: CI, test harness, cleanup](stage-00-baseline.md) | none | done |
| 01 | [Authentication and transport fixes](stage-01-auth-transport.md) | 00 | done |
| 02 | [Read validation and atomic updates](stage-02-read-validation.md) | 00 | done |
| 03 | [Customization tool fixes](stage-03-customization-fixes.md) | 00 | done |
| 04 | [Native customization export](stage-04-native-export.md) | 03 | done |
| 05 | [Metadata layer: cache, compact schema, fewer requests](stage-05-metadata-layer.md) | 02 | done |
| 06 | [Edit existing DocTypes](stage-06-update-doctype.md) | 05 | todo |
| 07 | [Site and app sync, portable custom DocTypes](stage-07-app-sync.md) | 04, 06 | todo |
| 08 | [Workflows and Notifications](stage-08-workflow-notification.md) | 07 | todo |
| 09 | [Report Builder reports and Workspaces](stage-09-reports-workspaces.md) | 08 | todo |
| 10 | [Read primitives, submit and cancel](stage-10-read-primitives.md) | 05 | todo |
| 11 | [Agent ergonomics: errors, instructions, annotations, toolsets, reports](stage-11-agent-ergonomics.md) | 09, 10 | todo |
| 12 | [Distribution and releases](stage-12-distribution.md) | 11 | todo |
| 13 | [Compatibility matrix and docs rewrite](stage-13-compat-and-docs.md) | 01–12 | todo |

Status values: `todo`, `in progress`, `partial`, `done`.

Stages 01, 02, and 03 depend only on 00 and can run in any order. Stage 10 depends only on 05, so it can run in parallel with 06–09. Stage 12 can run any time after 11 if you want early releases.

## Backlog (not scheduled)

Pick these up after stage 13, or when a real user needs them:

- **[Document lifecycle and workflow actions](backlog/lifecycle-workflow.md):** amend, workflow transitions on documents, update diffs, and mandatory-field previews. Minimal submit and cancel are in stage 10.
- **[Collaboration and files](backlog/collaboration-files.md):** document activity, comments, assignments, and attachments.
- **[Allowlisted method adapters](backlog/method-allowlist.md):** mapped documents and app-specific methods through a per-method allowlist.
- **Async HTTP.** Tools are synchronous, so the reload waits in `_wait_for_doctype` and similar helpers (up to about 60 s of `time.sleep`) block the MCP event loop. This hurts the developer flow more than the data flow, because DocType creation, DocType edits (stage 06), and exports all trigger Bench reloads. Move them to `httpx.AsyncClient` with async tools.
- **Streamable HTTP transport** with per-user authentication, for teams sharing one server. This needs its own security design.
- **Print formats and PDF**: render to text or HTML for the model, and optionally save a PDF to a confined path. Print Format templates are Jinja, so rule 2 applies.
- **Rename DocTypes and fields** with data migration (`frappe.rename_doc`, `rename_field`). Stage 06 refuses fieldname changes.
- **MCP resources and prompts**: DocType schemas as resources, and prompts such as "add a field to this DocType".
