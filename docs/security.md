# Security model

## Identity and authorization

The standalone process authenticates to Frappe with either an API token or the standard login endpoint. Requests execute as that user, and Frappe's resource/method APIs enforce its permissions and validation. API tokens are preferred outside local development. Password login over plain HTTP is blocked except for loopback and `.localhost` targets.

Administrator is a full-privilege account. For the local Aspire site only, the MCP defaults to `Administrator` / `admin`; an explicit username and password can select another role. The fallback is applied only to loopback and `.localhost` hosts. Remote sites require explicit credentials or an API token, and the project does not send the local default password to them. Do not expose a local development site with its default password to a network.

## Tool boundaries

- There is no arbitrary Python, SQL, or unrestricted dotted-method tool.
- Lists and report output have explicit caps.
- Document changes preview first; update/delete confirmations require the current `modified` token.
- Frappe's API validates writes and applies the connected user's roles and permissions.
- Schema creation uses native `DocType`, `Custom Field`, and `Property Setter` resources and remains subject to server permissions. DocType field types and validation are handled by Frappe's controller.
- `set_role_permissions` calls Frappe's System Manager-only Permission Manager endpoints; it requires an existing role and DocType.
- Report execution uses Frappe's report API; report permissions still apply.

## Filesystem writes

Filesystem output is optional and local to the MCP process. `FRAPPE_APPS_PATH` is the only writable root. The app and module must exist beneath it, and the selected remote Module Def must belong to that app. Resolved paths are checked against traversal and symlink escapes. Export previews expose paths and content hashes; overwritten files receive a backup before replacement.

Mount only the intended app tree when running the MCP host in a container. Review exported source and fixtures through Git before migration or deployment. The MCP does not run Bench commands, migrations, or deployments.

## Data handling

The MCP host may retain tool inputs and outputs in its own logs. Request only the records and fields needed. Do not put customer data or credentials in public issue reports or evidence. Binary/file browsing and arbitrary whitelisted methods are not exposed by the standalone release.
