"""Read primitives (paging, counts, search, site info) and minimal submit and cancel."""

from __future__ import annotations

import json

import pytest

from frappe_mcp import standalone

MODIFIED = "2026-10-28 09:00:00.000001"
COUNT = "/api/method/frappe.client.get_count"
SEARCH = "/api/method/frappe.desk.search.search_link"
VERSIONS = "/api/method/frappe.utils.change_log.get_versions"
LOGGED_USER = "/api/method/frappe.auth.get_logged_user"
SUBMIT = "/api/method/frappe.client.submit"
CANCEL = "/api/method/frappe.client.cancel"


@pytest.fixture
def site(fake_site):
    fake_site.add_doctype(
        "Example",
        [
            {"fieldname": "title", "fieldtype": "Data"},
            {"fieldname": "grand_total", "fieldtype": "Currency"},
            {"fieldname": "items", "fieldtype": "Table", "options": "Example Item"},
        ],
    )
    fake_site.add_doctype("Example Item", [{"fieldname": "item_code"}], istable=1)
    return fake_site


def _rows(count: int) -> list[dict]:
    return [{"name": f"EX-{index}"} for index in range(count)]


def _list_params(site, path: str = "/api/resource/Example") -> dict:
    (call,) = site.calls_to("GET", path)
    return call.params


# list_documents


def test_list_documents_pages_with_offset_and_reports_has_more(site) -> None:
    site.add("GET", "/api/resource/Example", _rows(4))

    result = standalone.list_documents("Example", limit=3, offset=6)

    assert result == {"rows": _rows(3), "offset": 6, "limit": 3, "has_more": True}
    params = _list_params(site)
    assert params["limit_start"] == 6
    assert params["limit_page_length"] == 4
    assert params["order_by"] == "modified desc"


def test_list_documents_last_page_has_no_more(site) -> None:
    site.add("GET", "/api/resource/Example", _rows(2))

    result = standalone.list_documents("Example", limit=3)

    assert result == {"rows": _rows(2), "offset": 0, "limit": 3, "has_more": False}


@pytest.mark.parametrize(
    ("order_by", "sent"),
    [
        ("title", "title asc"),
        ("grand_total DESC, name asc", "grand_total desc, name asc"),
        (" creation desc ", "creation desc"),
    ],
)
def test_list_documents_order_by_is_rebuilt_from_fieldnames(site, order_by, sent) -> None:
    site.add("GET", "/api/resource/Example", [])

    standalone.list_documents("Example", order_by=order_by)

    assert _list_params(site)["order_by"] == sent


@pytest.mark.parametrize(
    ("order_by", "message"),
    [
        ("name; drop table x", "order_by must be"),
        ("title desc limit 1", "order_by must be"),
        ("missing desc", "Unknown field for Example: missing"),
        ("items asc", "not a column"),
        ("`title` desc", "order_by must be"),
        ("title, name, modified, creation", "order_by must be"),
        ("", "order_by must be"),
    ],
)
def test_list_documents_order_by_rejects_sql_and_unknown_fields(site, order_by, message) -> None:
    with pytest.raises(ValueError, match=message):
        standalone.list_documents("Example", order_by=order_by)

    assert site.calls_to("GET", "/api/resource/Example") == []


def test_list_documents_forwards_validated_or_filters(site) -> None:
    site.add("GET", "/api/resource/Example", [])
    or_filters = [["title", "like", "%a%"], ["Example Item", "item_code", "=", "X"]]

    standalone.list_documents("Example", filters={"docstatus": 0}, or_filters=or_filters)

    params = _list_params(site)
    assert json.loads(params["or_filters"]) == or_filters
    assert json.loads(params["filters"]) == {"docstatus": 0}


def test_list_documents_validates_or_filters(site) -> None:
    with pytest.raises(ValueError, match="missing"):
        standalone.list_documents("Example", or_filters=[["missing", "=", 1]])
    with pytest.raises(ValueError, match="Unsupported filter operator"):
        standalone.list_documents("Example", or_filters={"title": ["regexp", "x"]})

    assert site.calls_to("GET", "/api/resource/Example") == []


