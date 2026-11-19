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

- [ ] **Walk the user through the first release**, using stage 12's Handoff: the PyPI trusted publisher, the `pypi` GitHub environment, tag `v<version>`, and running `release.yml` on that tag. **Don't tag or run the workflow yourself.**
- [ ] **After it is published,** drop the `uvx --from git+...` fallback from the README and `docs/installation-and-configuration.md`.
- [ ] **Add an MCP registry entry** (`server.json`) if the user wants it listed. Check the current format in the MCP registry docs first.

### Tool-list budget

- [ ] **Measure and pin the cost.** A contract test serializes `list_tools()`, as `tests/test_mcp_contract.py` gets it, and asserts a total character budget. Target: at most 32,000 characters for all tools (about half of today). Record the before and after numbers in the Handoff.
- [ ] **Shorten the docstrings.** Each docstring keeps what the model needs to call the tool correctly: what it does, the key constraints, the return shape in one line, and the confirmation protocol. Explanations, Frappe background, and edge cases move to `docs/tool-reference.md`. Don't drop a constraint that a test or a validation error relies on.
- [ ] **Share argument descriptions.** Use the existing `Annotated` aliases instead of repeating text in each docstring's Args section.
- [ ] **Report the cost per toolset** in `check`. For example, "toolsets: read (16 tools, ~x chars), ..." helps a user decide what to switch off.

### Using both servers

- [ ] **Use `frappe-dev` as the suggested server name** in the README, installation doc, and snippets, so it doesn't clash with the native server's `frappe`.
- [ ] **New doc `docs/native-frappe-mcp.md`:**
  - what each server does, using the comparison table;
  - how to configure both in Claude Code;
  - which to use for what: data and method calls with native, development with this server;
  - with both servers, set `FRAPPE_MCP_TOOLSETS=read,customize,developer`, so the record tools don't appear twice.

  Link it from the README (absolute URL), `llms.txt`, and `docs/ai-agent-guide.md`.
- [ ] **Add one line to `_INSTRUCTIONS`:** if another Frappe MCP server is connected, prefer it for record data and method calls, and use this one for DocTypes, customizations, and app files. Stay within the instruction length limit of stage 11.
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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Tool-list size before / after (characters, per toolset):
- Release published? (version, who ran it):
- Side-by-side check (Frappe version, result):
- Deviations from this plan:
- Notes for the next stage:
