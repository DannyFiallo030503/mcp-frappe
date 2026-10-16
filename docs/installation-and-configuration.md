# Installation and Configuration

## Deployment model

Frappe MCP is a Frappe app (`mcp_frappe`) installed into a Bench and installed on each target site. The app registers the authenticated endpoint:

```text
/api/method/mcp_frappe.mcp.handle_mcp
```

The MCP client sends JSON-RPC/MCP requests to this endpoint using Frappe token authentication. The Frappe session user becomes the identity used by all tools.

## Prerequisites

1. A running Frappe Bench and target site.
2. A Python runtime supported by that Bench. The package declares Python `>=3.10` and runtime dependencies `jsonschema>=4.24,<5.0` and `Click>=8.1.8,<9.0`.
3. An MCP client with Streamable HTTP support and custom HTTP headers.
4. A dedicated Frappe user and a least-privilege role assignment.
5. For customization tools, `System Manager` on that user. Exporting also requires the selected app to be installed and its target module to exist and be writable by the Bench process.

## Install from source

From the Bench directory:

```bash
bench get-app https://github.com/<OWNER>/mcp-frappe.git
bench --site <site-name> install-app mcp_frappe
bench restart
```

Replace `<OWNER>` with the published repository owner. If working from a local checkout, place/link it as `apps/mcp_frappe` and then run the site install command.

Check installation:

```bash
bench --site <site-name> list-apps
```

The output should include `mcp_frappe`. The app has a required app dependency on `frappe`; it does not require ERPNext or HRMS. Those apps are discovered if installed.

## Create API credentials

Create a dedicated Frappe user from the Desk, enable API access, and assign only the roles required for the use case. Generate an API key and secret from that user's API Access section. Do not use `Administrator` for normal MCP use.

For read-only use, grant the minimum read/report/print access necessary. Add `System Manager` only if the agent must use schema customization, role permission, fixture export, or rollback tools. Frappe permissions still apply even to a System Manager.

Token header format:

```http
Authorization: token <api_key>:<api_secret>
```

Keep the secret out of shell history, logs, repository files, screenshots, and evidence artifacts. Prefer the MCP client's secret store or environment interpolation. Rotate the key if exposed.

## Configure an MCP client

Use the URL of the Frappe site, including the endpoint path:

```text
https://<frappe-host>/api/method/mcp_frappe.mcp.handle_mcp
```

Example shape for clients accepting JSON server declarations:

```json
{
  "mcpServers": {
    "frappe": {
      "url": "https://<frappe-host>/api/method/mcp_frappe.mcp.handle_mcp",
      "headers": {
        "Authorization": "token ${FRAPPE_API_KEY}:${FRAPPE_API_SECRET}"
      }
    }
  }
}
```

This is a generic shape, not a guaranteed drop-in config for every client. Follow the client-specific instructions for Streamable HTTP, header support, and secret substitution. Do not put real credentials in a shared JSON config.

## Verify the connection

Connect from the MCP client and ask it to call `list_installed_apps`. The result should contain the apps installed on that site. Then call `list_doctypes` for one installed app. A `401`/`403` response usually indicates missing/invalid credentials or a site permission denial; verify the Frappe API user and roles before changing endpoint permissions.

For a raw HTTP smoke check, use a secure local shell and do not paste the credential into an issue or log:

```bash
curl --fail-with-body \
  -H "Authorization: token ${FRAPPE_API_KEY}:${FRAPPE_API_SECRET}" \
  -H "Content-Type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke-check","version":"1"}}}' \
  "https://<frappe-host>/api/method/mcp_frappe.mcp.handle_mcp"
```

An unauthenticated request is expected to be rejected. Do not configure Guest access as a workaround.

## Upgrades

Back up the site and app code using your normal Frappe deployment process before upgrading. Review the release notes and compatibility matrix. Restart the Bench processes after code changes. Run automated checks in an isolated site and perform a real MCP handshake before deploying to a shared site.

The MCP customization export tool writes files into the selected app tree. Treat those exported files as code changes: review the diff, commit them through your normal source-control process, and run the app's migration/deployment procedure on other sites. MCP does not deploy an app to other sites.

## Removal

Follow Frappe's supported app removal procedure for your Frappe version and site. Back up first. Removing the app may not remove all data created by other installed apps or tools. Review site data and app files separately before removal.
