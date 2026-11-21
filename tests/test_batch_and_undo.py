"""Stage 20: `create_documents`, the change journal, `list_mcp_changes`, and `undo_mcp_change`."""

from __future__ import annotations

import copy
import itertools
import json
import os
import stat

import pytest

from frappe_mcp import standalone

MODIFIED = "2026-11-20 10:00:00.000001"
LATER = "2026-11-20 11:00:00.000001"
COUNT_PATH = "/api/method/frappe.client.get_count"
EXAMPLE = standalone._resource("Example")
CUSTOM_FIELDS = standalone._resource("Custom Field")


@pytest.fixture
def site(fake_site):
    fake_site.add_doctype(
        "Example",
        [
            {"fieldname": "title", "fieldtype": "Data"},
            {"fieldname": "status", "fieldtype": "Select", "read_only": 1},
            {"fieldname": "script", "fieldtype": "Code"},
            {"fieldname": "secret", "fieldtype": "Password"},
            {"fieldname": "items", "fieldtype": "Table", "options": "Example Item"},
        ],
    )
    fake_site.add_doctype(
        "Example Item",
        [{"fieldname": "item_code"}, {"fieldname": "source", "read_only": 1}],
        istable=1,
    )
    fake_site.add_doctype("Named", [{"fieldname": "title", "fieldtype": "Data"}], autoname="Prompt")
    return fake_site


@pytest.fixture
def records_on(monkeypatch):
    monkeypatch.setenv("FRAPPE_MCP_JOURNAL_RECORDS", "YES")


def _confirm_batch(doctype: str, rows: list[dict]) -> dict:
    preview = standalone.create_documents(doctype, rows)
    return standalone.create_documents(
        doctype, rows, expected_preview=preview["preview_id"], confirm=True
    )


def _undo(entry_id: str) -> dict:
    preview = standalone.undo_mcp_change(entry_id)
    return standalone.undo_mcp_change(
        entry_id, expected_preview=preview["preview_id"], confirm=True
    )


def _registered(name: str):
    """The tool as an MCP host calls it, through `_tool()`'s wrapper."""
    return standalone.server._tool_manager._tools[name].fn


# create_documents


def test_batch_preview_counts_rows_and_sends_nothing(site) -> None:
    rows = [{"title": "A"}, {"title": "B", "items": [{"item_code": "X", "source": "S"}]}]

    preview = standalone.create_documents("Example", rows)

    assert preview["preview"] is True and preview["requires_confirmation"] is True
    assert preview["count"] == 2
    assert preview["fields"] == ["items", "title"]
    assert preview["problems"] == []
    assert len(preview["preview_id"]) == 64
    assert not site.calls_to("POST")


def test_batch_lists_problems_per_row_and_cannot_be_confirmed(site) -> None:
    rows = [
        {"title": "fine"},
        {"nope": 1},
        {"status": "Open"},
        {"script": "print(1)"},
        {"items": [{"item_code": "X", "parent": "EX-1"}]},
        {"items": [{"unknown": 1}]},
        {"items": "not a list"},
        {"name": "EX-9", "title": "x"},
        "not an object",
    ]

    preview = standalone.create_documents("Example", rows)

    assert [problem["row"] for problem in preview["problems"]] == [2, 3, 4, 5, 6, 7, 8, 9]
    text = json.dumps(preview["problems"])
    for expected in (
        "nope",
        "Read-only",
        "Code field",
        "parent",
        "unknown",
        "child table",
        "leave out name",
        "non-empty object",
    ):
        assert expected in text
    assert preview["preview_id"] is None
    with pytest.raises(ValueError, match="8 rows have problems"):
        standalone.create_documents("Example", rows, expected_preview="x", confirm=True)
    assert not site.calls_to("POST")


def test_batch_flags_duplicate_prompted_names(site) -> None:
    preview = standalone.create_documents("Named", [{"name": "N-1"}, {"name": "N-1"}])

    assert preview["problems"] == [{"row": 2, "problem": "same name as row 1"}]


