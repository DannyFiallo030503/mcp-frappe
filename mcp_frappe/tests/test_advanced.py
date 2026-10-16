import unittest
from unittest.mock import Mock, patch

import frappe

from mcp_frappe.tools import advanced


class TestAdvancedTools(unittest.TestCase):
    def test_report_rows_are_capped(self):
        report = Mock(ref_doctype="ToDo", disabled=0)
        result = {"columns": [], "result": [{"name": str(i)} for i in range(4)]}
        with (
            patch.object(advanced.frappe.db, "exists", return_value=True),
            patch.object(advanced.frappe, "get_doc", return_value=report),
            patch.object(advanced.frappe, "has_permission", return_value=True),
            patch("frappe.desk.query_report.run", return_value=result),
        ):
            output = advanced.run_report("Test Report", limit=2)

        self.assertEqual(len(output["result"]), 2)
        self.assertTrue(output["truncated"])
        self.assertEqual(output["returned_rows"], 2)

    def test_report_requires_report_permission(self):
        report = Mock(ref_doctype="ToDo", disabled=0)
        with (
            patch.object(advanced.frappe.db, "exists", return_value=True),
            patch.object(advanced.frappe, "get_doc", return_value=report),
            patch.object(advanced.frappe, "has_permission", return_value=False),
            self.assertRaises(frappe.PermissionError),
        ):
            advanced.run_report("Test Report")

    def test_file_size_limit_is_checked_before_read(self):
        doc = Mock(name="test-file", file_name="sample.txt")
        doc.check_permission.return_value = None
        with (
            patch.object(advanced.frappe, "get_doc", return_value=doc),
            patch.object(advanced, "get_file_path", return_value="/tmp/sample.txt"),
            patch.object(advanced.os.path, "isfile", return_value=True),
            patch.object(advanced.os.path, "getsize", return_value=200),
            patch.object(advanced, "get_file") as get_file,
            self.assertRaises(frappe.ValidationError),
        ):
            advanced.read_file("test-file", max_bytes=100)
        get_file.assert_not_called()

    def test_workflow_action_preview_does_not_write(self):
        doc = Mock(name="TODO-1", modified="2026-01-01 00:00:00", owner="user@example.com")
        doc.get.return_value = "Open"
        transition = {"action": "Approve", "next_state": "Approved"}
        workflow = Mock(workflow_state_field="status")
        with (
            patch.object(advanced.frappe, "has_permission", return_value=True),
            patch.object(advanced.frappe, "get_doc", return_value=doc),
            patch("frappe.model.workflow.get_transitions", return_value=[transition]),
            patch("frappe.model.workflow.get_workflow", return_value=workflow),
            patch("frappe.model.workflow.apply_workflow") as apply,
        ):
            output = advanced.apply_workflow_action("ToDo", "TODO-1", "Approve")

        self.assertTrue(output["preview"])
        self.assertEqual(output["next_state"], "Approved")
        apply.assert_not_called()
