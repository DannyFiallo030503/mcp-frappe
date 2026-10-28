# Upstream Code

The legacy Frappe-hosted implementation used a Streamable HTTP MCP transport based on [`frappe/mcp`](https://github.com/frappe/mcp), commit `11d5076b1bf4483b2ff6751a13e0736f5396b1e6` (version 0.1.1, MIT License). That vendored code now lives under `legacy/frappe_mcp_server/`, with the rest of the legacy app in `legacy/`. It is kept for history only: it is not packaged, imported, linted, or tested, and the standalone server in `frappe_mcp/` does not depend on it.

The upstream dependency pinned Werkzeug and Pydantic versions that differ from those managed by compatible Frappe releases, so the legacy app did not reinstall them; it declared only the additional `jsonschema` and Click dependencies the transport needed. Upstream implementation and copyright notices are retained.

If vendored code is ever updated or reused, record the source repository, commit, version, license, and local modifications. Preserve upstream notices and review the changed dependency constraints against supported Frappe versions.
