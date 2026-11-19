# Stage 17: Allowlisted method calls

> **Scheduled 2026-11-19.** This plan was in the backlog (as `backlog/method-allowlist.md`; it was stage 10 before the 2026-10-27 refocus). The design below is the backlog design. The additions for this plan revision are under "Changes for this revision"; where they conflict with the older text, the additions win.

**Depends on:** 16 · **Size:** medium · **Fixes:** structural gap 4 (mapped documents, app-specific actions) in [assessment.md](assessment.md), and FR-2026-005 in [field feedback](../field-feedback/feature-requests.md)

## Why

Real ERPNext work follows document chains: Quotation → Sales Order → Delivery Note → Sales Invoice → Payment Entry. Each step calls an app-specific whitelisted method. AGENTS.md forbids **unrestricted** method dispatch. The ROADMAP plans "controlled custom app method adapters with per-method allowlists". This stage builds that: the user lists exactly which methods may be called, and nothing else can be.

Field use found the developer version of the same need. An agent working on a custom app had to check what one of the app's whitelisted methods returned on the live site, and couldn't (`product-lines-null-central-2026-11-10`).

The native Frappe MCP can call **any** whitelisted method with `call_method` ([comparison](native-mcp-comparison.md)). This server keeps the allowlist, and it differs in other ways: methods are found from the app source (stage 16), write calls are previewed, and it works on v15.

## Changes for this revision

- **Ask the user first:** keep the allowlist (recommended: AGENTS.md forbids unrestricted dispatch), or call any method that the app source shows as whitelisted? Record the decision in the Handoff. A change to AGENTS.md needs the user's explicit approval.
- **Check entries against the source.** When `FRAPPE_APPS_PATH` has the method's app, the config loader checks each entry against `list_app_methods` (stage 16): the method exists, it is whitelisted, the `methods` HTTP verbs fit the entry's `kind`, and the arguments in `params` exist. A mismatch fails at startup with the file and line. Without the checkout, it logs a warning and continues.
- **Suggest entries, don't write them.** `list_app_methods` rows can be turned into ready-to-paste config entries with a new optional `as_config=True`. The server never writes the config file; the user adds the entries.
- **Developer read calls.** Calling a `read` entry for a method of a custom app is the main developer case. Bound the response the same way `run_report` does.
- **Paths:** this file moved from `backlog/`, so links to the assessment are now `assessment.md`.

## Read first

- `AGENTS.md`, `ROADMAP.md` (future work), `docs/security.md`
- Stage 16's `list_app_methods`
- Frappe `frappe/model/mapper.py`:
  - `make_mapped_doc(method, source_name, selected_children, args)` returns an **unsaved** mapped document;
  - it also calls `frappe.is_whitelisted(method)`.
- ERPNext source for the method names below. Read them from the ERPNext repo for the target version; the local Bench may contain only Frappe.

## Design

- **The config file.** `FRAPPE_MCP_METHODS` points to a JSON file (Python 3.10 has no `tomllib`). Each entry looks like:
  ```json
  {
    "method": "erpnext.selling.doctype.sales_order.sales_order.make_sales_invoice",
    "kind": "mapper",
    "source_doctype": "Sales Order",
    "target_doctype": "Sales Invoice",
    "description": "Create a draft Sales Invoice from a submitted Sales Order"
  }
  ```
- **`kind`:**
  - `mapper`: called through `make_mapped_doc`; returns a draft and never saves.
  - `read`: a GET with arguments.
  - `write`: a POST that requires preview and confirm.
- **Checks.** Method names must match a strict dotted-identifier pattern. No wildcards. Entries that aren't in the file can't be called. Arguments for `read` and `write` are checked against an optional `params` JSON-schema-like object in the entry. Without one, only scalar arguments are accepted.
- **No file, no tools.** Without the variable, the method tools aren't registered at all.

## Tasks

- [ ] **Load and validate the config at startup.** Fail clearly on a bad file. Write a test for each validation rule.
- [ ] **`list_allowed_methods()`.** Return the entries without internals.
- [ ] **`make_mapped_document(method, source_name)`.** The method must be a `mapper` entry. Call `frappe.model.mapper.make_mapped_doc` and return the draft: strip `__islocal` and similar keys, and bound child rows. The agent saves it with `create_document`; check that a mapped draft with child tables passes `create_document` validation, and adjust if needed.
- [ ] **`call_allowed_method(method, args=None, confirm=False)`.** `read` entries run directly. `write` entries preview the method, args, and description, then require `confirm`. Bound the response size.
- [ ] **Example config.** Ship `examples/erpnext-methods.json` with common mappers, after verifying each name against the ERPNext source:
  - Quotation → Sales Order;
  - Sales Order → Delivery Note and Sales Invoice;
  - Delivery Note → Sales Invoice;
  - Purchase Order → Purchase Receipt and Purchase Invoice;
  - payment entry from an invoice (`get_payment_entry`; check its signature, which takes `dt`/`dn`, so it may need a `read` entry rather than a mapper).
- [ ] **Toolsets and annotations.** The tools join `data`. `call_allowed_method` isn't read-only.
- [ ] **Tests:**
  - unknown methods are refused;
  - a wildcard in the config is refused;
  - kind mismatches are refused (a mapper through `call_allowed_method`, and the reverse);
  - write entries preview, then confirm;
  - mapper output is cleaned and bounded;
  - argument validation.

## Acceptance criteria

- No code path calls a method that isn't in the config. Add a test that tries `frappe.client.get_list`, `run_doc_method`, and an arbitrary `frappe.*` method.
- `docs/security.md` explains the allowlist model, and that the user is responsible for what they list. `docs/tool-reference.md`, `docs/ai-agent-guide.md` (a Quotation-to-Invoice flow), `ROADMAP.md`, and `docs/frappe-capability-study.md` are updated.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

A built-in catalog that is enabled by default. Calling `run_doc_method` for document buttons; it could be added later as an allowlisted `kind` if needed.

## Handoff

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- ERPNext method names verified against (version/commit):
- Deviations from this plan:
- Notes for the next stage:
