---
name: worktree-task
description: Use when the user hands over a task to carry end to end — "take this task", "work on X in a worktree", "do this and ship it", "run stage NN in a worktree", "/worktree-task <description>" — and the change should be built on its own branch off origin/main in its own worktree, tested, landed (a PR whose CI it watches, fixes and merges once green, a PR left open, or a fast-forward), and cleaned up afterwards.
---

# Carrying a task from description to merged, in its own worktree

One task, one branch, one worktree. The main checkout is never touched: it may hold other work
in progress (or another Claude session). The task ends when its branch is merged **and** its
branch and worktree are gone — not when the tests pass.

Merging here deploys nothing; the server runs from users' own checkouts. It still changes what
everyone builds on, so landing always needs the user's answer from step 6.

## Checklist (make a todo per step)

1. Set up the worktree
2. Understand, then build
3. Test and fix until green
4. Ask: exercise it against a local site before committing?
5. Commit
6. Ask: PR merged when green, PR left open, or fast-forward to main
7. Land it
8. PR: watch its checks, fix what fails, merge when green
9. Clean up after the merge

## 1. Set up the worktree

Name the branch:

- Work-plan stage → `stage-NN-<slug>` (from `docs/work-plan/README.md`), slug `stage-NN`.
- Anything else → `<type>/<slug>` (`feat`, `fix`, `docs`, `refactor`, `chore`, `test`; two or
  three words).

The worktree lives at `.worktrees/<slug>` (git-ignored).

```bash
ROOT=$(git rev-parse --path-format=absolute --git-common-dir | xargs dirname)
git -C "$ROOT" fetch origin main
git -C "$ROOT" worktree add --no-track -b <branch> "$ROOT/.worktrees/<slug>" origin/main
cd "$ROOT/.worktrees/<slug>" && uv sync --extra dev
```

A stage that depends on an earlier stage whose branch is not merged yet starts from that branch
instead (`origin/stage-NN-<slug>`), as the work-plan rules allow — say so in the final report.

`--no-track` matters: without it the branch tracks `origin/main`, and a bare `git push` aims at
`main`. Work **only** inside `$ROOT/.worktrees/<slug>` — `cd` there once and use absolute paths.

If the repo is CodeGraph-indexed, `.codegraph/` describes the main checkout, which may differ
from `origin/main` — confirm with Read/Grep inside the worktree before editing.

## 2. Understand, then build

Read `CLAUDE.md` and `AGENTS.md` (binding: no SQL/eval/unrestricted method dispatch, Frappe
permissions preserved, bounded outputs, preview + confirm mutations, confined filesystem writes,
no secrets or customer data in tests or evidence).

**Work-plan stage:** also read `docs/work-plan/README.md` and the stage file, and work only on
that stage. Find code by symbol name, not by the line numbers the stage cites. Check Frappe
behavior in Frappe's source, not from memory.

While building, follow the repo's rules: tests first in `tests/` (monkeypatch
`standalone._request`/`_meta_bundle`, never a live site); type hints and docstrings are the MCP
schema; update `test_server_registers_expected_tools` when the tool set changes; update
`e2e/run_standalone_*.py` when a tool's signature or return shape changes; update
`docs/tool-reference.md` and `docs/ai-agent-guide.md` (plus architecture/security/compatibility
docs when relevant) in the same change.

**Ask the user only for decisions that are theirs** — a tool-contract or security-model choice
the spec does not settle, anything that would relax an `AGENTS.md` rule, scope that grew beyond
the description or stage, or anything destructive. Use `AskUserQuestion`, batch the questions,
put your recommendation first. Everything else — naming, helper layout, which existing pattern to
copy, test structure — decide yourself and mention it in the final report.

## 3. Test and fix until green

Run what CI runs, from the worktree:

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

If you edited an `e2e/` runner, also check you added no new ruff errors there (`uv run ruff
check <file>`; the tree has pre-existing hits). Run the stage file's own Verification commands
too. Docs-only change: no suite, say so.

On failure: **REQUIRED SUB-SKILL** superpowers:systematic-debugging — find the cause, fix it,
re-run the full suite (not only the failing test). Loop until green. If it still fails after
three honest attempts, or the failure predates your change (reproduce it in a clean `origin/main`
worktree to know), stop and tell the user with the output.

## 4. Offer a live check

Skip this when nothing in `frappe_mcp/` changed (docs, skills, work-plan files) — say so and go
to step 5. Otherwise, one `AskUserQuestion` with a short summary of what changed and the step-3
results:

- **Go on to commit** (default) — unit tests only.
- **Exercise it against a local site** — only if the user has a **disposable** dev site on a
  loopback or `.localhost` host. Never point it at a remote or production site.

On yes: the server reads `FRAPPE_URL`, `FRAPPE_SITE`, `FRAPPE_API_KEY`/`FRAPPE_API_SECRET` (or
`FRAPPE_USERNAME`/`FRAPPE_PASSWORD`) and `FRAPPE_APPS_PATH` from the process environment only —
there is no `.env` loader (see `docs/installation-and-configuration.md`). Check with
`env | grep -c '^FRAPPE_'` and `echo "$FRAPPE_URL $FRAPPE_SITE"` (never echo the key, secret or
password); if they are not set, ask the user to export them in this session rather than writing
them into a file. Confirm `FRAPPE_URL` is loopback and `FRAPPE_SITE` ends in `.localhost`. Then
run, in this order and stopping at the first failure:

```bash
uv run python e2e/run_standalone_smoke.py                                                         # read-only
FRAPPE_ALLOW_MUTATION_TEST=YES uv run python e2e/run_standalone_mutation_acceptance.py            # document writes changed
FRAPPE_ALLOW_CUSTOMIZATION_TEST=YES uv run python e2e/run_standalone_customization_acceptance.py  # customization/export changed; needs FRAPPE_APPS_PATH
FRAPPE_ALLOW_DOCTYPE_MATRIX=YES uv run python e2e/run_standalone_doctype_matrix.py                # create_doctype changed; needs FRAPPE_APPS_PATH
```

Every runner after the smoke test writes temporary data (and the last two write files under
`FRAPPE_APPS_PATH`) — name which ones you are about to run in the question, and run only those
the user approved. Before committing, inspect any evidence file they rewrote (`e2e/*.json`,
`*.jsonl`, `*report*.md`) for real hostnames, credentials, and customer data; if the user does not
want refreshed evidence committed, `git checkout -- <file>` it.

## 5. Commit

**Work-plan stage:** before committing, fill in the stage's **Handoff** section (status, branch /
commits, what changed, deviations, notes for the next stage) and update its row in the status
table in `docs/work-plan/README.md` (`done`, or `partial` with what is left).

Then follow the `commit-and-pr` skill's steps 1 and 3 (read the diff, check evidence for private
data, stage by path, message with the attribution trailer from this session's system prompt).
Commit only on this task's branch.

## 6. Ask how to land it

