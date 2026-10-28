import pytest

from frappe_mcp import standalone


def test_fake_site_raises_a_clear_error_for_unregistered_requests(fake_site) -> None:
    fake_site.add("GET", "/api/resource/Role", [])

    with pytest.raises(AssertionError, match="no response for POST /api/resource/Role"):
        standalone._request("POST", "/api/resource/Role", json={"role_name": "X"})

    assert fake_site.calls_to("POST", "/api/resource/Role")[0].json == {"role_name": "X"}


def test_fake_site_matches_params_and_prefers_the_latest_route(fake_site) -> None:
    fake_site.add("GET", "/api/resource/Role", ["default"])
    fake_site.add("GET", "/api/resource/Role", ["filtered"], params={"limit_page_length": 1})
    fake_site.add("GET", "/api/resource/Role", ["override"], params=lambda p: p and "fields" in p)

    assert standalone._request("GET", "/api/resource/Role") == ["default"]
    assert standalone._request(
        "GET", "/api/resource/Role", params={"limit_page_length": 1}
    ) == ["filtered"]
    assert standalone._request("GET", "/api/resource/Role", params={"fields": "[]"}) == [
        "override"
    ]
    assert len(fake_site.calls) == 3


def test_fake_site_returns_copies_so_callers_cannot_mutate_registered_data(fake_site) -> None:
    fake_site.add_doctype("Example", [{"fieldname": "title"}])

    standalone._meta_bundle("Example")["doctype"]["fields"].append({"fieldname": "leaked"})

    fields = standalone._meta_bundle("Example")["doctype"]["fields"]
    assert [row["fieldname"] for row in fields] == ["title"]


def test_fake_site_blocks_real_http_clients(fake_site) -> None:
    with pytest.raises(AssertionError, match="real HTTP client"):
        standalone._client()