def test_batch_inserts_through_insert_many_in_one_request(site) -> None:
    site.add("POST", standalone._INSERT_MANY, ["N-1", "N-2"])

    result = _confirm_batch("Named", [{"name": "N-1", "title": "a"}, {"name": "N-2"}])

    assert result == {"created": 2, "doctype": "Named", "names": ["N-1", "N-2"]}
    (call,) = site.calls_to("POST")
    assert call.json == {
        "docs": [
            {"doctype": "Named", "title": "a", "__newname": "N-1"},
            {"doctype": "Named", "__newname": "N-2"},
        ]
    }


def test_batch_refuses_rows_changed_since_the_preview(site) -> None:
    preview = standalone.create_documents("Example", [{"title": "A"}])

    with pytest.raises(ValueError, match="fresh preview"):
        standalone.create_documents(
            "Example", [{"title": "B"}], expected_preview=preview["preview_id"], confirm=True
        )
    with pytest.raises(ValueError, match="fresh preview"):
        standalone.create_documents("Example", [{"title": "A"}], confirm=True)
    assert not site.calls_to("POST")


@pytest.mark.parametrize("count", [0, 101])
def test_batch_size_is_limited(site, count) -> None:
    with pytest.raises(ValueError, match="1 to 100"):
        standalone.create_documents("Example", [{"title": "x"}] * count)


def test_batch_of_one_hundred_is_allowed(site) -> None:
    assert standalone.create_documents("Example", [{"title": "x"}] * 100)["count"] == 100


def test_a_failing_row_leaves_nothing_behind(site) -> None:
    site.add(
        "POST",
        standalone._INSERT_MANY,
        standalone.FrappeError("Row 3: Title is required", "MandatoryError", status=417),
    )

    with pytest.raises(standalone.FrappeError, match="Nothing was created") as raised:
        _confirm_batch("Example", [{"title": "A"}, {"title": "B"}, {"title": "C"}])

    assert raised.value.exc_type == "MandatoryError"
    # One request, which Frappe rolled back; nothing to clean up afterwards.
    assert [call.method for call in site.calls if call.method != "GET"] == ["POST"]
    assert standalone.list_mcp_changes()["rows"] == []


def test_batch_refuses_schema_doctypes(site) -> None:
    with pytest.raises(ValueError, match="create_doctype"):
        standalone.create_documents("DocType", [{"name": "X"}])


def test_create_document_checks_child_rows_too(site) -> None:
    with pytest.raises(ValueError, match="items row 1: field cannot be written: parent"):
        standalone.create_document("Example", {"items": [{"item_code": "X", "parent": "Y"}]})


# The journal: what goes in, where, and its bound


def test_record_writes_are_not_journaled_by_default(site) -> None:
    site.add("POST", EXAMPLE, {"name": "EX-1", "modified": MODIFIED})

    result = standalone.create_document("Example", {"title": "A"}, confirm=True)

    assert "mcp_change" not in result
    assert standalone.list_mcp_changes()["rows"] == []
    assert standalone.list_mcp_changes()["records_journaled"] is False


def test_schema_writes_are_journaled_without_the_opt_in(site) -> None:
    site.add("POST", CUSTOM_FIELDS, {"name": "Example-x", "modified": MODIFIED})

    result = standalone.create_custom_field(
        "Example", {"fieldname": "x", "fieldtype": "Data"}, confirm=True
    )

    (row,) = standalone.list_mcp_changes()["rows"]
    assert row["id"] == result["mcp_change"]
    assert row["tool"] == "create_custom_field"
    assert row["target"] == {"doctype": "Example", "fieldname": "x", "custom_field": "Example-x"}
    assert row["undoable"] is True


def test_journal_keeps_the_newest_two_hundred_entries() -> None:
    for index in range(205):
        standalone._journal({}, "set_field_property", {"n": index}, undo=None)

    listed = standalone.list_mcp_changes(limit=50)
    assert listed["total"] == 200
    assert listed["rows"][0]["target"] == {"n": 204}
    assert len(listed["rows"]) == 50
    assert standalone._journal_load()[0]["target"] == {"n": 5}


