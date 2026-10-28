# Stage 01: Authentication and transport fixes

**Depends on:** 00 · **Size:** small · **Fixes:** bugs 3 and 4 in [assessment.md](assessment.md)

## Why

A failed login currently poisons the process until it restarts. An expired session is never renewed. API tokens can be sent in clear text to remote hosts. A server that runs for hours under an MCP host has to recover from the first two and refuse the third.

## Read first

- `standalone.py`: `_client`, `_check`, `_request`
- `docs/security.md`, `docs/installation-and-configuration.md`

## Tasks

- [x] **Don't cache a failed login.** In `_client()`, build the `httpx.Client` in a local variable, log in, and assign the global `_session` only after `_check` succeeds. Close the client on failure.
- [x] **One transport rule for both auth modes.** Extract `_is_local_target(url) -> bool` (loopback, `::1`, `*.localhost`). Require HTTPS for non-local targets with **token auth as well as password auth**.
- [x] **Explicit insecure opt-in.** Add `FRAPPE_ALLOW_INSECURE_HTTP=YES` for development networks such as Docker service names (`http://frontend:8080`). When it is set, allow plain HTTP and log one warning to stderr at startup. The default stays strict.
- [x] **Renew expired password sessions.** Do this only for password auth, in `_request`:
  1. On HTTP 401 or 403, call `/api/method/frappe.auth.get_logged_user`.
  2. If that also fails, or returns `Guest`, the session has expired. Log in again and retry the original request **once**.
  3. If the user is still logged in, this is a real permission denial. Raise the original error unchanged. Never retry it, and never try another endpoint.

  Retrying a POST is safe here because Frappe rejected it before running it (the request was treated as Guest). Add a comment saying so.
- [x] **Validate configuration early.** If `FRAPPE_TIMEOUT` isn't a positive number, fail with a clear message instead of a `ValueError` traceback.
- [x] **Tests.** Add tests for each case using `httpx.MockTransport` (patch `httpx.Client` construction, as the assessment probe did) or `fake_site`:
  - a failed login leaves `_session` as `None`;
  - token auth to `http://erp.example.com` is refused, and allowed with the opt-in;
  - an expired session triggers exactly one re-login and one retry;
  - a real 403 for a logged-in user is not retried;
  - token auth never attempts a re-login.

## Acceptance criteria

- All five scenarios above are covered by tests and pass.
- `docs/security.md` and `docs/installation-and-configuration.md` describe the HTTPS rule for both modes, the opt-in variable, and session renewal.

## Verification

```bash
uv run pytest -q
uv run ruff check frappe_mcp tests
```

## Out of scope

Error-message formatting (stage 11). OAuth or other auth methods.

## Handoff

- Status: done
- Branch / commits: `stage-01-auth-transport`, based on `origin/main` at `464507b`.
- What changed:
  - `_client()` builds the client locally and assigns `_session` only after a successful login; on failure it closes the client. Token auth and password auth share one transport check: HTTPS required unless `_is_local_target()` (`localhost`, `127.0.0.1`, `::1`, `*.localhost`) or `FRAPPE_ALLOW_INSECURE_HTTP=YES` (exact value). The check runs before any request leaves the process.
  - `_timeout()` validates `FRAPPE_TIMEOUT` (finite, > 0) with a clear `RuntimeError`. `main()` calls it first and exits with that one-line message instead of a traceback, then logs the single insecure-HTTP warning to stderr (via the module logger) when the opt-in is set.
  - `_request()`: for password auth only, a 401/403 triggers `_session_expired()` (GET `frappe.auth.get_logged_user`; an error or `Guest` means expired). If expired, `_reset_session()` closes the old client, `_client()` logs in again, and the original request is retried once. A logged-in user's denial is raised unchanged. Token auth never probes or re-logs in.
  - New `tests/test_transport.py` (33 cases) drives the real `_client`/`_request` through `httpx.MockTransport` by patching `standalone.httpx.Client`; it covers all five required scenarios plus a failed re-login, an exact-value opt-in check, the startup warning, and timeout validation.
  - Docs: `security.md` (new Transport and Sessions sections), `installation-and-configuration.md` (new variable, HTTPS rule for both modes, renewal), `architecture.md`, `README.md`, `tool-reference.md`, `ai-agent-guide.md`, and the HTTP-layer line in `CLAUDE.md`.
- Deviations from this plan: none. Frappe behavior was checked in v16.33.1 source: `get_logged_user` is `@frappe.whitelist()` without `allow_guest`, so an expired sid (treated as Guest) gets 403 from the probe, and `SessionExpired`/`AuthenticationError` are 401 while `PermissionError` is 403. v15 was not checked.
- Notes for the next stage: a `TransportError` raised by the session probe propagates unchanged, so the Bench reload-tolerance helpers keep working. Error-message formatting is still the plain `Frappe HTTP <status>: ...` text from `_check` (stage 11). The global `_session` is not lock-protected; if tools ever run concurrently in threads, a re-login race is possible (relevant to the async-HTTP backlog item).
