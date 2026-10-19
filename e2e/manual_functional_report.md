# Manual functional verification report

**Run date:** 2026-10-19
**MCP:** current checkout's standalone `stdio` server, called with a real MCP client
**Site:** existing Aspire-managed `cubazone.localhost` development site
**Identity:** `Administrator` for all MCP mutations and metadata operations
**Stack:** Frappe 15.96.0, ERPNext 15.96.1, HRMS 15.60.0, Cuba 14.3.7, erp_app 0.0.1
**Status:** extensive hands-on checks completed; this is not a claim of complete Frappe/ERPNext coverage or v16 compatibility.

The checks below used the MCP protocol over stdio and the MCP's configured HTTP connection to Frappe. Database inspection was read-only SQL through the MariaDB endpoint published by Aspire. Generated source files were inspected in the app checkout actually mounted by the site. Aspire was the only service lifecycle interface used.

## Results at a glance

| Area | Result | Evidence / limitation |
|---|---|---|
| MCP handshake and tool inventory | Pass | `initialize` and `tools/list` succeeded; 15 tools were advertised. |
| App, DocType and schema discovery | Pass as Administrator | 5 installed apps, ERPNext DocTypes, and Customer schema (85 fields) were returned. A report run correctly marked capped output as truncated. |
| Record mutation and stale-state protection | Pass for tested ToDo flow | Preview/create/read/update/delete worked; stale update and delete tokens were rejected and did not destroy the record. |
| Custom Field and Property Setter | Pass for representative fields | Both appeared in schema; export preview/confirm and rollback worked; temporary field was removed. Not exhaustive over their full option spaces. |
| Role permissions | Pass for one role/DocType | `set_role_permissions` reached Frappe's native Permission Manager methods; test DocType was removed afterward. |
| Standard, child and tree DocTypes | Partial pass | Broad metadata, generated files, child-row CRUD and tree parent linkage were confirmed. Developer-mode file writes can drop the MCP HTTP connection before a reliable result is returned. |
| Single DocType | Pass after reloader handling fix | Created a temporary Single through MCP, wrote a value through the normal resource endpoint, read the same value back using the fixed document name (the DocType name), and deleted the temporary DocType through MCP. Its values are stored in shared `tabSingles`, not a physical `tab<name>` table. |
| Submittable DocType | Partial pass | `is_submittable` and related settings round-tripped, but the MCP has no submit/cancel tool and `get_document` omitted `docstatus`. Lifecycle behavior is not verified. |
| Generated DB/source parity | Pass with caveat | For a regular DocType, verified `tabDocType`, physical table, JSON/PY/JS/test scaffold, and record row. Deletion removed metadata but left an empty physical table; exact test table was subsequently dropped after confirming zero rows. |
| App ownership protection | Fail / safety gap | Preview allowed destinations inside the core `frappe` source tree. No confirm/write was performed. |
| Browser Desk visual parity | Not tested | Artifacts and API behavior were checked; a side-by-side Desk-created control DocType was not created. |
| Frappe v16 | Not tested | This live site is Frappe/ERPNext v15. |

## Expanded manual DocType/customization matrix (2026-10-19)

After the first report, the real stdio MCP runner `e2e/run_standalone_doctype_matrix.py` was expanded and run against the same Aspire-managed site as `Administrator`. This was a second, temporary-data acceptance run; it did not install an app or create a site.

| Check | Result | What was read back |
|---|---|---|
| Standard DocType field types | 43/43 pass | `get_doctype_schema` returned every Frappe 15 DocField type in the created regular DocType. |
| Custom Field types | 43/43 pass | All 43 types created as native `Custom Field` records. Fold/Section Break ordering also passed. |
| Standard DocType variants | Pass | Child, regular, Single, Tree, submittable and Virtual metadata DocTypes created. |
| Site-custom DocType | Pass | Preview had no file destination; `custom=1` appeared in schema; no source files were returned; record create/list roundtrip passed. |
| DocType properties and role permissions | Pass for selected settings | Editable grid, quick entry, change/seen/view tracking, rename/import/copy, title/search/sort, description, max attachments, autoname, tree parent field, and System Manager permission row were checked in schema. |
| Native JSON and DB metadata | Pass | Parsed the generated app JSON: it contained all 43 types, requested settings and permission row. The live `DocType` metadata response matched the target name/module/table/submittable flags and field types. |
| Property Setters | 15/15 allowlisted properties passed | Each normalized value appeared in merged schema. A DocType-level description setter and Custom Field + label setter on child, Single, Tree, and submittable definitions also passed. |
| Record storage/links | Pass | Single value write/read, draft `docstatus`, Tree parent link, Table and Table MultiSelect child rows, Select, Link and Dynamic Link values round-tripped. |
| Export/rollback | Pass | Temporary DocType/customization fixtures exported; rollback restored files. |
| Cleanup | Pass | All nine unique temporary DocTypes were deleted through MCP, along with their dependent records/customizations. |

