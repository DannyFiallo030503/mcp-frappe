# Security model

## Identity and authorization

The standalone process authenticates to Frappe with either an API token or the standard login endpoint. Requests execute as that user, and Frappe's resource/method APIs enforce its permissions and validation. API tokens are preferred outside local development.

## Transport

One rule covers both auth modes: `FRAPPE_URL` must use HTTPS unless the host is a loopback address (`localhost`, `127.0.0.1`, `::1`) or ends in `.localhost`. An API token is a long-lived credential sent on every request, so plain HTTP to any other host is refused before any request goes out, for token auth as well as password login.

Development networks sometimes reach the site by a container service name such as `http://frontend:8080`. For those, set `FRAPPE_ALLOW_INSECURE_HTTP=YES` (exactly that value). The server then allows plain HTTP to any host and logs one warning to stderr at startup. Use it only on a network you trust; credentials and data travel unencrypted.

## Sessions

A failed password login is never cached: the next tool call tries to log in again instead of continuing as Guest.

When a password session expires, Frappe treats requests as Guest and rejects them with HTTP 401 or 403 before running them. On such a response the server asks `/api/method/frappe.auth.get_logged_user` who it is. If that call fails or answers `Guest`, the session has expired: the server logs in again and retries the original request once. If the user is still logged in, the response is a real permission denial and is returned unchanged, with no retry and no other endpoint tried. Token auth never re-logs in, because a token does not expire with a session.

Administrator is a full-privilege account. If used for local development, supply its password through the MCP host's secret/environment facility. The project has no embedded default password and does not write credentials into files, tests, logs, or evidence. Do not use a known default password on a network reachable site.

## Tool boundaries

- There is no arbitrary Python, SQL, or unrestricted dotted-method tool.
- Lists and report output have explicit caps.
- Document changes preview first; update/delete confirmations require the current `modified` token. Updates also send that token to Frappe, which re-checks it under a row lock (`check_if_latest`), so a change between the check and the write is refused.
- Field names in reads, filters, and writes are checked against the DocType's metadata (Custom Fields and Property Setters included); filter operators are allowlisted, and four-element filters may only name the DocType's own child tables. Writes refuse standard columns and read-only fields.
- Metadata comes from Frappe's form loader (`frappe.desk.form.load.getdoctype`), which Frappe serves to any logged-in user, as it does for Desk. The record tools never read the System Manager-only `DocType`, `Custom Field`, or `Property Setter` resources, so they work for any user with permission on the DocType, and Frappe checks that permission on every record read and write. Metadata is cached for `FRAPPE_META_TTL` seconds (default 300); a schema change made outside the MCP is seen once its entry expires, and Frappe's own validation still applies to every write. Set `FRAPPE_META_TTL=0` to always read fresh metadata.
- Frappe's API validates writes and applies the connected user's roles and permissions.
- Schema creation uses native `DocType`, `Custom Field`, and `Property Setter` resources and remains subject to server permissions. DocType field types and validation are handled by Frappe's controller.
- `set_role_permissions` calls Frappe's System Manager-only Permission Manager endpoints; it requires an existing role and DocType.
- Report execution uses Frappe's report API; report permissions still apply.

## Filesystem writes

Filesystem output is optional and local to the MCP process. `FRAPPE_APPS_PATH` is the only writable root. The app and module must exist beneath it, and the selected remote Module Def must belong to that app. Resolved paths are checked against traversal and symlink escapes. Export previews expose paths and content hashes; overwritten files receive a backup before replacement. Exports write only the module's `custom/` folder, the app's `fixtures/client_script.json`, and the managed block of `hooks.py`.

Mount only the intended app tree when running the MCP host in a container. Review exported source and fixtures through Git before migration or deployment. The MCP does not run Bench commands, migrations, or deployments.

## Data handling

The MCP host may retain tool inputs and outputs in its own logs. Request only the records and fields needed. Do not put customer data or credentials in public issue reports or evidence. Binary/file browsing and arbitrary whitelisted methods are not exposed by the standalone release.
