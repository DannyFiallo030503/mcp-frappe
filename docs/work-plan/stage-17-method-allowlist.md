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

- Status: done.
- Branch / commits: `stage-17-method-allowlist`, one commit on top of `main` (`12a96f4`).
- Decision (asked first, as the plan requires): **keep the allowlist**. Only methods the user lists in the `FRAPPE_MCP_METHODS` file can be called; AGENTS.md is unchanged. Tool-list budget (also the user's choice): the default list keeps its 34,000 cap (the method tools aren't registered without the file), and a second test caps the list with them at 36,500.
- What changed:
  - **Config loader** (`_load_method_config()`, called from `main()` through `_method_settings()` after the other startup settings, and from `mcp-frappe check`): a JSON list of 1-200 entries, at most 1 MB. Entry keys `method`, `kind` (`mapper`/`read`/`write`), `description` (≤300), `source_doctype`/`target_doctype` (mapper both; read only `target_doctype`; write neither), `params` (read/write only; name → `{type, required, enum, description}`, ≤50; types `string`, `integer`, `number`, `boolean`, `object`, `array`). Method paths must match a dotted identifier pattern (≥2 parts, ≤200 chars); wildcards, duplicates, unknown keys, `cmd`/`ignore_permissions` params, `frappe.client.*`, and the dispatchers (`make_mapped_doc`, `map_docs`, `frappe.handler.run_doc_method`, `runserverobj`, `execute_action`) are refused. Every problem is collected (up to 20 shown) with the file name and the entry's line (`_config_line()`), and stops startup.
  - **Source check** (`_method_source()`, `_source_problem()`): with a checkout of the method's app under `FRAPPE_APPS_PATH`, the module file is parsed with stage 16's `_file_methods()`; the method must be a whitelisted module function, `GET` must be allowed for `read` and `POST` for `write`, a mapper must take an argument, and every `params` name must be an argument (unless `**kwargs`). The argument names are kept, so an entry without `params` refuses names the function doesn't take. Without a checkout: one warning per app, and `checked_against_source: false`.
  - **Tools** (registered only with the file, in `data`, by `_register_method_tools()` before `_apply_toolsets()`): `list_allowed_methods()` (entries without internals, plus `checked_against_source`); `make_mapped_document(method, source_name)` (`GET frappe.model.mapper.make_mapped_doc`; `_mapped_draft()` returns `doctype`, `values` that pass `create_document`, `read_only` computed fields, child tables cut at 200 rows with a note); `call_allowed_method(method, args, limit, offset, confirm)` (read → `GET` with objects/arrays as JSON text; write → preview, then `POST` JSON on confirm; list results paged like `run_report`, nested values capped, over 100,000 characters returned as `result_text`; a `read` entry with `target_doctype` returning that DocType comes back as a draft, used for `get_payment_entry`). `_tool()` gained `read_only=` so the first two have `readOnlyHint: true`; `call_allowed_method` has `destructiveHint: true`.
  - **`list_app_methods(as_config=True)`**: module functions only, as suggested entries (`_config_suggestion()`): `mapper` for `make_*(source_name, ...)` with `source_doctype` from a controller file, `read` for `get_`/`list_`/`fetch_`/`search_`/`count_`/`is_`/`has_` names that allow GET, else `write`; `params` from the signature with types from plain annotations. Returns a `note`; the server never writes the file.
  - **`examples/erpnext-methods.json`**: Quotation → Sales Order; Sales Order → Delivery Note and Sales Invoice; Delivery Note → Sales Invoice; Purchase Order → Purchase Receipt and Purchase Invoice (mappers); `get_payment_entry` as a `read` entry with `target_doctype: Payment Entry` and `params` `dt` (enum), `dn`, `party_amount`, `bank_account`.
  - `mcp-frappe check` prints a `methods` row (entry count, warnings; FAIL on a bad file; never the file's directory). The smoke runner adds `as_config` to its `list_app_methods` probe and lists the allowlist when the tool is listed.
  - Tests: `tests/test_methods.py` (74: every loader rule with its line, dispatchers, duplicates, file-level errors, source checks, kwargs, warnings, `as_config` round-trip into the loader, unlisted methods including `frappe.client.get_list`, `run_doc_method`, and `frappe.utils.now` refused by both tools with no request sent, kind mismatches, write preview/confirm, argument checks, paging and cutting, mapper cleaning, a mapped draft passing `create_document`, child-row cap, target mismatch, `main()` exiting on a bad file, read-only mode); `test_mcp_contract.py` (budget with the method tools; an MCP-session refusal and write preview); `test_cli.py` (the `check` row).
  - Docs: tool reference (new "Allowlisted method calls" section, toolset table, annotations, budget, `as_config`), AI agent guide ("Quotation to Sales Invoice", "Call my app's method"), security (new "Allowlisted method calls" section: the user is responsible for what they list), architecture, compatibility ("not yet checked" entry, boundaries), installation (`FRAPPE_MCP_METHODS`, `check`), native-frappe-mcp, native-mcp-comparison, ROADMAP (stage 16 and 17 moved to Implemented), capability study, README, `llms.txt`, CHANGELOG, CLAUDE.md.
- ERPNext method names verified against (version/commit): ERPNext v15.124.4 (Frappe v15.126.0) and v16.53.0 (Frappe v16.55.1), local Bench checkouts. All seven example entries load with the source check on both (`make_*` mappers take `source_name`; `get_payment_entry(dt, dn, party_amount, bank_account, ...)` is the same in both, whitelisted for every HTTP method). `make_mapped_doc` differs only in its whitelist check (v15 `method not in frappe.whitelisted`, v16 `frappe.is_whitelisted`).
- Deviations from this plan:
  - The loader also refuses dispatching methods (`frappe.client.*`, `make_mapped_doc`, `map_docs`, `run_doc_method`, `runserverobj`, `execute_action`) even when listed, and `cmd`/`ignore_permissions` are never sent. This tightens the plan (an entry for one of them would make the allowlist unrestricted, or skip the record tools' code guards); decided without asking because it only narrows what can be called.
  - Mapper calls use `GET`, so Frappe rolls back what a mapper writes (Desk uses `POST`). Consequence, documented: mapping a Quotation for a Lead doesn't keep the Customer ERPNext would create.
  - `make_mapped_document` returns `values` (fields `create_document` accepts) and `read_only` separately, because `create_document` refuses read-only top-level fields; child rows are kept whole (minus local names and `__` keys), so links such as `so_detail` survive.
  - A `read` entry may set `target_doctype` to get its document result as a draft (for `get_payment_entry`).
  - `call_allowed_method` has `limit`/`offset` (paging like `run_report`).
  - `mcp-frappe check` validates the file too.
  - Live check not run (user chose unit tests only).
- Notes for the next stage:
  - The default tool list is 33,519 characters (as_config added ~130), so about 480 remain under 34,000; with the method tools it is 35,129 of 36,500.
  - The allowlist's source check reads only the one module file per entry, so it is fast even for ERPNext; it doesn't follow `override_whitelisted_methods` hooks.
  - Not checked on a site: mapping a submitted ERPNext document and saving the draft with `create_document`, and `get_payment_entry` over GET. Worth adding to a writing e2e runner once an ERPNext test flow exists.
  - Stage 18 (Bench commands) starts with a decision for the user, like this one.