The matrix checked type acceptance and selected high-value properties. It did **not** exhaust every optional DocField property or every DocType-level setting/value combination. Browser-rendered Desk comparison remains untested; no browser surface was available in this run. Virtual DocTypes were metadata-only: generic CRUD is not expected without a controller that implements storage. Site-custom DocTypes use database metadata and do not generate app JSON/Python/JavaScript files.

The MCP cleanup removed all nine current DocType definitions, their test records, Custom Fields and Property Setters. Frappe left physical tables when the standard DocTypes were deleted. The database check found six empty tables from the final run plus 49 older `MCP Matrix` tables and orphaned metadata rows from previous runs. After verifying that no `tabDocType` rows remained for that prefix, I removed the orphaned Custom Field, Property Setter, and Single rows and dropped only those 49 old test-prefix tables; after the final matrix, I verified and dropped its six empty tables as well (55 tables total). Final counts were zero for DocType, Custom Field, Property Setter, Single values, and `tabMCP Matrix ...` tables. The app checkout had no `mcp_matrix` files and remained clean.

The earlier “App ownership protection” failure above describes the first acceptance run. The current code now rejects protected `frappe` and `erpnext` source destinations before preview/write; the focused unit test covers that guard. Do not treat the old preview observation as a current passing live-site export test.

## Manual scenarios

### 1. Protocol and read-only discovery

- Started the current standalone MCP server and connected through an MCP stdio client.
- `initialize` succeeded and `tools/list` returned 15 tools.
- Discovered installed apps, listed DocTypes, read the Customer schema, listed a bounded record set, and ran `Item Balance (Simple)` with a one-row cap. The report response marked its output truncated.
- Anonymous Frappe ping returned HTTP 200 while a protected DocType REST request returned 403. The MCP calls themselves used `Administrator`.

### 2. Ordinary document operations

- Created a temporary ToDo through preview then confirmation, read it back, updated it, and deleted it.
- Reused a stale `expected_modified` token on update and delete. Both stale requests were rejected; the current record remained available until the valid delete was confirmed.
- Repeated a create with an explicit name in the acceptance run; identical payload was safe and a different payload under the same name was rejected.
- Created a regular test DocType and document, then verified the DocType row and inserted record directly in MariaDB. MCP readback matched the stored value.

### 3. Customization tools

- Created a Custom Field and a Property Setter through MCP and confirmed both appeared in the merged schema.
- Previewed and confirmed fixture export. The operation generated four expected files, including fixture data and a managed `hooks.py` block.
- Previewed and confirmed rollback, then deleted the temporary Custom Field and verified it disappeared from schema.
- Ran `set_role_permissions` for Sales User on a temporary DocType. Aspire's Frappe logs showed successful calls to native `permission_manager.add` and `permission_manager.update`; deleting the temporary DocType cleaned up its permission rows.

### 4. DocType definitions and field properties

Created temporary definitions under the `erp_app` / `CubaZone` module using `Administrator`:

- A child DocType and a regular, submittable DocType.
- A 43-field coverage definition spanning Data/email/phone/URL, Check, Select, Int/Float/Currency/Percent, Date/Datetime/Time/Duration, Long Text/Text/Text Editor/Markdown/Code/Password, Link/Dynamic Link, Attach/Attach Image/Image, Signature, Barcode, Color, Geolocation, JSON, Rating, Read Only, HTML, Button, Section/Column/Tab breaks, Table and Table MultiSelect, plus a permission-level-1 field.
- Naming series/autoname, tracking (`track_changes`, `track_views`, `track_seen`), rename/import flags, title/search/sort settings, quick entry, editable grid and max attachments.
- A separate property round-trip confirmed the above DocType settings in the returned schema. The parent record and one child row were created and read back, including the child value.
- A Tree DocType was created; root and leaf records round-tripped their parent link.

Frappe rejected the first child definition because a Table MultiSelect target child DocType had no Link field. The failed attempt left no partial definition. Adding the required Link field allowed the definition to proceed. This is evidence that Frappe's own metadata validation is being applied.

Inspected generated files in the live `erp_app` checkout: regular DocType JSON/PY/JS and test scaffold; child JSON/PY; Tree behavior and parent link. This verified native generated artifacts for those successful creations, but not visual equality with a Desk control.

### 5. Single storage and request disconnects

