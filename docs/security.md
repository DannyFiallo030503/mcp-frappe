# Security Model

## Identity and authorization

Every MCP request is served through Frappe's authenticated method endpoint. Frappe's session user is the effective identity. Tools use native permission checks, permission-aware Frappe APIs, or native controllers. Results can differ across users and sites; a successful result for one account is not evidence that another account can access the same data.

Frappe token authentication is carried in the `Authorization` header. Keep the endpoint behind HTTPS outside local development. Do not enable Guest access. Use a dedicated API user, grant only the roles necessary, and rotate credentials according to the host's policy.

## Permission enforcement

- DocType discovery filters out unreadable DocTypes and child tables without a readable parent.
- Schema and document reads filter fields using Frappe permitted field names.
- List operations use permission-aware list access and validate selected fields/filters.
- Document writes use Frappe's permission checks, field validation, and document controllers.
- Reports, workflows, workspaces, print formats, files, and translations are checked against applicable Frappe permissions.
- Customization operations require `System Manager`; export targets must be installed apps and modules owned by that app.
- `call_whitelisted_method` requires Frappe's whitelist and POST support. It does not allow arbitrary dotted Python functions.

The MCP server does not call `ignore_permissions=True` as a way to bypass Frappe permissions. Host-side Frappe configuration and administrator privileges remain security-critical.

## Write safeguards

Document writes default to a preview. A second request with `confirm: true` is required to execute. Update, delete, submit, cancel, and workflow confirmation also require the exact `expected_modified` token from the preview, preventing stale previews from silently applying after another user changes the document.

Explicit-name document creation is idempotent for an identical requested payload. It rejects a conflicting payload at the same name. Autonamed creates and whitelisted methods have no general idempotency guarantee.

Customization and role permission changes also require preview and confirmation. Export computes diffs, requires a current `preview_id`, backs up overwritten files, and returns a rollback manifest. Rollback itself previews all affected paths before confirmation.

These safeguards are not a substitute for human review of high-impact actions. In particular, a `confirm: true` call can perform an irreversible deletion or an operation in the named whitelisted method.

## Data exposure and output limits

Reports, files, print HTML, workspace layouts, document fields, and method results may contain sensitive business or personal information. Use a dedicated account, request narrow results, and configure MCP client logging accordingly. Read caps and row caps reduce accidental over-fetching; they are not a data loss prevention system.

The `read_file` tool caps reads to 5 MB. Binary files are base64 encoded, which does not make their contents safe to disclose. `render_print_format` returns HTML and enforces a configurable maximum output size. Report results and list operations also have explicit row limits.

## Whitelisted method boundary

The whitelist is a Frappe authorization boundary, not a guarantee that a method is harmless. Whitelisted code can trigger side effects available to the connected user. The MCP tool previews method and arguments and requires confirmation, but cannot infer the method's behavior or guarantee idempotency. Only call methods whose implementation and effects are understood.

## Filesystem writes

DocType creation and export write to app source directories accessible to the Bench process. The server validates installed-app ownership and module ownership, prevents traversal outside the selected app, rejects existing DocType JSON destinations, writes atomically, and keeps rollback manifests/backups beneath the app's managed backup directory. The Bench OS user must have suitable filesystem permissions.

An authorized Frappe user with `System Manager` can therefore trigger code-like app file changes. Restrict those credentials and review/commit generated files through normal source control. Export does not run arbitrary generated code or deploy it to another site.

## Threat reporting

Please report suspected vulnerabilities privately to the repository maintainers once the public repository contact is established. Do not include live API keys, customer records, or production files in issue reports. Rotate any credential that may have been exposed.
