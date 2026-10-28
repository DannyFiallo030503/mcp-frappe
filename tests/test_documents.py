"""Record tools: read validation, filter shapes, write validation, and atomic updates."""

from __future__ import annotations

import json

import pytest

from frappe_mcp import standalone

MODIFIED = "2026-10-19 12:00:00.000001"


@pytest.fixture
def site(fake_site):
    fake_site.add_doctype(
        "Example",
        [
            {"fieldname": "title", "fieldtype": "Data"},
            {"fieldname": "status", "fieldtype": "Select", "read_only": 1},
            {"fieldname": "grand_total", "fieldtype": "Currency"},
            {"fieldname": "items", "fieldtype": "Table", "options": "Example Item"},
            {"fieldname": "tags", "fieldtype": "Table MultiSelect", "options": "Example Tag"},
        ],
    )
    fake_site.add_doctype("Example Item", [{"fieldname": "item_code"}], istable=1)
    fake_site.add_doctype("Example Tag", [{"fieldname": "tag"}], istable=1)
    fake_site.add("GET", "/api/resource/Example", [{"name": "EX-1"}])
    fake_site.add(
        "GET",
        "/api/resource/Example/EX-1",
        {
            "doctype": "Example",
            "name": "EX-1",
            "title": "First",
            "owner": "dev@example.localhost",
            "docstatus": 1,
            "modified": MODIFIED,
        },
    )
    return fake_site


def _list_params(site) -> dict:
    (call,) = site.calls_to("GET", "/api/resource/Example")
    return call.params


def test_standard_columns_can_be_filtered_and_selected(site) -> None:
    filters = {"docstatus": 1, "creation": [">", "2026-10-01"]}

    standalone.list_documents(
        "Example", filters=filters, fields=["name", "modified", "docstatus", "owner"]
    )

    params = _list_params(site)
    assert json.loads(params["filters"]) == filters
    assert json.loads(params["fields"]) == ["name", "modified", "docstatus", "owner"]


def test_three_element_list_filters_are_accepted(site) -> None:
    filters = [["title", "like", "%First%"], ["grand_total", "between", [100, 200]]]

    standalone.list_documents("Example", filters=filters)

    assert json.loads(_list_params(site)["filters"]) == filters


def test_four_element_list_filters_accept_child_tables_and_the_doctype_itself(site) -> None:
    filters = [
        ["Example Item", "item_code", "=", "X"],
        ["Example Item", "parent", "is", "set"],
        ["Example Tag", "tag", "in", ["a", "b"]],
        ["Example", "modified", "timespan", "this week"],
    ]

    standalone.list_documents("Example", filters=filters)

    assert json.loads(_list_params(site)["filters"]) == filters


def test_child_columns_are_readable_only_on_child_doctypes(site) -> None:
    with pytest.raises(ValueError, match="parent"):
        standalone.list_documents("Example", filters={"parent": "X"})


def test_four_element_filter_rejects_a_doctype_that_is_not_a_child_table(site) -> None:
    with pytest.raises(ValueError, match="ToDo is not a child table of Example"):
        standalone.list_documents("Example", filters=[["ToDo", "status", "=", "Open"]])

    assert site.calls_to("GET", "/api/resource/Example") == []
    assert site.calls_to("GET", standalone._resource("DocType", "ToDo")) == []


def test_four_element_filter_rejects_unknown_child_field(site) -> None:
    with pytest.raises(ValueError, match="Example Item.*missing"):
        standalone.list_documents("Example", filters=[["Example Item", "missing", "=", 1]])


def test_unknown_operator_is_rejected_with_the_allowed_list(site) -> None:
    for filters in (
        {"title": ["regexp", ".*"]},
        [["title", "regexp", ".*"]],
        [["Example Item", "item_code", "descendants of", "X"]],
    ):
        with pytest.raises(ValueError, match="Unsupported filter operator.*not like.*timespan"):
            standalone.list_documents("Example", filters=filters)

    assert site.calls_to("GET", "/api/resource/Example") == []


def test_operators_are_matched_case_insensitively(site) -> None:
    standalone.list_documents("Example", filters=[["title", "NOT LIKE", "x%"]])