@pytest.mark.parametrize("limit", [0, 51])
def test_list_limit_is_bounded(limit) -> None:
    with pytest.raises(ValueError, match="between 1 and 50"):
        standalone.list_mcp_changes(limit=limit)


def test_long_name_lists_are_cut_in_the_listing() -> None:
    standalone._journal({}, "create_documents", {"names": [f"N-{i}" for i in range(30)]})

    (row,) = standalone.list_mcp_changes()["rows"]
    assert len(row["target"]["names"]) == 10 and row["target"]["more_names"] == 20


def test_journal_file_lives_under_the_apps_path_and_holds_no_secrets(
    site, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path))
    monkeypatch.setenv("FRAPPE_SITE", "dev.localhost")
    monkeypatch.setenv("FRAPPE_API_SECRET", "s3cret-value")
    monkeypatch.setenv("FRAPPE_PASSWORD", "pass-value")
    site.add("POST", EXAMPLE, {"name": "EX-1", "modified": MODIFIED, "title": "Customer data"})
    site.add("POST", CUSTOM_FIELDS, {"name": "Example-x", "modified": MODIFIED})

    standalone.create_document("Example", {"title": "Customer data"}, confirm=True)
    standalone.create_custom_field("Example", {"fieldname": "x", "fieldtype": "Data"}, confirm=True)

    path = tmp_path / ".mcp-frappe-journal" / "dev.localhost.json"
    text = path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    for private in ("s3cret-value", "pass-value", "Customer data", "EX-1", str(tmp_path)):
        assert private not in text
    assert standalone.list_mcp_changes()["storage"] == "file"
    assert standalone._journal_memory == []


def test_site_label_names_the_journal_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path))
    monkeypatch.setenv("FRAPPE_SITE_LABEL", "lab-dev")

    standalone._journal({}, "run_bench", {"command": "migrate"})

    assert (tmp_path / ".mcp-frappe-journal" / "lab-dev.json").is_file()


def test_journal_failure_never_fails_the_write(site, tmp_path, monkeypatch) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    apps = tmp_path / "apps"
    apps.mkdir()
    (apps / ".mcp-frappe-journal").symlink_to(elsewhere)
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(apps))
    site.add("POST", CUSTOM_FIELDS, {"name": "Example-x", "modified": MODIFIED})

    result = standalone.create_custom_field(
        "Example", {"fieldname": "x", "fieldtype": "Data"}, confirm=True
    )

    assert result["name"] == "Example-x"
    assert "symlink" in result["journal_error"] and "mcp_change" not in result
    assert not list(elsewhere.iterdir())


def test_a_missing_apps_path_is_reported_without_the_path(site, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path / "gone"))
    site.add("POST", CUSTOM_FIELDS, {"name": "Example-x", "modified": MODIFIED})

    result = standalone.create_custom_field(
        "Example", {"fieldname": "x", "fieldtype": "Data"}, confirm=True
    )

    assert "does not exist" in result["journal_error"]
    assert str(tmp_path) not in result["journal_error"]
    with pytest.raises(ValueError, match="does not exist") as raised:
        standalone.list_mcp_changes()
    assert str(tmp_path) not in str(raised.value)


def test_an_unreadable_journal_file_is_reported(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path))
    monkeypatch.setenv("FRAPPE_SITE", "dev.localhost")
    folder = tmp_path / ".mcp-frappe-journal"
    folder.mkdir()
    (folder / "dev.localhost.json").write_text("{not json")

    with pytest.raises(ValueError, match="unreadable; move it aside"):
        standalone.list_mcp_changes()


def test_other_writes_are_journaled_by_the_tool_wrapper_without_an_inverse(site) -> None:
    path = standalone._resource("Custom Field", "Example-x")
    site.add("GET", path, {"name": "Example-x", "modified": MODIFIED})
    site.add("DELETE", path, None)

    result = _registered("delete_document")(
        doctype="Custom Field", name="Example-x", expected_modified=MODIFIED, confirm=True
    )

    (row,) = standalone.list_mcp_changes()["rows"]
    assert row["id"] == result["mcp_change"]
    assert row["target"] == {"doctype": "Custom Field", "name": "Example-x"}
    assert row["undoable"] is False and "can't be restored" in row["no_undo"]
    with pytest.raises(ValueError, match="no safe inverse: a deleted record"):
        standalone.undo_mcp_change(row["id"])


