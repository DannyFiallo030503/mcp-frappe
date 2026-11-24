"""Batch-create ToDos, check a failing batch leaves nothing, then undo through the journal.

Opt-in like the mutation runner: it refuses non-local sites and needs
FRAPPE_ALLOW_MUTATION_TEST=YES. The server runs with FRAPPE_MCP_JOURNAL_RECORDS=YES and
without FRAPPE_APPS_PATH, so the journal stays in memory and no file is written. Every ToDo
it creates carries a unique marker and is removed at the end, by undo or by cleanup.
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
    if result.content:
        return json.loads(result.content[0].text)
    return None


async def call(session, tool: str, arguments: dict):
    return value(await session.call_tool(tool, arguments))


async def confirm(session, tool: str, arguments: dict, token: str = "preview_id") -> dict:
    preview = await call(session, tool, arguments)
    key = "expected_preview" if token == "preview_id" else "expected_modified"
    return await call(session, tool, {**arguments, key: preview[token], "confirm": True})


async def marked(session, marker: str) -> list[str]:
    page = await call(
        session,
        "list_documents",
        {
            "doctype": "ToDo",
            "filters": [["description", "like", f"{marker}%"]],
            "fields": ["name"],
            "limit": 20,
        },
    )
    return [row["name"] for row in page["rows"]]


async def run() -> dict:
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_SITE", "")
    hostname = urlparse(url).hostname or ""
    if os.environ.get("FRAPPE_ALLOW_MUTATION_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_MUTATION_TEST=YES to opt into temporary writes")
    if not (hostname in {"localhost", "127.0.0.1", "::1"} and site.endswith(".localhost")):
        raise RuntimeError("Batch and undo acceptance is restricted to loopback .localhost sites")

    env = {key: item for key, item in os.environ.items() if key != "FRAPPE_APPS_PATH"}
    env["FRAPPE_MCP_JOURNAL_RECORDS"] = "YES"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    marker = f"MCP batch acceptance {uuid.uuid4().hex}"
    rows = [{"description": f"{marker} {index}"} for index in range(3)]
    result: dict | None = None
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            try:
                # Frappe refuses an unknown Select value on the last row, inside insert_many.
                bad = [*rows, {"description": f"{marker} bad", "status": "Not A Status"}]
                preview = await call(session, "create_documents", {"doctype": "ToDo", "rows": bad})
                failed = await session.call_tool(
                    "create_documents",
                    {
                        "doctype": "ToDo",
                        "rows": bad,
                        "expected_preview": preview["preview_id"],
                        "confirm": True,
                    },
                )
                if not failed.isError or "Nothing was created" not in failed.content[0].text:
                    raise AssertionError("A batch with a failing row was not refused as a whole")
                if await marked(session, marker):
                    raise AssertionError("A failed batch left records behind")

                created = await confirm(
                    session, "create_documents", {"doctype": "ToDo", "rows": rows}
                )
                if len(created["names"]) != 3 or not created.get("mcp_change"):
                    raise AssertionError(f"Unexpected batch result: {created}")

                first = created["names"][0]
                updated = await confirm(
                    session,
                    "update_document",
                    {"doctype": "ToDo", "name": first, "values": {"description": f"{marker} x"}},
                    token="modified",
                )
                undo_update = await confirm(
                    session, "undo_mcp_change", {"entry_id": updated["mcp_change"]}
                )
                readback = await call(
                    session,
                    "get_document",
                    {"doctype": "ToDo", "name": first, "fields": ["description"]},
                )
                if readback.get("description") != rows[0]["description"]:
                    raise AssertionError("Undoing the update did not restore the old value")

                stale = await session.call_tool(
                    "undo_mcp_change", {"entry_id": created["mcp_change"]}
                )
                if not stale.isError or "changed after" not in stale.content[0].text:
                    raise AssertionError("Undo of a record changed afterwards was not refused")

                changes = await call(session, "list_mcp_changes", {"limit": 5})
                journal_tools = {row.get("tool") for row in changes["rows"]}
                expected_journal_tools = {"create_documents", "update_document"}
                if changes["storage"] != "memory":
                    raise AssertionError("The change journal was not kept in memory")
                if len(changes["rows"]) != 2 or not expected_journal_tools.issubset(journal_tools):
                    raise AssertionError(f"Unexpected change journal entries: {changes['rows']}")
                result = {
                    "failed_batch_left_nothing": True,
                    "batch_created": len(created["names"]),
                    "update_undone": bool(undo_update.get("undone")),
                    "changed_target_refused": True,
                    "journal_rows": len(changes["rows"]),
                    "journal_storage": changes["storage"],
                    "journal_tools": sorted(journal_tools),
                }
            finally:
                for name in await marked(session, marker):
                    await confirm(
                        session,
                        "delete_document",
                        {"doctype": "ToDo", "name": name},
                        token="modified",
                    )
                remaining = await marked(session, marker)
                if remaining:
                    raise RuntimeError(f"Temporary ToDos marked {marker!r} remain; remove them")
                if result is not None:
                    result["cleanup_marker_records_remaining"] = bool(remaining)

    if result is None:
        raise AssertionError("Batch/undo run finished without a result")
    return result


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
