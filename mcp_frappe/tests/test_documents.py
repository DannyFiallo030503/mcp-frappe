import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mcp_frappe.tools import documents


class TestDocumentWriteGuards(unittest.TestCase):
    def test_write_values_reject_protected_and_unknown_fields(self):
        meta = Mock()
        meta.get_permitted_fieldnames.return_value = ["title"]
        meta.has_field.side_effect = lambda field: field == "title"
        meta.get_field.return_value = SimpleNamespace(read_only=False, fieldtype="Data")
        with patch.object(documents.frappe, "session", SimpleNamespace(user="Administrator")):
            with self.assertRaises(frappe.ValidationError):
                documents._validate_values(meta, {"docstatus": 1}, "write")
            with self.assertRaises(frappe.ValidationError):
                documents._validate_values(meta, {"missing": "value"}, "write")

    def test_write_values_enforce_field_permissions_and_read_only(self):
        meta = Mock()
        meta.get_permitted_fieldnames.return_value = ["title", "locked"]
        meta.has_field.return_value = True
        with (
            patch.object(documents.frappe, "session", SimpleNamespace(user="reader@example.com")),
            patch.object(meta, "get_field", return_value=SimpleNamespace(read_only=False)),
            self.assertRaises(frappe.PermissionError),
        ):
            documents._validate_values(meta, {"secret": "value"}, "write")

        with (
            patch.object(documents.frappe, "session", SimpleNamespace(user="Administrator")),
            patch.object(meta, "get_field", return_value=SimpleNamespace(read_only=True)),
            self.assertRaises(frappe.ValidationError),
        ):
            documents._validate_values(meta, {"locked": "value"}, "write")

    def test_table_values_must_be_objects(self):
        meta = Mock(name="Example")
        meta.name = "Example"
        meta.get_permitted_fieldnames.return_value = ["items"]
        meta.has_field.return_value = True
        meta.get_field.return_value = SimpleNamespace(
            read_only=False, fieldtype="Table", options="Example Item"
        )
        with (
            patch.object(documents.frappe, "session", SimpleNamespace(user="Administrator")),
            self.assertRaises(frappe.ValidationError),
        ):
            documents._validate_values(meta, {"items": "not-a-list"}, "write")

    def test_create_preview_never_inserts(self):
        meta = Mock(issingle=False)
        with (
            patch.object(documents, "_get_meta", return_value=meta),
            patch.object(documents, "_assert_permission"),
            patch.object(documents, "_validate_values"),
            patch.object(documents.frappe.db, "exists", return_value=False),
            patch.object(documents.frappe, "get_doc") as get_doc,
        ):
            result = documents.create_document(
                "Example", {"title": "Example"}, name="EXAMPLE-1", confirm=False
            )

        self.assertTrue(result["preview"])
        self.assertTrue(result["idempotent"])
        get_doc.assert_not_called()

    def test_update_preview_does_not_save(self):
        meta = Mock(issingle=False)
        doc = Mock()
        doc.name = "EXAMPLE-1"
        doc.modified = datetime(2026, 1, 1)
        with (
            patch.object(documents, "_get_meta", return_value=meta),
            patch.object(documents, "_get_doc", return_value=doc),
            patch.object(documents, "_assert_permission"),
            patch.object(documents, "_validate_values"),
            patch.object(documents, "_modified", return_value="stamp"),
        ):
            result = documents.update_document("Example", {"title": "Updated"}, "EXAMPLE-1")

        self.assertTrue(result["preview"])
        self.assertEqual(result["modified"], "stamp")
        doc.save.assert_not_called()

    def test_update_confirmation_rejects_stale_preview(self):
        doc = Mock()
        doc.name = "EXAMPLE-1"
        doc.modified = datetime(2026, 1, 1)
        with (
            patch.object(documents, "_get_meta", return_value=Mock(issingle=False)),
            patch.object(documents, "_get_doc", return_value=doc),
            patch.object(documents, "_assert_permission"),
            patch.object(documents, "_validate_values"),
            patch.object(documents, "_modified", return_value="current"),
            patch.object(documents.frappe.db, "get_value", return_value=datetime(2026, 1, 1)),
            self.assertRaises(frappe.TimestampMismatchError),
        ):
            documents.update_document(
                "Example",
                {"title": "Updated"},
                "EXAMPLE-1",
                expected_modified="stale",
                confirm=True,
            )
        doc.save.assert_not_called()

    def test_delete_preview_never_deletes(self):
        doc = Mock()
        doc.name = "EXAMPLE-1"
        doc.modified = datetime(2026, 1, 1)
        with (
            patch.object(documents, "_get_meta", return_value=Mock(issingle=False)),
            patch.object(documents, "_get_doc", return_value=doc),
            patch.object(documents, "_assert_permission"),
            patch.object(documents, "_modified", return_value="stamp"),
            patch.object(documents.frappe, "delete_doc") as delete_doc,
        ):
            result = documents.delete_document("Example", "EXAMPLE-1")

        self.assertTrue(result["preview"])
        delete_doc.assert_not_called()

    def test_submit_rejects_non_submittable_doctype(self):
        meta = Mock(is_submittable=False)
        with (
            patch.object(documents, "_get_meta", return_value=meta),
            self.assertRaises(frappe.ValidationError),
        ):
            documents.submit_document("ToDo", "TODO-1")

    def test_submit_and_cancel_confirmation_call_native_document_methods(self):
        meta = Mock(is_submittable=True, issingle=False)
        doc = Mock()
        doc.name = "ORDER-1"
        doc.docstatus = 0
        doc.submit.side_effect = lambda: setattr(doc, "docstatus", 1)
        doc.cancel.side_effect = lambda: setattr(doc, "docstatus", 2)
        with (
            patch.object(documents, "_get_meta", return_value=meta),
            patch.object(documents, "_get_doc", return_value=doc),
            patch.object(documents, "_assert_permission"),
            patch.object(documents, "_modified", return_value="stamp"),
            patch.object(documents, "_check_expected_modified"),
        ):
            submitted = documents.submit_document(
                "Example Order", "ORDER-1", expected_modified="stamp", confirm=True
            )
            cancelled = documents.cancel_document(
                "Example Order", "ORDER-1", expected_modified="stamp", confirm=True
            )

        self.assertTrue(submitted["submitted"])
        self.assertTrue(cancelled["cancelled"])
        doc.submit.assert_called_once_with()
        doc.cancel.assert_called_once_with()

    def test_whitelisted_method_preview_does_not_execute(self):
        fn = Mock()
        with (
            patch.object(documents.frappe, "get_attr", return_value=fn),
            patch.object(documents.frappe, "is_whitelisted"),
            patch.dict(documents.frappe.allowed_http_methods_for_whitelisted_func, {fn: ["POST"]}),
            patch.object(documents.frappe, "call") as call,
        ):
            result = documents.call_whitelisted_method(
                "frappe.client.get_count", {"doctype": "ToDo"}
            )

        self.assertTrue(result["preview"])
        self.assertTrue(result["effects_unknown"])
        call.assert_not_called()

    def test_whitelisted_method_must_allow_mcp_post(self):
        fn = Mock()
        with (
            patch.object(documents.frappe, "get_attr", return_value=fn),
            patch.object(documents.frappe, "is_whitelisted"),
            patch.dict(documents.frappe.allowed_http_methods_for_whitelisted_func, {fn: ["GET"]}),
            self.assertRaises(frappe.PermissionError),
        ):
            documents.call_whitelisted_method("custom_app.api.read_only", confirm=True)

    def test_whitelisted_method_requires_frappe_whitelist(self):
        fn = Mock()
        with (
            patch.object(documents.frappe, "get_attr", return_value=fn),
            patch.object(
                documents.frappe,
                "is_whitelisted",
                side_effect=frappe.PermissionError,
            ),
            patch.object(documents.frappe, "call") as call,
            self.assertRaises(frappe.PermissionError),
        ):
            documents.call_whitelisted_method("custom_app.api.dangerous", confirm=True)
        call.assert_not_called()