def test_previews_and_record_deletes_are_not_journaled_by_the_wrapper(site) -> None:
    path = standalone._resource("Example", "EX-1")
    site.add("GET", path, {"name": "EX-1", "modified": MODIFIED})
    site.add("DELETE", path, None)

    _registered("delete_document")(doctype="Example", name="EX-1")
    _registered("delete_document")(
        doctype="Example", name="EX-1", expected_modified=MODIFIED, confirm=True
    )

    assert standalone.list_mcp_changes()["total"] == 0


def test_the_wrapper_journals_record_deletes_with_the_opt_in(site, records_on) -> None:
    path = standalone._resource("Example", "EX-1")
    site.add("GET", path, {"name": "EX-1", "modified": MODIFIED})
    site.add("DELETE", path, None)

    _registered("delete_document")(
        doctype="Example", name="EX-1", expected_modified=MODIFIED, confirm=True
    )

    (row,) = standalone.list_mcp_changes()["rows"]
    assert row["target"] == {"doctype": "Example", "name": "EX-1"}


# Undo: one inverse per kind of entry


def _rows_route(site, doctype: str, rows: list[dict]) -> None:
    site.add(
        "GET",
        standalone._resource(doctype),
        rows,
        params={"fields": '["name","modified","docstatus"]'},
    )


def test_undo_a_batch_deletes_its_records_in_reverse_order(site, records_on) -> None:
    site.add("POST", standalone._INSERT_MANY, ["EX-1", "EX-2"])
    rows = [
        {"name": "EX-1", "modified": MODIFIED, "docstatus": 0},
        {"name": "EX-2", "modified": LATER, "docstatus": 0},
    ]
    _rows_route(site, "Example", rows)
    created = _confirm_batch("Example", [{"title": "A"}, {"title": "B"}])
    for row in rows:
        site.add("GET", standalone._resource("Example", row["name"]), row)
        site.add("DELETE", standalone._resource("Example", row["name"]), None)

    preview = standalone.undo_mcp_change(created["mcp_change"])
    result = standalone.undo_mcp_change(
        created["mcp_change"], expected_preview=preview["preview_id"], confirm=True
    )

    assert preview["tool"] == "delete_document"
    assert preview["inverse"] == {
        "action": "delete_document",
        "doctype": "Example",
        "names": ["EX-2", "EX-1"],
    }
    assert result["result"] == {"deleted": ["EX-2", "EX-1"], "doctype": "Example"}
    assert [call.path for call in site.calls_to("DELETE")] == [
        standalone._resource("Example", "EX-2"),
        standalone._resource("Example", "EX-1"),
    ]
    (row,) = standalone.list_mcp_changes()["rows"]
    assert row["undoable"] is False and row["undone_at"]
    with pytest.raises(ValueError, match="already undone"):
        standalone.undo_mcp_change(created["mcp_change"])


def test_undo_is_refused_when_a_record_changed_afterwards(site, records_on) -> None:
    site.add("POST", EXAMPLE, {"name": "EX-1", "modified": MODIFIED})
    created = standalone.create_document("Example", {"title": "A"}, confirm=True)
    _rows_route(site, "Example", [{"name": "EX-1", "modified": LATER, "docstatus": 0}])

    with pytest.raises(ValueError, match="changed after this MCP change"):
        standalone.undo_mcp_change(created["mcp_change"])
    assert not site.calls_to("DELETE")


