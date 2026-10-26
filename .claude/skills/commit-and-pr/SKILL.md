---
name: commit-and-pr
description: Commit finished work and open a pull request through the GitHub MCP server. Use when the user asks to "commit", "commit and PR", "push this", "open a PR", "ship it", or "make a PR for this", or approves work that they now want landed. Do not commit on your own just because an edit is done — the work plan says commit, push, and PR only on request. The `gh` CLI is NOT installed on this machine, so any plan that runs `gh` will fail; PRs are created with the GitHub MCP tools instead.
---

# Committing and opening a pull request

A change lands well when someone else can read the branch, understand from the commit *why*
it happened, and review the PR without re-deriving the context you already have.

`gh` is not installed here. Reach for `mcp__github__*` tools for anything that touches
github.com, and plain `git` for anything local (`status`, `diff`, `commit`, `push` — push over
SSH works fine).

## 1. Look before you commit

Never commit a diff you have not read. Run these together:

```bash
git branch --show-current && git status --short --untracked-files=all && git diff HEAD --stat
```

Then read the actual diff (`git diff HEAD`, or `git diff HEAD -- <path>` when it is large).
You are looking for things that are cheap to catch now and expensive later:

- **Files that should not be tracked** — `.env`, credentials, `FRAPPE_API_KEY`/`FRAPPE_API_SECRET`
  values, local scratch files, `.mcp-frappe-backups/`, build output.
- **Private data in evidence** (`AGENTS.md` is binding here). When the diff touches
  `e2e/*.json`, `e2e/*.jsonl`, or the `e2e/*report*.md` files, check them for real hostnames,
  site names, user emails, API keys, session cookies, and customer records. Evidence and docs use
  placeholder or `.localhost` hostnames only. Fix it before committing, never after.
- **Docs out of sync.** If `frappe_mcp/standalone.py` changed a tool's signature, docstring, or
  return shape, `docs/tool-reference.md` and `docs/ai-agent-guide.md` must change in the same
  commit, and `tests/test_standalone.py::test_server_registers_expected_tools` must match the tool
  set. If they don't, finish them or tell the user before committing.
- **Unrelated changes** riding along. Stage by path (`git add <paths>`) so the commit is one
  idea. If the working tree really holds two ideas, make two commits.
- **Debug leftovers** — stray `print`, commented-out code, a `TODO` you meant to resolve.

When the diff touches code and the repo is CodeGraph-indexed (`.codegraph/` at the root), check
what the change reaches before you describe it:

```bash
codegraph impact "<symbol you changed>"     # callers a change reaches
codegraph affected <changed files>          # test files that cover them
```

Skip this for docs-only diffs and while the repo has no index.

## 2. Branch before you commit

If `git branch --show-current` says `main`, do not commit onto it. Branch first:

```bash
git checkout -b <branch>
```

- Work-plan stage (`docs/work-plan/stage-NN-*.md`): `stage-NN-<slug>`, as
  `docs/work-plan/README.md` requires.
- Anything else: `<type>/<short-slug>`, where `<type>` is `feat`, `fix`, `docs`, `refactor`,
  `chore`, or `test` (`docs/work-plan`, `fix/login-https-check`).

If you are already on a suitable branch, stay on it.

## 3. Verify, then write the commit message

Run what CI runs before committing anything that touches code:

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

The `e2e/` runners are not linted in CI and have pre-existing ruff errors; if you edited one,
check only that you added no new errors (`uv run ruff check e2e/<file>`). Never run the e2e
runners as part of committing — they need a live site and the user's say-so.

Use a heredoc so the body keeps its line breaks:

```bash
git add <paths> && git commit -F - <<'EOF'
<Subject line>

<body>

<attribution trailers>
EOF
```

**Subject** — imperative mood, capitalized like this repo's history (`Add CLAUDE.md with
commands and architecture overview`), under ~72 characters, no trailing period.

**Body** — say *why* the change exists and what a reader would otherwise have to reconstruct:
the problem, the approach, anything you deliberately did not do. For stage work, name the stage
(`Stage 03: customization tool fixes`). Do not narrate the diff — the diff is right there.

**Trailers** — this session's system prompt states the exact attribution required for commits
and PRs. Copy it from there verbatim; it can change per session, so never reuse one you remember
from earlier work.

## 4. Push

```bash
git push -u origin <branch>
```

## 5. Open the PR with the GitHub MCP server

Read `owner` and `repo` off the remote rather than assuming:

```bash
git remote -v
```

`git@github.com:DannyFiallo030503/mcp-frappe.git` → owner `DannyFiallo030503`, repo `mcp-frappe`.

Then call `mcp__github__create_pull_request` with `owner`, `repo`, `head` (your branch), `base`
(`main`, unless a stage builds on an unmerged earlier stage branch — then that branch), `title`,
and `body`.

**Title** — the commit subject. One line.

**Body** — written for a reviewer who has not seen this conversation:

```markdown
<One or two sentences: what this changes and why. For stage work, link the stage file.>

- **<Area>** — what changed here and the reasoning.
- **<Area>** — …

<Verification: the pytest and ruff commands and their result. Say whether any MCP client or
e2e run against a local site happened; if not, say so. "Docs only; no behavior changes." for
docs-only diffs.>

<Anything you noticed but deliberately left alone, so the reviewer knows it was a choice.>

<attribution footer from this session's system prompt>
```

Be honest in the verification line. A PR that overstates its own confidence costs the reviewer
more than one that admits a gap.

## 6. Report back

Give the user the PR URL that the tool returned. Do not construct a URL by hand. Mention
anything you chose not to do, so they can decide.

## When something is already in flight

- **A PR already exists for this branch** — find it with `mcp__github__list_pull_requests`
  (filter by `head`). Push the new commit and, if the description is now wrong, update it with
  `mcp__github__update_pull_request`. Do not open a second PR for the same branch.
- **The push is rejected as non-fast-forward** — someone else moved the branch. Stop and tell
  the user; do not force-push over work you have not looked at.
- **There is no remote** — commit locally, say so plainly, and let the user decide.

## What not to do

Commit and push only when the user has asked for it. Force-pushing, amending a pushed commit,
merging, or closing a PR are outward-facing actions that need their explicit go-ahead.
