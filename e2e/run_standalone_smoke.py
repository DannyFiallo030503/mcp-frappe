"""Exercise the standalone MCP protocol against a configured development site.

Required environment: FRAPPE_URL and either an API key/secret or
FRAPPE_PASSWORD. This script performs read-only MCP calls. It prints only
aggregate metadata and does not save credentials or record contents.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


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


async def run() -> dict:
    root = Path(__file__).resolve().parents[1]
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
            apps = result_value(await session.call_tool("list_installed_apps", {}))
            doctypes = result_value(
                await session.call_tool("list_doctypes", {"app": "erpnext", "limit": 5})
            )
            schema = result_value(
                await session.call_tool("get_doctype_schema", {"doctype": "Customer"})
            )
            rows = result_value(
                await session.call_tool(
                    "list_documents", {"doctype": "DocType", "fields": ["name"], "limit": 1}
                )
            )
            report = result_value(
                await session.call_tool(
                    "run_report", {"report_name": "Item Balance (Simple)", "limit": 1}
                )
            )
            evidence = {
                "server_name": initialized.serverInfo.name,
                "tool_count": len(tools.tools),
                "installed_app_count": len(apps),
                "doctype_sample_count": len(doctypes),
                "customer_schema_field_count": len(schema.get("fields", [])),
                "document_list_count": len(rows),
                "document_list_tool": "list_documents",
                "report_tool_returned_rows": report.get("returned_rows"),
                "report_tool_truncated": report.get("truncated"),
                "read_only": True,
            }
            if os.environ.get("FRAPPE_APPS_PATH"):
                app_doctypes = result_value(
                    await session.call_tool(
                        "list_doctypes", {"app": "erp_app", "module": "CubaZone", "limit": 5}
                    )
                )
                preview = result_value(
                    await session.call_tool(
                        "create_doctype",
                        {
                            "app": "erp_app",
                            "module": "CubaZone",
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
                                "app": "erp_app",
                                "module": "CubaZone",
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
                                    "app": "erp_app",
                                    "module": "CubaZone",
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
                                "app": "erp_app",
                                "module": "CubaZone",
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
