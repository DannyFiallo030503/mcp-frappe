"""Authentication and transport rules, driven through a mocked `httpx` transport."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator

import httpx
import pytest

from frappe_mcp import standalone

Handler = Callable[[httpx.Request], httpx.Response]

_ENV = (
    "FRAPPE_URL",
    "FRAPPE_SITE",
    "FRAPPE_API_KEY",
    "FRAPPE_API_SECRET",
    "FRAPPE_USERNAME",
    "FRAPPE_PASSWORD",
    "FRAPPE_TIMEOUT",
    "FRAPPE_ALLOW_INSECURE_HTTP",
)


class MockSite:
    """Routes every `httpx.Client` the module builds through one handler and records requests."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.handler: Handler = lambda request: httpx.Response(404)
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.Client] = []
        real_client = httpx.Client

        def build(**kwargs) -> httpx.Client:
            client = real_client(transport=httpx.MockTransport(self._handle), **kwargs)
            self.clients.append(client)
            return client

        monkeypatch.setattr(standalone.httpx, "Client", build)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


@pytest.fixture
def site(monkeypatch) -> Iterator[MockSite]:
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(standalone, "_session", None)
    yield MockSite(monkeypatch)


def _password_env(monkeypatch, url: str = "http://site1.localhost:8000") -> None:
    monkeypatch.setenv("FRAPPE_URL", url)
    monkeypatch.setenv("FRAPPE_USERNAME", "test@example.com")
    monkeypatch.setenv("FRAPPE_PASSWORD", "not-a-real-password")


def _token_env(monkeypatch, url: str) -> None:
    monkeypatch.setenv("FRAPPE_URL", url)
    monkeypatch.setenv("FRAPPE_API_KEY", "key")
    monkeypatch.setenv("FRAPPE_API_SECRET", "secret")


def _ok(message: object = "ok") -> httpx.Response:
    return httpx.Response(200, json={"message": message})


def _denied() -> httpx.Response:
    return httpx.Response(403, json={"exc_type": "PermissionError", "message": "Not permitted"})


def test_failed_login_does_not_cache_the_session(site, monkeypatch) -> None:
    _password_env(monkeypatch)
    site.handler = lambda request: httpx.Response(401, json={"message": "Invalid login"})

    with pytest.raises(RuntimeError, match="401"):
        standalone._client()

    assert standalone._session is None
    assert site.clients[0].is_closed


def test_successful_login_is_cached(site, monkeypatch) -> None:
    _password_env(monkeypatch)
    site.handler = lambda request: _ok("Logged In")

    client = standalone._client()

    assert standalone._client() is client
    assert site.paths() == ["/api/method/login"]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://site1.localhost",
        "https://erp.example.com",
    ],
)
def test_token_auth_is_allowed_over_https_or_to_local_targets(site, monkeypatch, url) -> None:
    _token_env(monkeypatch, url)

    assert standalone._client().headers["Authorization"] == "token key:secret"


@pytest.mark.parametrize("url", ["http://erp.example.com", "http://localhost.example.com"])
def test_token_auth_over_http_to_a_remote_host_is_refused(site, monkeypatch, url) -> None:
    _token_env(monkeypatch, url)

    with pytest.raises(RuntimeError, match="HTTPS"):
        standalone._client()

    assert standalone._session is None
    assert site.requests == []


def test_password_auth_over_http_to_a_remote_host_is_refused(site, monkeypatch) -> None:
    _password_env(monkeypatch, "http://erp.example.com")

    with pytest.raises(RuntimeError, match="HTTPS"):
        standalone._client()

    assert site.requests == []


@pytest.mark.parametrize("value", ["yes", "1", "true", ""])
def test_insecure_opt_in_needs_the_exact_value(site, monkeypatch, value) -> None:
    _token_env(monkeypatch, "http://frontend:8080")
    monkeypatch.setenv("FRAPPE_ALLOW_INSECURE_HTTP", value)

    with pytest.raises(RuntimeError, match="HTTPS"):
        standalone._client()


def test_insecure_opt_in_allows_plain_http_to_a_remote_host(site, monkeypatch) -> None:
    _token_env(monkeypatch, "http://frontend:8080")
    monkeypatch.setenv("FRAPPE_ALLOW_INSECURE_HTTP", "YES")
    site.handler = lambda request: _ok()

    assert standalone._request("GET", "/api/method/ping") == "ok"


def test_insecure_opt_in_logs_one_warning_at_startup(monkeypatch, caplog) -> None:
    monkeypatch.delenv("FRAPPE_TIMEOUT", raising=False)
    monkeypatch.setenv("FRAPPE_ALLOW_INSECURE_HTTP", "YES")
    runs: list[str] = []
    monkeypatch.setattr(standalone.server, "run", lambda transport: runs.append(transport))

    with caplog.at_level(logging.WARNING, logger=standalone.__name__):
        standalone.main()

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "FRAPPE_ALLOW_INSECURE_HTTP" in warnings[0].getMessage()
    assert runs == ["stdio"]


def test_strict_default_logs_no_warning_at_startup(monkeypatch, caplog) -> None:
    monkeypatch.delenv("FRAPPE_TIMEOUT", raising=False)
    monkeypatch.delenv("FRAPPE_ALLOW_INSECURE_HTTP", raising=False)
    monkeypatch.setattr(standalone.server, "run", lambda transport: None)

    with caplog.at_level(logging.WARNING, logger=standalone.__name__):
        standalone.main()

    assert caplog.records == []


