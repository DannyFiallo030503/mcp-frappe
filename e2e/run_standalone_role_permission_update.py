"""Live, isolated acceptance for set_role_permissions add and update paths."""

from __future__ import annotations

import asyncio
import datetime as dt
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
        raise RuntimeError("MCP tool call failed")
    structured = result.structuredContent or {}
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if structured:
        return structured
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, arguments):
    return value(await session.call_tool(tool, arguments))


async def delete_record(session, doctype, name):
    preview = await call(session, "delete_document", {"doctype": doctype, "name": name})
    if not preview.get("requires_confirmation"):
        raise AssertionError(f"{doctype} delete lacked confirmation preview")
    result = await call(
        session,
        "delete_document",
        {
            "doctype": doctype,
            "name": name,
            "confirm": True,
            "expected_modified": preview.get("modified"),
        },
    )
    if not result.get("deleted"):
        raise AssertionError(f"{doctype} cleanup did not report deletion")


async def run():
    if os.environ.get("FRAPPE_ALLOW_ROLE_PERMISSION_UPDATE_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_ROLE_PERMISSION_UPDATE_TEST=YES")
    parsed = urlparse(os.environ.get("FRAPPE_URL", ""))
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("This acceptance runner only supports a loopback test site")
    expected_port = os.environ.get("FRAPPE_E2E_EXPECTED_PORT", "")
    if not expected_port.isdecimal() or parsed.port != int(expected_port):
        raise RuntimeError("Set FRAPPE_E2E_EXPECTED_PORT to the verified test-site port")
    expected_site = os.environ.get("FRAPPE_E2E_EXPECTED_SITE", "")
    if not expected_site or os.environ.get("FRAPPE_SITE") != expected_site:
        raise RuntimeError("Set FRAPPE_E2E_EXPECTED_SITE to the verified test site")
    if os.environ.get("FRAPPE_USERNAME", "Administrator") != "Administrator":
        raise RuntimeError("This test requires the isolated site's Administrator")

    root = Path(__file__).resolve().parents[1]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(root),
    )
    role = f"MCP QA Permission {uuid.uuid4().hex[:10]}"
    permission_name = None
    result = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "frappe_major_version": None,
        "operation": "set_role_permissions add then update existing row",
        "doctype": "ToDo",
        "steps": {},
        "cleanup_verified": False,
        "site_isolated": True,
    }
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            site = await call(session, "get_site_info", {})
            result["frappe_major_version"] = site.get("frappe_major_version")
            if result["frappe_major_version"] != 16:
                raise RuntimeError("Refusing: configured MCP target is not Frappe v16")
            try:
                role_preview = await call(
                    session,
                    "create_document",
                    {
                        "doctype": "Role",
                        "values": {"role_name": role},
                    },
                )
                if not role_preview.get("requires_confirmation"):
                    raise AssertionError("Role creation lacked preview confirmation")
                created_role = await call(
                    session,
                    "create_document",
                    {
                        "doctype": "Role",
                        "values": {"role_name": role},
                        "confirm": True,
                    },
                )
                if created_role.get("name") != role:
                    raise AssertionError("Created role name did not match unique fixture")
                result["steps"]["temporary_role_create"] = "pass"

                preview = await call(
                    session,
                    "set_role_permissions",
                    {
                        "doctype": "ToDo",
                        "role": role,
                        "permissions": {"read": True},
                    },
                )
                if not preview.get("requires_confirmation"):
                    raise AssertionError("Permission add lacked confirmation preview")
                await call(
                    session,
                    "set_role_permissions",
                    {
                        "doctype": "ToDo",
                        "role": role,
                        "permissions": {"read": True},
                        "confirm": True,
                    },
                )
                rows = await call(
                    session,
                    "list_documents",
                    {
                        "doctype": "Custom DocPerm",
                        "filters": {"parent": "ToDo", "role": role, "permlevel": 0, "if_owner": 0},
                        "fields": ["name", "read", "write", "role", "parent"],
                        "limit": 5,
                    },
                )
                rows = (
                    rows.get("documents", rows.get("rows", [])) if isinstance(rows, dict) else rows
                )
                if (
                    not isinstance(rows, list)
                    or len(rows) != 1
                    or rows[0].get("read") not in (1, True)
                ):
                    raise AssertionError("Add path did not persist exactly one read permission")
                permission_name = rows[0]["name"]
                result["steps"]["add_path_and_database_readback"] = "pass"

                update_preview = await call(
                    session,
                    "set_role_permissions",
                    {
                        "doctype": "ToDo",
                        "role": role,
                        "permissions": {"read": False, "write": True},
                    },
                )
                if not update_preview.get("requires_confirmation"):
                    raise AssertionError("Update lacked confirmation preview")
                await call(
                    session,
                    "set_role_permissions",
                    {
                        "doctype": "ToDo",
                        "role": role,
                        "permissions": {"read": False, "write": True},
                        "confirm": True,
                    },
                )
                after = await call(
                    session,
                    "get_document",
                    {
                        "doctype": "Custom DocPerm",
                        "name": permission_name,
                        "fields": ["name", "read", "write", "role", "parent"],
                    },
                )
                if (
                    after.get("name") != permission_name
                    or after.get("read") not in (0, False)
                    or after.get("write") not in (1, True)
                ):
                    raise AssertionError("Existing permission row was not updated in place")
                result["steps"]["existing_row_update_and_database_readback"] = "pass"
                result["steps"]["stable_permission_row_identity"] = "pass"
            finally:
                # Search by the unique fixture name even if create_document persisted before
                # the client lost its response. Cleanup is verified, not inferred from flags.
                cleanup_ok = True
                try:
                    residual = await call(
                        session,
                        "list_documents",
                        {
                            "doctype": "Custom DocPerm",
                            "filters": {"parent": "ToDo", "role": role},
                            "fields": ["name"],
                            "limit": 10,
                        },
                    )
                    residual_rows = (
                        residual.get("documents", residual.get("rows", []))
                        if isinstance(residual, dict)
                        else residual
                    )
                    for row in residual_rows:
                        await delete_record(session, "Custom DocPerm", row["name"])
                    result["steps"]["permission_row_cleanup"] = "pass"
                except Exception:
                    cleanup_ok = False
                    result["steps"]["permission_row_cleanup"] = "failed"
                try:
                    role_rows_before_delete = await call(
                        session,
                        "list_documents",
                        {
                            "doctype": "Role",
                            "filters": {"name": role},
                            "fields": ["name"],
                            "limit": 2,
                        },
                    )
                    role_rows_before_delete = (
                        role_rows_before_delete.get(
                            "documents", role_rows_before_delete.get("rows", [])
                        )
                        if isinstance(role_rows_before_delete, dict)
                        else role_rows_before_delete
                    )
                    for row in role_rows_before_delete:
                        await delete_record(session, "Role", row["name"])
                    result["steps"]["temporary_role_cleanup"] = "pass"
                except Exception:
                    cleanup_ok = False
                    result["steps"]["temporary_role_cleanup"] = "failed"
                try:
                    remaining_roles = await call(
                        session,
                        "list_documents",
                        {
                            "doctype": "Role",
                            "filters": {"name": role},
                            "fields": ["name"],
                            "limit": 2,
                        },
                    )
                    remaining_perms = await call(
                        session,
                        "list_documents",
                        {
                            "doctype": "Custom DocPerm",
                            "filters": {"parent": "ToDo", "role": role},
                            "fields": ["name"],
                            "limit": 2,
                        },
                    )
                    role_rows = (
                        remaining_roles.get("documents", remaining_roles.get("rows", []))
                        if isinstance(remaining_roles, dict)
                        else remaining_roles
                    )
                    perm_rows = (
                        remaining_perms.get("documents", remaining_perms.get("rows", []))
                        if isinstance(remaining_perms, dict)
                        else remaining_perms
                    )
                    result["cleanup_verified"] = cleanup_ok and not role_rows and not perm_rows
                except Exception:
                    result["cleanup_verified"] = False
                result["steps"]["final_cleanup_query_empty"] = (
                    "pass" if result["cleanup_verified"] else "failed"
                )
    return result


if __name__ == "__main__":
    outcome = asyncio.run(run())
    print(json.dumps(outcome, indent=2, sort_keys=True))
    if not outcome.get("cleanup_verified"):
        raise SystemExit("Temporary Frappe fixture cleanup was not verified")