def test_list_documents_leaves_out_or_filters_when_not_given(site) -> None:
    site.add("GET", "/api/resource/Example", [])

    standalone.list_documents("Example")

    assert "or_filters" not in _list_params(site)


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (101, 0), (10, -1), (10, True)])
def test_list_documents_bounds_limit_and_offset(site, limit, offset) -> None:
    with pytest.raises(ValueError):
        standalone.list_documents("Example", limit=limit, offset=offset)


# count_documents


def test_count_documents_validates_and_forwards_filters(site) -> None:
    site.add("GET", COUNT, 42)
    filters = [["Example Item", "item_code", "=", "X"], ["grand_total", ">", 10]]

    result = standalone.count_documents("Example", filters=filters)

    assert result == {"doctype": "Example", "count": 42}
    (call,) = site.calls_to("GET", COUNT)
    assert call.params["doctype"] == "Example"
    assert json.loads(call.params["filters"]) == filters


def test_count_documents_rejects_unknown_fields_and_operators(site) -> None:
    with pytest.raises(ValueError, match="missing"):
        standalone.count_documents("Example", filters={"missing": 1})
    with pytest.raises(ValueError, match="Unsupported filter operator"):
        standalone.count_documents("Example", filters=[["title", "regexp", "x"]])

    assert site.calls_to("GET", COUNT) == []


def test_count_documents_raises_frappe_errors(site) -> None:
    site.add("GET", COUNT, standalone.FrappeError("Frappe HTTP 403: denied", "PermissionError"))

    with pytest.raises(standalone.FrappeError, match="403"):
        standalone.count_documents("Example")


# list_doctypes


def test_list_doctypes_search_and_app_produce_filters(fake_site) -> None:
    fake_site.add(
        "GET",
        "/api/resource/Module%20Def",
        [{"name": "Lab"}, {"name": "Lab Setup"}],
        params={"filters": json.dumps({"app_name": "lab_app"}, separators=(",", ":"))},
    )
    fake_site.add("GET", "/api/resource/DocType", [{"name": "Lab Sample"}])

    result = standalone.list_doctypes(search="sample", app="lab_app", include_child_tables=False)

    params = _list_params(fake_site, "/api/resource/DocType")
    assert json.loads(params["filters"]) == [
        ["module", "in", ["Lab", "Lab Setup"]],
        ["istable", "=", 0],
        ["name", "like", "%sample%"],
    ]
    assert json.loads(params["fields"]) == [
        "name",
        "module",
        "custom",
        "issingle",
        "istable",
        "is_submittable",
    ]
    assert params["limit_start"] == 0
    assert params["limit_page_length"] == 101
    assert result == {"rows": [{"name": "Lab Sample"}], "offset": 0, "limit": 100,
                      "has_more": False}


def test_list_doctypes_pages_with_has_more(fake_site) -> None:
    fake_site.add("GET", "/api/resource/DocType", _rows(3))

    result = standalone.list_doctypes(limit=2, offset=4)

    assert result == {"rows": _rows(2), "offset": 4, "limit": 2, "has_more": True}
    params = _list_params(fake_site, "/api/resource/DocType")
    assert params["limit_start"] == 4
    assert json.loads(params["filters"]) == {}


def test_list_doctypes_app_without_modules_returns_an_empty_page(fake_site) -> None:
    fake_site.add("GET", "/api/resource/Module%20Def", [])

    result = standalone.list_doctypes(app="ghost_app")

    assert result == {"rows": [], "offset": 0, "limit": 100, "has_more": False}
    assert fake_site.calls_to("GET", "/api/resource/DocType") == []


@pytest.mark.parametrize("search", ["", "50%", "a'b", "x" * 141])
def test_list_doctypes_rejects_unsafe_search_text(fake_site, search) -> None:
    with pytest.raises(ValueError, match="search"):
        standalone.list_doctypes(search=search)

    assert fake_site.calls == []


