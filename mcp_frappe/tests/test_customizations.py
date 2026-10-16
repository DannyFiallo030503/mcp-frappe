import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from mcp_frappe.tools import customizations
from mcp_frappe.tools.customizations import _fixture_filters, _merge_managed_filters


class TestManagedFixtureFilters(unittest.TestCase):
    def test_filters_are_scoped_to_doc_types_and_target_modules(self):
        filters = _fixture_filters(["Employee"], ["Prueba"])

        self.assertEqual(
            filters,
            [
                {
                    "dt": "Custom Field",
                    "filters": [["dt", "in", ["Employee"]], ["module", "in", ["Prueba"]]],
                },
                {
                    "dt": "Property Setter",
                    "filters": [["doc_type", "in", ["Employee"]], ["module", "in", ["Prueba"]]],
                },
                {
                    "dt": "Client Script",
                    "filters": [["dt", "in", ["Employee"]], ["module", "in", ["Prueba"]]],
                },
                {"dt": "Custom DocPerm", "filters": [["parent", "in", ["Employee"]]]},
            ],
        )

    def test_merging_adds_doc_types_without_dropping_module_scope(self):
        current = _fixture_filters(["Employee"], ["Prueba"])
        added = _fixture_filters(["MCP Test Record"], ["Prueba"])

        merged = _merge_managed_filters(current, added)

        self.assertEqual(
            merged[0]["filters"],
            [["dt", "in", ["Employee", "MCP Test Record"]], ["module", "in", ["Prueba"]]],
        )
        self.assertEqual(
            merged[1]["filters"],
            [["doc_type", "in", ["Employee", "MCP Test Record"]], ["module", "in", ["Prueba"]]],
        )

    def test_repeated_merge_is_idempotent(self):
        initial = _fixture_filters(["Employee"], ["Prueba"])
        once = _merge_managed_filters(initial, initial)
        twice = _merge_managed_filters(once, initial)

        self.assertEqual(twice, once)

    def test_merging_preserves_other_fixture_types_and_existing_modules(self):
        existing = [
            {
                "dt": "Custom Field",
                "filters": [["dt", "in", ["Employee"]], ["module", "in", ["HR"]]],
            },
            {"dt": "Client Script", "filters": [["dt", "=", "Employee"]]},
        ]
        merged = _merge_managed_filters(existing, _fixture_filters(["Supplier"], ["Prueba"]))

        self.assertEqual(
            merged[0]["filters"],
            [["dt", "in", ["Employee", "Supplier"]], ["module", "in", ["HR", "Prueba"]]],
        )
        self.assertIn(existing[1], merged)
        self.assertEqual(
            {row["dt"] for row in merged},
            {"Custom Field", "Client Script", "Property Setter", "Custom DocPerm"},
        )