Frappe's official [Single DocType documentation](https://docs.frappe.io/framework/user/en/basics/doctypes/single-doctype) says Single values are rows in `tabSingles` with `doctype`, `field`, and `value` columns. The Frappe 15 source checked in the Aspire-mounted app matches this: `Document.load_from_db()` reads `get_singles_dict()` when `meta.issingle` is true, and `Document.insert()` / `_save()` route persistence through `update_single()`. A Single has normal DocType metadata in `tabDocType`, a logical document name equal to the DocType name, and no dedicated SQL table. Therefore, searching for `tab<Single Name>` is not a valid persistence test.

After the MCP reloader-handling change, a live MCP test created a temporary Single DocType, wrote a value through `create_document`, read it back through `get_document` with `name` equal to the DocType name, asserted the returned value matched, then deleted the DocType. All four steps passed. This confirms the tested Single CRUD path; it does not establish every Single-specific field or hook combination.

Creating standard metadata in Developer Mode writes files into the mounted app tree. Frappe's Werkzeug/web worker autoreloader can restart the worker during that request. Earlier runs saw `Server disconnected without sending a response` while generated files and metadata were being created. A blind retry risks duplicates. The MCP now stages Frappe's installed boilerplate before the metadata request and reconciles the result after reload; the tested Single and other DocType creation paths completed after this change.

The earlier Single incident was a real request/reloader uncertainty, but it was not evidence that Frappe expected a dedicated table for Singles. A client-visible error also does not determine whether an operation committed, so the MCP checks persisted metadata after reload instead of blindly replaying a create.

Deleting a regular DocType also left its physical table after its metadata row disappeared. The only remaining table was the known, empty test table; we verified it had zero rows and dropped that exact test artifact. Final checks showed no matching `tabDocType` row and no such table. `erp_app` Git working tree is clean.

### 6. Core app export preview

Previewed standard DocType creation and customization export targeting `app="frappe"`. Both previews returned paths inside Frappe's own source tree, including its `hooks.py`. No confirmation was sent and no core file was modified. This is a reproduced safety gap: preview currently permits a destination that should be refused or protected by explicit app-ownership policy.

### 7. Temporary limited-user diagnostic

An auxiliary diagnostic logged in as a temporary non-System-Manager user and observed 403 responses when listing DocTypes or reading schema. Generic User creation also rejected `enabled` and `roles` as read-only fields, so the test role could not be assigned through the generic document tool. The temporary user's associated password row and user were removed afterward.

This is an MCP/tool-surface limitation for that separate identity test; it is not a limitation of Frappe `Administrator`. All requested DocType and customization scenarios in this report were run as `Administrator`, which is the appropriate identity for the user's stated test. Role membership management is not currently an advertised MCP tool, so limited-role equivalence remains unverified.

## Current tool surface and unsupported scenarios

The server advertised: `list_installed_apps`, `list_doctypes`, `get_doctype_schema`, `list_documents`, `get_document`, `create_document`, `update_document`, `delete_document`, `run_report`, `create_doctype`, `create_custom_field`, `set_field_property`, `set_role_permissions`, `export_customizations`, and `rollback_export`.

No tool was advertised for submit/cancel/amend/rename, workflow listing/actions, User/Role membership/User Permissions, comments/shares/assignments, file upload/download, print/PDF/email, workspaces/pages/charts/dashboards, translations, web forms, client/server scripts, or arbitrary app-specific methods. These are **not tested and not currently exposed by this MCP**. Generic CRUD should not be represented as coverage of their native workflows.

The reported Frappe 16.33.1 results from Dennis concern a different implementation/revision and are not evidence for this standalone server. For the earlier reported issues, the fresh-install-as-a-Frappe-app bug is not applicable to the current standalone architecture; the exact After Insert rollback reproduction needs Server Script support that the current MCP does not expose; and workflow-related cases cannot be run because workflow tools are absent. The core-app export preview risk was reproduced above.

## Cleanup and reproducibility

- Temporary ToDo, Custom Field, Property Setter, roles/permissions on temporary DocTypes, test records, and temporary DocTypes were removed through MCP where their DB records existed.
- One known empty DB table left after DocType deletion was explicitly verified as empty and dropped; final SQL checks found no matching DocType row or table.
- The temporary Single DocType and its generated files were removed after successful write/readback verification.
- `erp_app` is clean (`main...origin/main`). No customer data, credentials, tokens or private record values are included in this report.
- The current MCP repository contains pre-existing/in-progress standalone migration changes and is intentionally not described as clean by this test report.

## Conclusion

The MCP can create standard and site-custom DocTypes, accept all 43 Frappe 15 field types in both DocType fields and Custom Fields, persist representative settings and permissions, write native standard DocType JSON/controller scaffolding, round-trip records, and create/merge all 15 allowlisted Property Setter properties. Manual evidence covers the selected matrix described above, not all Frappe or ERPNext features. Remaining gaps include browser Desk comparison, Frappe 16 validation, every optional field/DocType setting combination, lifecycle/workflow tools, and end-to-end ERPNext business processes.
