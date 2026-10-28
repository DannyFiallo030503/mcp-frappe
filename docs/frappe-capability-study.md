# Frappe capability study and MCP coverage

**Purpose:** define what “works with Frappe” means for this project, record the framework and ERPNext surfaces that matter, and provide a traceable coverage plan before claiming broad parity with Desk.

**Study date:** 2026-10-19. The official documentation is a moving target; validate version-specific behavior against the target site's Frappe release and live metadata.

## Executive summary

Frappe is a metadata-driven application framework. A DocType is simultaneously a data model, form/list definition, validation boundary, naming configuration, permission boundary, and the basis for REST resources. Creating or saving documents invokes Frappe's normal controller lifecycle and installed app hooks. A standard DocType in Developer Mode can also create version-controlled app files. ERPNext is an application built on these same primitives and adds its own connected business processes, DocTypes, reports, permissions, and controller logic.

The MCP should therefore be app-generic and metadata-driven: connect to a site, discover installed apps/modules/DocTypes, read their live schemas, and expose safe operations over the native APIs. It must not contain a site-specific app or module, or a fixed ERPNext DocType catalog. Frappe + ERPNext are the minimum expected site apps, but additional installed apps must work through discovery and generic document operations without MCP code changes.

The current standalone implementation is a useful base, not full Desk parity. It has runtime discovery, schema/document CRUD, reports, core schema customization, role-permission changes, and guarded fixture export. Important Desk/API surfaces remain unwrapped: submit/cancel/amend/rename and workflow actions, files, print/PDF, comments/shares/assignments, user permissions, web forms/workspaces, translations, dashboards, and many app-specific actions. A successful smoke test of a few DocTypes does not prove those capabilities.

“Everything Frappe offers” cannot mean sending arbitrary Python, SQL, or any whitelisted method name: those interfaces can execute arbitrary site code and bypass the MCP's safe, reviewable tool contract. The practical goal is **coverage of Frappe's user-facing operations through explicit, bounded, permission-preserving tools**, with narrowly scoped adapters for app-specific actions where needed. Features that require code, app installation, patches, migrations, build assets, or deployment must be documented as development/release workflows rather than implied to be a database-only MCP operation.

## Deployment and compatibility contract

### Required environment

- A reachable Frappe site with Frappe Framework and ERPNext installed. HRMS and other apps are optional and must be discovered rather than assumed.
- A Frappe API user and credentials. Its roles and permissions define what the MCP can do; credentials do not start or repair the site.
- Network reachability from the MCP process to the site's HTTP endpoint, including the correct host/site routing header for a multi-site Bench.
- For local file export or generated standard app files, the relevant app checkout must be writable from the process that writes it. In containerized deployments, a shared/bind-mounted app path may be required; the MCP's local `FRAPPE_APPS_PATH` and Frappe's runtime app path must refer to the same intended checkout if the MCP is to inspect those generated files.
- This project is a standalone `stdio` MCP process. It is not installed as a Frappe app and does not itself create a site, install ERPNext, run migrations, build assets, or deploy code.

### Generic-app requirement

At startup and during use, discover app/module/DocType metadata from the target site. Do not require a custom app to be installed for ordinary records and supported customizations. For a standard DocType creation or local fixture export, verify that the selected remote Module Def belongs to the selected app and that the corresponding local module exists under the configured app root. This is a deliberate source-control safety check, not a hardcoded app catalog.

The generic resource layer can operate on DocTypes from any installed app, subject to permissions and the app's schema/controllers. It cannot automatically understand every business-specific controller method or all app-specific front-end behavior. Such operations need an explicit tool wrapper and compatibility tests.

### Version support

Frappe v15 documents API v2 as available from v15. API v1 remains `/api/`, with `/api/v1/` aliases from v15. Endpoints and metadata fields can vary by release. The current recorded real-site acceptance is Frappe 15.96.0 + ERPNext 15.96.1 and additional apps; it is a sample, not a support guarantee. Dennis's separate reported v16 run was against a different implementation/revision and must not be presented as validation of the current standalone server. Maintain a release matrix for every supported major line (at minimum v15 and v16) and test against clean sites with Frappe + ERPNext.

## Frappe's capability model

### 1. DocTypes, metadata, and records

A DocType definition includes fields, field order and layout, naming, permissions, table/single/tree/submittable behavior, list/form settings, search and sort behavior, links/actions, and other metadata. Frappe uses the metadata to provide database schema, ORM behavior, desk views, and generic REST resources. Child DocTypes are embedded rows associated with a parent; Single DocTypes are one settings record with a fixed name; Tree DocTypes add parent/child hierarchy; Virtual DocTypes use custom storage/behavior instead of the normal table model. These variants have different assumptions and need separate verification.