def test_undo_is_refused_for_a_submitted_record(site, records_on) -> None:
    site.add("POST", EXAMPLE, {"name": "EX-1", "modified": MODIFIED})
    created = standalone.create_document("Example", {"title": "A"}, confirm=True)
    _rows_route(site, "Example", [{"name": "EX-1", "modified": MODIFIED, "docstatus": 1}])

    with pytest.raises(ValueError, match="no safe inverse"):
        standalone.undo_mcp_change(created["mcp_change"])


def test_undo_is_refused_for_a_record_already_gone(site, records_on) -> None:
    site.add("POST", EXAMPLE, {"name": "EX-1", "modified": MODIFIED})
    created = standalone.create_document("Example", {"title": "A"}, confirm=True)
    _rows_route(site, "Example", [])

    with pytest.raises(ValueError, match="no longer exists"):
        standalone.undo_mcp_change(created["mcp_change"])


def test_a_stopped_batch_undo_keeps_the_rest_for_another_try(site, records_on) -> None:
    site.add("POST", standalone._INSERT_MANY, ["EX-1", "EX-2"])
    rows = [{"name": name, "modified": MODIFIED, "docstatus": 0} for name in ("EX-1", "EX-2")]
    _rows_route(site, "Example", rows)
    created = _confirm_batch("Example", [{"title": "A"}, {"title": "B"}])
    for row in rows:
        site.add("GET", standalone._resource("Example", row["name"]), row)
    site.add("DELETE", standalone._resource("Example", "EX-2"), None)
    site.add(
        "DELETE",
        standalone._resource("Example", "EX-1"),
        standalone.FrappeError("Cannot delete: linked", "LinkExistsError"),
    )

    with pytest.raises(RuntimeError, match="after deleting 1 of 2"):
        _undo(created["mcp_change"])

    (entry,) = standalone._journal_load()
    assert entry["undo"]["modified"] == {"EX-1": MODIFIED}
    assert not entry.get("undone_at")


def test_undo_a_custom_field_deletes_it(site) -> None:
    path = standalone._resource("Custom Field", "Example-x")
    site.add("POST", CUSTOM_FIELDS, {"name": "Example-x", "modified": MODIFIED})
    created = standalone.create_custom_field(
        "Example", {"fieldname": "x", "fieldtype": "Data"}, confirm=True
    )
    _rows_route(site, "Custom Field", [{"name": "Example-x", "modified": MODIFIED, "docstatus": 0}])
    site.add("GET", path, {"name": "Example-x", "modified": MODIFIED})
    site.add("DELETE", path, None)

    result = _undo(created["mcp_change"])

    assert result["undone"] is True and result["tool"] == "delete_document"
    assert [call.path for call in site.calls_to("DELETE")] == [path]
    # The inverse ran with the journal paused: no entry for the delete itself.
    assert standalone.list_mcp_changes()["total"] == 1


def _setters(site, rows: list[dict]) -> None:
    site.add(
        "GET",
        standalone._resource("Property Setter"),
        rows,
        params={"filters": standalone._filters({"doc_type": "Example", "property": "bold"})},
    )


def test_undo_a_new_property_setter_deletes_it(site) -> None:
    _setters(site, [])
    site.add(
        "POST",
        standalone._resource("Property Setter"),
        {"name": "Example-title-bold", "modified": MODIFIED},
    )
    saved = standalone.set_field_property("Example", "bold", 1, fieldname="title", confirm=True)
    path = standalone._resource("Property Setter", "Example-title-bold")
    _rows_route(
        site,
        "Property Setter",
        [{"name": "Example-title-bold", "modified": MODIFIED, "docstatus": 0}],
    )
    site.add("GET", path, {"name": "Example-title-bold", "modified": MODIFIED})
    site.add("DELETE", path, None)

    _undo(saved["mcp_change"])

    assert [call.path for call in site.calls_to("DELETE")] == [path]


