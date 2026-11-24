"""`customize_doctype`: one Customize Form save, previewed the way Frappe will apply it."""

from __future__ import annotations

import json

import pytest

from frappe_mcp import standalone

DOCTYPE = "Lab Order"
RECORD_PATH = standalone._resource("DocType", DOCTYPE)
SETTERS = standalone._resource("Property Setter")
CUSTOM_FIELDS = standalone._resource("Custom Field")
COUNT_PATH = "/api/method/frappe.client.get_count"
RUN = standalone._RUN_DOC_METHOD


def _base_rows() -> list[dict]:
    """The DocType's own fields: what Customize Form compares a new value with."""
    return [
        {"fieldname": "customer", "fieldtype": "Link", "options": "Customer", "reqd": 1},
        {"fieldname": "status", "fieldtype": "Select", "options": "Open\nClosed", "hidden": 0},
        {"fieldname": "notes", "fieldtype": "Data", "hidden": 0, "read_only": 0},
        {"fieldname": "total", "fieldtype": "Currency", "read_only": 1},
    ]


def _form_rows(custom: bool) -> list[dict]:
    rows = [
        {"doctype": "Customize Form Field", "name": f"df-{row['fieldname']}", **row}
        for row in _base_rows()
    ]
    if custom:
        rows.append(
            {
                "doctype": "Customize Form Field",
                "name": f"{DOCTYPE}-custom_batch",
                "fieldname": "custom_batch",
                "fieldtype": "Data",
                "label": "Batch",
                "is_custom_field": 1,
                "is_system_generated": 0,
            }
        )
    return rows


class _Site:
    """Customize Form state on a fake site; the POST save swaps in `after` setters."""

    def __init__(self, fake_site, *, custom: bool = False, setters: list | None = None) -> None:
        self.fake_site = fake_site
        self.setters = list(setters or [])
        self.after: list | None = None
        self.form = {
            "doctype": "Customize Form",
            "name": "Customize Form",
            "doc_type": DOCTYPE,
            "track_changes": 0,
            "fields": _form_rows(custom),
        }
        self.custom_fields = (
            [
                {
                    "name": f"{DOCTYPE}-custom_batch",
                    "fieldname": "custom_batch",
                    "insert_after": "total",
                    "is_system_generated": 0,
                    "modified": "2026-11-01 10:00:00",
                }
            ]
            if custom
            else []
        )
        fake_site.add_doctype(DOCTYPE, [dict(row) for row in self.form["fields"]], module="Lab")
        fake_site.add(
            "GET",
            RUN,
            lambda call: {"docs": [json.loads(json.dumps(self.form))]},
            params={"method": "fetch_to_customize"},
        )
        fake_site.add(
            "GET",
            RECORD_PATH,
            {
                "name": DOCTYPE,
                "modified": "2026-10-01 09:00:00",
                "track_changes": 0,
                "fields": _base_rows(),
            },
        )
        fake_site.add("GET", SETTERS, lambda call: list(self.setters))
        fake_site.add("GET", CUSTOM_FIELDS, lambda call: list(self.custom_fields))
        fake_site.add("GET", COUNT_PATH, 0)

        def save(call):
            if self.after is not None:
                self.setters = self.after
            return {"docs": [], "_server_messages": json.dumps([json.dumps({"message": "Saved"})])}

        fake_site.add("POST", RUN, save)

    def saved_form(self) -> dict:
        (post,) = self.fake_site.calls_to("POST", RUN)
        assert post.json["method"] == "save_customization"
        return json.loads(post.json["docs"])


def _setter(fieldname, prop, value, name="ps-1", modified="2026-11-02 10:00:00"):
    return {
        "name": name,
        "doctype_or_field": "DocField" if fieldname else "DocType",
        "field_name": fieldname,
        "row_name": None,
        "property": prop,
        "value": value,
        "modified": modified,
    }


def test_allowlists_stay_within_what_update_doctype_allows() -> None:
    assert set(standalone._CUSTOMIZE_FIELD_PROPERTIES) <= standalone._DOCFIELD_PROPERTIES
    assert set(standalone._CUSTOMIZE_DOCTYPE_PROPERTIES) <= standalone._DOCTYPE_PROPERTIES
    assert "istable" not in standalone._CUSTOMIZE_DOCTYPE_PROPERTIES


