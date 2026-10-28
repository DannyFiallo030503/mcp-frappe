"""`update_doctype` and `delete_doctype`: operation lists, previews, guards, and reloads."""

from __future__ import annotations

import copy

import httpx
import pytest

from frappe_mcp import standalone

MODIFIED = "2026-10-20 09:00:00.000001"
SAVED = "2026-10-20 09:05:00.000001"
DOCTYPE = "Lab Sample"
RECORD_PATH = standalone._resource("DocType", DOCTYPE)
COUNT_PATH = "/api/method/frappe.client.get_count"


def _row(name: str, fieldname: str, fieldtype: str, idx: int, **values) -> dict:
    return {
        "name": name,
        "doctype": "DocField",
        "parent": DOCTYPE,
        "parenttype": "DocType",
        "parentfield": "fields",
        "idx": idx,
        "fieldname": fieldname,
        "fieldtype": fieldtype,
        "label": fieldname.replace("_", " ").title(),
        "reqd": 0,
        **values,
    }


def _rows() -> list[dict]:
    return [
        _row("df-title", "title", "Data", 1),
        _row("df-status", "status", "Select", 2, options="Open\nClosed"),
        _row("df-notes", "notes", "Small Text", 3),
        _row("df-amount", "amount", "Currency", 4),
    ]


def _site(fake_site, *, custom: int = 0, app: str = "lab_app", module: str = "Lab") -> dict:
    record = {
        "name": DOCTYPE,
        "doctype": "DocType",
        "module": module,
        "custom": custom,
        "istable": 0,
        "issingle": 0,
        "track_changes": 0,
        "modified": MODIFIED,
        "fields": _rows(),
        "permissions": [{"role": "System Manager", "read": 1}],
    }
    fake_site.add("GET", RECORD_PATH, record)
    fake_site.add("GET", standalone._resource("Module Def"), [{"name": module, "app_name": app}])
    # Frappe's meta also holds the DocType's Custom Fields.
    fake_site.add_doctype(
        DOCTYPE,
        [*_rows(), {"fieldname": "custom_batch", "fieldtype": "Data", "is_custom_field": 1}],
        module=module,
    )
    fake_site.add("GET", COUNT_PATH, 0)
    fake_site.add(
        "GET",
        standalone._resource("DocType"),
        [{"name": "Customer", "istable": 0}, {"name": "Lab Sample Item", "istable": 1}],
    )
    return record


def _put_fields(fake_site) -> list[dict]:
    (put,) = fake_site.calls_to("PUT", RECORD_PATH)
    return put.json["data"]["fields"]


def _confirm(fake_site, operations: list[dict]) -> dict:
    fake_site.add("PUT", RECORD_PATH, {"name": DOCTYPE, "modified": SAVED})
    return standalone.update_doctype(
        DOCTYPE, operations, expected_modified=MODIFIED, confirm=True
    )


def test_add_field_keeps_existing_rows_and_sends_modified(fake_site) -> None:
    _site(fake_site)

    result = _confirm(
        fake_site,
        [
            {
                "op": "add_field",
                "field": {"fieldname": "sample_date", "fieldtype": "Date", "reqd": True},
                "insert_after": "title",
            }
        ],
    )

    fields = _put_fields(fake_site)
    assert [row["fieldname"] for row in fields] == [
        "title",
        "sample_date",
        "status",
        "notes",
        "amount",
    ]
    assert [row.get("name") for row in fields] == [
        "df-title",
        None,
        "df-status",
        "df-notes",
        "df-amount",
    ]
    assert [row["idx"] for row in fields] == [1, 2, 3, 4, 5]
    assert fields[1] == {
        "doctype": "DocField",
        "fieldname": "sample_date",
        "fieldtype": "Date",
        "reqd": 1,
        "idx": 2,
    }
    (put,) = fake_site.calls_to("PUT", RECORD_PATH)
    assert put.json["data"]["modified"] == MODIFIED
    assert set(put.json["data"]) == {"fields", "modified"}
    assert result["updated"] is True
    assert result["modified"] == SAVED
    assert result["reconciled_after_server_reload"] is False


