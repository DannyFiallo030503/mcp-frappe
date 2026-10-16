# Instructions for AI coding agents

These instructions apply to the entire repository.

## Project purpose

This repository is a generic Frappe app that exposes Frappe sites as an authenticated MCP server. Keep it independent of CubaZone-specific apps and avoid hardcoded ERPNext/HRMS catalogs. Discover site apps and metadata at runtime.

## Required behavior

- Preserve the connected Frappe user's permissions. Never add `ignore_permissions=True` to bypass authorization.
- Do not add arbitrary SQL execution, arbitrary Python evaluation, or an unbounded method dispatcher.
- Bound returned lists, report rows, file bytes, and rendered HTML.
- Validate user-provided DocType/field/app/module names against Frappe metadata and installed app ownership.
- For mutations, provide a preview and require explicit confirmation. For existing-document changes, require a preview-bound `expected_modified` token.
- Treat whitelisted methods as potentially side-effecting and non-idempotent.
- Restrict filesystem writes to the selected installed app and preserve backups/rollback behavior.
- Keep secrets, customer data, and live credential files out of source, tests, and evidence.

## Before changing tools

1. Inspect the existing tool implementation and Frappe APIs used by the target operation.
2. Add type hints and a meaningful docstring; the MCP input schema is generated from function signatures/docstrings.
3. Add focused automated coverage under `mcp_frappe/tests/` or the matching vendored transport test directory.
4. Update `docs/tool-reference.md` and `docs/ai-agent-guide.md`; update security/architecture docs when relevant.
5. Exercise changed behavior using authenticated MCP HTTP calls against an isolated Frappe site when possible. Do not target a production site.
6. Inspect generated files and evidence for credentials or private data before sharing.

## Documentation conventions

User-facing documentation is written in English. Keep examples generic and runnable only after replacing placeholders. Never publish a credential or an actual customer identifier.