# search_records


def test_search_records_calls_search_link_and_trims_results(site) -> None:
    site.add(
        "GET",
        SEARCH,
        [
            {"value": "EX-1", "label": "First", "description": "Lab, 10"},
            {"value": "EX-2", "description": "y" * 600, "extra": "dropped"},
        ],
    )

    result = standalone.search_records(
        "Example", "fir", filters={"docstatus": 0}, limit=5
    )

    assert result == {
        "doctype": "Example",
        "results": [
            {"value": "EX-1", "label": "First", "description": "Lab, 10"},
            {"value": "EX-2", "description": "y" * 500},
        ],
    }
    (call,) = site.calls_to("GET", SEARCH)
    assert call.params == {
        "doctype": "Example",
        "txt": "fir",
        "page_length": 5,
        "filters": json.dumps({"docstatus": 0}, separators=(",", ":")),
    }


def test_search_records_never_sends_a_query_method(site) -> None:
    site.add("GET", SEARCH, [])

    standalone.search_records("Example", "x")

    (call,) = site.calls_to("GET", SEARCH)
    assert set(call.params) == {"doctype", "txt", "page_length"}


@pytest.mark.parametrize("limit", [0, 51])
def test_search_records_caps_its_limit(site, limit) -> None:
    with pytest.raises(ValueError, match="limit must be between 1 and 50"):
        standalone.search_records("Example", "x", limit=limit)

    assert site.calls_to("GET", SEARCH) == []


def test_search_records_validates_filters_and_text(site) -> None:
    with pytest.raises(ValueError, match="missing"):
        standalone.search_records("Example", "x", filters={"missing": 1})
    with pytest.raises(ValueError, match="text"):
        standalone.search_records("Example", "x" * 141)

    assert site.calls_to("GET", SEARCH) == []


def test_search_records_bounds_a_long_result_list(site) -> None:
    site.add("GET", SEARCH, [{"value": f"EX-{index}"} for index in range(80)])

    result = standalone.search_records("Example", "", limit=3)

    assert [row["value"] for row in result["results"]] == ["EX-0", "EX-1", "EX-2"]


# get_site_info


def _versions() -> dict:
    return {
        "frappe": {
            "title": "Frappe Framework",
            "description": "Full stack web framework",
            "branch": "version-16",
            "branch_version": "16.x.x-develop (abc1234)",
            "color": None,
            "logo": "/assets/frappe/logo.svg",
            "version": "16.33.1",
        },
        "lab_app": {"title": "Lab App", "description": "Lab", "branch": "main",
                    "version": "0.0.1"},
        "other_app": {"title": "Other", "version": "1.2.0"},
    }


@pytest.fixture
def info_site(fake_site, monkeypatch):
    monkeypatch.delenv("FRAPPE_APPS_PATH", raising=False)
    fake_site.add("GET", LOGGED_USER, "dev@example.localhost")
    fake_site.add("GET", VERSIONS, _versions())
    return fake_site


def test_get_site_info_trims_versions(info_site) -> None:
    result = standalone.get_site_info()

    assert result["user"] == "dev@example.localhost"
    assert result["frappe_major_version"] == 16
    assert result["apps"] == [
        {"app": "frappe", "title": "Frappe Framework", "version": "16.33.1"},
        {"app": "lab_app", "title": "Lab App", "version": "0.0.1"},
        {"app": "other_app", "title": "Other", "version": "1.2.0"},
    ]
    assert result["apps_path"] == {"configured": False}
    assert "developer_mode" not in result
    assert any("developer mode" in note for note in result["notes"])
    assert "branch" not in json.dumps(result["apps"])