@pytest.mark.parametrize("value", ["abc", "0", "-5", "nan", "inf", ""])
def test_invalid_timeout_fails_with_a_clear_message(site, monkeypatch, value) -> None:
    _token_env(monkeypatch, "https://erp.example.com")
    monkeypatch.setenv("FRAPPE_TIMEOUT", value)

    with pytest.raises(RuntimeError, match="FRAPPE_TIMEOUT must be a positive number"):
        standalone._client()


def test_invalid_timeout_stops_startup(monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_TIMEOUT", "soon")
    monkeypatch.setattr(standalone.server, "run", lambda transport: pytest.fail("server ran"))

    with pytest.raises(SystemExit, match="FRAPPE_TIMEOUT must be a positive number"):
        standalone.main()


def test_valid_timeout_is_used(site, monkeypatch) -> None:
    _token_env(monkeypatch, "https://erp.example.com")
    monkeypatch.setenv("FRAPPE_TIMEOUT", "12.5")

    assert standalone._client().timeout.read == 12.5


def test_expired_session_logs_in_again_and_retries_once(site, monkeypatch) -> None:
    _password_env(monkeypatch)
    logins = []
    resources = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/method/login":
            logins.append(request)
            return _ok("Logged In")
        if path == "/api/method/frappe.auth.get_logged_user":
            return _denied()
        resources.append(request)
        return _denied() if len(resources) == 1 else httpx.Response(200, json={"data": {"x": 1}})

    site.handler = handler
    first = standalone._client()

    result = standalone._request("POST", "/api/resource/ToDo", json={"description": "x"})

    assert result == {"x": 1}
    assert len(logins) == 2
    assert len(resources) == 2
    assert resources[1].content == resources[0].content
    assert first.is_closed
    assert standalone._session is not first


def test_session_probe_returning_guest_counts_as_expired(site, monkeypatch) -> None:
    _password_env(monkeypatch)
    resources = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/method/login":
            return _ok("Logged In")
        if path == "/api/method/frappe.auth.get_logged_user":
            return _ok("Guest")
        resources.append(request)
        return _denied() if len(resources) == 1 else _ok("done")

    site.handler = handler

    assert standalone._request("GET", "/api/resource/ToDo") == "done"
    assert site.paths().count("/api/method/login") == 2


def test_retry_happens_only_once_even_if_it_is_denied_again(site, monkeypatch) -> None:
    _password_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/method/login":
            return _ok("Logged In")
        return _denied()

    site.handler = handler

    with pytest.raises(RuntimeError, match="403"):
        standalone._request("GET", "/api/resource/ToDo")

    assert site.paths() == [
        "/api/method/login",
        "/api/resource/ToDo",
        "/api/method/frappe.auth.get_logged_user",
        "/api/method/login",
        "/api/resource/ToDo",
    ]


def test_failed_relogin_leaves_no_session(site, monkeypatch) -> None:
    _password_env(monkeypatch)
    logins = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/method/login":
            logins.append(request)
            if len(logins) == 1:
                return _ok("Logged In")
            return httpx.Response(401, json={"message": "Invalid login"})
        return _denied()

    site.handler = handler

    with pytest.raises(RuntimeError, match="401"):
        standalone._request("GET", "/api/resource/ToDo")

    assert standalone._session is None


def test_real_permission_denial_for_a_logged_in_user_is_not_retried(site, monkeypatch) -> None:
    _password_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/method/login":
            return _ok("Logged In")
        if path == "/api/method/frappe.auth.get_logged_user":
            return _ok("test@example.com")
        return httpx.Response(
            403, json={"exc_type": "PermissionError", "_server_messages": "No permission for ToDo"}
        )

    site.handler = handler
    client = standalone._client()

    with pytest.raises(RuntimeError, match="Frappe HTTP 403: No permission for ToDo"):
        standalone._request("POST", "/api/resource/ToDo", json={"description": "x"})

    assert site.paths() == [
        "/api/method/login",
        "/api/resource/ToDo",
        "/api/method/frappe.auth.get_logged_user",
    ]
    assert standalone._session is client
    assert not client.is_closed


@pytest.mark.parametrize("status", [401, 403])
def test_token_auth_never_attempts_a_relogin(site, monkeypatch, status) -> None:
    _token_env(monkeypatch, "https://erp.example.com")
    site.handler = lambda request: httpx.Response(status, json={"message": "Not permitted"})

    with pytest.raises(RuntimeError, match=str(status)):
        standalone._request("GET", "/api/resource/ToDo")

    assert site.paths() == ["/api/resource/ToDo"]


def test_other_errors_are_not_probed_or_retried(site, monkeypatch) -> None:
    _password_env(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/method/login":
            return _ok("Logged In")
        return httpx.Response(417, json={"message": "Validation failed"})

    site.handler = handler

    with pytest.raises(RuntimeError, match="417"):
        standalone._request("POST", "/api/resource/ToDo", json={})

    assert site.paths() == ["/api/method/login", "/api/resource/ToDo"]


def test_error_responses_carry_frappes_exception_type(site, monkeypatch) -> None:
    _token_env(monkeypatch, "http://site1.localhost:8000")
    site.handler = lambda request: httpx.Response(
        417,
        json={"exc_type": "TimestampMismatchError", "_server_messages": "modified after"},
    )

    with pytest.raises(standalone.FrappeError, match="417") as raised:
        standalone._request("PUT", "/api/resource/ToDo/T-1", json={})

    assert raised.value.exc_type == "TimestampMismatchError"
    assert isinstance(raised.value, RuntimeError)