def test_preview_loads_customize_form_with_a_get_and_lists_a_new_setter(fake_site) -> None:
    _Site(fake_site)

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "set_property", "fieldname": "notes", "property": "hidden", "value": True}]
    )

    assert preview["preview"] is True
    assert preview["property_setters"] == [
        {"action": "create", "property": "hidden", "fieldname": "notes", "to": 1}
    ]
    assert preview["custom_fields"] == []
    assert len(preview["preview_id"]) == 64
    (fetch,) = fake_site.calls_to("GET", RUN)
    assert json.loads(fetch.params["docs"])["doc_type"] == DOCTYPE
    assert not fake_site.calls_to("POST")


def test_unset_doctype_settings_use_customize_form_schema_allowlist(fake_site) -> None:
    _Site(fake_site)

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "set_property", "property": "default_view", "value": "List"}]
    )

    assert preview["property_setters"] == [
        {"action": "create", "property": "default_view", "to": "List"}
    ]
    assert preview["warnings"] == []
    assert not fake_site.calls_to("POST")


def test_confirm_sends_the_whole_form_to_save_customization(fake_site) -> None:
    site = _Site(fake_site)
    site.after = [_setter("notes", "hidden", "1")]
    operations = [{"op": "set_property", "fieldname": "notes", "property": "hidden", "value": 1}]
    preview = standalone.customize_doctype(DOCTYPE, operations)

    result = standalone.customize_doctype(
        DOCTYPE, operations, expected_preview=preview["preview_id"], confirm=True
    )

    form = site.saved_form()
    assert [row["fieldname"] for row in form["fields"]] == [
        "customer",
        "status",
        "notes",
        "total",
    ]
    assert next(row for row in form["fields"] if row["fieldname"] == "notes")["hidden"] == 1
    assert form["doc_type"] == DOCTYPE
    assert result["saved"] is True
    assert result["property_setters"] == [
        {"action": "create", "property": "hidden", "fieldname": "notes", "to": "1"}
    ]
    assert result["warnings"] == []
    assert result["frappe_messages"] == ["Saved"]


def test_setting_the_standard_value_previews_removing_the_redundant_setter(fake_site) -> None:
    site = _Site(fake_site, setters=[_setter("notes", "hidden", "1")])
    site.form["fields"][2]["hidden"] = 1  # the setter is applied in the form

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "set_property", "fieldname": "notes", "property": "hidden", "value": 0}]
    )

    assert preview["property_setters"] == [
        {"action": "remove", "property": "hidden", "fieldname": "notes", "from": "1"}
    ]


def test_changing_a_customized_value_updates_its_setter(fake_site) -> None:
    site = _Site(fake_site, setters=[_setter("notes", "label", "Remarks")])
    site.form["fields"][2]["label"] = "Remarks"

    preview = standalone.customize_doctype(
        DOCTYPE,
        [{"op": "set_property", "fieldname": "notes", "property": "label", "value": "Comments"}],
    )

    assert preview["property_setters"] == [
        {
            "action": "update",
            "property": "label",
            "fieldname": "notes",
            "from": "Remarks",
            "to": "Comments",
        }
    ]


def test_doctype_settings_use_customize_forms_list(fake_site) -> None:
    _Site(fake_site)

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "set_property", "property": "track_changes", "value": 1}]
    )

    assert preview["property_setters"] == [
        {"action": "create", "property": "track_changes", "to": 1}
    ]
    with pytest.raises(ValueError, match="not a DocType setting"):
        standalone.customize_doctype(
            DOCTYPE, [{"op": "set_property", "property": "istable", "value": 1}]
        )


def test_moving_a_field_previews_the_field_order_setter(fake_site) -> None:
    _Site(fake_site)

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "move_field", "fieldname": "notes", "insert_after": None}]
    )

    assert preview["property_setters"] == [
        {
            "action": "create",
            "property": "field_order",
            "to": json.dumps(["notes", "customer", "status", "total"]),
        }
    ]


def test_moving_back_to_the_standard_order_removes_the_field_order_setter(fake_site) -> None:
    order = json.dumps(["notes", "customer", "status", "total"])
    site = _Site(fake_site, setters=[_setter(None, "field_order", order)])
    rows = site.form["fields"]
    site.form["fields"] = [rows[2], rows[0], rows[1], rows[3]]

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "move_field", "fieldname": "notes", "insert_after": "status"}]
    )

    assert preview["property_setters"] == [
        {"action": "remove", "property": "field_order", "from": order}
    ]