def test_get_site_info_lists_site_apps_in_the_local_checkout(info_site, tmp_path, monkeypatch):
    (tmp_path / "lab_app" / "lab_app").mkdir(parents=True)
    (tmp_path / "unrelated_app" / "unrelated_app").mkdir(parents=True)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    (outside / "other_app").mkdir(parents=True)
    (tmp_path / "other_app").symlink_to(outside / "other_app")
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path))

    result = standalone.get_site_info()

    assert result["apps_path"] == {
        "configured": True,
        "site_apps_found": ["lab_app"],
        "site_apps_missing": ["frappe", "other_app"],
    }
    assert str(tmp_path) not in json.dumps(result)
    assert str(outside) not in json.dumps(result)


def test_get_site_info_reports_a_missing_apps_path(info_site, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path / "missing"))

    result = standalone.get_site_info()

    assert result["apps_path"] == {"configured": True, "exists": False}
    assert str(tmp_path) not in json.dumps(result)


def test_get_site_info_handles_an_unparsable_frappe_version(fake_site, monkeypatch) -> None:
    monkeypatch.delenv("FRAPPE_APPS_PATH", raising=False)
    fake_site.add("GET", LOGGED_USER, "Administrator")
    fake_site.add("GET", VERSIONS, {"frappe": {"title": "Frappe", "version": "develop"}})

    assert standalone.get_site_info()["frappe_major_version"] is None


# submit_document and cancel_document


def _doc(docstatus: int) -> dict:
    return {
        "doctype": "Lab Test",
        "name": "LT-1",
        "title": "Draft",
        "docstatus": docstatus,
        "modified": MODIFIED,
        "results": [{"name": "row1", "doctype": "Lab Test Result", "idx": 1}],
    }


@pytest.fixture
def lab(fake_site):
    def add(*, submittable: int = 1, workflow: str | None = None, docstatus: int = 0):
        fake_site.add_doctype(
            "Lab Test",
            [{"fieldname": "title", "fieldtype": "Data"}],
            is_submittable=submittable,
            __workflow_docs=[
                {"doctype": "Workflow", "name": workflow, "is_active": 1},
                {"doctype": "Workflow State", "name": "Draft"},
            ]
            if workflow
            else [],
        )
        fake_site.add("GET", "/api/resource/Lab%20Test/LT-1", _doc(docstatus))
        return fake_site

    return add


def test_submit_preview_shows_state_and_needs_confirmation(lab) -> None:
    site = lab()

    preview = standalone.submit_document("Lab Test", "LT-1")

    assert preview["preview"] is True
    assert preview["requires_confirmation"] is True
    assert preview["docstatus"] == 0
    assert preview["modified"] == MODIFIED
    assert preview["is_submittable"] is True
    assert preview["workflow"] is None
    assert any("on_submit" in warning for warning in preview["warnings"])
    assert site.calls_to("POST") == []


def test_submit_refuses_a_doctype_that_is_not_submittable(lab) -> None:
    site = lab(submittable=0)

    for confirm in (False, True):
        with pytest.raises(ValueError, match="not submittable"):
            standalone.submit_document(
                "Lab Test", "LT-1", expected_modified=MODIFIED, confirm=confirm
            )

    assert site.calls_to("POST") == []


def test_submit_refuses_a_doctype_with_an_active_workflow(lab) -> None:
    site = lab(workflow="Lab Approval")

    for confirm in (False, True):
        with pytest.raises(ValueError, match="Workflow Lab Approval"):
            standalone.submit_document(
                "Lab Test", "LT-1", expected_modified=MODIFIED, confirm=confirm
            )

    assert site.calls_to("POST") == []