def test_add_field_goes_last_without_insert_after_and_first_with_null(fake_site) -> None:
    _site(fake_site)

    _confirm(
        fake_site,
        [
            {"op": "add_field", "field": {"fieldname": "last_one", "fieldtype": "Data"}},
            {
                "op": "add_field",
                "field": {"fieldname": "first_one", "fieldtype": "Data"},
                "insert_after": None,
            },
        ],
    )

    names = [row["fieldname"] for row in _put_fields(fake_site)]
    assert names[0] == "first_one"
    assert names[-1] == "last_one"


def test_update_remove_and_move_keep_row_names(fake_site) -> None:
    _site(fake_site)

    _confirm(
        fake_site,
        [
            {"op": "update_field", "fieldname": "title", "set": {"label": "Name", "reqd": 1}},
            {"op": "remove_field", "fieldname": "notes"},
            {"op": "move_field", "fieldname": "amount", "insert_after": None},
            {"op": "set_properties", "set": {"track_changes": True}},
        ],
    )

    fields = _put_fields(fake_site)
    assert [(row["name"], row["fieldname"]) for row in fields] == [
        ("df-amount", "amount"),
        ("df-title", "title"),
        ("df-status", "status"),
    ]
    assert fields[1]["label"] == "Name"
    assert fields[1]["reqd"] == 1
    (put,) = fake_site.calls_to("PUT", RECORD_PATH)
    assert put.json["data"]["track_changes"] == 1


def test_preview_lists_added_removed_moved_changed_and_properties(fake_site) -> None:
    _site(fake_site)
    fake_site.add("GET", COUNT_PATH, 7, params={"filters": '[["notes","is","set"]]'})

    preview = standalone.update_doctype(
        DOCTYPE,
        [
            {
                "op": "add_field",
                "field": {"fieldname": "customer", "fieldtype": "Link", "options": "Customer"},
                "insert_after": "title",
            },
            {"op": "update_field", "fieldname": "status", "set": {"label": "State"}},
            {"op": "remove_field", "fieldname": "notes"},
            {"op": "move_field", "fieldname": "amount", "insert_after": "title"},
            {"op": "set_properties", "set": {"track_changes": 1}},
        ],
    )

    assert preview["preview"] is True
    assert preview["modified"] == MODIFIED
    assert preview["app"] == "lab_app"
    assert preview["requires_developer_mode"] is True
    changes = preview["changes"]
    assert changes["added"] == [
        {"fieldname": "customer", "fieldtype": "Link", "label": None, "insert_after": "amount"}
    ]
    assert changes["removed"] == [
        {"fieldname": "notes", "fieldtype": "Small Text", "label": "Notes",
         "records_with_value": 7}
    ]
    assert changes["moved"] == [{"fieldname": "amount", "insert_after": "title"}]
    assert changes["changed"] == {"status": {"label": {"from": "Status", "to": "State"}}}
    assert changes["properties"] == {"track_changes": {"from": 0, "to": 1}}
    assert any("Removing notes: 7 records" in warning for warning in preview["warnings"])
    assert not fake_site.calls_to("PUT")


def test_preview_points_at_the_doctype_json_in_the_local_checkout(
    fake_site, tmp_path, monkeypatch
) -> None:
    module_path = tmp_path / "lab_app" / "lab_app" / "lab"
    module_path.mkdir(parents=True)
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path))
    _site(fake_site)

    preview = standalone.update_doctype(
        DOCTYPE, [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}]
    )

    files = preview["app_files"]
    assert files["json"] == str(module_path / "doctype" / "lab_sample" / "lab_sample.json")
    assert files["local_checkout"] is True
    assert files["json_exists"] is False


def test_custom_doctype_preview_has_no_app_files(fake_site) -> None:
    _site(fake_site, custom=1, app="erpnext", module="Selling")

    preview = standalone.update_doctype(
        DOCTYPE, [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}]
    )

    assert preview["custom"] is True
    assert "app_files" not in preview


def test_fieldname_change_is_refused(fake_site) -> None:
    _site(fake_site)

    with pytest.raises(ValueError, match="renaming fields is not supported"):
        standalone.update_doctype(
            DOCTYPE,
            [{"op": "update_field", "fieldname": "title", "set": {"fieldname": "headline"}}],
        )