class TestCustomizationTools(unittest.TestCase):
    def test_schema_changes_require_system_manager(self):
        with (
            patch.object(
                customizations.frappe,
                "session",
                SimpleNamespace(user="reader@example.com"),
            ),
            patch.object(customizations.frappe, "get_roles", return_value=["Employee"]),
            self.assertRaises(frappe.PermissionError),
        ):
            customizations._require_system_manager()

    def test_create_doctype_preview_does_not_write(self):
        with (
            TemporaryDirectory() as module_path,
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value=Path(module_path)),
            patch.object(customizations.frappe.db, "exists", return_value=False),
            patch.object(customizations.frappe, "get_doc") as get_doc,
        ):
            result = customizations.create_doctype(
                app="prueba",
                module="Prueba",
                name="MCP Preview Record",
                fields=[{"fieldname": "title", "fieldtype": "Data", "label": "Title"}],
            )

        self.assertTrue(result["preview"])
        self.assertTrue(result["requires_confirmation"])
        self.assertTrue(result["destination"].endswith("mcp_preview_record.json"))
        get_doc.assert_not_called()

    def test_create_doctype_rejects_existing_doctype(self):
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value="/tmp"),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            self.assertRaises(frappe.ValidationError),
        ):
            customizations.create_doctype(
                app="prueba", module="Prueba", name="Employee", fields=[{"fieldname": "title"}]
            )

    def test_custom_field_preview_validates_target_and_does_not_insert(self):
        meta = Mock()
        meta.has_field.return_value = False
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value="/tmp"),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            patch.object(customizations.frappe, "has_permission", return_value=True),
            patch.object(customizations.frappe, "get_meta", return_value=meta),
            patch.object(customizations.frappe, "get_doc") as get_doc,
        ):
            result = customizations.create_custom_field(
                app="prueba",
                module="Prueba",
                doctype="Employee",
                field={"fieldname": "custom_mcp_preview", "fieldtype": "Data"},
            )

        self.assertTrue(result["preview"])
        self.assertEqual(result["field"]["module"], "Prueba")
        self.assertEqual(result["field"]["dt"], "Employee")
        get_doc.assert_not_called()

    def test_custom_field_rejects_existing_field(self):
        meta = Mock()
        meta.has_field.return_value = True
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value="/tmp"),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            patch.object(customizations.frappe, "has_permission", return_value=True),
            patch.object(customizations.frappe, "get_meta", return_value=meta),
            self.assertRaises(frappe.ValidationError),
        ):
            customizations.create_custom_field(
                app="prueba",
                module="Prueba",
                doctype="Employee",
                field={"fieldname": "first_name", "fieldtype": "Data"},
            )

    def test_custom_field_requires_target_write_permission(self):
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value="/tmp"),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            patch.object(customizations.frappe, "has_permission", return_value=False),
            self.assertRaises(frappe.PermissionError),
        ):
            customizations.create_custom_field(
                app="prueba",
                module="Prueba",
                doctype="Employee",
                field={"fieldname": "custom_denied", "fieldtype": "Data"},
            )

    def test_property_setter_preview_is_scoped_to_target_module(self):
        meta = Mock()
        meta.has_field.return_value = True
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value="/tmp"),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            patch.object(customizations.frappe, "has_permission", return_value=True),
            patch.object(customizations.frappe, "get_meta", return_value=meta),
            patch.object(customizations.frappe, "get_doc") as get_doc,
        ):
            result = customizations.set_field_property(
                app="prueba",
                module="Prueba",
                doctype="Employee",
                fieldname="first_name",
                property="label",
                value="Given name",
                property_type="Data",
            )

        self.assertTrue(result["preview"])
        self.assertEqual(result["setter"]["module"], "Prueba")
        self.assertEqual(result["setter"]["doctype_or_field"], "DocField")
        get_doc.assert_not_called()

    def test_property_setter_rejects_unknown_fieldname(self):
        meta = Mock()
        meta.has_field.return_value = False
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value="/tmp"),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            patch.object(customizations.frappe, "has_permission", return_value=True),
            patch.object(customizations.frappe, "get_meta", return_value=meta),
            self.assertRaises(frappe.ValidationError),
        ):
            customizations.set_field_property(
                app="prueba",
                module="Prueba",
                doctype="Employee",
                fieldname="missing_field",
                property="label",
                value="Missing",
                property_type="Data",
            )

    def test_client_script_preview_validates_scope_and_does_not_insert(self):
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value="/tmp"),
            patch.object(customizations.frappe.db, "exists", side_effect=[True, False]),
            patch.object(customizations.frappe, "get_doc") as get_doc,
        ):
            result = customizations.create_client_script(
                app="prueba", module="Prueba", doctype="Employee", name="MCP Preview Client",
                script="frappe.msgprint('ok')"
            )

        self.assertTrue(result["preview"])
        self.assertEqual(result["view"], "Form")
        self.assertEqual(result["doctype"], "Employee")
        get_doc.assert_not_called()

    def test_client_script_rejects_invalid_view(self):
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_app_module", return_value="/tmp"),
            patch.object(customizations.frappe.db, "exists", side_effect=[True, False]),
            self.assertRaises(frappe.ValidationError),
        ):
            customizations.create_client_script(
                app="prueba", module="Prueba", doctype="Employee", name="MCP Dashboard",
                script="x", view="Dashboard"
            )

    def test_role_permissions_preview_preserves_unspecified_flags(self):
        current = {flag: 0 for flag in customizations._PERMISSION_FLAGS}
        current["read"] = 1
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            patch.object(customizations.frappe.db, "get_value", side_effect=[0, current]),
            patch.object(customizations.frappe, "get_meta", return_value=SimpleNamespace(istable=False)),
        ):
            result = customizations.set_role_permissions(
                doctype="MCP Test Record", role="Sales User", permissions={"write": True}
            )

        self.assertTrue(result["preview"])
        self.assertTrue(result["resulting"]["read"])
        self.assertTrue(result["resulting"]["write"])
        self.assertFalse(result["resulting"]["delete"])

    def test_role_permissions_rejects_unknown_permission_flag(self):
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            patch.object(customizations.frappe.db, "get_value", return_value=0),
            patch.object(customizations.frappe, "get_meta", return_value=SimpleNamespace(istable=False)),
            self.assertRaises(frappe.ValidationError),
        ):
            customizations.set_role_permissions(
                doctype="MCP Test Record", role="Sales User", permissions={"run_python": True}
            )


