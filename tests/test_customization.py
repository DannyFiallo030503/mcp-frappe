"""Custom Field and Property Setter tools: no local checkout, fixed keys, update in place."""

from __future__ import annotations

import pytest

from frappe_mcp import standalone

MODIFIED = "2026-10-19 12:00:00.000001"
SETTER = "Customer-customer_group-label"


@pytest.fixture
def site(fake_site, monkeypatch):
    monkeypatch.delenv("FRAPPE_APPS_PATH", raising=False)
    fake_site.add_doctype(
        "Customer",
        [
            {"fieldname": "customer_name", "fieldtype": "Data"},
            {"fieldname": "customer_group", "fieldtype": "Link", "label": "Customer Group"},
        ],
    )
    fake_site.add(
        "GET",
        standalone._resource("Module Def"),
        [{"name": "Selling", "app_name": "erpnext"}],
        params={"filters": standalone._filters({"name": "Selling"})},
    )
    fake_site.add(
        "GET",
        standalone._resource("Module Def"),
        [],
        params={"filters": standalone._filters({"name": "Missing"})},
    )
    fake_site.add("POST", standalone._resource("Custom Field"), {"name": "Customer-custom_x"})
    fake_site.add("POST", standalone._resource("Property Setter"), {"name": SETTER})
    return fake_site


def _setters(site, property: str, rows: list[dict]) -> None:
    site.add(
        "GET",
        standalone._resource("Property Setter"),
        rows,
        params={"filters": standalone._filters({"doc_type": "Customer", "property": property})},
    )


FIELD = {
    "fieldname": "custom_x",
    "fieldtype": "Data",
    "label": "X",
    "insert_after": "customer_name",
}


def test_custom_field_needs_no_local_checkout(site) -> None:
    preview = standalone.create_custom_field(doctype="Customer", field=FIELD)
    result = standalone.create_custom_field(doctype="Customer", field=FIELD, confirm=True)

    assert preview["requires_confirmation"] is True
    assert result == {"name": "Customer-custom_x"}
    (call,) = site.calls_to("POST", standalone._resource("Custom Field"))
    assert call.json == {"data": {**FIELD, "doctype": "Custom Field", "dt": "Customer"}}
    assert not site.calls_to("GET", standalone._resource("Module Def"))


@pytest.mark.parametrize("key", ["dt", "module", "doctype"])
def test_custom_field_rejects_fixed_keys_in_field(site, key) -> None:
    with pytest.raises(ValueError, match=key):
        standalone.create_custom_field(doctype="Customer", field={**FIELD, key: "Other"})
    assert not site.calls_to("POST")


def test_custom_field_rejects_existing_fieldname(site) -> None:
    with pytest.raises(ValueError, match="already exists"):
        standalone.create_custom_field(
            doctype="Customer", field={"fieldname": "customer_name", "fieldtype": "Data"}
        )


def test_custom_field_module_is_checked_remotely(site) -> None:
    standalone.create_custom_field(
        doctype="Customer", field=FIELD, app="erpnext", module="Selling", confirm=True
    )

    (call,) = site.calls_to("POST", standalone._resource("Custom Field"))
    assert call.json["data"]["module"] == "Selling"
    assert call.json["data"]["dt"] == "Customer"


def test_custom_field_module_app_mismatch_is_rejected(site) -> None:
    with pytest.raises(ValueError, match="does not belong to app other_app"):
        standalone.create_custom_field(
            doctype="Customer", field=FIELD, app="other_app", module="Selling"
        )
    with pytest.raises(ValueError, match="Unknown module"):
        standalone.create_custom_field(doctype="Customer", field=FIELD, module="Missing")
    with pytest.raises(ValueError, match="module"):
        standalone.create_custom_field(doctype="Customer", field=FIELD, app="erpnext")
    assert not site.calls_to("POST")


def test_property_setter_is_created_without_local_checkout(site) -> None:
    _setters(site, "label", [])

    preview = standalone.set_field_property(
        doctype="Customer", fieldname="customer_group", property="label", value="Group"
    )
    standalone.set_field_property(
        doctype="Customer",
        fieldname="customer_group",
        property="label",
        value="Group",
        confirm=True,
    )

    assert preview["mode"] == "create"
    (call,) = site.calls_to("POST", standalone._resource("Property Setter"))
    data = call.json["data"]
    assert data["property_type"] == "Data"
    assert data["doctype_or_field"] == "DocField"
    assert data["field_name"] == "customer_group"
    assert "module" not in data


def test_second_property_change_updates_the_existing_setter(site) -> None:
    existing = {
        "name": SETTER,
        "field_name": "customer_group",
        "row_name": None,
        "value": "Group",
        "property_type": "Data",
        "modified": MODIFIED,
    }
    _setters(site, "label", [existing])
    site.add("GET", standalone._resource("Property Setter", SETTER), existing)
    site.add("PUT", standalone._resource("Property Setter", SETTER), {"name": SETTER})
    arguments = {
        "doctype": "Customer",
        "fieldname": "customer_group",
        "property": "label",
        "value": "Segment",
    }

    preview = standalone.set_field_property(**arguments)
    standalone.set_field_property(**arguments, expected_modified=MODIFIED, confirm=True)

    assert preview["mode"] == "update"
    assert preview["name"] == SETTER
    assert preview["current_value"] == "Group"
    assert preview["value"] == "Segment"
    assert preview["modified"] == MODIFIED
    assert not site.calls_to("POST")
    (call,) = site.calls_to("PUT")
    assert call.json == {
        "data": {"value": "Segment", "property_type": "Data", "modified": MODIFIED}
    }