def test_protected_app_doctype_is_refused_with_customize_hint(fake_site) -> None:
    _site(fake_site, app="frappe", module="Core")

    with pytest.raises(ValueError, match="create_custom_field and set_field_property"):
        standalone.update_doctype(
            DOCTYPE, [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}]
        )
    with pytest.raises(ValueError, match="create_custom_field and set_field_property"):
        standalone.delete_doctype(DOCTYPE)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ({"fieldname": "lab", "fieldtype": "Link", "options": "Laboratory"}, "unknown DocType"),
        ({"fieldname": "items", "fieldtype": "Table", "options": "Customer"}, "child table"),
        ({"fieldname": "lab", "fieldtype": "Link"}, "need options"),
        ({"fieldname": "kind", "fieldtype": "Select"}, "need options"),
        ({"fieldname": "kind", "fieldtype": "Wingding"}, "unsupported fieldtype"),
        ({"fieldname": "Bad Name", "fieldtype": "Data"}, "lowercase"),
        ({"fieldname": "title", "fieldtype": "Data"}, "already has a field title"),
        ({"fieldname": "custom_batch", "fieldtype": "Data"}, "already has a field"),
        (
            {"fieldname": "intro", "fieldtype": "HTML", "options": "<script></script>"},
            "does not set options on HTML",
        ),
        (
            {"fieldname": "more", "fieldtype": "Section Break", "collapsible_depends_on": "1"},
            "unsupported field properties: collapsible_depends_on",
        ),
    ],
)
def test_invalid_new_fields_are_refused(fake_site, field, message) -> None:
    _site(fake_site)

    with pytest.raises(ValueError, match=message):
        standalone.update_doctype(DOCTYPE, [{"op": "add_field", "field": field}])
    assert not fake_site.calls_to("PUT")


def test_table_field_to_a_child_doctype_is_accepted(fake_site) -> None:
    _site(fake_site)

    preview = standalone.update_doctype(
        DOCTYPE,
        [
            {
                "op": "add_field",
                "field": {"fieldname": "items", "fieldtype": "Table", "options": "Lab Sample Item"},
            }
        ],
    )

    assert preview["changes"]["added"][0]["fieldname"] == "items"


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ({"op": "rename_field", "fieldname": "title"}, "op is one of"),
        ({"op": "remove_field", "fieldname": "missing"}, "no field named 'missing'"),
        ({"op": "move_field", "fieldname": "title"}, "missing insert_after"),
        ({"op": "move_field", "fieldname": "title", "insert_after": "title"}, "after itself"),
        ({"op": "move_field", "fieldname": "title", "insert_after": "gone"}, "no field named"),
        ({"op": "update_field", "fieldname": "title", "set": {}}, "non-empty object"),
        ({"op": "set_properties", "set": {"istable": 1}}, "unsupported DocType properties"),
        ({"op": "set_properties", "set": {"links": []}}, "unsupported DocType properties"),
        ({"op": "remove_field", "fieldname": "title", "extra": 1}, "unexpected extra"),
    ],
)
def test_invalid_operations_are_refused(fake_site, operation, message) -> None:
    _site(fake_site)

    with pytest.raises(ValueError, match=message):
        standalone.update_doctype(DOCTYPE, [operation])


def test_operations_that_change_nothing_are_refused(fake_site) -> None:
    _site(fake_site)

    with pytest.raises(ValueError, match="unchanged"):
        standalone.update_doctype(
            DOCTYPE, [{"op": "move_field", "fieldname": "status", "insert_after": "title"}]
        )


def test_fieldtype_change_that_can_lose_data_is_refused(fake_site) -> None:
    _site(fake_site)
    fake_site.add("GET", COUNT_PATH, 3, params={"filters": '[["notes","is","set"]]'})

    with pytest.raises(ValueError, match="can lose data and 3 records"):
        standalone.update_doctype(
            DOCTYPE, [{"op": "update_field", "fieldname": "notes", "set": {"fieldtype": "Int"}}]
        )


def test_fieldtype_change_on_an_empty_column_is_allowed(fake_site) -> None:
    _site(fake_site)

    preview = standalone.update_doctype(
        DOCTYPE, [{"op": "update_field", "fieldname": "notes", "set": {"fieldtype": "Int"}}]
    )

    assert preview["changes"]["changed"]["notes"]["fieldtype"] == {
        "from": "Small Text",
        "to": "Int",
    }
    assert any("no record has a value" in warning for warning in preview["warnings"])


