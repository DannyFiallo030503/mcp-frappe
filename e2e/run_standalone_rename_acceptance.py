"""Test Frappe's native Custom Field rename with a temporary ToDo field.

The runner is opt-in and restricted to loopback .localhost sites. It creates one uniquely
named nullable Data field, renames it through Frappe, renames it back, and deletes it in a
finally block. It emits no field name, hostname, document value, or secret.
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
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool: str, arguments: dict):
    return value(await session.call_tool(tool, arguments))


async def confirm_preview(session, tool: str, arguments: dict) -> dict:
    preview = await call(session, tool, arguments)
    return await call(
        session,
        tool,
        {**arguments, "expected_preview": preview["preview_id"], "confirm": True},
    )


async def field_rows(session, names: set[str]) -> list[dict]:
    found = []
    for name in sorted(names):
        rows = await call(
            session,
            "list_documents",
            {
                "doctype": "Custom Field",
                "filters": [["dt", "=", "ToDo"], ["fieldname", "=", name]],
                "fields": ["name", "fieldname"],
                "limit": 2,
            },
        )
        found.extend(rows["rows"])
    return found


async def delete_fields(session, names: set[str]) -> None:
    for row in await field_rows(session, names):
        preview = await call(
            session, "delete_document", {"doctype": "Custom Field", "name": row["name"]}
        )
        await call(
            session,
            "delete_document",
            {
                "doctype": "Custom Field",
                "name": row["name"],
                "expected_modified": preview["modified"],
                "confirm": True,
            },
        )


async def run() -> dict:
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_SITE", "")
    hostname = urlparse(url).hostname or ""
    if os.environ.get("FRAPPE_ALLOW_CUSTOMIZATION_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_CUSTOMIZATION_TEST=YES to opt into schema changes")
    if not (hostname in {"localhost", "127.0.0.1", "::1"} and site.endswith(".localhost")):
        raise RuntimeError("Rename acceptance is restricted to loopback .localhost sites")

    env = {key: item for key, item in os.environ.items() if key != "FRAPPE_APPS_PATH"}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    suffix = uuid.uuid4().hex[:10]
    old = f"mcp_rename_{suffix}"
    new = f"mcp_renamed_{suffix}"
    names = {old, new}
    result = None
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                arguments = {
                    "doctype": "ToDo",
                    "field": {
                        "fieldname": old,
                        "fieldtype": "Data",
                        "label": "MCP Rename Acceptance",
                        "insert_after": "description",
                    },
                }
                preview = await call(session, "create_custom_field", arguments)
                if not preview.get("requires_confirmation"):
                    raise AssertionError("Custom Field creation did not return a preview")
                await call(session, "create_custom_field", {**arguments, "confirm": True})

                await confirm_preview(
                    session,
                    "rename",
                    {"kind": "custom_field", "doctype": "ToDo", "old": old, "new": new},
                )
                schema = await call(
                    session, "get_doctype_schema", {"doctype": "ToDo", "detail": "full"}
                )
                fieldnames = {row.get("fieldname") for row in schema["doctype"]["fields"]}
                if new not in fieldnames or old in fieldnames:
                    raise AssertionError("Frappe schema did not reflect the Custom Field rename")

                await confirm_preview(
                    session,
                    "rename",
                    {"kind": "custom_field", "doctype": "ToDo", "old": new, "new": old},
                )
                schema = await call(
                    session, "get_doctype_schema", {"doctype": "ToDo", "detail": "full"}
                )
                fieldnames = {row.get("fieldname") for row in schema["doctype"]["fields"]}
                if old not in fieldnames or new in fieldnames:
                    raise AssertionError("Frappe schema did not restore the original fieldname")
                result = {
                    "native_custom_field_rename_succeeded": True,
                    "rename_back_restored_schema": True,
                }
            finally:
                await delete_fields(session, names)
                schema = await call(
                    session, "get_doctype_schema", {"doctype": "ToDo", "detail": "full"}
                )
                fieldnames = {row.get("fieldname") for row in schema["doctype"]["fields"]}
                remaining = bool(names & fieldnames)
                if remaining:
                    raise RuntimeError(
                        "Temporary Custom Field remains; remove it before continuing"
                    )
                if result is not None:
                    result["cleanup_test_field_metadata_remaining"] = remaining
    if result is None:
        raise AssertionError("Rename run finished without a result")
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