def test_undo_an_updated_property_setter_restores_the_old_value(site) -> None:
    path = standalone._resource("Property Setter", "Example-title-bold")
    setter = {
        "name": "Example-title-bold",
        "field_name": "title",
        "value": "0",
        "property_type": "Check",
        "modified": MODIFIED,
    }
    _setters(site, [setter])
    site.add("GET", path, {**setter})
    site.add("PUT", path, {**setter, "value": "1", "modified": LATER})
    saved = standalone.set_field_property(
        "Example", "bold", 1, fieldname="title", expected_modified=MODIFIED, confirm=True
    )
    _setters(site, [{**setter, "value": "1", "modified": LATER}])
    site.add("GET", path, {**setter, "value": "1", "modified": LATER})

    preview = standalone.undo_mcp_change(saved["mcp_change"])
    standalone.undo_mcp_change(
        saved["mcp_change"], expected_preview=preview["preview_id"], confirm=True
    )

    assert preview["inverse"]["current_value"] == "1" and preview["inverse"]["value"] == "0"
    last = site.calls_to("PUT", path)[-1]
    assert last.json == {"data": {"value": "0", "property_type": "Check", "modified": LATER}}


def test_a_doctype_setter_that_replaced_field_setters_has_no_undo(site) -> None:
    _setters(site, [{"name": "Example-title-bold", "field_name": "title", "value": "1"}])
    site.add(
        "POST",
        standalone._resource("Property Setter"),
        {"name": "Example-main-bold", "modified": MODIFIED},
    )

    saved = standalone.set_field_property("Example", "bold", 1, confirm=True)

    (row,) = standalone.list_mcp_changes()["rows"]
    assert row["id"] == saved["mcp_change"]
    assert row["undoable"] is False and "replaced field-level" in row["no_undo"]


def test_undo_an_update_writes_the_old_values_back(site, records_on) -> None:
    path = standalone._resource("Example", "EX-1")
    before = {
        "name": "EX-1",
        "title": "Old",
        "modified": MODIFIED,
        "items": [
            {
                "name": "row-1",
                "idx": 1,
                "item_code": "A",
                "parent": "EX-1",
                "doctype": "Example Item",
                "owner": "x@example.localhost",
            }
        ],
    }
    site.add("GET", path, before)
    site.add("PUT", path, {**before, "title": "New", "items": [], "modified": LATER})
    saved = standalone.update_document(
        "Example",
        "EX-1",
        {"title": "New", "items": []},
        expected_modified=MODIFIED,
        confirm=True,
    )
    site.add("GET", path, {**before, "title": "New", "items": [], "modified": LATER})

    _undo(saved["mcp_change"])

    last = site.calls_to("PUT", path)[-1]
    assert last.json == {
        "data": {
            "title": "Old",
            "items": [{"name": "row-1", "idx": 1, "item_code": "A"}],
            "modified": LATER,
        }
    }
    assert standalone.list_mcp_changes()["total"] == 1


def test_undo_an_update_is_refused_after_a_later_change(site, records_on) -> None:
    path = standalone._resource("Example", "EX-1")
    site.add("GET", path, {"name": "EX-1", "title": "Old", "modified": MODIFIED})
    site.add("PUT", path, {"name": "EX-1", "title": "New", "modified": LATER})
    saved = standalone.update_document(
        "Example", "EX-1", {"title": "New"}, expected_modified=MODIFIED, confirm=True
    )
    site.add("GET", path, {"name": "EX-1", "title": "Newer", "modified": "2026-11-20 12:00:00"})

    with pytest.raises(ValueError, match="changed after this MCP change"):
        standalone.undo_mcp_change(saved["mcp_change"])


def test_a_password_update_is_journaled_without_an_inverse(site, records_on) -> None:
    path = standalone._resource("Example", "EX-1")
    site.add("GET", path, {"name": "EX-1", "secret": "*****", "modified": MODIFIED})
    site.add("PUT", path, {"name": "EX-1", "modified": LATER})

    standalone.update_document(
        "Example", "EX-1", {"secret": "new"}, expected_modified=MODIFIED, confirm=True
    )

    (entry,) = standalone._journal_load()
    assert entry["undo"] is None and "Password" in entry["no_undo"]
    assert "*****" not in json.dumps(entry) and "new" not in json.dumps(entry["target"])


