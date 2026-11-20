# Stage 15: Adoption: release, tool-list cost, and working next to the native MCP

**Depends on:** 14 · **Size:** medium · **Source:** [native MCP comparison](native-mcp-comparison.md)

## Why

Frappe v16 ships an MCP server with nothing to install. This server must be easy to install, cheap to keep in context, and clearly complementary:

- **It isn't on PyPI yet.** There is no tag, and 0.3.0 has been in ROADMAP "Next" since stage 12. Until it is published, `uvx mcp-frappe` works for nobody else.
- **The tool list is expensive.** 35 tools cost about 60,000 characters (about 15,000 tokens) in every session; the native server has 13 tools. Most of that is long docstrings.
- **Nothing says how to use both servers.** Both suggest the server name `frappe`, and both have tools named `list_documents`, `get_document`, and so on. The docs don't say which server to use for what.

## Read first

- `standalone.py`: every `@_tool` docstring, the `Annotated` argument aliases, `_INSTRUCTIONS`
- `tests/test_mcp_contract.py`
- `.github/workflows/release.yml`, CHANGELOG, stage 12's Handoff (the publishing steps)
- [native-mcp-comparison.md](native-mcp-comparison.md)

## Tasks

### Release (the user runs it)

- [x] **Walk the user through the first release**, using stage 12's Handoff: the PyPI trusted publisher, the `pypi` GitHub environment, tag `v<version>`, and running `release.yml` on that tag. **Don't tag or run the workflow yourself.**
- [x] **After it is published,** drop the `uvx --from git+...` fallback from the README and `docs/installation-and-configuration.md`.
- [x] **Add an MCP registry entry** (`server.json`) if the user wants it listed. Check the current format in the MCP registry docs first.

### Tool-list budget

- [x] **Measure and pin the cost.** A contract test serializes `list_tools()`, as `tests/test_mcp_contract.py` gets it, and asserts a total character budget. Target: at most 32,000 characters for all tools (about half of today). Record the before and after numbers in the Handoff.
- [x] **Shorten the docstrings.** Each docstring keeps what the model needs to call the tool correctly: what it does, the key constraints, the return shape in one line, and the confirmation protocol. Explanations, Frappe background, and edge cases move to `docs/tool-reference.md`. Don't drop a constraint that a test or a validation error relies on.
- [x] **Share argument descriptions.** Use the existing `Annotated` aliases instead of repeating text in each docstring's Args section.
- [x] **Report the cost per toolset** in `check`. For example, "toolsets: read (16 tools, ~x chars), ..." helps a user decide what to switch off.

### Using both servers

- [x] **Use `frappe-dev` as the suggested server name** in the README, installation doc, and snippets, so it doesn't clash with the native server's `frappe`.
- [x] **New doc `docs/native-frappe-mcp.md`:**
  - what each server does, using the comparison table;
  - how to configure both in Claude Code;
  - which to use for what: data and method calls with native, development with this server;
  - with both servers, set `FRAPPE_MCP_TOOLSETS=read,customize,developer`, so the record tools don't appear twice.

  Link it from the README (absolute URL), `llms.txt`, and `docs/ai-agent-guide.md`.
- [x] **Add one line to `_INSTRUCTIONS`:** if another Frappe MCP server is connected, prefer it for record data and method calls, and use this one for DocTypes, customizations, and app files. Stay within the instruction length limit of stage 11.
- [ ] **Check it on a site.** On a v16 development site with native MCP enabled, configure both servers in Claude Code and run one task that uses each. Record the result, versions only, in `docs/compatibility-and-validation.md`. Ask the user before using a site.

## Acceptance criteria

