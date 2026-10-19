# Standalone Frappe MCP acceptance report

**Run date:** 2026-10-16
**Target:** local development Frappe site; URL and login secret intentionally omitted
**Connection:** official MCP Python client over stdio, then Frappe HTTP API
**Stack:** Frappe 15.96.0, ERPNext 15.96.1, HRMS 15.60.0, Cuba 14.3.7, erp_app 0.0.1

The local login used environment variables. Neither the password, session cookie, site URL, document values, nor report rows were written to the repository. See the machine-readable [evidence summary](standalone_real_http_evidence.json).

## Results

### MCP startup and discovery

- Initialized the standalone `frappe` MCP server over `stdio`.
- `tools/list` returned all 14 registered tools.
- Discovered 5 apps with modules and 5 ERPNext DocTypes in a bounded sample.
- Read the `Customer` DocType schema, including merged site Custom Fields and Property Setters (85 fields).
- Listed one `DocType` record with only its `name` field.
- Read one result row from a Query Report and verified the response limit marked the output as truncated.

### Preview paths

- Previewed standard DocType creation with the target app/module mapping and verified the local file destination.
- Previewed Custom Field and Property Setter creation on an app-owned DocType.
- Previewed customization export and checked it returned a content-bound `preview_id` and file diffs.
- Confirmed the returned paths stayed below the configured app directory.

### Document mutation lifecycle

Using one uniquely marked temporary ToDo:

1. Previewed and confirmed creation.
2. Previewed an update; a deliberately stale `expected_modified` value was rejected.
3. Confirmed the update with the preview's current token and verified the readback.
4. Previewed and confirmed deletion using the current token.
5. Verified the record was absent after cleanup.

### Customize Form and export round trip

Using one temporary Custom Field and Property Setter on a DocType in the local `erp_app` tree:

1. Previewed and created both records through Frappe REST.
2. Verified the standalone schema merged the Custom Field and Property Setter.
3. Previewed and confirmed export of standard DocType JSON, customization fixtures, and the managed `hooks.py` fixture block.
4. Previewed and confirmed `rollback_export`; it restored/removes files using the export manifest.
5. Deleted the temporary Property Setter and Custom Field and verified the field was absent from the schema.
6. Removed the test-only backup artifacts after verifying the app Git working tree was clean.

The local Bench is in development mode and watches app files. Export and rollback can restart its web worker and interrupt a following HTTP request. The acceptance runner retries safe reads/previews during this brief reload. If a confirmed delete response is interrupted, it checks whether the record still exists before deciding whether another delete is needed.

## Existing site report definitions

Two reports selected during discovery returned Frappe errors on this site: one has no query configured, and another references a database column that is not present. The report definitions were left unchanged. A valid small Query Report was used to verify the MCP report call and row cap. These are site-owned report configuration issues, not MCP transport failures.

## Cleanup and final site state

- The target site's installed app list contains Frappe, ERPNext, HRMS, Cuba, and erp_app; it does **not** contain `mcp_frappe`.
- The temporary ToDo, Custom Field, and Property Setter were removed.
- The `erp_app` working tree was clean after removing the test-only backup artifacts.
- No migrations, app installations, Bench restarts, or deployments were run by the MCP.

## Checks

- `pytest`: 8 passed.
- Ruff lint: passed.
- Real read-only smoke runner: passed.
- Real document mutation runner: passed.
- Real customization/export/rollback runner: passed.

The old [HTTP acceptance report](REAL_HTTP_REPORT.md) covers the previous Frappe-hosted design only and is retained as migration history.