class TestDocumentWritesAgainstFrappe(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self.old_user = frappe.session.user
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user(self.old_user)
        super().tearDown()

    def test_create_is_idempotent_and_update_delete_require_confirmation(self):
        name = "MCP-WRITE-TEST-0001"
        values = {"description": "MCP write test", "status": "Open"}

        preview = documents.create_document("ToDo", values, name=name)
        self.assertTrue(preview["preview"])
        self.assertFalse(frappe.db.exists("ToDo", name))

        created = documents.create_document("ToDo", values, name=name, confirm=True)
        self.assertEqual(created["name"], name)
        replay = documents.create_document("ToDo", values, name=name, confirm=True)
        self.assertTrue(replay["already_exists"])
        self.assertTrue(replay["idempotent_replay"])
        with self.assertRaises(frappe.ValidationError):
            documents.create_document("ToDo", {"description": "different"}, name=name, confirm=True)

        update_preview = documents.update_document(
            "ToDo", {"description": "MCP write test updated"}, name=name
        )
        self.assertEqual(update_preview["name"], name)
        with self.assertRaises(frappe.TimestampMismatchError):
            documents.update_document(
                "ToDo",
                {"description": "stale"},
                name=name,
                expected_modified="2000-01-01 00:00:00",
                confirm=True,
            )
        updated = documents.update_document(
            "ToDo",
            {"description": "MCP write test updated"},
            name=name,
            expected_modified=update_preview["modified"],
            confirm=True,
        )
        self.assertTrue(updated["updated"])
        self.assertEqual(frappe.db.get_value("ToDo", name, "description"), "MCP write test updated")

        delete_preview = documents.delete_document("ToDo", name)
        self.assertTrue(delete_preview["preview"])
        self.assertTrue(frappe.db.exists("ToDo", name))
        deleted = documents.delete_document(
            "ToDo", name, expected_modified=delete_preview["modified"], confirm=True
        )
        self.assertTrue(deleted["deleted"])
        self.assertFalse(frappe.db.exists("ToDo", name))

    def test_update_rejects_protected_fields_and_whitelisted_calls_execute(self):
        with self.assertRaises(frappe.ValidationError):
            documents.update_document("ToDo", {"docstatus": 1}, name="TODO-1")

        result = documents.call_whitelisted_method(
            "frappe.client.get_count",
            {"doctype": "ToDo"},
            confirm=True,
        )
        self.assertTrue(result["called"])
        self.assertEqual(result["method"], "frappe.client.get_count")