def test_adding_a_custom_field_previews_it_and_the_field_order(fake_site) -> None:
    _Site(fake_site)

    preview = standalone.customize_doctype(
        DOCTYPE,
        [
            {
                "op": "add_custom_field",
                "field": {"fieldname": "custom_lab_code", "fieldtype": "Data", "label": "Lab Code"},
                "insert_after": "customer",
            }
        ],
    )

    assert preview["custom_fields"] == [
        {
            "action": "create",
            "fieldname": "custom_lab_code",
            "insert_after": "customer",
            "fieldtype": "Data",
            "label": "Lab Code",
        }
    ]
    # Customize Form stores the order whenever it differs from the DocType's own order.
    assert preview["property_setters"] == [
        {
            "action": "create",
            "property": "field_order",
                "to": json.dumps(["customer", "custom_lab_code", "status", "notes", "total"]),
        }
    ]


def test_adding_a_custom_field_rejects_name_frappe_would_replace(fake_site) -> None:
    _Site(fake_site)

    with pytest.raises(ValueError, match="derives fieldname 'custom_lab_code'"):
        standalone.customize_doctype(
            DOCTYPE,
            [
                {
                    "op": "add_custom_field",
                    "field": {"fieldname": "lab_code", "fieldtype": "Data", "label": "Lab Code"},
                }
            ],
        )


def test_adding_a_custom_field_rejects_batch_that_changes_derived_name(fake_site) -> None:
    _Site(fake_site)

    with pytest.raises(ValueError, match="derives fieldname 'custom_other'"):
        standalone.customize_doctype(
            DOCTYPE,
            [
                {
                    "op": "add_custom_field",
                    "field": {
                        "fieldname": "custom_lab_code",
                        "fieldtype": "Data",
                        "label": "Lab Code",
                    },
                },
                {
                    "op": "set_property",
                    "fieldname": "custom_lab_code",
                    "property": "label",
                    "value": "Other",
                },
            ],
        )


def test_a_new_custom_field_row_is_marked_custom_in_the_saved_form(fake_site) -> None:
    site = _Site(fake_site)
    operations = [
        {
            "op": "add_custom_field",
            "field": {"fieldname": "custom_lab_code", "fieldtype": "Data", "label": "Lab Code"},
        }
    ]
    preview = standalone.customize_doctype(DOCTYPE, operations)

    def save(call):
        site.custom_fields = [*site.custom_fields, {"name": "x", "fieldname": "custom_lab_code"}]
        site.setters = [_setter(None, "field_order", "[]")]
        return {"docs": []}

    fake_site.add("POST", RUN, save)
    result = standalone.customize_doctype(
        DOCTYPE, operations, expected_preview=preview["preview_id"], confirm=True
    )

    row = site.saved_form()["fields"][-1]
    assert row["fieldname"] == "custom_lab_code"
    assert row["is_custom_field"] == 1 and row["is_system_generated"] == 0
    assert result["warnings"] == []


def test_custom_fields_change_directly_not_through_setters(fake_site) -> None:
    _Site(fake_site, custom=True, setters=[_setter(None, "field_order", "[]")])

    preview = standalone.customize_doctype(
        DOCTYPE,
        [{"op": "set_property", "fieldname": "custom_batch", "property": "bold", "value": 1}],
    )

    assert preview["custom_fields"] == [
        {"action": "update", "fieldname": "custom_batch", "set": {"bold": {"from": None, "to": 1}}}
    ]
    assert [change["property"] for change in preview["property_setters"]] == ["field_order"]


def test_moving_a_custom_field_updates_its_insert_after(fake_site) -> None:
    _Site(fake_site, custom=True)

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "move_field", "fieldname": "custom_batch", "insert_after": "customer"}]
    )

    assert preview["custom_fields"] == [
        {
            "action": "update",
            "fieldname": "custom_batch",
            "set": {"insert_after": {"from": "total", "to": "customer"}},
        }
    ]


def test_related_fieldtype_changes_are_allowed_with_a_warning(fake_site) -> None:
    _Site(fake_site)

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "change_fieldtype", "fieldname": "notes", "fieldtype": "Small Text"}]
    )

    assert preview["property_setters"] == [
        {"action": "create", "property": "fieldtype", "fieldname": "notes", "to": "Small Text"}
    ]
    assert "Small Text" in preview["warnings"][0]


def test_unrelated_fieldtype_changes_are_refused(fake_site) -> None:
    _Site(fake_site)

    with pytest.raises(ValueError, match="does not change Data to Int"):
        standalone.customize_doctype(
            DOCTYPE, [{"op": "change_fieldtype", "fieldname": "notes", "fieldtype": "Int"}]
        )
    with pytest.raises(ValueError, match="use change_fieldtype"):
        standalone.customize_doctype(
            DOCTYPE,
            [{"op": "set_property", "fieldname": "notes", "property": "fieldtype", "value": "Int"}],
        )


