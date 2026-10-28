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
    doctypes: dict[str, dict[str, Any]] = field(default_factory=dict)

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
        **properties: Any,
    ) -> FakeSite:
        """Register a DocType for `standalone._meta_bundle()`.

        Answers `getdoctype` the way Frappe's `get_meta_bundle` does: `{"docs": [...]}` with
        the DocType first and then one entry per Table / Table MultiSelect field's DocType,
        looked up among the DocTypes registered here when the request is made. Frappe has
        already merged Custom Fields and Property Setters into `fields`.
        """
        self.doctypes[doctype] = {"doctype": "DocType", "name": doctype, **properties,
                                  "fields": fields}
        if not any(
            route.method == "GET" and route.path == standalone._GETDOCTYPE for route in self.routes
        ):
            self.add("GET", standalone._GETDOCTYPE, self._getdoctype)
        return self

    def _getdoctype(self, call: Call) -> dict[str, Any]:
        name = (call.params or {}).get("doctype")
        if name not in self.doctypes:
            raise standalone.FrappeError(
                f"Frappe HTTP 404: DocType {name} not found", "DoesNotExistError"
            )
        main = self.doctypes[name]
        docs = [main]
        for row in main["fields"]:
            if row.get("fieldtype") in standalone._TABLE_FIELDTYPES:
                if row.get("options") not in self.doctypes:
                    raise AssertionError(f"fake_site: register child DocType {row.get('options')}")
                docs.append(self.doctypes[row["options"]])
        return copy.deepcopy({"docs": docs, "user_settings": "{}"})

    def meta_calls(self) -> list[Call]:
        """Return the metadata reads: `getdoctype` and any raw schema resource read."""
        schema_paths = {
            standalone._GETDOCTYPE,
            *(standalone._resource(name) for name in standalone._SCHEMA_DOCTYPES),
        }
        return [
            call
            for call in self.calls
            if call.method == "GET"
            and (call.path in schema_paths or call.path.startswith("/api/resource/DocType/"))
        ]

    def calls_to(self, method: str, path: str | None = None) -> list[Call]:
        """Return the recorded calls with this method, and this path when given."""
        return [
            call
            for call in self.calls
            if call.method == method.upper() and (path is None or call.path == path)
        ]


def _reset_module_caches() -> None:
    standalone._meta_cache.clear()
    for value in vars(standalone).values():
        cache_clear = getattr(value, "cache_clear", None)
        if callable(cache_clear):
            cache_clear()


@pytest.fixture
def fake_site(monkeypatch) -> Iterator[FakeSite]:
    """Replace `standalone._request` with a `FakeSite` and keep real HTTP out of reach."""
    site = FakeSite()
    _reset_module_caches()
    monkeypatch.delenv("FRAPPE_META_TTL", raising=False)
    monkeypatch.setattr(standalone, "_request", site)

    def no_client() -> None:
        raise AssertionError("fake_site is active; tests must not build a real HTTP client")

    monkeypatch.setattr(standalone, "_client", no_client)
    yield site
    _reset_module_caches()