### Single DocType storage

A Single DocType still has a normal DocType definition (the metadata record in `tabDocType`, with its fields and flags), but it does **not** get a physical `tab<DocType Name>` table. Frappe stores each value of its one logical document as a separate row in the shared `tabSingles` table, keyed by `doctype` and `field`, with the value in `value`. The logical document's name is always the DocType name. The official docs describe these columns as `doctype`, `field`, and `value` ([Single DocType](https://docs.frappe.io/framework/user/en/basics/doctypes/single-doctype)).

This matches the checked-out Frappe 15 implementation: `Document.load_from_db()` branches on `meta.issingle` and calls `frappe.db.get_singles_dict()`; `Document.update_single()` rewrites that DocType's entries in `tabSingles`. The ordinary `_save()` lifecycle still performs validation and hooks before persistence. So verifying a Single by looking for a `tab<name>` SQL table is incorrect: verify the `tabDocType` metadata, the app files for a standard DocType, and the relevant `tabSingles` rows. A Single with no configured field values may have no value rows yet, while its metadata row can still exist. This storage distinction does not by itself indicate a failure to create the DocType.

Frappe DocFields include stored data types and UI/layout types. Common stored types include `Data`, `Small Text`, `Long Text`, `Text`, `Text Editor`, `Markdown Editor`, `Code`, `Password`, `Int`, `Float`, `Currency`, `Percent`, `Check`, `Date`, `Datetime`, `Time`, `Duration`, `Select`, `Link`, `Dynamic Link`, `Table`, `Table MultiSelect`, `Attach`, `Attach Image`, `Image`, `Signature`, `Barcode`, `Color`, `Geolocation`, and `JSON`. Layout/behavior fields include `Section Break`, `Column Break`, `Tab Break`, `HTML`, `Button`, and `Read Only`; the complete available list is version-dependent. Options are semantic: for example, Link points to a DocType, Table points to a child DocType, Dynamic Link uses another field to choose its target, Select stores allowed options, and Image references an attachment field.

For a visual-equivalence test, don't just check the `DocType` database row. Verify metadata readback, database table/columns where applicable, DocType JSON and generated controller files, normal Desk form/list behavior, child rows, and native validations after the request completes. Table/single/virtual/tree/submittable differences must be tested independently.

### 2. Naming, lifecycle, and document state

Frappe supports user-assigned names, numeric/autoincrement names, name-from-field, naming series, expressions, random/hash values, UUIDs, controller `autoname`, and runtime Document Naming Rules. Naming series requires the expected field; field-based naming needs unique values. Existing records may constrain changing a naming strategy.

Normal document operations invoke permissions and lifecycle methods. Insert/save/submit/cancel/delete can trigger controller methods, app hooks, notifications, ledger writes, integrations, and other side effects. In particular, a generic create/update tool is not equivalent to every state transition. Submit, cancel, amend, rename, workflow transitions, document methods, comments, and background actions are distinct operations. Errors must preserve Frappe's transaction semantics: if an endpoint returns an error after a partial operation, the MCP must not convert it to an apparently successful request that causes a commit.

### 3. Permissions and users

Frappe permissions combine authenticated identity, roles, DocType permissions, permission levels, owner restrictions, User Permissions, shares, and app-specific permission hooks. DocType permission flags include read, write, create, delete, submit, cancel, amend, report, export, import, set user permissions, share, print, and email. Field permission levels can hide values even when a user can read a document. User Permissions restrict documents through Link field values. Role visibility does not grant access unless the target operation passes Frappe's checks.

The MCP must always use the configured API user and native APIs, never `ignore_permissions`, direct SQL, or a privileged alternate endpoint. Schema discovery and permission errors must be handled per item so a restricted or virtual DocType does not make an entire discovery call fail. User, Role, Role Profile, User Permission, shares, and Page/Report role permissions are separate surfaces; changing role permissions alone does not cover them.

### 4. Customize Form and app development

Site-specific schema customizations are stored as `Custom Field` and `Property Setter` records. Customize Form also manages links/actions and other form configuration. Client Scripts add browser-side logic; Server Scripts add site-managed server logic subject to site restrictions; Custom DocPerm changes role permission rows. Behavioral extensions in an app use controller methods, `doc_events`, other hooks, Python modules, JavaScript, templates, and app assets.

