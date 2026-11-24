"""Observe concurrent role-permission updates on one isolated temporary row."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def value(result):
    if result.isError:
        detail = result.content[0].text if result.content else "MCP tool call failed"
        raise RuntimeError(detail[:350])
    if result.structuredContent:
        data = result.structuredContent
        return data.get("result", data)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, args):
    return value(await session.call_tool(tool, args))


async def confirmed(session, tool, args):
    preview = await call(session, tool, args)
    if not preview.get("requires_confirmation"):
        raise AssertionError(f"{tool} did not return a confirmation preview")
    write = {**args, "confirm": True}
    if tool == "delete_document":
        write["expected_modified"] = preview.get("modified")
    return await call(session, tool, write)


async def delete_record(session, doctype, name):
    result = await confirmed(session, "delete_document", {"doctype": doctype, "name": name})
    if not result.get("deleted"):
        raise AssertionError(f"Cleanup failed for temporary {doctype}")


async def run():
    url = os.environ.get("FRAPPE_URL", "")
    site_name = os.environ.get("FRAPPE_SITE", "")
    if os.environ.get("FRAPPE_ALLOW_ROLE_PERMISSION_RACE_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_ROLE_PERMISSION_RACE_TEST=YES")
    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"} or not site_name.endswith(
        ".localhost"
    ):
        raise RuntimeError("This runner only changes an isolated loopback .localhost site")
    if os.environ.get("FRAPPE_USERNAME", "Administrator") != "Administrator":
        raise RuntimeError("The isolated race test requires Administrator")

    role = "MCP Race " + uuid.uuid4().hex[:10]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    report = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "frappe_version": None,
        "doctype": "ToDo",
        "operation": "two MCP processes confirm concurrent updates to one Custom DocPerm row",
        "outcome": None,
        "final_row_matches_one_complete_writer": False,
        "cleanup_verified": False,
        "site_isolated": True,
    }

    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        admin = await stack.enter_async_context(ClientSession(read, write))
        await admin.initialize()
        site = await call(admin, "get_site_info", {})
        if site.get("frappe_major_version") != 16:
            raise RuntimeError("Refusing: this runner targets isolated Frappe v16")
        report["frappe_version"] = next(
            (x.get("version") for x in site.get("apps", []) if x.get("app") == "frappe"), None
        )
        try:
            await confirmed(
                admin, "create_document", {"doctype": "Role", "values": {"role_name": role}}
            )
            await confirmed(
                admin,
                "set_role_permissions",
                {"doctype": "ToDo", "role": role, "permissions": {"read": True}},
            )
            original = await call(
                admin,
                "list_documents",
                {
                    "doctype": "Custom DocPerm",
                    "filters": {"parent": "ToDo", "role": role, "permlevel": 0, "if_owner": 0},
                    "fields": ["name", "read", "write"],
                    "limit": 2,
                },
            )
            rows = original.get("rows", [])
            if len(rows) != 1:
                raise AssertionError("Expected exactly one starting permission row")
            permission_name = rows[0]["name"]

            read_a, write_a = await stack.enter_async_context(stdio_client(params))
            client_a = await stack.enter_async_context(ClientSession(read_a, write_a))
            await client_a.initialize()
            read_b, write_b = await stack.enter_async_context(stdio_client(params))
            client_b = await stack.enter_async_context(ClientSession(read_b, write_b))
            await client_b.initialize()

            update_a = {
                "doctype": "ToDo",
                "role": role,
                "permissions": {"read": False, "write": True},
            }
            update_b = {
                "doctype": "ToDo",
                "role": role,
                "permissions": {"read": True, "write": False},
            }
            previews = await asyncio.gather(
                call(client_a, "set_role_permissions", update_a),
                call(client_b, "set_role_permissions", update_b),
            )
            if not all(p.get("requires_confirmation") for p in previews):
                raise AssertionError("Both writers must preview before either update is confirmed")
            results = await asyncio.gather(
                call(client_a, "set_role_permissions", {**update_a, "confirm": True}),
                call(client_b, "set_role_permissions", {**update_b, "confirm": True}),
                return_exceptions=True,
            )
            stale = [
                isinstance(item, Exception) and "TimestampMismatchError" in str(item)
                for item in results
            ]
            accepted = [not isinstance(item, Exception) for item in results]
            if sum(accepted) == 1 and sum(stale) == 1:
                report["outcome"] = "one update accepted; the overlapping stale update was refused"
            elif all(accepted):
                report["outcome"] = "both updates completed serially"
            else:
                raise AssertionError(
                    "Concurrent update failed for a reason other than a stale-write conflict"
                )
            final = await call(
                admin,
                "get_document",
                {"doctype": "Custom DocPerm", "name": permission_name, "fields": ["read", "write"]},
            )
            raw_read, raw_write = final.get("read"), final.get("write")
            final_pair = (
                str(raw_read).lower() in {"1", "true"},
                str(raw_write).lower() in {"1", "true"},
            )
            report["final_flags"] = {"read": final_pair[0], "write": final_pair[1]}
            report["final_row_matches_one_complete_writer"] = final_pair in {
                (False, True),
                (True, False),
            }
            report["same_permission_row"] = final.get("name") == permission_name
            if sum(accepted) == 1:
                expected_pair = (False, True) if accepted[0] else (True, False)
                report["final_row_matches_accepted_writer"] = final_pair == expected_pair
            else:
                report["final_row_matches_accepted_writer"] = report[
                    "final_row_matches_one_complete_writer"
                ]
            if not report["final_row_matches_accepted_writer"] or not report["same_permission_row"]:
                raise AssertionError("Final state did not match one complete accepted writer")
        finally:
            residual = await call(
                admin,
                "list_documents",
                {
                    "doctype": "Custom DocPerm",
                    "filters": {"parent": "ToDo", "role": role},
                    "fields": ["name"],
                    "limit": 10,
                },
            )
            for row in residual.get("rows", []):
                await delete_record(admin, "Custom DocPerm", row["name"])
            roles = await call(
                admin,
                "list_documents",
                {"doctype": "Role", "filters": {"name": role}, "fields": ["name"], "limit": 2},
            )
            for row in roles.get("rows", []):
                await delete_record(admin, "Role", row["name"])
            remaining = await call(
                admin,
                "list_documents",
                {"doctype": "Role", "filters": {"name": role}, "fields": ["name"], "limit": 1},
            )
            residual = await call(
                admin,
                "list_documents",
                {
                    "doctype": "Custom DocPerm",
                    "filters": {"parent": "ToDo", "role": role},
                    "fields": ["name"],
                    "limit": 1,
                },
            )
            report["cleanup_verified"] = not remaining.get("rows") and not residual.get("rows")

    if not report["cleanup_verified"]:
        raise AssertionError("Temporary Role or permission row remains")
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
