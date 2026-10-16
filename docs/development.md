# Development Guide

## Repository structure

This is a Frappe app repository. The MCP tool implementation lives under `mcp_frappe/`; the Streamable HTTP transport based on `frappe/mcp` is vendored under `frappe_mcp/`. See [architecture](architecture.md) and [UPSTREAM.md](../UPSTREAM.md).

## Local development

Use a disposable Frappe Bench site. Do not point destructive acceptance checks at a production site. After code changes, restart/reload the Bench processes as required by the development setup. Install `mcp_frappe` on the test site and exercise the real endpoint with an authenticated user.

The endpoint is:

```text
/api/method/mcp_frappe.mcp.handle_mcp
```

Test credentials should be read from a local secret file or environment and must never be committed or included in evidence. The real HTTP runner reads `MCP_FRAPPE_CREDENTIALS` and optionally `MCP_FRAPPE_URL`; see `e2e/run_real_http.py` for its exact behavior. Running the runner clears and rewrites `e2e/real_http_evidence.jsonl`, and it creates identifiable site data. Review its cleanup behavior and use a disposable site.

## Automated checks

Automated unit/transport coverage is under:

- `mcp_frappe/tests/`
- `frappe_mcp/server/tests/`
- `frappe_mcp/server/tools/test_*.py`
- `frappe_mcp/server/prompts/test_prompts.py`

From a Frappe Bench with the app installed on a disposable site:

```bash
bench --site <test-site> set-config allow_tests true
bench --site <test-site> run-tests --app mcp_frappe
bench --site <test-site> set-config allow_tests false
```

Ensure tests are disabled again even if the run fails. Use your shell's cleanup/trap mechanism. The automated suite does not replace authenticated MCP-over-HTTP acceptance checks.

## Real HTTP acceptance run

Set `MCP_FRAPPE_URL` to the site's endpoint and `MCP_FRAPPE_CREDENTIALS` to a local JSON file containing `api_key` and `api_secret` for the dedicated test user. Then run:

```bash
python e2e/run_real_http.py
```

The runner performs actual HTTP requests, including an intentional anonymous denial. It creates schema/customization examples and records redacted summaries in the JSONL evidence file. It can leave retained demonstration records and fixture files on the target site, so it must only be used against an isolated site after reviewing the runner. Refer to [compatibility and validation](compatibility-and-validation.md) for the completed run and its limits.

## Contribution checklist

- Keep Frappe/ERPNext/HRMS support generic and runtime-discovered.
- Preserve native Frappe permissions; do not add permission bypasses.
- Bound list/report/file/HTML output and validate caller-controlled names/fields.
- Make mutating tools previewable and explicit; use concurrency tokens for existing documents.
- Document idempotency, side effects, limits, and permissions in the tool docstring and reference.
- Add focused tests and real endpoint coverage for behavior changes.
- Keep vendored upstream license notices intact and document upstream changes.
- Avoid committing credentials, customer data, test exports, or local Bench artifacts.