Standard DocTypes created in Developer Mode belong to an app/module and normally generate JSON and controller scaffolding (plus client script/test scaffolding where Frappe's version and DocType type provide them). The site database and app source tree are separate state. Fixture export makes selected site customization records portable; install/migrate imports fixtures. Standard DocType source, customizations, hooks, patches, translations, web artifacts, assets, and app code all have different lifecycle/deployment requirements. MCP file export must stay confined to the selected app checkout, preview diffs, back up files, and support guarded rollback. A filesystem rollback cannot undo remote DB changes.

### 5. Desk, web, reports, and collaboration surfaces

Frappe includes list/form views, workspaces, pages, dashboards/charts, search, saved filters, print formats/PDF, web forms/portal pages, reports (Report Builder, Query Report, Script Report), notifications, assignments, comments, communications, tags, versions/audit history, attachments/files, email, and translations. Some are generic DocTypes; some are dedicated endpoints; some require browser JavaScript or server-side code. Treating them all as raw DocType CRUD is incomplete and can skip special permissions, lifecycle, or binary transport behavior.

### 6. ERPNext application capabilities

ERPNext is not a single schema. Its installed modules provide domain-specific DocTypes, reports, workflows, validations, and controller actions, including:

- Accounting: Company, Chart of Accounts, fiscal years, journals, invoices, payments, taxes, dimensions, budgets, books, and financial statements. Submitting transactional documents can create General Ledger and Payment Ledger entries; stock-related transactions can also update the Stock Ledger.
- Selling and CRM: leads/opportunities, customers, quotations, orders, delivery, invoices, pricing, and customer payments.
- Buying: suppliers, requests for quotation, purchase orders/receipts/invoices, and supplier payments.
- Stock: items, warehouses, batches/serial numbers, receipts/issues/transfers/reconciliation, valuation, and stock reports.
- Manufacturing: BOMs, work orders, job cards, production planning, and stock/accounting effects where configured.
- Projects and support: projects, tasks, timesheets, issues, service contracts/visits, and service reporting.
- Assets and quality: asset lifecycle/depreciation/maintenance and inspection/quality processes.
- HR and payroll vary by app/release; HRMS is a separate optional app in many installations. Never assume it exists just because ERPNext does.

Generic CRUD can read and create draft records where permissions and required fields permit. It does **not** prove a complete ERPNext flow. Each domain needs real scenarios covering prerequisites, controller calculations, workflow state, submit/cancel/amend, ledger/stock side effects, and report reconciliation. Do not bypass ERPNext controllers to manufacture ledger or stock entries.

## Current MCP coverage (repository snapshot)

Status meanings: **Implemented** means code exists; **site-verified** means the cited standalone real-site report exercised that path. These are independent. **Missing** means there is no explicit tool in the current standalone tool list. An API endpoint existing in Frappe is not by itself MCP coverage.

| Capability | Current MCP surface | Status / gap |
|---|---|---|
| App/module discovery | `list_installed_apps` | Implemented; site-verified on one v15 stack. Discovery uses Module Def; verify app list accuracy against `installed applications` for unusual apps. |
| DocType discovery and schema | `list_doctypes`, `get_doctype_schema` | Implemented; live-verified as Administrator on v15, including a Customer schema and temporary Custom Field/Property Setter merge. A temporary limited user received 403 on DocType metadata. Per-item virtual DocType exceptions, v16 metadata, and broader field permission-level filtering still need testing. |
| Record list/read/create/update/delete | `list_documents`, `get_document`, `create_document`, `update_document`, `delete_document` | Implemented; live-verified on ToDo and temporary regular/child DocTypes, including stale-token rejection and child-row roundtrip. A temporary Single value was also written and read back under the fixed Single name, then cleaned up. `get_document` omitted system `docstatus` for a submittable DocType; virtual DocTypes, broader permission cases, and rollback after partial lifecycle failures still need testing. |
| Query/Script report execution | `run_report` | Implemented; one valid report site-verified. Its endpoint returns no rows for Report Builder reports (Frappe's `get_report_result` skips them); `upsert_report` samples those through `reportview.get_list`. Prepared report/download flows are not established. Errors, role restrictions, pagination, and large reports need coverage. |
| Standard DocType creation | `create_doctype(is_custom=false)` | Manually verified on Frappe 15.96.0 for regular, child, Single, Tree, submittable, and Virtual metadata definitions; all 43 v15 DocField types round-tripped; representative tracking, naming, search/sort, layout, import/copy, title, attachment, and permission settings round-tripped; standard source scaffolding was generated. Record checks covered Single storage behavior, Table/Table MultiSelect rows, Tree parent links, naming, and draft docstatus. Not every DocType-level option/value combination or Desk rendering was tested. |
| Site-custom DocType creation | `create_doctype(is_custom=true)` | Manually verified as Administrator on v15: preview, `custom=1` metadata persistence, no generated app files, and generic record create/list. The path requires a matching remote Module Def but not a local checkout or Developer Mode. Custom Virtual DocTypes are rejected by Frappe. |
| DocType edits and deletion | `update_doctype`, `delete_doctype` | Added in work-plan stage 06 and unit-tested against Frappe v16.33.1 source behavior; not yet live-verified. Editing refuses `frappe`/`erpnext` DocTypes, field renames, and data-losing fieldtype changes; deletion refuses DocTypes that others link to. |
| Site and app sync | `diff_doctype`, `list_app_drift`, `promote_custom_doctype` | Added in work-plan stage 07 and unit-tested against Frappe v16.33.1 source behavior; not yet live-verified. Comparison is read-only and reads only below `FRAPPE_APPS_PATH`; promotion lets Frappe write the files in developer mode. |
| Workflows and Notifications | `get_workflow`, `upsert_workflow`, `list_notifications`, `upsert_notification` | Added in work-plan stage 08 and unit-tested against Frappe v16.33.1 source behavior; not yet live-verified. Python conditions are refused, Notification templates allow only `{{ doc.<field> }}` placeholders with allowlisted filters, and only Email and System Notification channels are supported. Workflows export as app fixtures. |
| Reports and Workspaces | `upsert_report`, `get_workspace`, `upsert_workspace` | Added in work-plan stage 09 and unit-tested against Frappe v16.33.1 source behavior; not yet live-verified. Report Builder reports only (Query and Script Reports are code); Workspaces are public and standard, built from checked shortcuts, link cards, and existing Number Cards; URL shortcuts and custom HTML blocks are refused. |
| Role permission changes | `set_role_permissions` | Live-verified for one Sales User permission setup on a temporary DocType through native Permission Manager methods. Does not create roles/users, assign memberships, manage User Permissions, shares, or Page/Report role rules. |
| Custom Field creation | `create_custom_field` | Manually verified 43/43 Frappe 15 DocField types as Custom Fields on a temporary standard DocType, including layout ordering; representative Custom Fields also round-tripped on child, Single, Tree, and submittable DocTypes. Export/rollback and cleanup passed. This tests type acceptance, not every optional field-property combination. |
| Property Setter | `set_field_property` | Manually verified all 15 public allowlisted properties (`label`, `hidden`, `read_only`, `reqd`, `default`, `in_list_view`, `in_standard_filter`, `options`, `description`, `depends_on`, `mandatory_depends_on`, `read_only_depends_on`, `collapsible`, `bold`, `width`) by merged-schema readback; DocType-level description and setters on child, Single, Tree, and submittable DocTypes also passed. This is the MCP's allowlist, not the full Customize Form surface. |
| Portable customizations | `export_customizations`, `rollback_export` | Live-verified for selected fixtures and rollback. Only selected fixture DocTypes are exported; no deploy/migrate/remote DB rollback. Preview currently accepts `frappe` as the destination app, which can target core source files; no confirm/write was performed. |
| Submit/cancel/amend/rename/workflow actions | none | Missing. These are separate lifecycle transitions and essential for ERPNext transactions. Workflow definitions are covered above; applying their actions to documents is not. |
| Comments, shares, assignments, tags, versions | none | Missing explicit tools; do not infer from generic DocType CRUD. |
| File upload/download/attach | none | Missing; binary limits, privacy, and File permissions need a dedicated design. |
| Print format/PDF/email | none | Missing. Requires permission-aware render/download/send wrappers. |
| User/Role/User Permission management | none except DocType role permissions | Missing as a general identity and row-restriction surface. High impact; require least privilege. |
| Page/dashboard/chart/filter, private Workspaces | none | Missing (public Workspaces: see `upsert_workspace` above). A portion is metadata CRUD, but special endpoints and user-specific visibility/preferences need explicit tools. |
| Web Form/portal, translations, notifications | none | Missing. Website/public access and code execution/security have distinct risk models. |
| App code/hooks/server/client scripts/patches | guarded fixture export only | Missing as general code editing. Must remain a reviewed development workflow; no arbitrary code execution by the MCP. |
| ERPNext end-to-end flows | generic records/reports only | Not proven. No end-to-end sales, procurement, accounting, stock, manufacturing, project, asset, or quality scenario is in the current report. |

The [manual functional report](../e2e/manual_functional_report.md) and [standalone real-site acceptance report](../e2e/standalone_real_http_report.md) document the paths exercised against one populated v15 development site. They are not evidence for v16, a clean site, every DocType type/configuration, all roles, visual parity against Desk, or ERPNext business-process parity.

## What visual equivalence requires for DocType creation

The benchmark is the result of creating through Desk with the same metadata and Developer Mode—not just accepting a JSON payload or inserting a DocType row. For each test DocType, verify all applicable layers:

1. **Metadata round trip:** read it through `get_doctype_schema` and compare flags, naming properties, permissions, layout order, field types/options/defaults/validation and relevant field settings against the requested definition.
2. **Database:** verify expected table/columns for regular and child DocTypes; verify Single storage behavior; verify virtual DocTypes do not assume a generated table. Check parent/child linkage and tree fields/indexes where applicable.
3. **App artifacts:** verify correct app/module path, JSON, Python controller, JavaScript and test scaffolds where native Frappe generates them. Compare with a Desk-created control DocType from the same Frappe release. Confirm no files were written under `frappe`, `erpnext`, or another app unless explicitly intended and protected.
4. **Desk behavior:** verify list/form layout, sections/tabs/columns, quick entry/editable grid, list filters/search/title/sort, link/autocomplete, required/read-only/hidden/depends-on, image/attachment, and child-table rendering.
5. **Runtime behavior:** create/read/update a record through the normal API; exercise naming, defaults, mandatory/link/select validation, controller lifecycle, permissions, submit/cancel/amend (where enabled), tracking/versioning, and workflow (where configured).
6. **Persistence/restart:** verify metadata survives a fresh request and site reload. For portable standard app definitions, verify fixture/source and migrate behavior only in a disposable test environment; the MCP itself must not run migration.

At minimum cover: regular DocType, child table, Single, tree, submittable, and the supported combinations that Frappe allows. Test naming series and field-based naming, permissions at levels 0 and 1, owner-only permissions, all major fieldtype families, child tables and multi-select, and DocType properties (tracking, rename/import, search/title/sort, quick entry/grid, attachments, web flags). `is_virtual` needs a separate design because arbitrary virtual storage/controller behavior cannot be created by ordinary generic metadata alone.

### Field configuration test dimensions

Test representative configurations systematically, not by setting every checkbox on every field (many combinations are invalid or meaningless):

- Data validation options: name/email/phone/URL; Select values/default/sorting; Link options and fetch-from; Dynamic Link target field; Table/child DocType; Attach/Attach Image/Image reference; JSON/code editor options.
- Common flags: label, description, default, reqd, unique, read_only, hidden, in_list_view, in_standard_filter, bold, allow_on_submit, permlevel, search_index, no_copy, set_only_once, print_hide, report_hide, depends_on, mandatory_depends_on, read_only_depends_on, fetch_from, and precision where valid.
- Layout: Section Break, Column Break, Tab Break, collapsible/depends_on, ordering, and child table grid settings.
- Numeric/date behavior: precision, currency reference, default values, min/max where supported, date/time fields, and invalid input.
- Relationships: Link/Dynamic Link, child tables, Table MultiSelect, `options`, and required referenced record behavior.
- Security: field permission levels, restricted fields, password/secret data handling, attachments, owner and role restrictions.

Frappe validation remains authoritative. The MCP should validate obvious malformed shape/unsupported properties before preview, but must avoid inventing rules that diverge from the target release. A preview should show the full normalized payload (bounded) or a deterministic summary and hash so users can inspect the exact definition.

## Coverage and test strategy

### Test tiers

1. **Unit:** payload builders, schema merge, field visibility, allowlists, path confinement, confirmation/stale-state checks, endpoint serialization, and error/rollback behavior.
2. **MCP contract:** initialize/list tools and invoke every tool through an actual MCP client. Assert argument schemas and returned JSON types.
3. **HTTP integration:** disposable site; Frappe + ERPNext only for baseline; then add a small third-party/custom app to prove generic discovery. Use users with System Manager, role-limited, field permlevel, owner-only, and denied access.
4. **Visual comparison:** create control DocTypes/configurations in Desk and by MCP on the same disposable site, compare metadata, generated files, DB shape, and form/list behavior.
5. **Business scenarios:** ERPNext sales-to-payment and purchase-to-payment at minimum; stock movement and ledger effects; submit/cancel/amend; workflow and permissions. Add manufacturing, projects, assets, quality, support, and optional HRMS as separately tagged suites.
6. **Version matrix:** clean Frappe + ERPNext sites for each supported major release, with the same seeded roles and checks. Record exact app versions, enabled features, prerequisites, and cleanup evidence.

### Acceptance evidence format

Every manual/live run should record: exact MCP commit, Frappe/ERPNext/app versions, site type (disposable/development), API user role profile (never credentials), test case IDs, input definition hash, preview and result summaries, generated paths, metadata/database verification, Desk comparison, permission outcomes, cleanup status, and failures. Redact customer data, credentials, tokens, cookies, and private record values. Clearly label untested/version-dependent cases.

### Priority implementation order

1. Confirm the current standalone release can install/configure against a clean site with Frappe + ERPNext and a limited API user; fix request error handling so failed mutations cannot accidentally commit.
2. Make DocType creation fully inspectable and robust; validate fields/properties/permissions, generated artifacts, app ownership, and every DocType type in the matrix.
3. Add lifecycle tools: submit, cancel, amend/copy, rename, and workflow action discovery/execution, each with preview/current-state checks where sensible and native endpoint semantics.
4. Add permission-aware identity surfaces: User Permissions, shares, comments/assignments, and Page/Report access as separately scoped tools.
5. Add file and print operations with size/privacy bounds; then workspace/report/notification/web surfaces based on user scenarios.
6. Add app-specific adapters only for explicit actions, each with a named allowlist, permission model, version support, and tests. Keep arbitrary RPC/SQL/Python out of scope.
7. Build repeatable Frappe+ERPNext v15/v16 and generic custom-app acceptance suites. Update the compatibility claim only from those reports.

## Official references

- [Understanding DocTypes](https://docs.frappe.io/framework/user/en/basics/doctypes) — model/view metadata, variants, and document concepts.
- [Types of DocType](https://docs.frappe.io/framework/user/en/tutorial/types-of-doctype) — child/table, Single, Tree, and submittable examples.
- [Single DocType](https://docs.frappe.io/framework/user/en/basics/doctypes/single-doctype) — shared `tabSingles` storage model.
- [Virtual DocTypes](https://docs.frappe.io/framework/user/en/basics/doctypes/virtual-doctype) — custom data-source/controller requirements.
- [Field Types](https://docs.frappe.io/framework/user/en/basics/doctypes/fieldtypes) — field type behavior and field options.
- [Naming](https://docs.frappe.io/framework/user/en/basics/doctypes/naming) — document names and naming strategies.
- [Create a DocType](https://docs.frappe.io/framework/user/en/tutorial/create-a-doctype) — Desk creation and generated app boilerplate.
- [Apps](https://docs.frappe.io/framework/user/en/basics/apps) — app structure, modules, and installation.
- [REST API integration guide](https://docs.frappe.io/framework/user/en/guides/integration/rest_api) and [REST API reference](https://docs.frappe.io/framework/user/en/api/rest) — resource CRUD, RPC, API versions, file upload, commit/error behavior.
- [Document API](https://docs.frappe.io/framework/user/en/api/document) — lifecycle, permissions, rename, and document operations.
- [Controllers](https://docs.frappe.io/framework/user/en/basics/doctypes/controllers) and [Hooks](https://docs.frappe.io/framework/user/en/python-api/hooks) — lifecycle and app extension points.
- [Users and Permissions](https://docs.frappe.io/framework/user/en/basics/users-and-permissions) — roles, permissions, permission levels, User Permissions, and access control.
- [Customizing DocTypes](https://docs.frappe.io/framework-copy/user/en/basics/doctypes/customize) and [Customization](https://docs.frappe.io/framework-git/doctypes/customization) — Custom Fields, Property Setters, Customize Form, and fixtures.
- [Reports](https://docs.frappe.io/framework/user/en/desk/reports) and [Script Reports](https://docs.frappe.io/framework/user/en/desk/reports/script-report) — report types and execution constraints.
- [Printing](https://docs.frappe.io/framework/user/en/desk/printing) and [Web Forms](https://docs.frappe.io/framework/user/en/web-form) — print/PDF and web form behavior.
- [ERPNext Accounting Introduction](https://docs.frappe.io/erpnext/accounting-introduction), [Selling](https://docs.frappe.io/erpnext/selling), and [Assets](https://docs.frappe.io/erpnext/assets/introduction) — examples of domain workflows and controller side effects.