def test_related_fieldtype_change_only_warns(fake_site) -> None:
    _site(fake_site)
    fake_site.add("GET", COUNT_PATH, 9)

    preview = standalone.update_doctype(
        DOCTYPE, [{"op": "update_field", "fieldname": "amount", "set": {"fieldtype": "Float"}}]
    )

    assert any("Customize Form allows" in warning for warning in preview["warnings"])


def test_reqd_on_a_field_with_empty_rows_warns(fake_site) -> None:
    _site(fake_site)
    fake_site.add("GET", COUNT_PATH, 4, params={"filters": '[["notes","is","not set"]]'})

    preview = standalone.update_doctype(
        DOCTYPE, [{"op": "update_field", "fieldname": "notes", "set": {"reqd": 1}}]
    )

    assert any("4 records leave it empty" in warning for warning in preview["warnings"])


def test_stale_expected_modified_is_refused(fake_site) -> None:
    _site(fake_site)

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.update_doctype(
            DOCTYPE,
            [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}],
            expected_modified="2026-01-01 00:00:00",
            confirm=True,
        )
    assert not fake_site.calls_to("PUT")


def test_timestamp_mismatch_on_save_is_reported_as_stale(fake_site) -> None:
    _site(fake_site)
    fake_site.add(
        "PUT",
        RECORD_PATH,
        standalone.FrappeError("Frappe HTTP 417: changed", "TimestampMismatchError"),
    )

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.update_doctype(
            DOCTYPE,
            [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}],
            expected_modified=MODIFIED,
            confirm=True,
        )


def test_developer_mode_refusal_is_explained(fake_site) -> None:
    _site(fake_site)
    fake_site.add(
        "PUT",
        RECORD_PATH,
        standalone.FrappeError("Frappe HTTP 417: Not in Developer Mode",
                               "CannotCreateStandardDoctypeError"),
    )

    with pytest.raises(ValueError, match="developer mode"):
        standalone.update_doctype(
            DOCTYPE,
            [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}],
            expected_modified=MODIFIED,
            confirm=True,
        )


def _reload_during_put(fake_site, monkeypatch, saved_record: dict) -> list[str]:
    """Make the PUT fail with a transport error, then serve `saved_record` on re-read."""
    record = _site(fake_site)
    state = {"put": False}
    waited: list[str] = []

    def put(call):
        state["put"] = True
        raise httpx.RemoteProtocolError("Server disconnected")

    def get(call):
        return copy.deepcopy(saved_record if state["put"] else record)

    fake_site.add("PUT", RECORD_PATH, put)
    fake_site.add("GET", RECORD_PATH, get)
    monkeypatch.setattr(standalone, "_wait_for_server_ready", lambda: waited.append("ready"))
    return waited


def test_transport_error_during_save_is_reconciled_by_rereading(fake_site, monkeypatch) -> None:
    rows = _rows()
    rows[0]["bold"] = 1
    saved = {"name": DOCTYPE, "module": "Lab", "custom": 0, "modified": SAVED, "fields": rows}
    waited = _reload_during_put(fake_site, monkeypatch, saved)

    result = standalone.update_doctype(
        DOCTYPE,
        [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}],
        expected_modified=MODIFIED,
        confirm=True,
    )

    assert waited == ["ready"]
    assert result["reconciled_after_server_reload"] is True
    assert result["modified"] == SAVED
    assert len(fake_site.calls_to("PUT", RECORD_PATH)) == 1


def test_transport_error_without_the_change_saved_fails_without_resending(
    fake_site, monkeypatch
) -> None:
    unsaved = {"name": DOCTYPE, "module": "Lab", "custom": 0, "modified": MODIFIED,
               "fields": _rows()}
    _reload_during_put(fake_site, monkeypatch, unsaved)

    with pytest.raises(RuntimeError, match="does not hold the change"):
        standalone.update_doctype(
            DOCTYPE,
            [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}],
            expected_modified=MODIFIED,
            confirm=True,
        )
    assert len(fake_site.calls_to("PUT", RECORD_PATH)) == 1