def test_undo_needs_the_inverse_tool_to_be_listed(site, monkeypatch) -> None:
    site.add("POST", CUSTOM_FIELDS, {"name": "Example-x", "modified": MODIFIED})
    created = standalone.create_custom_field(
        "Example", {"fieldname": "x", "fieldtype": "Data"}, confirm=True
    )
    tools = dict(standalone.server._tool_manager._tools)
    del tools["delete_document"]
    monkeypatch.setattr(standalone.server._tool_manager, "_tools", tools)

    with pytest.raises(ValueError, match="needs delete_document"):
        standalone.undo_mcp_change(created["mcp_change"])


def test_undo_refuses_unknown_ids_and_stale_previews(site) -> None:
    with pytest.raises(ValueError, match="No journal entry"):
        standalone.undo_mcp_change("missing")
    site.add("POST", CUSTOM_FIELDS, {"name": "Example-x", "modified": MODIFIED})
    created = standalone.create_custom_field(
        "Example", {"fieldname": "x", "fieldtype": "Data"}, confirm=True
    )
    _rows_route(site, "Custom Field", [{"name": "Example-x", "modified": MODIFIED, "docstatus": 0}])

    with pytest.raises(ValueError, match="preview again"):
        standalone.undo_mcp_change(created["mcp_change"], expected_preview="0" * 64, confirm=True)
    assert not site.calls_to("DELETE")


# DocTypes: creation and edits


DOCTYPE = "Lab Sample"
RECORD_PATH = standalone._resource("DocType", DOCTYPE)


def _field(name: str, fieldname: str, fieldtype: str, **values) -> dict:
    return {
        "name": name,
        "doctype": "DocField",
        "fieldname": fieldname,
        "fieldtype": fieldtype,
        "label": fieldname.title(),
        **values,
    }


def _doctype_site(fake_site) -> dict:
    """A DocType record that update_doctype's PUT really changes, with a new `modified`."""
    stamps = (f"2026-11-20 12:00:{second:02d}.000001" for second in itertools.count())
    state = {
        "record": {
            "name": DOCTYPE,
            "doctype": "DocType",
            "module": "Lab",
            "custom": 1,
            "istable": 0,
            "issingle": 0,
            "track_changes": 0,
            "modified": next(stamps),
            "fields": [
                _field("df-1", "title", "Data"),
                _field("df-2", "status", "Select", options="Open\nClosed"),
                _field("df-3", "notes", "Small Text", bold=1),
                _field("df-4", "amount", "Currency"),
            ],
        }
    }

    def put(call):
        data = call.json["data"]
        assert data["modified"] == state["record"]["modified"]
        state["record"] = {**state["record"], **data, "modified": next(stamps)}
        # Frappe's meta follows the saved record.
        fake_site.doctypes[DOCTYPE]["fields"] = copy.deepcopy(data["fields"])
        return copy.deepcopy(state["record"])

    fake_site.add("GET", RECORD_PATH, lambda call: copy.deepcopy(state["record"]))
    fake_site.add("PUT", RECORD_PATH, put)
    fake_site.add("GET", standalone._resource("Module Def"), [{"name": "Lab", "app_name": "lab"}])
    fake_site.add_doctype(DOCTYPE, copy.deepcopy(state["record"]["fields"]))
    fake_site.add("GET", COUNT_PATH, 0)
    fake_site.add("GET", standalone._resource("DocType"), [])
    return state


def _shape(record: dict) -> list[tuple]:
    return [
        (
            row["fieldname"],
            row["fieldtype"],
            row.get("label"),
            row.get("options"),
            row.get("bold") or 0,
        )
        for row in record["fields"]
    ]


