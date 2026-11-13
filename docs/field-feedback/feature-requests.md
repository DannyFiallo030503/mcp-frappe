# Field feature requests

Add an entry when a real task requires an operation that the current MCP toolset does not provide. First check the tool reference and existing open requests. Do not use this file for permission denials, intentionally refused operations, or bugs in an existing tool.

## Open requests

### FR-2026-001 — Expose a safe configured site label in site info

- **Status:** open
- **Reported:** 2026-11-04
- **Task reference:** sanitized Platform MCP multi-site usage policy
- **Frappe version / apps:** not applicable; limitation confirmed in the standalone server contract
- **Needed operation:** let an agent verify which configured site connection a tool call targets when several MCP instances are available.
- **Why existing tools are insufficient:** each process is configured for one `FRAPPE_URL` / optional `FRAPPE_SITE`, but `get_site_info` returns user and app versions without a site label. The host's server alias helps select a connection but is not returned or checked by the server.
- **Expected behavior:** optionally configure a non-sensitive site alias and return it from `get_site_info` (or equivalent initialization metadata); never expose credentials, raw URLs, or secrets. Each process remains bound to one target.
- **Permissions and safety:** the label is informational only and must not enable per-call target switching; credentials, hostname, and private site identifiers remain hidden unless explicitly configured as a safe alias.
- **Evidence:** current single-target startup configuration and `get_site_info` implementation; no live site call was made.
- **Acceptance:** with two named MCP instances, each reports its configured safe alias and user/app context, while all tool calls remain pinned to its own site; unit tests prove no cross-instance session/config leakage.
- **Implementation:** pending

### FR-2026-002 — Address multiple named sites through one MCP connection

- **Status:** open
- **Reported:** 2026-11-04
- **Task reference:** sanitized Platform multi-site MCP requirement
- **Frappe version / apps:** not applicable; current behavior is fixed by process configuration
- **Needed operation:** let one MCP server/host connection manage more than one explicitly named site at a time.
- **Why existing tools are insufficient:** `FRAPPE_URL`, optional `FRAPPE_SITE`, credentials, and the cached HTTP session bind one process to one target. Separate MCP processes can already be configured for separate sites, and `FRAPPE_SITE` routes to one host-selected site on a multi-site Bench, but one process has no per-call site selection.
- **Expected behavior:** expose named site connections with explicit target selection and report the selected alias on every operation; preserve separate credentials, sessions, app paths, and site outcomes. A task must never silently fall back to a default site.
- **Permissions and safety:** require explicit site selection, isolate credentials/session/cache/filesystem roots per target, preserve Frappe permissions, and bind every preview/confirmation to the same site alias. Do not allow a write preview from one site to be confirmed on another.
- **Evidence:** current startup settings and singleton HTTP session; no live site call was made.
- **Acceptance:** two test sites can be read in one host session with results attributed to the correct alias; cross-target preview confirmation is rejected; session, metadata, and filesystem isolation tests pass.
- **Implementation:** pending

### FR-2026-003 — Enforce a read-only profile for remote sites

- **Status:** open
- **Reported:** 2026-11-04
- **Task reference:** sanitized Platform remote-site access requirement
- **Frappe version / apps:** not applicable; current remote behavior follows startup configuration
- **Needed operation:** connect to remote Frappe sites for inspection while guaranteeing that MCP mutation tools cannot be used.
- **Why existing tools are insufficient:** HTTPS remote URLs are supported, and `FRAPPE_MCP_READ_ONLY=YES` already exposes only read tools when explicitly configured. However, writable toolsets remain enabled by default for remote URLs; the server does not automatically enforce a remote read-only policy.
- **Expected behavior:** provide a clearly documented and validated remote read-only profile; decide whether remote targets must default to read-only or require an explicit, auditable opt-in to any writable mode. The read-only profile must omit every mutation tool from `tools/list` and reject attempts to invoke them.
- **Permissions and safety:** require HTTPS for remote targets, use credentials from the host's secret store, preserve remote Frappe permissions, and keep the read-only guarantee independent of the user's Frappe role. Do not silently enable writes.
- **Evidence:** current URL validation, toolset filtering, and `FRAPPE_MCP_READ_ONLY` configuration; no live remote call was made.
- **Acceptance:** test against a remote HTTPS test site that reads work, mutation tools are absent and cannot be called, and no write occurs; test startup behavior for all remote/read-only combinations.
- **Implementation:** pending

### FR-2026-004 — Safely sync an app DocType that is missing from the site

- **Status:** open
- **Reported:** 2026-11-09
- **Task reference:** product-line-as-item-group
- **Frappe version / apps:** Frappe 15.96.0, ERPNext 15.96.1, erp_app 0.0.1
- **Needed operation:** preview and synchronize an existing standard DocType from an app checkout when its JSON/controller files exist but the DocType metadata is absent on the configured site.
- **Why existing tools are insufficient:** `list_app_drift` identified an app DocType as `missing_on_site`; `create_doctype` refused to overwrite the existing app file; `update_doctype` requires a DocType already present on the site. No MCP operation safely syncs that existing app definition into site metadata.
- **Expected behavior:** offer a preview of the existing app definition and dependency/metadata changes, then apply with an expected preview token and verify with `get_doctype_schema` and `list_app_drift`.
- **Permissions and safety:** require app/module ownership and normal Frappe permissions; refuse to overwrite differing files; never run migrations implicitly; make the affected DocType and dependencies explicit.
- **Evidence:** [sanitized task report](cubazone/product-line-as-item-group.md); app drift showed missing DocTypes and `create_doctype` refused overwrite.
- **Acceptance:** a missing-on-site DocType with matching checked-in app files can be synchronized through preview/confirm without replacing files, with schema and drift verification; mismatched definitions are refused.
- **Implementation:** pending

## Template

```markdown
### FR-YYYY-NNN — Short capability name

- **Status:** open
- **Reported:** YYYY-MM-DD
- **Task reference:** public issue/PR or sanitized slug
- **Frappe version / apps:** only verified, non-sensitive details; otherwise unknown
- **Needed operation:** the user-visible operation, stated narrowly
- **Why existing tools are insufficient:** name the checked tools and the exact gap
- **Expected behavior:** inputs, outputs, and verification needed
- **Permissions and safety:** native permission boundary, preview/confirmation, limits, idempotency, or filesystem constraints that apply
- **Evidence:** safe artifact/link or none
- **Acceptance:** observable conditions for considering the capability complete
- **Implementation:** link and date when addressed
```
