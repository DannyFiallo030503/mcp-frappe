import json

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.wrappers import Request, Response

from mcp_frappe.mcp import handle_mcp, mcp
from mcp_frappe.tools import advanced
from mcp_frappe.tools.discovery import (
    get_doctype_schema,
    get_document,
    list_doctypes,
    list_documents,
    list_installed_apps,
)


class TestInstalledSiteDiscovery(FrappeTestCase):
    def setUp(self):
        super().setUp()
        self.old_user = frappe.session.user
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.set_user(self.old_user)
        super().tearDown()

    def test_installed_apps_are_discovered_from_site(self):
        apps = {entry["app"]: entry["modules"] for entry in list_installed_apps()}

        self.assertIn("frappe", apps)
        self.assertIn("erpnext", apps)
        self.assertIn("hrms", apps)
        self.assertIn("mcp_frappe", apps)
        self.assertTrue(apps["hrms"])

    def test_doctypes_are_discovered_for_official_apps(self):
        hrms_types = {entry["doctype"] for entry in list_doctypes(app="hrms")}
        erpnext_types = {entry["doctype"] for entry in list_doctypes(app="erpnext")}

        self.assertIn("Leave Application", hrms_types)
        self.assertIn("Item", erpnext_types)
        self.assertTrue(all(entry["app"] == "hrms" for entry in list_doctypes(app="hrms")))

    def test_schema_and_document_reads_work_against_installed_doctypes(self):
        schema = get_doctype_schema("Employee")
        self.assertEqual(schema["doctype"], "Employee")
        self.assertIn("first_name", {field["fieldname"] for field in schema["fields"]})

        rows = list_documents("Company", fields=["name"], limit=1)
        self.assertLessEqual(len(rows), 1)
        self.assertTrue(all(set(row) == {"name"} for row in rows))

        singleton = get_document("System Settings", child_row_limit=1)
        self.assertEqual(singleton["doctype"], "System Settings")
        self.assertEqual(singleton["name"], "System Settings")

        self.assertIn("app", schema)
        self.assertIn("title_field", schema)
        self.assertIn("in_standard_filter", schema["fields"][0])

    def test_advanced_discovery_tools_run_against_the_site(self):
        self.assertIsInstance(advanced.list_reports(limit=1), list)
        self.assertIsInstance(advanced.list_workflows(limit=1), list)
        self.assertIsInstance(advanced.list_workspaces(limit=1), list)
        self.assertIsInstance(advanced.list_files(limit=1), list)
        self.assertIsInstance(advanced.list_print_formats("ToDo", limit=1), list)

    def test_all_stage_one_tools_are_registered_with_mcp(self):
        expected = {
            "list_installed_apps",
            "list_doctypes",
            "get_doctype_schema",
            "list_documents",
            "get_document",
            "create_doctype",
            "create_custom_field",
            "set_field_property",
            "create_client_script",
            "set_role_permissions",
            "export_customizations",
            "rollback_export",
            "create_document",
            "update_document",
            "delete_document",
            "submit_document",
            "cancel_document",
            "call_whitelisted_method",
            "list_reports",
            "run_report",
            "list_workflows",
            "get_workflow_actions",
            "apply_workflow_action",
            "list_workspaces",
            "get_workspace",
            "list_print_formats",
            "render_print_format",
            "list_files",
            "read_file",
            "search_translations",
        }

        self.assertTrue(expected.issubset(mcp._tool_registry))

    def test_mcp_transport_initializes_and_lists_tools(self):
        initialize = Request.from_values(
            method="POST",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "unit-test", "version": "1"},
                    },
                }
            ),
            content_type="application/json",
        )
        initialized = json.loads(mcp.handle(initialize, Response()).get_data(as_text=True))
        self.assertEqual(initialized["id"], 1)
        self.assertIn("protocolVersion", initialized["result"])

        tools_request = Request.from_values(
            method="POST",
            data=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
            content_type="application/json",
        )
        listed = json.loads(mcp.handle(tools_request, Response()).get_data(as_text=True))
        names = {tool["name"] for tool in listed["result"]["tools"]}
        self.assertIn("list_installed_apps", names)
        self.assertIn("get_document", names)

        call = Request.from_values(
            method="POST",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "list_installed_apps", "arguments": {}},
                }
            ),
            content_type="application/json",
        )
        called = json.loads(mcp.handle(call, Response()).get_data(as_text=True))
        apps = json.loads(called["result"]["content"][0]["text"])
        self.assertIn("mcp_frappe", {entry["app"] for entry in apps})

        create_preview = Request.from_values(
            method="POST",
            data=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "create_document",
                        "arguments": {
                            "doctype": "ToDo",
                            "values": {"description": "MCP protocol preview"},
                            "name": "MCP-PROTOCOL-PREVIEW",
                        },
                    },
                }
            ),
            content_type="application/json",
        )
        preview = json.loads(mcp.handle(create_preview, Response()).get_data(as_text=True))
        tool_preview = json.loads(preview["result"]["content"][0]["text"])
        self.assertTrue(tool_preview["preview"])
        self.assertFalse(frappe.db.exists("ToDo", "MCP-PROTOCOL-PREVIEW"))

    def test_http_endpoint_is_whitelisted_without_guest_access(self):
        self.assertIn(handle_mcp, frappe.whitelisted)
        self.assertNotIn(handle_mcp, frappe.guest_methods)
