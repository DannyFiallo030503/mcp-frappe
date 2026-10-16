import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from mcp_frappe.tools import discovery


class TestReadableFields(unittest.TestCase):
    def test_administrator_sees_all_meta_fields(self):
        meta = SimpleNamespace(
            fields=[SimpleNamespace(fieldname="title"), SimpleNamespace(fieldname="")]
        )
        with patch.object(discovery.frappe, "session", SimpleNamespace(user="Administrator")):
            self.assertEqual(discovery._readable_fields(meta), {"title", "name"})

    def test_regular_user_uses_permitted_fieldnames(self):
        meta = Mock()
        meta.get_permitted_fieldnames.return_value = ["name", "title"]
        with patch.object(discovery.frappe, "session", SimpleNamespace(user="reader@example.com")):
            self.assertEqual(discovery._readable_fields(meta), {"name", "title"})
        meta.get_permitted_fieldnames.assert_called_once_with(
            user="reader@example.com", permission_type="read", parenttype=None
        )


class TestListDocuments(unittest.TestCase):
    def setUp(self):
        self.meta = Mock()
        self.meta.istable = 0
        self.meta.fields = [SimpleNamespace(fieldname="name"), SimpleNamespace(fieldname="title")]
        self.meta.get_search_fields.return_value = ["title"]
        self.patches = [
            patch.object(discovery.frappe.db, "exists", return_value=True),
            patch.object(discovery.frappe, "get_meta", return_value=self.meta),
            patch.object(discovery.frappe, "has_permission", return_value=True),
            patch.object(
                discovery.frappe, "get_list", return_value=[{"name": "A-1", "title": "A"}]
            ),
            patch.object(discovery.frappe, "session", SimpleNamespace(user="Administrator")),
        ]
        self.mocks = [item.start() for item in self.patches]
        self.addCleanup(self.stop_patches)

    def stop_patches(self):
        for item in reversed(self.patches):
            item.stop()

    def test_reads_requested_fields_through_get_list(self):
        self.assertEqual(
            discovery.list_documents("Example", fields=["name", "title"], limit=7),
            [{"name": "A-1", "title": "A"}],
        )
        self.mocks[3].assert_called_once_with(
            "Example",
            filters={},
            fields=["name", "title"],
            order_by="modified desc",
            page_length=7,
        )

    def test_rejects_missing_doctype(self):
        self.mocks[0].return_value = False
        with self.assertRaises(frappe.DoesNotExistError):
            discovery.list_documents("Missing")

    def test_rejects_child_tables_as_standalone_documents(self):
        self.meta.istable = 1
        with self.assertRaises(frappe.ValidationError):
            discovery.list_documents("Example Child")

    def test_rejects_limits_outside_one_to_one_hundred(self):
        for limit in (0, 101):
            with self.subTest(limit=limit), self.assertRaises(frappe.ValidationError):
                discovery.list_documents("Example", limit=limit)

    def test_rejects_unreadable_fields_and_unsearchable_filters(self):
        with self.assertRaises(frappe.PermissionError):
            discovery.list_documents("Example", fields=["secret"])
        with self.assertRaises(frappe.ValidationError):
            discovery.list_documents("Example", filters={"secret": "value"})

    def test_rejects_doctype_without_read_permission(self):
        self.mocks[2].return_value = False
        with self.assertRaises(frappe.PermissionError):
            discovery.list_documents("Example")


class TestGetDocTypeSchema(unittest.TestCase):
    def test_returns_only_fields_permitted_to_current_user(self):
        meta = Mock(
            istable=0,
            module="Example Module",
            custom=0,
            is_submittable=0,
            fields=[
                SimpleNamespace(
                    fieldname="title",
                    label="Title",
                    fieldtype="Data",
                    options=None,
                    reqd=1,
                    read_only=0,
                    hidden=0,
                    permlevel=0,
                ),
                SimpleNamespace(
                    fieldname="secret",
                    label="Secret",
                    fieldtype="Data",
                    options=None,
                    reqd=0,
                    read_only=0,
                    hidden=0,
                    permlevel=1,
                ),
            ],
        )
        meta.get_permitted_fieldnames.return_value = ["name", "title"]
        with (
            patch.object(discovery.frappe.db, "exists", return_value=True),
            patch.object(discovery.frappe, "get_meta", return_value=meta),
            patch.object(discovery.frappe, "has_permission", return_value=True),
            patch.object(discovery.frappe, "session", SimpleNamespace(user="reader@example.com")),
        ):
            schema = discovery.get_doctype_schema("Example")

        self.assertEqual([field["fieldname"] for field in schema["fields"]], ["title"])
        self.assertEqual(schema["module"], "Example Module")

    def test_rejects_doctype_without_read_permission(self):
        meta = SimpleNamespace(istable=0)
        with (
            patch.object(discovery.frappe.db, "exists", return_value=True),
            patch.object(discovery.frappe, "get_meta", return_value=meta),
            patch.object(discovery.frappe, "has_permission", return_value=False),
            self.assertRaises(frappe.PermissionError),
        ):
            discovery.get_doctype_schema("Example")