def test_submit_reads_the_workflow_fresh_not_from_the_cache(lab, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_META_TTL", "300")
    site = lab()
    site.add("GET", "/api/resource/Lab%20Test", [])
    standalone.list_documents("Lab Test")
    site.add_doctype(
        "Lab Test",
        [{"fieldname": "title", "fieldtype": "Data"}],
        is_submittable=1,
        __workflow_docs=[{"doctype": "Workflow", "name": "Late Workflow"}],
    )

    with pytest.raises(ValueError, match="Workflow Late Workflow"):
        standalone.submit_document("Lab Test", "LT-1")


def test_submit_refuses_a_document_that_is_not_a_draft(lab) -> None:
    lab(docstatus=1)

    with pytest.raises(ValueError, match="docstatus 1"):
        standalone.submit_document("Lab Test", "LT-1")


def test_submit_confirm_posts_the_full_document_with_modified(lab) -> None:
    site = lab()
    site.add("POST", SUBMIT, {**_doc(1), "modified": "2026-10-28 09:05:00"})

    result = standalone.submit_document(
        "Lab Test", "LT-1", expected_modified=MODIFIED, confirm=True
    )

    assert result == {
        "submitted": True,
        "doctype": "Lab Test",
        "name": "LT-1",
        "docstatus": 1,
        "modified": "2026-10-28 09:05:00",
    }
    (call,) = site.calls_to("POST", SUBMIT)
    assert call.json == {"doc": _doc(0)}
    assert call.json["doc"]["modified"] == MODIFIED


def test_submit_confirm_refuses_a_stale_token(lab) -> None:
    site = lab()

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.submit_document(
            "Lab Test", "LT-1", expected_modified="2026-01-01 00:00:00", confirm=True
        )
    with pytest.raises(ValueError, match="changed since preview"):
        standalone.submit_document("Lab Test", "LT-1", confirm=True)

    assert site.calls_to("POST") == []


def test_submit_maps_a_timestamp_mismatch_to_the_stale_message(lab) -> None:
    site = lab()
    site.add(
        "POST",
        SUBMIT,
        standalone.FrappeError("Frappe HTTP 417: modified", "TimestampMismatchError"),
    )

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.submit_document(
            "Lab Test", "LT-1", expected_modified=MODIFIED, confirm=True
        )


def test_cancel_requires_docstatus_one(lab) -> None:
    for docstatus in (0, 2):
        site = lab(docstatus=docstatus)
        for confirm in (False, True):
            with pytest.raises(ValueError, match=f"docstatus {docstatus}"):
                standalone.cancel_document(
                    "Lab Test", "LT-1", expected_modified=MODIFIED, confirm=confirm
                )
        assert site.calls_to("POST") == []


def test_cancel_preview_warns_about_on_cancel(lab) -> None:
    lab(docstatus=1)

    preview = standalone.cancel_document("Lab Test", "LT-1")

    assert preview["preview"] is True
    assert preview["docstatus"] == 1
    assert preview["modified"] == MODIFIED
    assert any("on_cancel" in warning for warning in preview["warnings"])


def test_cancel_refuses_a_doctype_with_an_active_workflow(lab) -> None:
    lab(docstatus=1, workflow="Lab Approval")

    with pytest.raises(ValueError, match="Workflow Lab Approval"):
        standalone.cancel_document("Lab Test", "LT-1")


def test_cancel_confirm_checks_modified_then_calls_frappe_cancel(lab) -> None:
    site = lab(docstatus=1)
    site.add("POST", CANCEL, {**_doc(2), "modified": "2026-10-28 09:10:00"})

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.cancel_document("Lab Test", "LT-1", expected_modified="old", confirm=True)
    assert site.calls_to("POST", CANCEL) == []

    result = standalone.cancel_document(
        "Lab Test", "LT-1", expected_modified=MODIFIED, confirm=True
    )

    assert result == {
        "cancelled": True,
        "doctype": "Lab Test",
        "name": "LT-1",
        "docstatus": 2,
        "modified": "2026-10-28 09:10:00",
    }
    (call,) = site.calls_to("POST", CANCEL)
    assert call.json == {"doctype": "Lab Test", "name": "LT-1"}


def test_compact_schema_names_the_active_workflow(lab) -> None:
    lab(workflow="Lab Approval")

    assert standalone.get_doctype_schema("Lab Test")["workflow"] == "Lab Approval"
    assert standalone.get_doctype_schema("Lab Test", detail="full")["workflow"] == "Lab Approval"


def test_compact_schema_leaves_out_workflow_when_none(lab) -> None:
    lab()

    assert "workflow" not in standalone.get_doctype_schema("Lab Test")
