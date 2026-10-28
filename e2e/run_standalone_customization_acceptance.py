"""Create and remove a temporary Custom Field and round-trip its app export.

This test changes the development site and app files temporarily. It is
restricted to loopback .localhost sites, requires explicit opt-in, and attempts
to roll back exports and delete the temporary Custom Field in a finally block.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def value(result):
    if result.isError:
        raise RuntimeError(result.content[0].text if result.content else "MCP tool failed")
    structured = result.structuredContent or {}
    if isinstance(structured, dict) and "result" in structured:
        return structured["result"]
    if result.content:
        return json.loads(result.content[0].text)
    return None


async def read_tool(session, name: str, arguments: dict, retries: int = 5):
    last_error = None
    for attempt in range(retries + 1):
        result = await session.call_tool(name, arguments)
        try:
            return value(result)
        except RuntimeError as error:
            last_error = error
            if not any(
                token in str(error).lower()
                for token in (
                    "peer closed",
                    "connecterror",
                    "connection refused",
                    "server disconnected",
                    "reset by peer",
                    "incomplete read",
                    "503",
                )
            ):
                raise
            if attempt < retries:
                await asyncio.sleep(1)
    raise last_error or RuntimeError("Frappe API did not recover")


async def delete_if_present(session, doctype: str, name: str) -> bool:
    """Reconcile a delete if Frappe restarts its dev worker after the request."""
    for attempt in range(6):
        rows = await read_tool(
            session,
            "list_documents",
            {"doctype": doctype, "filters": {"name": name}, "fields": ["name"], "limit": 1},
        )
        if not rows["rows"]:
            return True
        preview = await read_tool(
            session, "delete_document", {"doctype": doctype, "name": name}
        )
        try:
            result = value(
                await session.call_tool(
                    "delete_document",
                    {
                        "doctype": doctype,
                        "name": name,
                        "expected_modified": preview["modified"],
                        "confirm": True,
                    },
                )
            )
            if result.get("deleted"):
                return True
        except RuntimeError as error:
            if not any(
                token in str(error).lower()
                for token in (
                    "peer closed",
                    "connecterror",
                    "connection refused",
                    "server disconnected",
                    "reset by peer",
                    "incomplete read",
                    "503",
                )
            ):
                raise
        if attempt < 5:
            await asyncio.sleep(1)
    return False


async def run() -> dict:
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_SITE", "")
    hostname = urlparse(url).hostname or ""
    if os.environ.get("FRAPPE_ALLOW_CUSTOMIZATION_TEST") != "YES":
        raise RuntimeError(
            "Set FRAPPE_ALLOW_CUSTOMIZATION_TEST=YES to opt into schema/file changes"
        )
    if not (hostname in {"localhost", "127.0.0.1", "::1"} and site.endswith(".localhost")):
        raise RuntimeError("Customization acceptance is restricted to loopback .localhost sites")
    if not os.environ.get("FRAPPE_APPS_PATH"):
        raise RuntimeError("Set FRAPPE_APPS_PATH to the local Bench apps directory")

    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(root),
    )
    fieldname = "mcp_test_" + uuid.uuid4().hex[:12]
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            doctypes = value(
                await session.call_tool(
                    "list_doctypes",
                    {
                        "app": "erp_app",
                        "module": "CubaZone",
                        "limit": 1,
                    },
                )
            )
            if not doctypes["rows"]:
                raise RuntimeError("No app-owned DocType is available for customization testing")
            doctype = doctypes["rows"][0]["name"]
            custom_field_name = None
            property_setter_name = None
            rollback_id = None
            rolled_back = False
            field_deleted = False
            setter_deleted = False
            try:
                preview = value(
                    await session.call_tool(
                        "create_custom_field",
                        {
                            "app": "erp_app",
                            "module": "CubaZone",
                            "doctype": doctype,
                            "field": {
                                "fieldname": fieldname,
                                "fieldtype": "Data",
                                "label": "MCP Test Field",
                            },
                        },
                    )
                )
                if not preview.get("requires_confirmation"):
                    raise AssertionError("Custom Field did not return a preview")
                created = value(
                    await session.call_tool(
                        "create_custom_field",
                        {
                            "app": "erp_app",
                            "module": "CubaZone",
                            "doctype": doctype,
                            "field": {
                                "fieldname": fieldname,
                                "fieldtype": "Data",
                                "label": "MCP Test Field",
                            },
                            "confirm": True,
                        },
                    )
                )
                custom_field_name = created.get("name")
                if not custom_field_name:
                    raise AssertionError("Frappe did not return the Custom Field name")

                property_data = {
                    "app": "erp_app",
                    "module": "CubaZone",
                    "doctype": doctype,
                    "fieldname": fieldname,
                    "property": "label",
                    "value": "MCP Test Field Updated",
                    "property_type": "Data",
                }
                property_preview = value(
                    await session.call_tool("set_field_property", property_data)
                )
                if not property_preview.get("requires_confirmation"):
                    raise AssertionError("Property Setter did not return a preview")
                property_created = value(
                    await session.call_tool(
                        "set_field_property", {**property_data, "confirm": True}
                    )
                )
                property_setter_name = property_created.get("name")
                if not property_setter_name:
                    raise AssertionError("Frappe did not return the Property Setter name")

                schema = value(await session.call_tool("get_doctype_schema", {"doctype": doctype}))
                schema_fields = {row.get("fieldname"): row for row in schema.get("fields", [])}
                if fieldname not in schema_fields:
                    raise AssertionError("Created Custom Field is missing from the DocType schema")
                if schema_fields[fieldname].get("label") != "MCP Test Field Updated":
                    raise AssertionError("Property Setter is missing from the merged schema")
                export_preview = value(
                    await session.call_tool(
                        "export_customizations",
                        {
                            "app": "erp_app",
                            "module": "CubaZone",
                            "doctypes": [doctype],
                        },
                    )
                )
                if not export_preview.get("preview_id") or not export_preview.get(
                    "requires_confirmation"
                ):
                    raise AssertionError("Customization export did not return a preview token")
                rollback_id = "export-" + export_preview["preview_id"][:32] + ".json"
                exported = value(
                    await session.call_tool(
                        "export_customizations",
                        {
                            "app": "erp_app",
                            "module": "CubaZone",
                            "doctypes": [doctype],
                            "confirm": True,
                            "expected_preview": export_preview["preview_id"],
                        },
                    )
                )
                rollback_id = exported.get("rollback_id") or rollback_id
                if not exported.get("exported"):
                    raise AssertionError("Export did not return a rollback ID")
                native_file = f"/custom/{doctype.lower().replace(' ', '_')}.json"
                if not any(path.endswith(native_file) for path in exported.get("files", [])):
                    raise AssertionError("Export did not write the native custom/ file")
                rollback_preview = await read_tool(
                    session,
                    "rollback_export",
                    {"app": "erp_app", "module": "CubaZone", "rollback_id": rollback_id},
                )
                rolled = value(
                    await session.call_tool(
                        "rollback_export",
                        {
                            "app": "erp_app",
                            "module": "CubaZone",
                            "rollback_id": rollback_id,
                            "expected_preview": rollback_preview["preview_id"],
                            "confirm": True,
                        },
                    )
                )
                rolled_back = bool(rolled.get("rolled_back"))

                field_deleted = await delete_if_present(
                    session, "Custom Field", custom_field_name
                )
                final_schema = value(
                    await session.call_tool("get_doctype_schema", {"doctype": doctype})
                )
                absent = fieldname not in {
                    row.get("fieldname") for row in final_schema.get("fields", [])
                }
                return {
                    "custom_field_preview_and_create": True,
                    "property_setter_preview_and_confirm": True,
                    "schema_reflects_custom_field": True,
                    "schema_reflects_property_setter": True,
                    "export_preview_and_confirm": True,
                    "native_custom_file_export": True,
                    "rollback_preview_and_confirm": rolled_back,
                    "temporary_custom_field_deleted": field_deleted,
                    "field_absent_from_schema": absent,
                    "exported_file_count": len(exported.get("files", [])),
                }
            finally:
                if rollback_id and not rolled_back:
                    try:
                        preview = await read_tool(
                            session,
                            "rollback_export",
                            {"app": "erp_app", "module": "CubaZone", "rollback_id": rollback_id},
                        )
                        value(
                            await session.call_tool(
                                "rollback_export",
                                {
                                    "app": "erp_app",
                                    "module": "CubaZone",
                                    "rollback_id": rollback_id,
                                    "expected_preview": preview["preview_id"],
                                    "confirm": True,
                                },
                            )
                        )
                    except Exception as error:
                        raise RuntimeError(
                            "Export cleanup failed; inspect the returned rollback ID manually"
                        ) from error
                if custom_field_name and not field_deleted:
                    try:
                        if property_setter_name and not setter_deleted:
                            remaining_setters = await read_tool(
                                session,
                                "list_documents",
                                {
                                    "doctype": "Property Setter",
                                    "filters": {"name": property_setter_name},
                                    "fields": ["name"],
                                    "limit": 1,
                                },
                            )
                            if remaining_setters["rows"]:
                                setter_deleted = await delete_if_present(
                                    session, "Property Setter", property_setter_name
                                )
                            else:
                                setter_deleted = True
                        remaining = await read_tool(
                            session,
                            "list_documents",
                            {
                                "doctype": "Custom Field",
                                "filters": {"name": custom_field_name},
                                "fields": ["name"],
                                "limit": 1,
                            },
                        )
                        if not remaining["rows"]:
                            field_deleted = True
                        else:
                            field_deleted = await delete_if_present(
                                session, "Custom Field", custom_field_name
                            )
                    except Exception as error:
                        raise RuntimeError(
                            f"Temporary Custom Field cleanup failed: {custom_field_name}"
                        ) from error


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