def test_property_setter_update_requires_current_modified(site) -> None:
    existing = {"name": SETTER, "field_name": "customer_group", "modified": MODIFIED}
    _setters(site, "label", [existing])
    site.add("GET", standalone._resource("Property Setter", SETTER), existing)
    arguments = {
        "doctype": "Customer",
        "fieldname": "customer_group",
        "property": "label",
        "value": "Segment",
        "confirm": True,
    }

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.set_field_property(**arguments)
    with pytest.raises(ValueError, match="changed since preview"):
        standalone.set_field_property(**arguments, expected_modified="2026-01-01 00:00:00")
    assert not site.calls_to("PUT")
    assert not site.calls_to("POST")


def test_property_setter_create_rejects_a_stale_modified_token(site) -> None:
    _setters(site, "label", [])

    with pytest.raises(ValueError, match="no longer exists"):
        standalone.set_field_property(
            doctype="Customer",
            fieldname="customer_group",
            property="label",
            value="Group",
            expected_modified=MODIFIED,
            confirm=True,
        )


def test_timestamp_mismatch_on_setter_put_reads_as_stale(site) -> None:
    existing = {"name": SETTER, "field_name": "customer_group", "modified": MODIFIED}
    _setters(site, "label", [existing])
    site.add("GET", standalone._resource("Property Setter", SETTER), existing)
    site.add(
        "PUT",
        standalone._resource("Property Setter", SETTER),
        standalone.FrappeError("changed", "TimestampMismatchError"),
    )

    with pytest.raises(ValueError, match="changed since preview"):
        standalone.set_field_property(
            doctype="Customer",
            fieldname="customer_group",
            property="label",
            value="Segment",
            expected_modified=MODIFIED,
            confirm=True,
        )


@pytest.mark.parametrize(
    ("property", "value", "expected_type", "stored"),
    [
        ("hidden", True, "Check", "1"),
        ("reqd", 0, "Check", "0"),
        ("bold", "1", "Check", "1"),
        ("label", "Group", "Data", "Group"),
        ("width", "160px", "Data", "160px"),
        ("depends_on", "eval:doc.customer_name", "Data", "eval:doc.customer_name"),
        ("options", "A\nB", "Text", "A\nB"),
        ("description", "Help", "Text", "Help"),
        ("default", "A", "Text", "A"),
    ],
)
def test_property_type_is_inferred(site, property, value, expected_type, stored) -> None:
    _setters(site, property, [])

    preview = standalone.set_field_property(
        doctype="Customer", fieldname="customer_group", property=property, value=value
    )

    assert preview["property_type"] == expected_type
    assert preview["value"] == stored


def test_property_type_must_match_the_map(site) -> None:
    with pytest.raises(ValueError, match="property_type for width is Data"):
        standalone.set_field_property(
            doctype="Customer",
            fieldname="customer_group",
            property="width",
            value="160",
            property_type="Int",
        )


@pytest.mark.parametrize("value", ["yes", 2, "", None, 1.5])
def test_check_values_must_be_zero_or_one(site, value) -> None:
    with pytest.raises(ValueError, match="0 or 1"):
        standalone.set_field_property(
            doctype="Customer", fieldname="customer_group", property="hidden", value=value
        )


def test_text_values_must_be_strings(site) -> None:
    with pytest.raises(ValueError, match="must be a string"):
        standalone.set_field_property(
            doctype="Customer", fieldname="customer_group", property="label", value=1
        )


def test_unsupported_property_and_unknown_field_are_rejected(site) -> None:
    with pytest.raises(ValueError, match="allowlist"):
        standalone.set_field_property(
            doctype="Customer", fieldname="customer_group", property="fieldtype", value="Data"
        )
    with pytest.raises(ValueError, match="Unknown fieldname"):
        standalone.set_field_property(
            doctype="Customer", fieldname="missing", property="label", value="X"
        )


def test_property_setter_module_app_mismatch_is_rejected(site) -> None:
    with pytest.raises(ValueError, match="does not belong to app other_app"):
        standalone.set_field_property(
            doctype="Customer",
            fieldname="customer_group",
            property="label",
            value="Group",
            app="other_app",
            module="Selling",
        )


def test_doctype_level_setter_lists_field_setters_frappe_replaces(site) -> None:
    _setters(
        site,
        "description",
        [{"name": "Customer-customer_group-description", "field_name": "customer_group"}],
    )

    preview = standalone.set_field_property(
        doctype="Customer", property="description", value="Customers we sell to"
    )

    assert preview["mode"] == "create"
    assert preview["doctype_or_field"] == "DocType"
    assert preview["replaces_field_setters"] == ["customer_group"]
