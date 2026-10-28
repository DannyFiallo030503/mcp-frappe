"""Shared test fixtures. Tests never talk to a live Frappe site."""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from frappe_mcp import standalone

Matcher = dict[str, Any] | Callable[[Any], bool] | None


@dataclass
class Call:
    """One request the code under test sent through `standalone._request`."""

    method: str
    path: str
    kwargs: dict[str, Any]

    @property
    def params(self) -> Any:
        return self.kwargs.get("params")

    @property
    def json(self) -> Any:
        return self.kwargs.get("json")


@dataclass
class _Route:
    method: str
    path: str
    response: Any
    params: Matcher = None
    json: Matcher = None

    def matches(self, call: Call) -> bool:
        return (
            self.method == call.method
            and self.path == call.path
            and _matches(self.params, call.params)
            and _matches(self.json, call.json)
        )


def _matches(matcher: Matcher, value: Any) -> bool:
    if matcher is None:
        return True
    if callable(matcher):
        return bool(matcher(value))
    if not isinstance(value, dict):
        return False
    return all(key in value and value[key] == expected for key, expected in matcher.items())


@dataclass
class FakeSite:
    """A stand-in for a Frappe site, wired in place of `standalone._request`.

    Register responses with `add()`. Paths are the exact strings the code passes to
    `_request`, so build them with `standalone._resource()` when names need quoting.
    """

    routes: list[_Route] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def add(
        self,
        method: str,
        path: str,
        response: Any = None,
        *,
        params: Matcher = None,
        json: Matcher = None,
    ) -> FakeSite:
        """Register a response for `(method, path)`.

        `params` and `json` narrow the match: a dict matches when the request's value holds
        those items, a callable gets the request's value and returns a bool. When several
        routes match, the one registered last wins, so a test can override a default.

        `response` is returned as a deep copy. An exception instance is raised instead, and a
        callable is called with the `Call` and its return value is used.
        """
        self.routes.append(_Route(method.upper(), path, response, params, json))
        return self

    def __call__(self, method: str, path: str, **kwargs: Any) -> Any:
        call = Call(method.upper(), path, kwargs)
        self.calls.append(call)
        for route in reversed(self.routes):
            if route.matches(call):
                if isinstance(route.response, BaseException):
                    raise route.response
                if callable(route.response):
                    return route.response(call)
                return copy.deepcopy(route.response)
        registered = "\n".join(f"  {route.method} {route.path}" for route in self.routes)
        raise AssertionError(
            f"fake_site has no response for {call.method} {call.path} "
            f"(params={call.params!r}, json={call.json!r}). Registered:\n"
            f"{registered or '  (none)'}"
        )

    def add_doctype(
        self,
        doctype: str,
        fields: list[dict[str, Any]],
        *,
        custom_fields: list[dict[str, Any]] | None = None,
        property_setters: list[dict[str, Any]] | None = None,
        **properties: Any,
    ) -> FakeSite:
        """Register the three reads `standalone._doctype()` makes to build a merged schema."""
        self.add(
            "GET",
            standalone._resource("DocType", doctype),
            {"name": doctype, **properties, "fields": fields},
        )
        self.add(
            "GET",
            standalone._resource("Custom Field"),
            custom_fields or [],
            params={"filters": standalone._filters({"dt": doctype})},
        )
        self.add(
            "GET",
            standalone._resource("Property Setter"),
            property_setters or [],
            params={"filters": standalone._filters({"doc_type": doctype})},
        )
        return self

    def calls_to(self, method: str, path: str | None = None) -> list[Call]:
        """Return the recorded calls with this method, and this path when given."""
        return [
            call
            for call in self.calls
            if call.method == method.upper() and (path is None or call.path == path)
        ]


def _reset_module_caches() -> None:
    # Clear any functools cache on the module (stage 05 adds a metadata cache).
    for value in vars(standalone).values():
        cache_clear = getattr(value, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


@pytest.fixture
def fake_site(monkeypatch) -> Iterator[FakeSite]:
    """Replace `standalone._request` with a `FakeSite` and keep real HTTP out of reach."""
    site = FakeSite()
    _reset_module_caches()
    monkeypatch.setattr(standalone, "_request", site)

    def no_client() -> None:
        raise AssertionError("fake_site is active; tests must not build a real HTTP client")

    monkeypatch.setattr(standalone, "_client", no_client)
    yield site
    _reset_module_caches()
