# Instructions for AI coding agents

This repository builds a generic, standalone Frappe MCP server. The MCP process runs outside Frappe and connects over standard HTTP APIs. Do not change target sites, install a Frappe app, run migrations, or deploy unless the user explicitly asks.

## Required behavior

- Preserve the connected Frappe user's permissions; do not bypass authorization.
- Do not add arbitrary SQL, Python evaluation, or unrestricted method dispatch.
- Bound list, report, and response sizes.
- Validate DocType, field, app, and module names against remote metadata.
- Preview mutations and require confirmation; bind existing-document changes to a current `modified` value.
- Restrict optional filesystem writes to `FRAPPE_APPS_PATH`, verify remote app/module ownership, and preserve backups.
- Keep credentials, private site data, and customer data out of source, tests, and evidence.

## Before changing tools

1. Inspect the existing tool and the standard Frappe API used.
2. Add type hints and an informative docstring; the MCP schema comes from signatures/docstrings.
3. Add focused coverage for changed behavior.
4. Update tool reference and AI guide; update architecture/security/compatibility docs when needed.
5. Exercise changed behavior with an MCP client and a non-production site when possible.
6. Inspect evidence and generated files for secrets and private data.

## Documentation

User-facing documentation is in English. Examples use placeholders or local development hostnames; never publish a credential or real customer identifier.
