"""Exercise the standalone MCP protocol against a configured development site.

Required environment: FRAPPE_URL and either an API key/secret or
FRAPPE_PASSWORD. This script performs read-only MCP calls. It prints only
aggregate metadata and does not save credentials or record contents.

It expects ERPNext (the Customer schema and the "Item Balance (Simple)" report). With
FRAPPE_APPS_PATH, FRAPPE_TEST_APP, and FRAPPE_TEST_MODULE set, it also previews (never
confirms) DocType creation, a Custom Field, a Property Setter, and an export in that app.

With FRAPPE_SMOKE_LIMITED_USER=YES, for a user without System Manager, it instead calls each
read tool once and records ``pass``, ``denied (<status> <exception>)``, or ``fail``.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# The disposable test app and module the runner writes to (never frappe or erpnext).
APP = os.environ.get("FRAPPE_TEST_APP", "")
MODULE = os.environ.get("FRAPPE_TEST_MODULE", "")


def result_value(result):
    if result.isError:
        raise RuntimeError(result.content[0].text if result.content else "MCP tool failed")
    structured = result.structuredContent or {}
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if result.content:
        text = result.content[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return None


def outcome(error: Exception) -> str:
    """Summarize a tool error as its HTTP status and Frappe exception class, no message text."""
    match = re.search(r"Frappe HTTP (\d+) (\w+)", str(error))
    if match:
        status, exc_type = match.groups()
        kind = "denied" if status in {"401", "403"} else "fail"
        return f"{kind} ({status} {exc_type})"
    return f"fail ({type(error).__name__})"


async def probe_read_tools(session) -> dict:
    """Call each read tool on its own and record pass/denied/fail, for a limited user."""
    probes = [
        ("get_site_info", {}),
        ("list_installed_apps", {}),
        ("list_doctypes", {"app": "erpnext", "limit": 5}),
        ("get_doctype_schema", {"doctype": "Customer"}),
        ("list_documents", {"doctype": "Customer", "fields": ["name"], "limit": 1}),
        ("count_documents", {"doctype": "Customer"}),
        ("search_records", {"doctype": "Customer", "text": "a", "limit": 1}),
        ("list_reports", {"limit": 5}),
        ("get_report_filters", {"report_name": "Item Balance (Simple)"}),
        ("run_report", {"report_name": "Item Balance (Simple)", "limit": 1}),
        ("get_workflow", {"doctype": "Customer"}),
        ("list_notifications", {"limit": 5}),
        ("get_workspace", {"name": "Selling"}),
    ]
    results = {}
    for tool, arguments in probes:
        try:
            result_value(await session.call_tool(tool, arguments))
            results[tool] = "pass"
        except Exception as error:
            results[tool] = outcome(error)
    return results


async def run() -> dict:
    root = Path(__file__).resolve().parents[1]
    if os.environ.get("FRAPPE_SMOKE_LIMITED_USER") == "YES":
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "frappe_mcp.standalone"],
            env=os.environ.copy(),
            cwd=str(root),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                site_info = result_value(await session.call_tool("get_site_info", {}))
                return {
                    "mode": "limited user, one call per read tool",
                    "tool_count": len(tools.tools),
                    "frappe_major_version": site_info.get("frappe_major_version"),
                    "identity": "non-Administrator user (roles in the evidence file name)",
                    "read_tools": await probe_read_tools(session),
                    "read_only": True,
                }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(root),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            site_info = result_value(await session.call_tool("get_site_info", {}))
            apps = result_value(await session.call_tool("list_installed_apps", {}))
            doctypes = result_value(
                await session.call_tool("list_doctypes", {"app": "erpnext", "limit": 5})
            )["rows"]
            schema = result_value(
                await session.call_tool("get_doctype_schema", {"doctype": "Customer"})
            )
            rows = result_value(
                await session.call_tool(
                    "list_documents", {"doctype": "DocType", "fields": ["name"], "limit": 1}
                )
            )["rows"]
            report = result_value(
                await session.call_tool(
                    "run_report", {"report_name": "Item Balance (Simple)", "limit": 1}
                )
            )
            evidence = {
                "server_name": initialized.serverInfo.name,
                "tool_count": len(tools.tools),
                "installed_app_count": len(apps),
                "site_info_app_count": len(site_info.get("apps", [])),
                "frappe_major_version": site_info.get("frappe_major_version"),
                "doctype_sample_count": len(doctypes),
                "customer_schema_field_count": len(schema.get("fields", [])),
                "document_list_count": len(rows),
                "document_list_tool": "list_documents",
                "report_tool_returned_rows": report.get("returned_rows"),
                "report_tool_truncated": report.get("truncated"),
                "read_only": True,
                "read_tools": await probe_read_tools(session),
            }
            if os.environ.get("FRAPPE_APPS_PATH") and APP and MODULE:
                app_doctypes = result_value(
                    await session.call_tool(
                        "list_doctypes", {"app": APP, "module": MODULE, "limit": 5}
                    )
                )["rows"]
                preview = result_value(
                    await session.call_tool(
                        "create_doctype",
                        {
                            "app": APP,
                            "module": MODULE,
                            "name": "MCP Standalone Preview Only DocType",
                            "fields": [
                                {"fieldname": "title", "fieldtype": "Data", "label": "Title"}
                            ],
                        },
                    )
                )
                evidence["custom_app_doctype_count"] = len(app_doctypes)
                evidence["create_doctype_preview"] = bool(preview.get("requires_confirmation"))
                if app_doctypes:
                    selected = app_doctypes[0]["name"]
                    custom_field = result_value(
                        await session.call_tool(
                            "create_custom_field",
                            {
                                "app": APP,
                                "module": MODULE,
                                "doctype": selected,
                                "field": {
                                    "fieldname": "mcp_preview_field",
                                    "fieldtype": "Data",
                                    "label": "MCP Preview Field",
                                },
                            },
                        )
                    )
                    evidence["custom_field_preview"] = bool(
                        custom_field.get("requires_confirmation")
                    )
                    doctype_schema = result_value(
                        await session.call_tool("get_doctype_schema", {"doctype": selected})
                    )
                    fields = [
                        field
                        for field in doctype_schema.get("fields", [])
                        if field.get("fieldname")
                    ]
                    if fields:
                        property_preview = result_value(
                            await session.call_tool(
                                "set_field_property",
                                {
                                    "app": APP,
                                    "module": MODULE,
                                    "doctype": selected,
                                    "fieldname": fields[0]["fieldname"],
                                    "property": "label",
                                    "value": fields[0].get("label") or fields[0]["fieldname"],
                                    "property_type": "Data",
                                },
                            )
                        )
                        evidence["property_setter_preview"] = bool(
                            property_preview.get("requires_confirmation")
                        )
                    export = result_value(
                        await session.call_tool(
                            "export_customizations",
                            {
                                "app": APP,
                                "module": MODULE,
                                "doctypes": [selected],
                            },
                        )
                    )
                    evidence["customization_export_preview"] = bool(
                        (export.get("requires_confirmation") and export.get("preview_id"))
                        or export.get("nothing_to_export")
                    )
                    evidence["export_preview_file_count"] = len(export.get("files", []))
            return evidence


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
