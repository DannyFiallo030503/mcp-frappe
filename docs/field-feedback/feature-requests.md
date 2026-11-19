# Field feature requests

Add an entry when a real task requires an operation that the current MCP toolset does not provide. First check the tool reference and existing open requests. Do not use this file for permission denials, intentionally refused operations, or bugs in an existing tool.

Each entry has a **Source**: `field` when a task needed the operation, or `requirement` when it came from a usage policy or a requirement written before any task needed it.

## Open requests

### FR-2026-004 — Safely sync an app DocType that is missing from the site

- **Status:** open
- **Source:** field
- **Reported:** 2026-11-09
- **Task reference:** product-line-as-item-group
- **Frappe version / apps:** Frappe 15.96.0, ERPNext 15.96.1, a custom app 0.0.1
- **Needed operation:** preview and synchronize an existing standard DocType from an app checkout when its JSON/controller files exist but the DocType metadata is absent on the configured site.
- **Why existing tools are insufficient:** `list_app_drift` identified an app DocType as `missing_on_site`; `create_doctype` refused to overwrite the existing app file; `update_doctype` requires a DocType already present on the site. No MCP operation safely syncs that existing app definition into site metadata.
- **Expected behavior:** offer a preview of the existing app definition and dependency/metadata changes, then apply with an expected preview token and verify with `get_doctype_schema` and `list_app_drift`.
- **Permissions and safety:** require app/module ownership and normal Frappe permissions; refuse to overwrite differing files; never run migrations implicitly; make the affected DocType and dependencies explicit.
- **Evidence:** [sanitized task report](reports/product-line-as-item-group.md); app drift showed missing DocTypes and `create_doctype` refused overwrite.
- **Acceptance:** a missing-on-site DocType with matching checked-in app files can be synchronized through preview/confirm without replacing files, with schema and drift verification; mismatched definitions are refused.
- **Implementation:** planned in [stage 18](../work-plan/stage-18-bench-runner.md) (`reload-doc` / `migrate`). Meanwhile, since [stage 14](../work-plan/stage-14-field-feedback-fixes.md) `create_doctype`'s refusal says to run `bench migrate` when the app already has the DocType's file, as `list_app_drift` already did.

### FR-2026-005 — Call an app's whitelisted method to check its response