One `AskUserQuestion` with the summary of what changed and the test results (and the live
check's, if any):

- **PR, merge when green** (recommended) — open a PR, watch its CI, fix and re-push whatever
  fails, and merge it once every check passes. This answer is the go-ahead for the merge and for
  `--force-with-lease` pushes to this task's own branch (step 8).
- **PR, leave it open** — reviewable; CI runs; the user merges later.
- **Rebase + fast-forward main** — no PR; `main` moves to your commits.
- **Leave it** — keep branch and worktree, stop here.

A stage based on an unmerged earlier stage branch can only be a PR against that branch, or left;
do not offer the fast-forward.

## 7. Land it

**Fast-forward:**

```bash
git fetch origin main && git rebase origin/main
# rebase moved anything? re-run the step-3 suite before going on
git push origin HEAD:main          # plain fast-forward; if rejected, fetch + rebase + retest, never --force
```

Then go to step 9.

**PR (either kind):** rebase onto `origin/main` the same way, `git push -u origin <branch>`, then
the `commit-and-pr` skill's steps 5–6 (GitHub MCP — `gh` is not installed). **Merge when green**
→ step 8. **Leave it open** → report the URL and stop; step 9 happens when the user comes back
and says it merged (or asks you to watch it — then step 8).

## 8. Watch the PR, fix what fails, merge when green

`.github/workflows/ci.yml` (added in work-plan stage 00) runs `uv sync --extra dev`,
`uv run pytest -q`, and `uv run ruff check frappe_mcp tests` on Python 3.10 and 3.12. **If the
branch has no `.github/workflows/`, there is no CI:** the step-3 run is the only check — say so
and merge only if the user confirms it.

Read check runs with `mcp__github__pull_request_read` (`method: "get_check_runs"`, owner
`DannyFiallo030503`, repo `mcp-frappe`). Their logs are not reachable from here (no Actions MCP
tool, no `gh`). The Monitor tool cannot call MCP tools, so pace the polls with a timer: Bash
`sleep 180` with `run_in_background` after a push, then `sleep 90` between checks. You are
re-invoked when it exits; never poll in a tight loop.

Each poll, read `method: "get"` and `get_check_runs` and compare:

- **Head** — the PR's `head.sha` must equal your pushed `HEAD`; a stale head means your push has
  not registered yet.
- **Mergeable** — `mergeable: false` / `mergeable_state: "dirty"` is a conflict, and GitHub runs
  no `pull_request` workflow on a conflicting PR, so zero check runs never turns green.
- **Checks** — every matrix job present, `status: "completed"`, `conclusion: "success"`.
  `queued` / `in_progress` → keep waiting. `cancelled` → a newer push superseded it.

**All green** → `git fetch origin main`. If `main` moved since your push, rebase once, re-run the
step-3 suite, `git push --force-with-lease=<branch>:<sha you pushed> origin HEAD:<branch>`, and
wait for green again (one round). Then merge with `mcp__github__merge_pull_request`,
`merge_method: "merge"` (this repo's history uses merge commits), `expectedHeadSha` = the green
head — a push that slipped in after the green run makes the merge refuse.

**A check failed (or the PR conflicts)** → reproduce what CI runs from a clean state in the
worktree:

| Failed | Reproduce | Usual cause when it passed in step 3 |
|---|---|---|
| pytest | `uv sync --extra dev --frozen && uv run pytest -q`; on 3.10 too: `uv run --python 3.10 pytest -q` | a file left uncommitted (`git status`), `uv.lock` not updated, syntax or stdlib API newer than 3.10 |
| ruff | `uv run ruff check frappe_mcp tests` | an uncommitted fix, a new file not formatted |
| Conflict | `git fetch origin main && git rebase origin/main`, resolve, re-run step 3 | `main` moved under the PR |

Use **superpowers:systematic-debugging** for the cause. Fix it as a new commit on the branch
(`Fix …` saying what CI caught — no amend), re-run step 3, `git push` (a conflict rebase pushes
with `--force-with-lease` as above), and poll again. Never weaken or skip a test to turn it
green; change a test only when the test itself is wrong, and say so in the report.

**Can't reproduce it** → treat it as a flake once: re-trigger CI by closing and reopening the PR
(`mcp__github__update_pull_request` `state: "closed"`, then `"open"`). A second unexplained
failure, or three fix rounds without green, ends the loop: stop, leave the PR open and unmerged,
and give the user the failed check's `html_url`, what you tried, and what you think it is. Never
merge with a check red, pending, or missing.

## 9. Clean up after the merge

Confirm first: `git fetch origin && git merge-base --is-ancestor <branch> origin/main` (or the
PR's state is `merged`). Then, from `$ROOT`:

```bash
git -C "$ROOT" worktree remove "$ROOT/.worktrees/<slug>"    # refuses if dirty — look, don't --force
git -C "$ROOT" branch -d <branch>                           # -D only if verified merged above
git -C "$ROOT" push origin --delete <branch>                # if it was pushed and still exists
git -C "$ROOT" worktree prune
```

Leave the session's working directory outside the removed worktree. Report: what landed, the
commit/PR, test results, any live check, CI fixes pushed in step 8, and decisions you took on
your own.

## Common mistakes

| Mistake | Instead |
|---|---|
| Branching from local `main` or the current branch | `origin/main` after a fetch (or the unmerged earlier stage branch) |
| Editing files under `$ROOT` instead of the worktree | Absolute paths inside `.worktrees/<slug>` |
| Running e2e runners without asking, or against a non-local site | Only on the user's yes, only loopback/`.localhost`, only the runners they approved |
| Committing refreshed e2e evidence unread | Inspect for hostnames, credentials, customer data first |
| Writing `FRAPPE_*` credentials into a file "so the runner works" | The user exports them in the session; never echo or commit them |
| Tool behavior changed, docs or tool-set test not | Same commit: tool-reference, ai-agent-guide, expected-tools test, e2e runners |
| Stage done without Handoff and status table | Fill both before committing |
| Asking about naming, layout, test style | Decide; report it at the end |
| Pushing to `main` or merging without the step-6 answer | Always ask |
| Merging with a check pending, red, missing, or for an older head | Every job `success` on the current head, merged with `expectedHeadSha` |
| Skipping or loosening a test to get the PR green | Fix the cause; stop after three rounds and report |
| Skipping re-test after a rebase that moved | Rebased code is untested code |
| `worktree remove --force` / deleting an unmerged branch | Verify merged first; surface leftovers |