class TestCustomizationToolsOnSite(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self.old_user = frappe.session.user
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user(self.old_user)
        super().tearDown()

    def test_client_script_confirmation_inserts_native_document(self):
        preview = customizations.create_client_script(
            app="mcp_frappe",
            module="Frappe MCP",
            doctype="MCP Test Record",
            name="MCP Test Client Script",
            script="frappe.msgprint('MCP test')",
        )
        self.assertTrue(preview["preview"])
        result = customizations.create_client_script(
            app="mcp_frappe",
            module="Frappe MCP",
            doctype="MCP Test Record",
            name="MCP Test Client Script",
            script="frappe.msgprint('MCP test')",
            confirm=True,
        )
        self.assertTrue(frappe.db.exists("Client Script", result["client_script"]))

    def test_role_permission_confirmation_uses_native_permission_manager(self):
        preview = customizations.set_role_permissions(
            doctype="MCP Test Record",
            role="Sales User",
            permlevel=9,
            permissions={"read": True},
        )
        self.assertTrue(preview["preview"])
        result = customizations.set_role_permissions(
            doctype="MCP Test Record",
            role="Sales User",
            permlevel=9,
            permissions={"read": True},
            confirm=True,
        )
        self.assertTrue(result["updated"])
        self.assertEqual(
            frappe.db.get_value(
                "Custom DocPerm",
                {"parent": "MCP Test Record", "role": "Sales User", "permlevel": 9},
                "read",
            ),
            1,
        )

    def test_export_and_rollback_restore_test_app_files(self):
        preview = customizations.export_customizations(
            app="mcp_frappe",
            doctypes=["MCP Test Record"],
            modules=["Frappe MCP"],
            include_standard_doctypes=False,
        )
        result = customizations.export_customizations(
            app="mcp_frappe",
            doctypes=["MCP Test Record"],
            modules=["Frappe MCP"],
            include_standard_doctypes=False,
            expected_preview=preview["preview_id"],
            confirm=True,
        )
        self.assertTrue(result["exported"])
        self.assertTrue(result["rollback_id"].startswith("export-"))
        preview = customizations.rollback_export("mcp_frappe", result["rollback_id"])
        self.assertTrue(preview["preview"])
        rollback = customizations.rollback_export(
            "mcp_frappe", result["rollback_id"], confirm=True
        )
        self.assertTrue(rollback["rolled_back"])

    def test_export_rejects_modules_outside_target_app(self):
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(customizations, "_require_installed_app", return_value=Mock()),
            patch.object(customizations.frappe, "get_all", return_value=[Mock(name="Prueba")]),
            self.assertRaises(frappe.ValidationError),
        ):
            customizations.export_customizations(
                app="prueba", doctypes=["Employee"], modules=["HR"]
            )

    def test_export_preview_reports_only_selected_doctype_and_module(self):
        rows = [
            [SimpleNamespace(name="Prueba")],
            [{"name": "Employee-custom_marker", "dt": "Employee", "fieldname": "custom_marker"}],
            [
                {
                    "name": "Employee-custom_marker-label",
                    "doc_type": "Employee",
                    "field_name": "custom_marker",
                }
            ],
        ]
        with (
            patch.object(customizations, "_require_system_manager"),
            patch.object(
                customizations, "_require_installed_app", return_value=Path("/tmp/prueba")
            ),
            patch.object(customizations.frappe, "get_all", side_effect=[*rows, [], []]),
            patch.object(customizations.frappe.db, "exists", return_value=True),
            patch.object(customizations.frappe, "get_meta", return_value=SimpleNamespace(custom=1)),
            patch.object(customizations, "_fixture_plan", side_effect=lambda *args: (Path("/tmp") / (args[1] + ".json"), "[]")),
            patch.object(customizations, "_planned_hooks", return_value=(Path("/tmp/hooks.py"), "")),
        ):
            result = customizations.export_customizations(
                app="prueba", doctypes=["Employee"], modules=["Prueba"]
            )

        self.assertTrue(result["preview"])
        self.assertEqual(result["custom_fields"][0]["fieldname"], "custom_marker")
        self.assertEqual(result["property_setters"][0]["field_name"], "custom_marker")
        self.assertEqual(len(result["diffs"]), 5)
        self.assertTrue(all("diff" in item for item in result["diffs"]))

    def test_export_preview_includes_client_scripts_and_custom_permissions(self):
        filters = customizations._fixture_filters(["Employee"], ["Prueba"])
        self.assertEqual(filters[2]["dt"], "Client Script")
        self.assertEqual(filters[3]["dt"], "Custom DocPerm")

    def test_rollback_restores_backups_and_removes_new_files(self):
        with TemporaryDirectory() as temporary:
            app_path = Path(temporary) / "app"
            app_path.mkdir()
            backup_dir = Path(temporary) / "backups"
            backup_dir.mkdir()
            old_file = app_path / "hooks.py"
            old_file.write_text("original", encoding="utf-8")
            new_file = app_path / "fixtures" / "new.json"
            new_file.parent.mkdir()
            new_file.write_text("generated", encoding="utf-8")
            backup = backup_dir / "old.bak"
            backup.write_text("original", encoding="utf-8")
            rollback_id = "export-test.json"
            (backup_dir / rollback_id).write_text(
                '[{"path":"hooks.py","backup":"old.bak"},'
                '{"path":"fixtures/new.json","backup":null}]', encoding="utf-8"
            )
            with (
                patch.object(customizations, "_require_system_manager"),
                patch.object(customizations, "_require_installed_app", return_value=app_path),
                patch.object(customizations, "_backup_dir", return_value=backup_dir),
            ):
                preview = customizations.rollback_export("app", rollback_id)
                result = customizations.rollback_export("app", rollback_id, confirm=True)
            self.assertTrue(preview["preview"])
            self.assertTrue(result["rolled_back"])
            self.assertEqual(old_file.read_text(encoding="utf-8"), "original")
            self.assertFalse(new_file.exists())
