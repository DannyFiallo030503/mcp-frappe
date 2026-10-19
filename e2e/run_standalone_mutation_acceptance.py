"""Create, update, verify, and remove one uniquely marked ToDo through MCP.

This acceptance runner is intentionally opt-in. It refuses non-local sites and
always attempts cleanup after creation. Set FRAPPE_ALLOW_MUTATION_TEST=YES only
for an isolated development site.
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


async def run() -> dict:
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_SITE", "")
    hostname = urlparse(url).hostname or ""
    if os.environ.get("FRAPPE_ALLOW_MUTATION_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_MUTATION_TEST=YES to opt into temporary writes")
    if not (hostname in {"localhost", "127.0.0.1", "::1"} and site.endswith(".localhost")):
        raise RuntimeError("Mutation acceptance is restricted to loopback .localhost sites")

    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(root),
    )
    marker = f"MCP standalone acceptance {uuid.uuid4().hex}"
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            created_name = None
            cleaned = False
            try:
                preview = value(
                    await session.call_tool(
                        "create_document",
                        {
                            "doctype": "ToDo",
                            "values": {"description": marker},
                        },
                    )
                )
                if not preview.get("requires_confirmation"):
                    raise AssertionError("Create did not return a confirmation preview")
                created = value(
                    await session.call_tool(
                        "create_document",
                        {
                            "doctype": "ToDo",
                            "values": {"description": marker},
                            "confirm": True,
                        },
                    )
                )
                created_name = created.get("name")
                if not created_name:
                    raise AssertionError("Frappe did not return the created ToDo name")

                update_preview = value(
                    await session.call_tool(
                        "update_document",
                        {
                            "doctype": "ToDo",
                            "name": created_name,
                            "values": {"description": marker + " updated"},
                        },
                    )
                )
                stale = await session.call_tool(
                    "update_document",
                    {
                        "doctype": "ToDo",
                        "name": created_name,
                        "values": {"description": marker + " updated"},
                        "expected_modified": "stale-preview-token",
                        "confirm": True,
                    },
                )
                if not stale.isError:
                    raise AssertionError("A stale update confirmation was accepted")
                updated = value(
                    await session.call_tool(
                        "update_document",
                        {
                            "doctype": "ToDo",
                            "name": created_name,
                            "values": {"description": marker + " updated"},
                            "expected_modified": update_preview["modified"],
                            "confirm": True,
                        },
                    )
                )
                readback = value(
                    await session.call_tool(
                        "get_document",
                        {
                            "doctype": "ToDo",
                            "name": created_name,
                            "fields": ["description", "status"],
                        },
                    )
                )
                if readback.get("description") != marker + " updated":
                    raise AssertionError("Updated record did not match the requested value")

                delete_preview = value(
                    await session.call_tool(
                        "delete_document",
                        {
                            "doctype": "ToDo",
                            "name": created_name,
                        },
                    )
                )
                deleted = value(
                    await session.call_tool(
                        "delete_document",
                        {
                            "doctype": "ToDo",
                            "name": created_name,
                            "expected_modified": delete_preview["modified"],
                            "confirm": True,
                        },
                    )
                )
                cleaned = bool(deleted.get("deleted"))
                remaining = value(
                    await session.call_tool(
                        "list_documents",
                        {
                            "doctype": "ToDo",
                            "filters": {"name": created_name},
                            "fields": ["name"],
                            "limit": 1,
                        },
                    )
                )
                if remaining:
                    raise AssertionError("Temporary ToDo remains after deletion")
                return {
                    "create_preview": True,
                    "create_confirmed": True,
                    "update_preview": True,
                    "stale_update_rejected": True,
                    "update_confirmed": bool(updated),
                    "readback_verified": True,
                    "delete_preview": True,
                    "delete_confirmed": cleaned,
                    "record_absent_after_cleanup": True,
                }
            finally:
                if created_name and not cleaned:
                    try:
                        value(
                            await session.call_tool(
                                "get_document",
                                {
                                    "doctype": "ToDo",
                                    "name": created_name,
                                },
                            )
                        )
                        removal = value(
                            await session.call_tool(
                                "delete_document",
                                {
                                    "doctype": "ToDo",
                                    "name": created_name,
                                },
                            )
                        )
                        value(
                            await session.call_tool(
                                "delete_document",
                                {
                                    "doctype": "ToDo",
                                    "name": created_name,
                                    "expected_modified": removal["modified"],
                                    "confirm": True,
                                },
                            )
                        )
                    except Exception as error:
                        raise RuntimeError(
                            f"Cleanup failed for temporary ToDo {created_name}; remove it manually"
                        ) from error


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
