"""The record tools keep to record data: no schema, configuration, or code records or fields."""

from __future__ import annotations

import pytest

from frappe_mcp import standalone

MODIFIED = "2026-11-19 12:00:00.000001"


@pytest.fixture
def site(fake_site):
    fake_site.add_doctype(
        "Example",
        [
            {"fieldname": "title", "fieldtype": "Data"},
            {"fieldname": "script", "fieldtype": "Code", "options": "Python"},
            {"fieldname": "items", "fieldtype": "Table", "options": "Example Item"},
        ],
    )
    fake_site.add_doctype(
        "Example Item",
        [
            {"fieldname": "item_code", "fieldtype": "Data"},
            {"fieldname": "condition", "fieldtype": "Code", "options": "PythonExpression"},
        ],
        istable=1,
    )
    fake_site.add(
        "GET",
        "/api/resource/Example/EX-1",
        {"doctype": "Example", "name": "EX-1", "modified": MODIFIED},
    )
    return fake_site


def _write(tool: str, doctype: str, confirm: bool = False):
    if tool == "create":
        return standalone.create_document(doctype, {"title": "x"}, confirm=confirm)
    if tool == "update":
        return standalone.update_document(
            doctype, "X-1", {"title": "x"}, expected_modified=MODIFIED, confirm=confirm
        )
    return standalone.delete_document(doctype, "X-1", expected_modified=MODIFIED, confirm=confirm)


@pytest.mark.parametrize(
    ("tool", "doctype", "points_to"),
    [
        ("create", "DocType", "create_doctype"),
        ("update", "DocType", "update_doctype"),
        ("delete", "DocType", "delete_doctype"),
        ("create", "DocField", "update_doctype"),
        ("update", "DocField", "update_doctype"),
        ("delete", "DocField", "update_doctype"),
        ("update", "DocPerm", "set_role_permissions"),
        ("delete", "DocPerm", "set_role_permissions"),
        ("create", "Custom Field", "create_custom_field"),
        ("update", "Custom Field", "set_field_property"),
        ("create", "Property Setter", "set_field_property"),
        ("update", "Property Setter", "set_field_property"),
        ("create", "Custom DocPerm", "set_role_permissions"),
        ("update", "Custom DocPerm", "set_role_permissions"),
        ("update", "Customize Form", "set_field_property"),
        ("create", "Workflow", "upsert_workflow"),
        ("update", "Workflow", "upsert_workflow"),
        ("create", "Notification", "upsert_notification"),
        ("update", "Notification", "upsert_notification"),
        ("create", "Report", "upsert_report"),
        ("update", "Report", "upsert_report"),
        ("create", "Workspace", "upsert_workspace"),
        ("update", "Workspace", "upsert_workspace"),
    ],
)
def test_schema_and_configuration_writes_name_the_right_tool(
    fake_site, tool, doctype, points_to
) -> None:
    with pytest.raises(ValueError, match=points_to) as caught:
        _write(tool, doctype, confirm=True)

    assert f"{tool}_document" in str(caught.value)
    assert fake_site.calls == []


@pytest.mark.parametrize(
    "doctype",
    [
        "Custom Field",
        "Property Setter",
        "Custom DocPerm",
        "Workflow",
        "Notification",
        "Report",
        "Workspace",
    ],
)
def test_records_no_tool_deletes_can_still_be_deleted(fake_site, doctype) -> None:
    path = standalone._resource(doctype, "X-1")
    fake_site.add("GET", path, {"name": "X-1", "modified": MODIFIED})
    fake_site.add("DELETE", path, None)

    preview = standalone.delete_document(doctype, "X-1")
    result = standalone.delete_document(doctype, "X-1", expected_modified=MODIFIED, confirm=True)

    assert preview["modified"] == MODIFIED
    assert result["deleted"] is True
    assert len(fake_site.calls_to("DELETE", path)) == 1


@pytest.mark.parametrize("doctype", sorted(standalone._CODE_DOCTYPES))
@pytest.mark.parametrize("tool", ["create", "update", "delete"])
def test_code_doctypes_are_refused_by_every_record_tool(fake_site, tool, doctype) -> None:
    with pytest.raises(ValueError, match="code that Frappe runs"):
        _write(tool, doctype)

    assert fake_site.calls == []


def test_code_doctype_list_covers_the_frappe_records_that_hold_code() -> None:
    required = {
        "Server Script",
        "Client Script",
        "System Console",
        "Print Format",
        "Webhook",
        "Assignment Rule",
        "Web Form",
        "Custom HTML Block",
        "Email Template",
    }

    assert required <= standalone._CODE_DOCTYPES
    assert not standalone._CODE_DOCTYPES & set(standalone._RECORD_TOOL_REDIRECTS)


def test_a_code_field_write_is_refused_on_an_ordinary_doctype(site) -> None:
    with pytest.raises(ValueError, match=r"Example\.script is a Code field"):
        standalone.create_document("Example", {"title": "x", "script": "frappe.db.commit()"})
    with pytest.raises(ValueError, match=r"Example\.script is a Code field"):
        standalone.update_document("Example", "EX-1", {"script": "x = 1"})

    assert site.calls_to("POST") == [] and site.calls_to("PUT") == []


def test_a_code_field_in_a_child_row_is_refused(site) -> None:
    rows = [{"item_code": "A"}, {"item_code": "B", "condition": "doc.total > 0"}]

    with pytest.raises(ValueError, match=r"Example Item\.condition .*items row 2"):
        standalone.create_document("Example", {"title": "x", "items": rows}, confirm=True)

    assert site.calls_to("POST") == []


def test_child_rows_without_code_fields_are_written(site) -> None:
    site.add("POST", "/api/resource/Example", {"name": "EX-2"})

    result = standalone.create_document(
        "Example", {"title": "x", "items": [{"item_code": "A"}]}, confirm=True
    )

    assert result == {"name": "EX-2"}


def test_a_field_turned_into_code_by_a_property_setter_is_refused(fake_site) -> None:
    # getdoctype returns the field type after Property Setters, so the check follows it.
    fake_site.add_doctype("Note", [{"fieldname": "content", "fieldtype": "Code"}])

    with pytest.raises(ValueError, match="Code field"):
        standalone.create_document("Note", {"content": "x"})


def test_allowlisted_code_fields_hold_plain_data(fake_site) -> None:
    fake_site.add_doctype(
        "Translation",
        [
            {"fieldname": "language", "fieldtype": "Link", "options": "Language"},
            {"fieldname": "source_text", "fieldtype": "Code"},
            {"fieldname": "translated_text", "fieldtype": "Code"},
        ],
    )

    preview = standalone.create_document(
        "Translation", {"language": "es", "source_text": "Save", "translated_text": "Guardar"}
    )

    assert preview["preview"] is True
    for (doctype, fieldname), reason in standalone._PLAIN_CODE_FIELDS.items():
        assert doctype and fieldname and len(reason) > 20


def test_template_fields_outside_code_fields_are_refused(fake_site) -> None:
    fake_site.add_doctype(
        "Auto Repeat",
        [
            {"fieldname": "frequency", "fieldtype": "Select"},
            {"fieldname": "subject", "fieldtype": "Data"},
            {"fieldname": "message", "fieldtype": "Text"},
        ],
    )

    assert standalone.create_document("Auto Repeat", {"frequency": "Daily"})["preview"]
    with pytest.raises(ValueError, match="Jinja"):
        standalone.create_document("Auto Repeat", {"frequency": "Daily", "message": "{{ doc }}"})