def test_confirm_invalidates_the_metadata_cache(fake_site) -> None:
    _site(fake_site)
    fake_site.add_doctype("Lab Report", [{"fieldname": "x", "fieldtype": "Data"}])
    standalone.get_doctype_schema(DOCTYPE)
    standalone.get_doctype_schema("Lab Report")
    assert DOCTYPE in standalone._meta_cache

    _confirm(fake_site, [{"op": "update_field", "fieldname": "title", "set": {"bold": 1}}])

    assert DOCTYPE not in standalone._meta_cache
    assert "Lab Report" in standalone._meta_cache


def test_confirm_invalidates_a_new_child_table(fake_site) -> None:
    _site(fake_site)
    fake_site.add_doctype("Lab Sample Item", [{"fieldname": "x", "fieldtype": "Data"}],
                          istable=1)
    standalone.get_doctype_schema("Lab Sample Item")

    _confirm(
        fake_site,
        [
            {
                "op": "add_field",
                "field": {"fieldname": "items", "fieldtype": "Table", "options": "Lab Sample Item"},
            }
        ],
    )

    assert "Lab Sample Item" not in standalone._meta_cache


def _delete_site(fake_site, *, standard_links=(), custom_links=()) -> dict:
    record = _site(fake_site)
    fake_site.add("GET", standalone._resource("DocField"), list(standard_links))
    fake_site.add("GET", standalone._resource("Custom Field"), list(custom_links))
    fake_site.add("GET", COUNT_PATH, 12, params={"doctype": DOCTYPE})
    return record


def test_delete_doctype_refuses_a_doctype_others_link_to(fake_site) -> None:
    _delete_site(
        fake_site,
        standard_links=[
            {"parent": "Lab Test", "fieldname": "sample", "fieldtype": "Link"},
            {"parent": DOCTYPE, "fieldname": "parent_sample", "fieldtype": "Link"},
        ],
        custom_links=[{"dt": "Customer", "fieldname": "custom_sample", "fieldtype": "Link"}],
    )

    with pytest.raises(ValueError, match="Customer.custom_sample .*Lab Test.sample") as error:
        standalone.delete_doctype(DOCTYPE)
    assert "parent_sample" not in str(error.value)
    (docfields,) = fake_site.calls_to("GET", standalone._resource("DocField"))
    assert docfields.params["parent"] == "DocType"
    assert not fake_site.calls_to("DELETE")


def test_delete_doctype_preview_says_what_frappe_removes_and_keeps(fake_site) -> None:
    _delete_site(fake_site)

    preview = standalone.delete_doctype(DOCTYPE)

    assert preview["preview"] is True
    assert preview["modified"] == MODIFIED
    assert preview["records"] == 12
    assert preview["requires_administrator"] is True
    assert any("lab_app/lab_app/lab/doctype/lab_sample" in item
               for item in preview["frappe_removes"])
    assert any("`tabLab Sample`" in item for item in preview["frappe_keeps"])
    assert not fake_site.calls_to("DELETE")


def test_delete_doctype_confirm_deletes_and_forgets_metadata(fake_site) -> None:
    _delete_site(fake_site)
    fake_site.add("DELETE", RECORD_PATH, "ok")
    standalone.get_doctype_schema(DOCTYPE)

    result = standalone.delete_doctype(DOCTYPE, expected_modified=MODIFIED, confirm=True)

    assert result == {
        "deleted": True,
        "doctype": DOCTYPE,
        "reconciled_after_server_reload": False,
    }
    assert len(fake_site.calls_to("DELETE", RECORD_PATH)) == 1
    assert DOCTYPE not in standalone._meta_cache


def test_delete_doctype_refuses_a_stale_token(fake_site) -> None:
    _delete_site(fake_site)

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.delete_doctype(DOCTYPE, expected_modified="old", confirm=True)
    assert not fake_site.calls_to("DELETE")


def test_delete_doctype_reconciles_a_reload(fake_site, monkeypatch) -> None:
    _delete_site(fake_site)
    fake_site.add("DELETE", RECORD_PATH, httpx.RemoteProtocolError("Server disconnected"))
    monkeypatch.setattr(standalone, "_wait_for_document_deleted", lambda doctype, name: True)

    result = standalone.delete_doctype(DOCTYPE, expected_modified=MODIFIED, confirm=True)

    assert result["reconciled_after_server_reload"] is True