class TestGetDocument(unittest.TestCase):
    def test_serializes_readable_child_rows_and_reports_truncation(self):
        parent_meta = SimpleNamespace(
            istable=0,
            issingle=0,
            fields=[
                SimpleNamespace(fieldname="title", fieldtype="Data", options=None),
                SimpleNamespace(fieldname="rows", fieldtype="Table", options="Example Row"),
            ],
            has_field=lambda name: name in {"title", "rows"},
        )
        child_meta = SimpleNamespace(
            fields=[SimpleNamespace(fieldname="value")],
            has_field=lambda name: name == "value",
        )
        children = [
            SimpleNamespace(
                name=f"ROW-{n}", get=lambda key, n=n: f"value-{n}" if key == "value" else f"ROW-{n}"
            )
            for n in range(2)
        ]
        doc = SimpleNamespace(
            name="PARENT-1", get=lambda key: "Example" if key == "title" else children
        )

        with (
            patch.object(discovery.frappe.db, "exists", return_value=True),
            patch.object(discovery.frappe, "get_meta", side_effect=[parent_meta, child_meta]),
            patch.object(discovery.frappe, "get_doc", return_value=doc),
            patch.object(discovery.frappe, "has_permission", return_value=True),
            patch.object(discovery.frappe, "session", SimpleNamespace(user="Administrator")),
        ):
            result = discovery.get_document("Example", "PARENT-1", child_row_limit=1)

        self.assertEqual(result["title"], "Example")
        self.assertEqual(result["rows"], [{"name": "ROW-0", "value": "value-0"}])
        self.assertEqual(result["_truncated_child_tables"], ["rows"])

    def test_requires_name_for_non_single_doctype(self):
        meta = SimpleNamespace(istable=0, issingle=0)
        with (
            patch.object(discovery.frappe.db, "exists", return_value=True),
            patch.object(discovery.frappe, "get_meta", return_value=meta),
            self.assertRaises(frappe.ValidationError),
        ):
            discovery.get_document("Example")

    def test_rejects_child_tables_as_standalone_documents(self):
        meta = SimpleNamespace(istable=1)
        with (
            patch.object(discovery.frappe.db, "exists", return_value=True),
            patch.object(discovery.frappe, "get_meta", return_value=meta),
            self.assertRaises(frappe.ValidationError),
        ):
            discovery.get_document("Example Row", "ROW-1")

    def test_rejects_unreadable_requested_fields(self):
        meta = Mock(istable=0, issingle=0, fields=[SimpleNamespace(fieldname="secret")])
        meta.has_field.return_value = True
        meta.get_permitted_fieldnames.return_value = ["name"]
        doc = SimpleNamespace(name="PARENT-1", get=lambda key: "private")
        with (
            patch.object(discovery.frappe.db, "exists", return_value=True),
            patch.object(discovery.frappe, "get_meta", return_value=meta),
            patch.object(discovery.frappe, "get_doc", return_value=doc),
            patch.object(discovery.frappe, "has_permission", return_value=True),
            patch.object(discovery.frappe, "session", SimpleNamespace(user="reader@example.com")),
            self.assertRaises(frappe.PermissionError),
        ):
            discovery.get_document("Example", "PARENT-1", fields=["secret"])

    def test_rejects_document_without_read_permission(self):
        meta = SimpleNamespace(istable=0, issingle=0)
        doc = SimpleNamespace(name="PARENT-1")
        with (
            patch.object(discovery.frappe.db, "exists", return_value=True),
            patch.object(discovery.frappe, "get_meta", return_value=meta),
            patch.object(discovery.frappe, "get_doc", return_value=doc),
            patch.object(discovery.frappe, "has_permission", return_value=False),
            self.assertRaises(frappe.PermissionError),
        ):
            discovery.get_document("Example", "PARENT-1")


if __name__ == "__main__":
    unittest.main()