- **Status:** open
- **Source:** field
- **Reported:** 2026-11-10
- **Task reference:** product-lines-null-central-2026-11-10
- **Frappe version / apps:** not recorded
- **Needed operation:** call one of the custom app's whitelisted methods with arguments and read its live response, to check what a downstream service receives.
- **Why existing tools are insufficient:** the record tools return stored field values, not what an app method computes from them. The task could read the Items and Item Groups but not the method's derived output, so the cause of the null values was not confirmed through MCP. No tool calls an app method, by design: AGENTS.md forbids unrestricted method dispatch.
- **Expected behavior:** list the app's whitelisted methods and their arguments, then call one that a developer has allowed, with bounded output and the connected user's permissions.
- **Permissions and safety:** an explicit allowlist of methods, set by the developer, never a free-form dotted path; read-only methods callable without confirmation and others behind preview and confirm; Frappe's own whitelist and permission checks still apply.
- **Evidence:** [sanitized task report](reports/product-lines-null-central-2026-11-10.md).
- **Acceptance:** an allowlisted read method of the app can be called and its bounded response read; a method outside the allowlist is refused.
- **Implementation:** planned in [stage 16](../work-plan/stage-16-dev-diagnostics.md) (list an app's whitelisted methods) and [stage 17](../work-plan/stage-17-method-allowlist.md) (allowlisted method calls).

## Resolved and closed

### FR-2026-001 — Expose a safe configured site label in site info

- **Status:** resolved
- **Source:** requirement
- **Reported:** 2026-11-04
- **Task reference:** sanitized multi-site usage policy of the consuming project
- **Frappe version / apps:** not applicable; limitation confirmed in the standalone server contract
- **Needed operation:** let an agent verify which configured site connection a tool call targets when several MCP instances are available.
- **Why existing tools are insufficient:** each process is configured for one `FRAPPE_URL` / optional `FRAPPE_SITE`, but `get_site_info` returned user and app versions without a site label. The host's server alias helps select a connection but is not returned or checked by the server.
- **Expected behavior:** optionally configure a non-sensitive site alias and return it from `get_site_info` (or equivalent initialization metadata); never expose credentials, raw URLs, or secrets. Each process remains bound to one target.
- **Permissions and safety:** the label is informational only and must not enable per-call target switching; credentials, hostname, and private site identifiers remain hidden unless explicitly configured as a safe alias.
- **Evidence:** current single-target startup configuration and `get_site_info` implementation; no live site call was made.
- **Acceptance:** with two named MCP instances, each reports its configured safe alias and user/app context, while all tool calls remain pinned to its own site; unit tests prove no cross-instance session/config leakage.
- **Implementation:** [stage 14](../work-plan/stage-14-field-feedback-fixes.md), 2026-11-19: optional `FRAPPE_SITE_LABEL` (1–64 characters, validated at startup), returned by `get_site_info` as `site_label` and printed by `mcp-frappe check`. It is never used for routing. Unit-tested; not yet checked with two live instances.

### FR-2026-002 — Address multiple named sites through one MCP connection

- **Status:** closed (won't do)
- **Source:** requirement
- **Reported:** 2026-11-04
- **Task reference:** sanitized multi-site requirement of the consuming project
- **Frappe version / apps:** not applicable; current behavior is fixed by process configuration
- **Needed operation:** let one MCP server/host connection manage more than one explicitly named site at a time.
- **Why existing tools are insufficient:** `FRAPPE_URL`, optional `FRAPPE_SITE`, credentials, and the cached HTTP session bind one process to one target. Separate MCP processes can already be configured for separate sites, and `FRAPPE_SITE` routes to one host-selected site on a multi-site Bench, but one process has no per-call site selection.
- **Expected behavior:** expose named site connections with explicit target selection and report the selected alias on every operation; preserve separate credentials, sessions, app paths, and site outcomes. A task must never silently fall back to a default site.
- **Permissions and safety:** require explicit site selection, isolate credentials/session/cache/filesystem roots per target, preserve Frappe permissions, and bind every preview/confirmation to the same site alias. Do not allow a write preview from one site to be confirmed on another.
- **Evidence:** current startup settings and singleton HTTP session; no live site call was made.
- **Acceptance:** two test sites can be read in one host session with results attributed to the correct alias; cross-target preview confirmation is rejected; session, metadata, and filesystem isolation tests pass.
- **Implementation:** closed as won't do in [stage 14](../work-plan/stage-14-field-feedback-fixes.md), 2026-11-19. The login session, the metadata cache, `FRAPPE_APPS_PATH`, and the preview tokens all assume one site, and hosts already keep sites apart by running one server per site. Configure one server per site, each with its own `FRAPPE_SITE_LABEL` (FR-2026-001).

### FR-2026-003 — Enforce a read-only profile for remote sites

- **Status:** resolved
- **Source:** requirement
- **Reported:** 2026-11-04
- **Task reference:** sanitized remote-site access requirement of the consuming project
- **Frappe version / apps:** not applicable; current remote behavior follows startup configuration
- **Needed operation:** connect to remote Frappe sites for inspection while guaranteeing that MCP mutation tools cannot be used.
- **Why existing tools are insufficient:** HTTPS remote URLs are supported, and `FRAPPE_MCP_READ_ONLY=YES` already exposed only read tools when explicitly configured. However, writable toolsets remained enabled by default for remote URLs; the server did not automatically enforce a remote read-only policy.
- **Expected behavior:** provide a clearly documented and validated remote read-only profile; decide whether remote targets must default to read-only or require an explicit, auditable opt-in to any writable mode. The read-only profile must omit every mutation tool from `tools/list` and reject attempts to invoke them.
- **Permissions and safety:** require HTTPS for remote targets, use credentials from the host's secret store, preserve remote Frappe permissions, and keep the read-only guarantee independent of the user's Frappe role. Do not silently enable writes.
- **Evidence:** current URL validation, toolset filtering, and `FRAPPE_MCP_READ_ONLY` configuration; no live remote call was made.
- **Acceptance:** test against a remote HTTPS test site that reads work, mutation tools are absent and cannot be called, and no write occurs; test startup behavior for all remote/read-only combinations.
- **Implementation:** [stage 14](../work-plan/stage-14-field-feedback-fixes.md), 2026-11-19: a target that isn't loopback or `.localhost` exposes only the `read` toolset unless `FRAPPE_MCP_READ_ONLY=NO`; the startup log and `mcp-frappe check` report the decision, and naming a writable toolset without the opt-in stops the server at startup. All startup combinations are unit-tested; the live check against a remote HTTPS site is still pending.

## Template

```markdown
### FR-YYYY-NNN — Short capability name

- **Status:** open
- **Source:** field (a task needed it) or requirement (a policy or requirement, before any task)
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