@pytest.mark.parametrize(
    "filters",
    [
        {"title": ["="]},
        {"title": {"op": "="}},
        [["title", "Open"]],
        [["Example", "title", "=", "x", "extra"]],
        ["title", "=", "Open"],
        "title = 'Open'",
    ],
)
def test_malformed_filters_are_rejected(site, filters) -> None:
    with pytest.raises(ValueError, match="[Ff]ilter"):
        standalone.list_documents("Example", filters=filters)


def test_unknown_fields_are_rejected_for_reads(site) -> None:
    with pytest.raises(ValueError, match="missing"):
        standalone.list_documents("Example", fields=["name", "missing"])
    with pytest.raises(ValueError, match="missing"):
        standalone.list_documents("Example", filters={"missing": 1})
    with pytest.raises(ValueError, match="missing"):
        standalone.get_document("Example", "EX-1", fields=["missing"])


def test_get_document_can_select_standard_columns(site) -> None:
    result = standalone.get_document("Example", "EX-1", fields=["modified", "owner"])

    assert result["modified"] == MODIFIED
    assert result["owner"] == "dev@example.localhost"
    assert "title" not in result


@pytest.mark.parametrize("field", ["docstatus", "name", "modified", "owner", "parent"])
def test_writes_reject_standard_columns(site, field) -> None:
    create_error = "leave out name" if field == "name" else f"Field cannot be written: {field}"
    with pytest.raises(ValueError, match=create_error):
        standalone.create_document("Example", {field: "x"})
    with pytest.raises(ValueError, match=f"Field cannot be written: {field}"):
        standalone.update_document("Example", "EX-1", {field: "x"})


def test_writes_reject_read_only_fields(site) -> None:
    with pytest.raises(ValueError, match="Read-only field cannot be written: status"):
        standalone.update_document("Example", "EX-1", {"status": "Open"})


def test_create_document_passes_name_as_newname_for_prompt_named_doctypes(fake_site) -> None:
    fake_site.add_doctype("Prompted", [{"fieldname": "title"}], autoname="Prompt")
    fake_site.add("POST", "/api/resource/Prompted", lambda call: call.json["data"])

    preview = standalone.create_document("Prompted", {"name": "P-1", "title": "x"})
    created = standalone.create_document(
        "Prompted", {"name": "P-1", "title": "x"}, confirm=True
    )

    assert preview["fields"] == ["name", "title"]
    assert created == {"doctype": "Prompted", "__newname": "P-1", "title": "x"}


def test_create_document_rejects_name_when_frappe_names_the_record(site) -> None:
    with pytest.raises(ValueError, match="autoname"):
        standalone.create_document("Example", {"name": "EX-9", "title": "x"})


def test_update_sends_expected_modified_for_frappes_atomic_check(site) -> None:
    site.add("PUT", "/api/resource/Example/EX-1", {"name": "EX-1"})

    standalone.update_document(
        "Example", "EX-1", {"title": "Second"}, expected_modified=MODIFIED, confirm=True
    )

    (call,) = site.calls_to("PUT", "/api/resource/Example/EX-1")
    assert call.json == {"data": {"title": "Second", "modified": MODIFIED}}


def test_timestamp_mismatch_from_frappe_maps_to_the_stale_message(site) -> None:
    site.add(
        "PUT",
        "/api/resource/Example/EX-1",
        standalone.FrappeError(
            "Frappe HTTP 417: modified after you have opened it",
            exc_type="TimestampMismatchError",
        ),
    )

    with pytest.raises(ValueError, match="Document changed since preview"):
        standalone.update_document(
            "Example", "EX-1", {"title": "Second"}, expected_modified=MODIFIED, confirm=True
        )


def test_other_update_errors_are_not_reported_as_stale(site) -> None:
    site.add(
        "PUT",
        "/api/resource/Example/EX-1",
        standalone.FrappeError("Frappe HTTP 417: invalid", exc_type="ValidationError"),
    )

    with pytest.raises(standalone.FrappeError, match="invalid"):
        standalone.update_document(
            "Example", "EX-1", {"title": "Second"}, expected_modified=MODIFIED, confirm=True
        )
