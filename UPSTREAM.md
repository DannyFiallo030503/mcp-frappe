# Upstream Code

The Streamable HTTP MCP transport is based on [`frappe/mcp`](https://github.com/frappe/mcp), commit `11d5076b1bf4483b2ff6751a13e0736f5396b1e6` (version 0.1.1, MIT License). The vendored implementation is retained under `frappe_mcp/` so it can run inside this Frappe app's Python package.

The upstream dependency pins Werkzeug and Pydantic versions that differ from those managed by compatible Frappe releases. This app therefore does not reinstall those dependencies; it declares only the additional `jsonschema` and Click dependencies required by the transport. Upstream implementation and copyright notices are retained.

When updating vendored code, record the source repository, commit, version, license, and local modifications. Preserve upstream notices and review the changed dependency constraints against supported Frappe versions.
