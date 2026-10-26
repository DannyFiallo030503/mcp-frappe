---
name: commit-and-pr
description: Commit mcp-frappe changes, push a branch, and open or update a GitHub pull request when the user explicitly asks to commit, push, ship, or create a PR. Do not invoke merely because edits are complete, and do not use for read-only review requests.
---

# Commit and open a pull request

Land finished work as a focused, reviewable change. This is the Codex adaptation of the Claude
commit-and-PR workflow in `.claude/skills/commit-and-pr/`. Only commit, push, or mutate GitHub
state when the user explicitly requests the corresponding action. `AGENTS.md` is binding.

## Inspect before staging

Run these checks and read the actual diff:

```bash
git branch --show-current
git status --short --untracked-files=all
git diff HEAD --stat
git diff HEAD
```

Check for secrets, `.env` files, `FRAPPE_API_KEY`/`FRAPPE_API_SECRET`/`FRAPPE_PASSWORD` values,
`.mcp-frappe-backups/`, scratch files, build output, debug leftovers, and unrelated user changes.
Stage explicit paths rather than the entire working tree.

- **Evidence files** (`e2e/*.json`, `e2e/*.jsonl`, `e2e/*report*.md`): check for real hostnames,
  site names, user emails, credentials, cookies, and customer records. Docs and evidence use
  placeholder or `.localhost` hostnames only.
- **Tool contract**: if `frappe_mcp/standalone.py` changed a tool's signature, docstring, or
  return shape, `docs/tool-reference.md`, `docs/ai-agent-guide.md`, the affected
  `e2e/run_standalone_*.py` runners, and `tests/test_standalone.py::test_server_registers_expected_tools`
  must change with it.

If the repository has a `.codegraph/` index, inspect changed symbols and affected tests before
describing coverage (`codegraph impact "<symbol>"`, `codegraph affected <files>`). Skip this for
documentation-only changes.

## Branch, verify, and commit

If the current branch is `main`, create a focused branch before committing:

- work-plan stage (`docs/work-plan/stage-NN-*.md`): `stage-NN-<slug>`
- anything else: `<type>/<short-slug>` with `feat`, `fix`, `docs`, `refactor`, `chore`, or `test`

If already on a suitable branch, stay on it.

For code changes, run what CI runs and record the results:

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

`e2e/` has pre-existing ruff errors; if you edited a runner, check only that you added none. Do
not run the e2e runners unless the user asked, and then only against a loopback `.localhost`
site.

Commit subjects follow this repository's history: imperative mood, capitalized, no trailing
period (`Add CLAUDE.md with commands and architecture overview`). Add a body when the motivation
or tradeoff is not evident from the diff, and name the work-plan stage when the change belongs to
one. Do not invent an attribution trailer; include one only when the active session explicitly
requires its exact text.

After staging only the intended paths, review the staged diff before committing:

```bash
git diff --cached --stat
git diff --cached
```

## Push only when requested

If the user asked to push or open a PR, push the current branch without force:

```bash
git push -u origin <branch>
```

If the push is rejected as non-fast-forward, stop and report it. Do not force-push, rebase over
unreviewed remote work, or amend a pushed commit without explicit approval.

## Open or update the PR

Read the remote instead of assuming the owner and name (`git remote -v`; currently
`DannyFiallo030503/mcp-frappe`). Base is `main`, unless a stage builds on an unmerged earlier
stage branch.

First check whether a PR already exists for the branch. Update that PR when appropriate instead of
opening a duplicate.

Use an available GitHub connector or MCP tool to create/update the PR and capture the returned URL.
If no connector is available, check for `gh` before attempting to use it. If neither is available,
report that limitation after completing only the explicitly authorized local or push operations.

Write the PR for a reviewer who has not seen the conversation:

- Summarize what changed and why; link the work-plan stage file when there is one.
- Identify the main areas changed and meaningful tradeoffs.
- List exact verification commands and results, and whether any MCP client or e2e run against a
  local site happened. If a check did not run, say so.
- Call out tool-contract changes, refreshed e2e evidence, and intentionally deferred work.

Use the actual URL returned by GitHub; never guess one.

## Report back

Provide the commit hash, pushed branch when applicable, actual PR URL when created or updated, test
results, and any requested operation that could not be completed. Do not merge or close the PR
unless the user separately asks for it.
