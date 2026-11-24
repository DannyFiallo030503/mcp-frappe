"""Authentication and transport rules, driven through a mocked `httpx` transport."""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable, Iterator

import anyio
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
        standalone.main([])

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "FRAPPE_ALLOW_INSECURE_HTTP" in warnings[0].getMessage()
    assert runs == ["stdio"]


def test_strict_default_logs_no_warning_at_startup(monkeypatch, caplog) -> None:
    monkeypatch.delenv("FRAPPE_TIMEOUT", raising=False)
    monkeypatch.delenv("FRAPPE_ALLOW_INSECURE_HTTP", raising=False)
    monkeypatch.setattr(standalone.server, "run", lambda transport: None)

    with caplog.at_level(logging.WARNING, logger=standalone.__name__):
        standalone.main([])

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
        standalone.main([])


def test_valid_timeout_is_used(site, monkeypatch) -> None:
    _token_env(monkeypatch, "https://erp.example.com")
    monkeypatch.setenv("FRAPPE_TIMEOUT", "12.5")

    assert standalone._client().timeout.read == 12.5


def test_parallel_tool_startup_creates_one_authenticated_client(site, monkeypatch) -> None:
    _password_env(monkeypatch)
    site.handler = lambda request: (
        time.sleep(0.1) or _ok("Logged In")
        if request.url.path == "/api/method/login"
        else _ok()
    )

    async def get_clients():
        clients = []

        async def get_client():
            clients.append(await anyio.to_thread.run_sync(standalone._client))

        async with anyio.create_task_group() as group:
            group.start_soon(get_client)
            group.start_soon(get_client)
        return clients

    clients = anyio.run(get_clients)

    assert len(clients) == 2 and clients[0] is clients[1]
    assert site.paths().count("/api/method/login") == 1


def test_reset_defers_closing_a_client_until_in_flight_requests_finish(site, monkeypatch) -> None:
    _password_env(monkeypatch)
    site.handler = lambda request: _ok("Logged In")
    client = standalone._acquire_client()

    standalone._reset_session(client)

    assert standalone._session is None
    assert not client.is_closed

    standalone._release_client(client)

    assert client.is_closed


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

    with pytest.raises(
        RuntimeError, match="Frappe HTTP 403 PermissionError: No permission for ToDo"
    ):
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


# A site that can't be reached


def test_connecting_gives_up_sooner_than_reading(site, monkeypatch) -> None:
    _token_env(monkeypatch, "https://erp.example.com")
    monkeypatch.setenv("FRAPPE_TIMEOUT", "30")

    timeout = standalone._client().timeout

    assert timeout.connect == 5.0
    assert timeout.read == 30.0


def test_a_short_timeout_also_bounds_connecting(site, monkeypatch) -> None:
    _token_env(monkeypatch, "https://erp.example.com")
    monkeypatch.setenv("FRAPPE_TIMEOUT", "2")

    assert standalone._client().timeout.connect == 2.0


def _registered(name: str):
    """The function FastMCP calls for a tool, as opposed to the module-level one."""
    registered = standalone.server._tool_manager._tools[name].fn

    @functools.wraps(registered)
    def call(*args, **kwargs):
        async def invoke():
            return await registered(*args, **kwargs)

        return anyio.run(invoke)

    return call


_URL = "http://user:hunter2-secret@127.0.0.1:8000/private/path?token=abc"


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (httpx.ConnectError("[Errno 111] Connection refused"), "connection refused"),
        (httpx.ConnectTimeout("timed out"), "no connection within 5 s"),
        (httpx.ReadTimeout("timed out"), "no answer within 30 s"),
        (
            httpx.ConnectError("[Errno -2] Name or service not known"),
            "host name could not be resolved",
        ),
        (
            httpx.ConnectError("[Errno -3] Temporary failure in name resolution"),
            "host name could not be resolved",
        ),
        (httpx.RemoteProtocolError("Server disconnected"), "the connection was closed"),
    ],
)
def test_an_unreachable_site_names_the_site_and_the_next_step(
    fake_site, monkeypatch, error, kind
) -> None:
    monkeypatch.setenv("FRAPPE_URL", _URL)
    monkeypatch.setenv("FRAPPE_API_KEY", "key-should-not-appear")
    monkeypatch.delenv("FRAPPE_TIMEOUT", raising=False)
    fake_site.add("GET", "/api/method/frappe.auth.get_logged_user", error)

    with pytest.raises(RuntimeError) as raised:
        _registered("get_site_info")()

    message = str(raised.value)
    assert not isinstance(raised.value, httpx.TransportError)
    assert "http://127.0.0.1:8000" in message
    assert kind in message
    assert "mcp-frappe check" in message
    for leaked in ("hunter2", "user:", "/private", "token", "key-should-not-appear"):
        assert leaked not in message


def test_the_dns_failure_is_found_through_the_cause(fake_site, monkeypatch) -> None:
    import socket

    monkeypatch.setenv("FRAPPE_URL", "https://erp.example.com")
    error = httpx.ConnectError("connect failed")
    error.__cause__ = socket.gaierror(-2, "Name or service not known")
    fake_site.add("GET", "/api/method/frappe.auth.get_logged_user", error)

    with pytest.raises(RuntimeError, match="host name could not be resolved"):
        _registered("get_site_info")()


def test_module_functions_still_raise_the_transport_error(fake_site, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_URL", "http://127.0.0.1:8000")
    fake_site.add(
        "GET", "/api/method/frappe.auth.get_logged_user", httpx.ConnectError("refused")
    )

    with pytest.raises(httpx.ConnectError):
        standalone.get_site_info()


def test_reload_reconciliation_still_sees_the_raw_transport_error(fake_site, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_URL", "http://127.0.0.1:8000")
    fake_site.add("GET", "/api/resource/Example/EX-1", {"name": "EX-1", "modified": "current"})
    fake_site.add("DELETE", "/api/resource/Example/EX-1", httpx.ConnectError("worker restarted"))
    monkeypatch.setattr(standalone, "_wait_for_document_deleted", lambda *args: True)

    result = _registered("delete_document")(
        "Example", "EX-1", expected_modified="current", confirm=True
    )

    assert result["reconciled_after_server_reload"] is True


def test_registered_tools_keep_their_name_docstring_and_signature() -> None:
    import inspect

    wrapped = _registered("list_documents")

    assert wrapped.__name__ == "list_documents"
    assert wrapped.__doc__ == standalone.list_documents.__doc__
    assert inspect.signature(wrapped) == inspect.signature(standalone.list_documents)