- The tool list fits the budget, and a test enforces it.
- The docs explain how to run both servers side by side, with the suggested server names.
- The release is published, or the Handoff records why not and who decided.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
uv run pytest -q tests/test_docs_links.py
```

## Out of scope

Merging or removing tools. Changing the default toolsets: all four stay on (stage 11 rule).

## Handoff

- Status: done, except the release, which the user runs after this stage merges (see below).
- Branch / commits: `stage-15-adoption-and-context`, one commit on top of `main` (`b96d591`).
- What changed:
  - **Tool list.** `_tool()` passes `inspect.cleandoc(fn.__doc__)` as the description (Python before 3.13 keeps docstring indentation, so the size differed between CI versions), registers with `structured_output=False` (the generated output schemas were `{"additionalProperties": true}` or a bare `result` array), and drops `title` from the annotations (the tool's own `title` stays). `_trim_schema()` removes pydantic's `title` keywords (never a property named `title`), `additionalProperties: true`, `items: {}`, and the `null` branch plus `default: null` of optional arguments; an explicit `null` is still accepted. All 35 docstrings were rewritten to what the model needs (what the tool does, key constraints, return shape, confirmation); the details were already in `docs/tool-reference.md`. The `Annotated` alias descriptions (`Filters`, `Fields`, `OrderBy`, `ExpectedModified`, `ExpectedPreview`, `Confirm`, `Detail`, `ReportFilters`) are shorter and replace the old per-tool `Args:` lines for those arguments.
  - **Budget test.** `tests/test_mcp_contract.py` serializes `list_tools()` as a client receives it (`model_dump(mode="json", exclude_none=True)`, `json.dumps` default separators) and asserts at most 32,000 characters; further tests check there are no generated titles or output schemas, no `null` branches, that an explicit `null` still works, and that a list result still arrives one block per item.
  - **`check`** prints a `tool list` row from `_tool_list_cost()`: `read 16 tools ~10,191 chars, data 5 tools ~3,758 chars, ...; listed ~N chars; off: ...`.
  - **Both servers.** New `docs/native-frappe-mcp.md` (comparison, which to use for what, Claude Code setup for both, `FRAPPE_MCP_TOOLSETS=read,customize,developer`, the tool-name overlap), linked from the README (absolute URL), `llms.txt`, and `docs/ai-agent-guide.md`. README and the installation doc use `frappe-dev` as the server name. `_INSTRUCTIONS` gained one line (1,475 of the 1,500 characters the stage 11 test allows).
  - **Registry.** `server.json` (schema `2025-12-11`, name `io.github.DannyFiallo030503/mcp-frappe`, PyPI package, stdio, the `FRAPPE_*` variables with secrets marked) and the `<!-- mcp-name: ... -->` marker in the README, which the registry checks in the PyPI description. `tests/test_cli.py` checks that the versions match `__version__`, the marker matches `name`, the description is at most 100 characters, and each variable is one the server reads. Publishing steps are in `docs/development.md`.
  - Docs: tool-reference (tool-list size section, annotations), architecture, development, compatibility ("not yet checked": the side-by-side run), CHANGELOG (a **Breaking** entry for the missing `structuredContent` and annotation title), the comparison table, CLAUDE.md. The `create_custom_field` docstring said to edit a Custom Field with `update_document`, which has refused that since stage 14; it now says `set_field_property`.
  - e2e: `run_standalone_smoke.py` decodes `list_installed_apps` from its content blocks; the other runners read `content[0]` for dict results, which still works (their `structuredContent` branches are now dead code, left alone).
- Tool-list size before / after (characters, per toolset):
  - Before: 61,215 total (read 19,785, data 6,908, customize 11,637, developer 22,885). This is the "about 60,000" of the comparison doc; on Python 3.10/3.12 it was larger, because docstrings kept their indentation.
  - After: 31,853 total on Python 3.10, 3.12, and 3.13 (read 10,191, data 3,758, customize 6,054, developer 11,850). Instructions: 1,475 characters.
  - Where it went: output schemas and generated titles ~8,800, `null` branches and default noise ~2,900, docstrings 22,900 → ~10,000, argument descriptions 7,500 → ~4,000, annotation titles ~1,300.
- Release published? (version, who ran it): yes, after the stage merged. 0.3.0 is on PyPI (uploaded 2026-11-20), published by the user through `release.yml` on tag `v0.3.0`; the changelog date was corrected from 2026-11-19 to 2026-11-20 afterwards. The `uvx --from git+...` fallback is gone from the README and the installation doc. The MCP registry refused a local `mcp-publisher publish` as `dfiallo35` (the `io.github.DannyFiallo030503/` namespace belongs to that personal account), so `.github/workflows/publish-mcp.yml` publishes through GitHub Actions OIDC, which grants the repository owner's namespace; the user runs it.
- Side-by-side check (Frappe version, result): not run. The user chose unit tests only; recorded under "not yet checked" in `docs/compatibility-and-validation.md`. The Claude Code commands in `docs/native-frappe-mcp.md` follow the Frappe MCP guide (fetched 2026-11-19): `claude mcp add --transport http frappe <site>/api/v2/mcp` and `claude mcp login frappe`, or `Authorization: token <key>:<secret>`.
- Deviations from this plan:
  - Beyond shortening docstrings, the budget needed schema changes: no output schema (so no `structuredContent` in results) and no `title` in the annotations. Both are listed under **Breaking** in the changelog; the JSON a model reads is unchanged. The stage 11 test that required `annotations.title == title` now requires the annotation title to be absent.
  - The FastMCP server name (`serverInfo.name`) stays `frappe`; only the suggested host-side name changed to `frappe-dev`.
  - `_trim_schema()` edits `server._tool_manager._tools[name].parameters`, a private FastMCP attribute (`mcp>=1.26,<2.0`). A FastMCP upgrade that moves it fails the schema tests, not silently.
- Notes for the next stage:
  - There are only ~150 characters left under the budget. Stage 16's new tools must fit: keep each description to two or three short sentences, reuse the aliases, and if the budget must grow, raise `TOOL_LIST_BUDGET` in `tests/test_mcp_contract.py` deliberately and record the new number here and in the tool reference.
  - `_INSTRUCTIONS` has 25 characters left under its 1,500 limit; trim before adding a line.
  - Release: drop the `git+` fallback once 0.3.0 is on PyPI, and bump `server.json` together with `__version__` on every release (a test enforces it).