def test_undo_a_doctype_edit_restores_fields_order_and_settings(fake_site) -> None:
    state = _doctype_site(fake_site)
    original = copy.deepcopy(state["record"])
    operations = [
        {
            "op": "add_field",
            "field": {"fieldname": "batch", "fieldtype": "Data"},
            "insert_after": "title",
        },
        {"op": "remove_field", "fieldname": "notes"},
        {"op": "move_field", "fieldname": "amount", "insert_after": None},
        {"op": "update_field", "fieldname": "status", "set": {"label": "State"}},
        {"op": "set_properties", "set": {"track_changes": 1}},
    ]
    preview = standalone.update_doctype(DOCTYPE, operations)
    saved = standalone.update_doctype(
        DOCTYPE, operations, expected_modified=preview["modified"], confirm=True
    )
    assert [row["fieldname"] for row in state["record"]["fields"]] == [
        "amount",
        "title",
        "batch",
        "status",
    ]

    undo = standalone.undo_mcp_change(saved["mcp_change"])
    standalone.undo_mcp_change(
        saved["mcp_change"], expected_preview=undo["preview_id"], confirm=True
    )

    assert undo["tool"] == "update_doctype"
    assert undo["inverse"]["changes"]["added"][0]["fieldname"] == "notes"
    assert _shape(state["record"]) == _shape(original)
    assert state["record"]["track_changes"] == 0


def test_undo_a_doctype_edit_is_refused_after_a_later_edit(fake_site) -> None:
    state = _doctype_site(fake_site)
    operations = [{"op": "update_field", "fieldname": "status", "set": {"label": "State"}}]
    preview = standalone.update_doctype(DOCTYPE, operations)
    saved = standalone.update_doctype(
        DOCTYPE, operations, expected_modified=preview["modified"], confirm=True
    )
    state["record"]["modified"] = "2026-11-21 08:00:00.000001"

    with pytest.raises(ValueError, match="changed after this MCP change"):
        standalone.undo_mcp_change(saved["mcp_change"])


def test_undo_a_created_custom_doctype_deletes_it(fake_site) -> None:
    created = {"done": False}
    fake_site.add(
        "GET", standalone._resource("Module Def"), [{"name": "Lab", "app_name": "lab", "custom": 0}]
    )
    fake_site.add(
        "GET",
        standalone._resource("DocType"),
        lambda call: [{"name": DOCTYPE}] if created["done"] else [],
    )

    def post(call):
        created["done"] = True
        return {"name": DOCTYPE}

    fake_site.add("POST", standalone._resource("DocType"), post)
    record = {
        "name": DOCTYPE,
        "doctype": "DocType",
        "module": "Lab",
        "custom": 1,
        "modified": MODIFIED,
        "fields": [],
    }
    fake_site.add("GET", RECORD_PATH, record)
    fields = [{"fieldname": "title", "fieldtype": "Data", "label": "Title"}]

    result = standalone.create_doctype("lab", "Lab", DOCTYPE, fields, is_custom=True, confirm=True)

    fake_site.add("GET", standalone._resource("DocField"), [])
    fake_site.add("GET", standalone._resource("Custom Field"), [])
    fake_site.add("GET", COUNT_PATH, 0)
    fake_site.add("DELETE", RECORD_PATH, None)
    preview = standalone.undo_mcp_change(result["mcp_change"])
    assert preview["tool"] == "delete_doctype" and preview["inverse"]["records"] == 0
    standalone.undo_mcp_change(
        result["mcp_change"], expected_preview=preview["preview_id"], confirm=True
    )
    assert [call.path for call in fake_site.calls_to("DELETE")] == [RECORD_PATH]

    fake_site.add("GET", RECORD_PATH, {**record, "modified": LATER})
    standalone._journal_update(result["mcp_change"], lambda entry: entry.pop("undone_at"))
    with pytest.raises(ValueError, match="changed after this MCP change"):
        standalone.undo_mcp_change(result["mcp_change"])


def test_journal_entries_hold_no_environment_values(site, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_API_KEY", "key-value")
    monkeypatch.setenv("FRAPPE_API_SECRET", "secret-value")
    site.add("POST", CUSTOM_FIELDS, {"name": "Example-x", "modified": MODIFIED})

    standalone.create_custom_field("Example", {"fieldname": "x", "fieldtype": "Data"}, confirm=True)

    text = json.dumps(standalone._journal_load())
    for value in os.environ.values():
        if len(value) > 6 and "value" in value:
            assert value not in text