@pytest.mark.parametrize(
    ("fieldname", "prop", "value", "message"),
    [
        ("customer", "reqd", 0, "mandatory standard field optional"),
        ("total", "read_only", 0, "read-only standard field editable"),
        ("notes", "allow_on_submit", 1, "Allow on Submit"),
        ("status", "options", "Open\nClosed\nOn Hold", "only on Data and Read Only"),
        ("customer", "translatable", 1, "cannot be translatable"),
    ],
)
def test_changes_customize_form_would_skip_are_refused(
    fake_site, fieldname, prop, value, message
) -> None:
    _Site(fake_site)

    with pytest.raises(ValueError, match=message):
        standalone.customize_doctype(
            DOCTYPE,
            [{"op": "set_property", "fieldname": fieldname, "property": prop, "value": value}],
        )


def test_code_and_unknown_properties_are_refused(fake_site) -> None:
    _Site(fake_site)

    with pytest.raises(ValueError, match="not a field property"):
        standalone.customize_doctype(
            DOCTYPE,
            [
                {
                    "op": "set_property",
                    "fieldname": "notes",
                    "property": "collapsible_depends_on",
                    "value": "eval:1",
                }
            ],
        )
    with pytest.raises(ValueError, match="options of HTML fields are code"):
        standalone.customize_doctype(
            DOCTYPE,
            [
                {
                    "op": "add_custom_field",
                    "field": {"fieldname": "banner", "fieldtype": "HTML", "options": "<b>x</b>"},
                }
            ],
        )
    with pytest.raises(ValueError, match="Check property"):
        standalone.customize_doctype(
            DOCTYPE,
            [{"op": "set_property", "fieldname": "notes", "property": "hidden", "value": "yes"}],
        )


def test_an_operation_that_changes_nothing_is_refused(fake_site) -> None:
    _Site(fake_site)

    with pytest.raises(ValueError, match="unchanged"):
        standalone.customize_doctype(
            DOCTYPE,
            [{"op": "set_property", "fieldname": "notes", "property": "hidden", "value": 0}],
        )


def test_site_custom_and_single_doctypes_are_refused(fake_site) -> None:
    fake_site.add_doctype("Lab Custom", [], custom=1)
    fake_site.add_doctype("Lab Settings", [], issingle=1)

    with pytest.raises(ValueError, match="update_doctype"):
        standalone.customize_doctype(
            "Lab Custom", [{"op": "set_property", "property": "track_changes", "value": 1}]
        )
    with pytest.raises(ValueError, match="Single"):
        standalone.customize_doctype(
            "Lab Settings", [{"op": "set_property", "property": "track_changes", "value": 1}]
        )


def test_a_stale_preview_is_refused(fake_site) -> None:
    site = _Site(fake_site)
    operations = [{"op": "set_property", "fieldname": "notes", "property": "hidden", "value": 1}]
    preview = standalone.customize_doctype(DOCTYPE, operations)

    with pytest.raises(ValueError, match="fresh preview"):
        standalone.customize_doctype(DOCTYPE, operations, expected_preview="0" * 64, confirm=True)

    site.setters = [_setter("status", "label", "State")]
    with pytest.raises(ValueError, match="fresh preview"):
        standalone.customize_doctype(
            DOCTYPE, operations, expected_preview=preview["preview_id"], confirm=True
        )
    assert not fake_site.calls_to("POST")


def test_a_change_frappe_skipped_is_reported(fake_site) -> None:
    site = _Site(fake_site)
    site.after = []  # Frappe saved, but wrote no setter (it skipped the change with a message)
    operations = [{"op": "set_property", "fieldname": "notes", "property": "hidden", "value": 1}]
    preview = standalone.customize_doctype(DOCTYPE, operations)

    result = standalone.customize_doctype(
        DOCTYPE, operations, expected_preview=preview["preview_id"], confirm=True
    )

    assert "Property Setter notes.hidden" in result["warnings"][-1]


def test_making_a_field_mandatory_warns_with_the_empty_count(fake_site) -> None:
    _Site(fake_site)
    fake_site.add("GET", COUNT_PATH, 3)

    preview = standalone.customize_doctype(
        DOCTYPE, [{"op": "set_property", "fieldname": "notes", "property": "reqd", "value": 1}]
    )

    assert "3 records leave it empty" in preview["warnings"][0]
