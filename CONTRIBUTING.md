# Contributing

Thanks for helping improve Frappe MCP. Read [`AGENTS.md`](AGENTS.md), the [AI agent guide](docs/ai-agent-guide.md), and [security model](docs/security.md) before changing behavior.

## Before opening a change

- Search existing tools and docs to avoid duplicate behavior.
- Keep changes generic across Frappe apps; do not couple the project to one customer's site.
- Preserve Frappe permission checks and native document behavior.
- Add bounds and validation to caller-controlled inputs and outputs.
- For writes, implement preview/confirmation and concurrency checks where applicable.
- Add focused automated coverage and update user-facing docs for behavior changes.
- Exercise the endpoint over authenticated HTTP on a disposable site for integration-sensitive changes.
- Do not include credentials, customer data, local Bench state, or unreviewed export artifacts.

## Development workflow

See [`docs/development.md`](docs/development.md) for repository layout, test directories, Bench commands, and the real HTTP acceptance runner. The test site should be disposable because acceptance runs create Frappe records and app files.

## Pull request contents

Describe the user-visible change, relevant permission and compatibility effects, and how it was validated. Link to an issue when one exists. Include focused evidence, but redact user data and secrets. Avoid claiming support for Frappe versions that were not checked.

## Style and scope

Use existing Python conventions and Ruff configuration in `pyproject.toml`. Keep each tool focused and documented with a clear schema and side-effect description. Do not change vendored upstream code without recording the origin and retaining its license notices.
