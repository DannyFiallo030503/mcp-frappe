# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read `AGENTS.md` first; its rules (no SQL/Python eval/unrestricted method dispatch, preserve Frappe permissions, bounded outputs, preview + confirm mutations, confined filesystem writes, no secrets or customer data in evidence) are binding. Do not touch target sites, install apps, migrate, or deploy unless explicitly asked.

## Commands

```bash
uv sync --extra dev                                   # install with pytest + ruff
uv run pytest -q                                      # unit tests (testpaths = tests/)
uv run pytest -q tests/test_standalone.py::test_name  # single test
uv run ruff check frappe_mcp tests e2e/run_standalone_*.py
uv run mcp-frappe                                     # run the stdio server (or: uv run python -m frappe_mcp.standalone)
```

Ruff: line length 100, target py310, rules `E,F,I,B,UP`; `legacy/` is excluded. The tree is not lint-clean yet (pre-existing E501/I001 hits, most of them in `e2e/`), so check only that your changes add no new errors.

The e2e runners in `e2e/run_standalone_*.py` launch a real MCP stdio session against a live site configured with `FRAPPE_*` env vars (see `docs/installation-and-configuration.md`). `run_standalone_smoke.py` is read-only; the mutation, customization, and DocType-matrix runners write temporary data, refuse non-loopback targets, and need opt-in env vars (`FRAPPE_ALLOW_MUTATION_TEST=YES`, `FRAPPE_ALLOW_CUSTOMIZATION_TEST=YES`). Run them only on a disposable dev site, and only when asked.

## Architecture

A standalone MCP server (FastMCP over stdio) that runs outside Frappe and talks to a site only through its public HTTP API (`/api/resource/...`, `/api/method/...`). Everything lives in one module, `frappe_mcp/standalone.py`:

- **HTTP layer**: `_client()` builds a lazily created, global `httpx.Client` from env vars. It uses token auth (`FRAPPE_API_KEY`/`FRAPPE_API_SECRET`) or `/api/method/login` with a password (HTTPS only, except loopback/`.localhost`). `FRAPPE_SITE` is sent as the `Host` header for multi-site Benches. `_request()` → `_check()` unwraps Frappe's `data`/`message` envelope and turns errors into `RuntimeError`. `_resource()` URL-quotes DocType and document names.
- **Tools**: each `@server.tool()` function is an MCP tool. The tool schema comes from the type hints and docstring, so keep both accurate. `tests/test_standalone.py::test_server_registers_expected_tools` pins the exact tool set; update it when you add or remove a tool.
- **Validation**: `_doctype()` merges a DocType's fields with Custom Fields and Property Setters. `_assert_fields()` checks caller field names against that merged schema and rejects reserved or read-only fields on writes. App, module, and DocType names must match `_SAFE_NAME`. DocType settings and role-permission flags are allowlisted (`_DOCTYPE_PROPERTIES`, `_ROLE_PERMISSION_FLAGS`).
- **Mutation protocol**: tools return a preview when `confirm=False`. Update and delete previews return `modified`, and confirmation must pass it back as `expected_modified` (`_expected_modified()` re-reads the document). Exports and rollbacks use a `preview_id`, a SHA-256 of the preview content, which must match on confirmation.
- **Filesystem writes** (standard `create_doctype`, `export_customizations`, `rollback_export`): `_app_path()` resolves `FRAPPE_APPS_PATH/<app>/<app>/<module_slug>` with strict resolution and `is_relative_to` checks against traversal and symlink escapes. `_assert_remote_module()` checks that the remote Module Def belongs to the app. `_assert_writable_app()` refuses `frappe`/`erpnext` (`_PROTECTED_APPS`). Writes go through `_atomic_write()`. Exports back up overwritten files and write a manifest under `<app>/.mcp-frappe-backups/`, maintain a managed block in `hooks.py` between `_HOOK_BEGIN`/`_HOOK_END`, and rollback refuses to proceed if exported files changed since the export.
- **Bench reload tolerance**: standard DocType creation and exports can make a developer-mode Bench reload its web worker mid-request. Code such as `_wait_for_doctype`, `_wait_for_server_ready`, and `_wait_for_document_deleted` reconciles the remote state after an `httpx.TransportError` instead of assuming failure. Keep this pattern for new write paths that can trigger a reload.

Unit tests isolate the remote site by monkeypatching `standalone._request`, `standalone._doctype`, etc., and use `tmp_path` with `FRAPPE_APPS_PATH` for filesystem cases. They never need a live site.

`legacy/` holds the old implementation that ran as an installed Frappe app. It is not packaged, linted, or tested; don't import from it or assume its tools exist. `UPSTREAM.md` describes that legacy vendored transport.

## Docs to keep in sync

When tool behavior changes, update `docs/tool-reference.md` and `docs/ai-agent-guide.md`, and update `docs/architecture.md`, `docs/security.md`, or `docs/compatibility-and-validation.md` when relevant. `llms.txt` and `README.md` link to these. Docs are in English and use placeholder or `.localhost` hostnames only.
