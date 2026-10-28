# Stage 01: Authentication and transport fixes

**Depends on:** 00 · **Size:** small · **Fixes:** bugs 3 and 4 in [assessment.md](assessment.md)

## Why

A failed login currently poisons the process until it restarts. An expired session is never renewed. API tokens can be sent in clear text to remote hosts. A server that runs for hours under an MCP host has to recover from the first two and refuse the third.

## Read first

- `standalone.py`: `_client`, `_check`, `_request`
- `docs/security.md`, `docs/installation-and-configuration.md`

## Tasks

- [ ] **Don't cache a failed login.** In `_client()`, build the `httpx.Client` in a local variable, log in, and assign the global `_session` only after `_check` succeeds. Close the client on failure.
- [ ] **One transport rule for both auth modes.** Extract `_is_local_target(url) -> bool` (loopback, `::1`, `*.localhost`). Require HTTPS for non-local targets with **token auth as well as password auth**.
- [ ] **Explicit insecure opt-in.** Add `FRAPPE_ALLOW_INSECURE_HTTP=YES` for development networks such as Docker service names (`http://frontend:8080`). When it is set, allow plain HTTP and log one warning to stderr at startup. The default stays strict.
- [ ] **Renew expired password sessions.** Do this only for password auth, in `_request`:
  1. On HTTP 401 or 403, call `/api/method/frappe.auth.get_logged_user`.
  2. If that also fails, or returns `Guest`, the session has expired. Log in again and retry the original request **once**.
  3. If the user is still logged in, this is a real permission denial. Raise the original error unchanged. Never retry it, and never try another endpoint.

  Retrying a POST is safe here because Frappe rejected it before running it (the request was treated as Guest). Add a comment saying so.
- [ ] **Validate configuration early.** If `FRAPPE_TIMEOUT` isn't a positive number, fail with a clear message instead of a `ValueError` traceback.
- [ ] **Tests.** Add tests for each case using `httpx.MockTransport` (patch `httpx.Client` construction, as the assessment probe did) or `fake_site`:
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

_Fill this in at the end of the session._

- Status:
- Branch / commits:
- What changed:
- Deviations from this plan:
- Notes for the next stage:
