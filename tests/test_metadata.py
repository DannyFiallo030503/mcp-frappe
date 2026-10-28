"""Metadata layer: `getdoctype` bundles, the TTL cache, compact schema, and compact records."""

from __future__ import annotations

import json

import pytest

from frappe_mcp import standalone

MODIFIED = "2026-10-19 12:00:00.000001"

# DocField's Check and Int properties in Frappe v16.33.1 (docfield.json). `as_dict(no_nulls=True)`
# drops None but keeps these zeros, so every real field row carries all of them.
_DOCFIELD_ZEROS = (
    "reqd search_index in_list_view in_standard_filter in_global_search in_preview "
    "allow_in_quick_entry bold translatable collapsible fetch_if_empty hidden read_only unique "
    "set_only_once allow_bulk_edit permlevel ignore_user_permissions allow_on_submit report_hide "
    "remember_last_selected_value ignore_xss_filter in_filter no_copy print_hide "
    "print_hide_if_no_value columns hide_days hide_seconds hide_border non_negative "
    "show_dashboard is_virtual sort_options not_nullable show_on_timeline "
    "make_attachment_public sticky mask show_description_on_click length"
).split()


def _docfield(parent: str, idx: int, fieldname: str, fieldtype: str, **values) -> dict:
    """A field row shaped like the ones `getdoctype` returns."""
    return {
        "name": f"{idx:010x}",
        "owner": "Administrator",
        "creation": "2026-01-05 10:00:00.000000",
        "modified": "2026-01-05 10:00:00.000000",
        "modified_by": "Administrator",
        "docstatus": 0,
        "idx": idx,
        "parent": parent,
        "parentfield": "fields",
        "parenttype": "DocType",
        "doctype": "DocField",
        **{key: 0 for key in _DOCFIELD_ZEROS},
        "fieldname": fieldname,
        "fieldtype": fieldtype,
        "label": fieldname.replace("_", " ").title(),
        "oldfieldname": fieldname,
        "oldfieldtype": fieldtype,
        "print_width": "",
        "width": "",
        **values,
    }


def _doctype_meta(name: str, fields: list[dict], **values) -> dict:
    """A DocType entry of a `getdoctype` response, form assets included."""
    return {
        "name": name,
        "owner": "Administrator",
        "creation": "2026-01-05 10:00:00.000000",
        "modified": "2026-01-05 10:00:00.000000",
        "modified_by": "Administrator",
        "docstatus": 0,
        "idx": 0,
        "doctype": "DocType",
        "module": "Selling",
        "custom": 0,
        "istable": 0,
        "issingle": 0,
        "is_submittable": 0,
        "is_tree": 0,
        "track_changes": 1,
        "naming_rule": "Expression",
        "autoname": "naming_series:",
        "sort_field": "modified",
        "sort_order": "DESC",
        "engine": "InnoDB",
        "fields": fields,
        "permissions": [
            {"role": "Sales User", "read": 1, "write": 1, "create": 1, "permlevel": 0}
        ],
        "links": [],
        "actions": [],
        "states": [],
        "masked_fields": [],
        "__js": "frappe.ui.form.on('" + name + "', {});\n" * 50,
        "__list_js": "frappe.listview_settings['" + name + "'] = {};",
        "__print_formats": [],
        "__workflow_docs": [],
        "__form_grid_templates": {},
        "__dashboard": {"fieldname": "customer"},
        **values,
    }


def _sales_order_bundle() -> dict:
    """A 100-field DocType with layout fields and one child table."""
    fields = []
    layout = ("Section Break", "Column Break", "Tab Break", "HTML", "Button", "Fold")
    for idx in range(1, 101):
        if idx % 10 == 0:
            fieldtype = layout[(idx // 10) % len(layout)]
            fields.append(_docfield("Sales Order", idx, f"layout_{idx}", fieldtype))
        elif idx == 5:
            fields.append(
                _docfield("Sales Order", idx, "items", "Table", options="Sales Order Item", reqd=1)
            )
        else:
            fields.append(_docfield("Sales Order", idx, f"field_{idx}", "Data"))
    child_fields = [
        _docfield("Sales Order Item", 1, "item_code", "Link", options="Item", reqd=1),
        _docfield("Sales Order Item", 2, "section_qty", "Section Break"),
        _docfield("Sales Order Item", 3, "qty", "Float"),
    ]
    return {
        "docs": [
            _doctype_meta("Sales Order", fields, title_field="field_1"),
            _doctype_meta("Sales Order Item", child_fields, istable=1),
        ],
        "user_settings": "{}",
    }


@pytest.fixture
def sales_order(fake_site):
    fake_site.add("GET", standalone._GETDOCTYPE, _sales_order_bundle())
    return fake_site


@pytest.fixture
def site(fake_site):
    fake_site.add_doctype(
        "Example",
        [
            {"fieldname": "title", "fieldtype": "Data", "label": "Title"},
            {"fieldname": "details", "fieldtype": "Section Break"},
            {"fieldname": "notes", "fieldtype": "Small Text"},
            {"fieldname": "qty", "fieldtype": "Int"},
            {"fieldname": "items", "fieldtype": "Table", "options": "Example Item"},
            {"fieldname": "tags", "fieldtype": "Table MultiSelect", "options": "Example Tag"},
        ],
    )
    fake_site.add_doctype(
        "Example Item",
        [
            {"fieldname": "item_code", "fieldtype": "Data"},
            {"fieldname": "col", "fieldtype": "Column Break"},
            {"fieldname": "rate", "fieldtype": "Currency"},
        ],
        istable=1,
    )
    fake_site.add_doctype("Example Tag", [{"fieldname": "tag", "fieldtype": "Link"}], istable=1)
    fake_site.add("GET", "/api/resource/Example", [{"name": "EX-1"}])
    fake_site.add(
        "GET",
        "/api/resource/Example/EX-1",
        {
            "doctype": "Example",
            "name": "EX-1",
            "owner": "dev@example.localhost",
            "modified": MODIFIED,
            "docstatus": 0,
            "title": "First",
            "notes": None,
            "qty": 0,
            "items": [
                {
                    "doctype": "Example Item",
                    "name": f"row{index}",
                    "idx": index,
                    "parent": "EX-1",
                    "parenttype": "Example",
                    "parentfield": "items",
                    "owner": "dev@example.localhost",
                    "item_code": f"ITEM-{index}",
                    "rate": 0,
                    "col": None,
                }
                for index in range(1, 4)
            ],
            "tags": [{"name": f"tag{index}", "idx": index, "tag": "x"} for index in range(1, 4)],
        },
    )
    return fake_site


def _getdoctype_calls(site) -> list:
    return site.calls_to("GET", standalone._GETDOCTYPE)


def test_meta_bundle_reads_getdoctype_and_drops_form_assets(sales_order) -> None:
    bundle = standalone._meta_bundle("Sales Order")

    (call,) = _getdoctype_calls(sales_order)
    assert call.params == {"doctype": "Sales Order"}
    assert bundle["doctype"]["name"] == "Sales Order"
    assert list(bundle["children"]) == ["Sales Order Item"]
    assert not [key for key in bundle["doctype"] if key.startswith("__")]
    assert bundle["doctype"]["permissions"][0]["role"] == "Sales User"


def test_meta_bundle_rejects_an_unexpected_response(fake_site) -> None:
    fake_site.add("GET", standalone._GETDOCTYPE, {"message": "nope"})

    with pytest.raises(RuntimeError, match="Unexpected getdoctype response"):
        standalone._meta_bundle("Example")


def test_meta_bundle_rejects_unsafe_names(fake_site) -> None:
    with pytest.raises(ValueError, match="Invalid DocType name"):
        standalone._meta_bundle("../DocType")
    assert fake_site.calls == []


def test_record_tools_never_read_raw_schema_resources(site) -> None:
    standalone.list_documents(
        "Example",
        filters=[["Example Item", "item_code", "=", "ITEM-1"]],
        fields=["name", "title"],
    )
    standalone.get_document("Example", "EX-1")
    standalone.get_document("Example", "EX-1", fields=["title", "items"])

    assert [call.path for call in site.meta_calls()] == [standalone._GETDOCTYPE]


def test_filtered_list_makes_two_requests_cold_and_one_warm(site) -> None:
    arguments = {
        "doctype": "Example",
        "filters": [["Example", "title", "like", "%a%"], ["Example Tag", "tag", "=", "x"]],
        "fields": ["name", "title"],
    }

    standalone.list_documents(**arguments)
    assert len(site.calls) == 2

    standalone.list_documents(**arguments)
    assert len(site.calls) == 3
    assert len(_getdoctype_calls(site)) == 1


def test_child_table_validation_uses_the_bundle(site) -> None:
    standalone.list_documents("Example", filters=[["Example Item", "rate", ">", 1]])

    with pytest.raises(ValueError, match="Unknown field for Example Item"):
        standalone.list_documents("Example", filters=[["Example Item", "missing", "=", 1]])

    calls = _getdoctype_calls(site)
    assert [call.params["doctype"] for call in calls] == ["Example"]


def test_cache_expires_after_the_ttl(site, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_META_TTL", "60")
    now = [1000.0]
    monkeypatch.setattr(standalone.time, "monotonic", lambda: now[0])

    standalone._meta_bundle("Example")
    now[0] += 59
    standalone._meta_bundle("Example")
    assert len(_getdoctype_calls(site)) == 1

    now[0] += 2
    standalone._meta_bundle("Example")
    assert len(_getdoctype_calls(site)) == 2


def test_zero_ttl_turns_the_cache_off(site, monkeypatch) -> None:
    monkeypatch.setenv("FRAPPE_META_TTL", "0")

    standalone._meta_bundle("Example")
    standalone._meta_bundle("Example")

    assert len(_getdoctype_calls(site)) == 2
    assert standalone._meta_cache == {}


@pytest.mark.parametrize("value", ["-1", "soon", "nan", "inf"])
def test_invalid_ttl_is_rejected(site, monkeypatch, value) -> None:
    monkeypatch.setenv("FRAPPE_META_TTL", value)

    with pytest.raises(RuntimeError, match="FRAPPE_META_TTL"):
        standalone._meta_bundle("Example")


def test_cached_bundles_are_copies(site) -> None:
    standalone._meta_bundle("Example")["doctype"]["fields"].clear()

    assert standalone._meta_bundle("Example")["doctype"]["fields"]


def _warm(site, *doctypes: str) -> None:
    for doctype in doctypes:
        standalone._meta_bundle(doctype)
    site.calls.clear()


def test_create_custom_field_invalidates_the_doctype_and_its_parents(site) -> None:
    site.add("POST", standalone._resource("Custom Field"), {"name": "Example Item-custom_x"})
    _warm(site, "Example", "Example Item", "Example Tag")
    field = {"fieldname": "custom_x", "fieldtype": "Data"}

    standalone.create_custom_field(doctype="Example Item", field=field)
    assert set(standalone._meta_cache) == {"Example", "Example Item", "Example Tag"}

    standalone.create_custom_field(doctype="Example Item", field=field, confirm=True)
    assert set(standalone._meta_cache) == {"Example Tag"}

    standalone.get_document("Example", "EX-1")
    assert [call.params["doctype"] for call in _getdoctype_calls(site)] == ["Example"]


def test_failed_custom_field_write_still_invalidates(site) -> None:
    site.add("POST", standalone._resource("Custom Field"), standalone.FrappeError("boom"))
    _warm(site, "Example")

    with pytest.raises(standalone.FrappeError):
        standalone.create_custom_field(
            doctype="Example", field={"fieldname": "custom_x", "fieldtype": "Data"}, confirm=True
        )

    assert "Example" not in standalone._meta_cache


def test_set_field_property_invalidates_the_doctype(site) -> None:
    site.add("GET", standalone._resource("Property Setter"), [])
    site.add("POST", standalone._resource("Property Setter"), {"name": "Example-title-label"})
    _warm(site, "Example")

    standalone.set_field_property("Example", "label", "Name", fieldname="title", confirm=True)

    assert "Example" not in standalone._meta_cache


def test_set_role_permissions_invalidates_the_doctype(site) -> None:
    site.add("GET", standalone._resource("Role"), [{"name": "Tester", "disabled": 0}])
    site.add("GET", standalone._resource("Custom DocPerm"), [{"name": "perm1"}])
    site.add(
        "POST", "/api/method/frappe.core.page.permission_manager.permission_manager.update", None
    )
    _warm(site, "Example")

    standalone.set_role_permissions("Example", "Tester", {"read": True})
    assert "Example" in standalone._meta_cache

    standalone.set_role_permissions("Example", "Tester", {"read": True}, confirm=True)
    assert "Example" not in standalone._meta_cache


def test_writing_a_schema_record_clears_the_whole_cache(site) -> None:
    site.add_doctype("Custom Field", [{"fieldname": "label", "fieldtype": "Data"}])
    site.add(
        "GET",
        standalone._resource("Custom Field", "Example-custom_x"),
        {"name": "Example-custom_x", "modified": MODIFIED},
    )
    site.add("PUT", standalone._resource("Custom Field", "Example-custom_x"), {})
    _warm(site, "Example", "Example Tag")

    standalone.update_document(
        "Custom Field",
        "Example-custom_x",
        {"label": "X"},
        expected_modified=MODIFIED,
        confirm=True,
    )

    assert standalone._meta_cache == {}


def test_writing_an_ordinary_record_keeps_the_cache(site) -> None:
    site.add("PUT", "/api/resource/Example/EX-1", {})
    _warm(site, "Example")

    standalone.update_document(
        "Example", "EX-1", {"title": "Second"}, expected_modified=MODIFIED, confirm=True
    )

    assert "Example" in standalone._meta_cache


def test_compact_schema_has_no_layout_fields_or_falsy_keys(sales_order) -> None:
    schema = standalone.get_doctype_schema("Sales Order")

    assert schema["name"] == "Sales Order"
    assert schema["module"] == "Selling"
    assert schema["title_field"] == "field_1"
    assert "istable" not in schema and "custom" not in schema
    fieldtypes = {row["fieldtype"] for row in schema["fields"]}
    assert fieldtypes == {"Data", "Table"}
    assert len(schema["fields"]) == 90
    for row in [*schema["fields"], *schema["child_tables"]["Sales Order Item"]]:
        assert set(row) <= set(standalone._COMPACT_FIELD_KEYS)
        assert all(row.values())
    assert schema["fields"][4] == {
        "fieldname": "items",
        "label": "Items",
        "fieldtype": "Table",
        "options": "Sales Order Item",
        "reqd": 1,
    }
    assert [row["fieldname"] for row in schema["child_tables"]["Sales Order Item"]] == [
        "item_code",
        "qty",
    ]


def test_compact_schema_is_at_least_five_times_smaller_than_full(sales_order) -> None:
    compact = json.dumps(standalone.get_doctype_schema("Sales Order"))
    full = json.dumps(standalone.get_doctype_schema("Sales Order", detail="full"))

    assert len(full) / len(compact) >= 5


def test_full_schema_returns_the_bundle(sales_order) -> None:
    schema = standalone.get_doctype_schema("Sales Order", detail="full")

    assert set(schema) == {"doctype", "children", "workflow"}
    assert len(schema["doctype"]["fields"]) == 100
    assert "Sales Order Item" in schema["children"]


def test_schema_detail_is_validated(sales_order) -> None:
    with pytest.raises(ValueError, match="detail"):
        standalone.get_doctype_schema("Sales Order", detail="everything")


def test_default_get_document_returns_only_non_empty_data_fields(site) -> None:
    result = standalone.get_document("Example", "EX-1")

    assert result["title"] == "First"
    assert result["qty"] == 0
    assert "notes" not in result
    assert "owner" not in result
    assert {"doctype", "name", "modified", "docstatus"} <= set(result)
    assert result["items"][0] == {"name": "row1", "idx": 1, "item_code": "ITEM-1", "rate": 0}


def test_get_document_with_fields_keeps_full_child_rows(site) -> None:
    result = standalone.get_document("Example", "EX-1", fields=["items", "notes", "owner"])

    assert result["notes"] is None
    assert result["owner"] == "dev@example.localhost"
    assert result["items"][0]["parent"] == "EX-1"


def test_get_document_truncates_table_multiselect(site) -> None:
    result = standalone.get_document("Example", "EX-1", child_row_limit=2)

    assert len(result["items"]) == 2
    assert len(result["tags"]) == 2
    assert result["_truncated_child_tables"] == ["items", "tags"]


def test_export_follows_child_tables_from_the_bundle(site) -> None:
    assert standalone._customized_doctypes(["Example"]) == [
        "Example",
        "Example Item",
        "Example Tag",
    ]
    assert [call.path for call in site.meta_calls()] == [standalone._GETDOCTYPE] * 3
