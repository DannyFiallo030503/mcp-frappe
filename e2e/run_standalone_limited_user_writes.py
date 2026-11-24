"""Exercise record CRUD as a temporary non-Administrator Frappe user.

Opt-in and loopback-only. The fixture uses a Website User with no System Manager role, disables
the welcome email, and keeps its random password in process memory. It removes the marked ToDo,
the test user's User Password history row, and the user itself, then verifies cleanup.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


class ToolFailure(RuntimeError):
    def __init__(self, text: str):
        match = re.search(r"Frappe HTTP (\d+) (\w+)", text)
        self.status = match.group(1) if match else None
        self.exc_type = match.group(2) if match else None
        super().__init__(
            f"Frappe HTTP {self.status} {self.exc_type}" if match else "MCP tool error"
        )


def value(result):
    if result.isError:
        raise ToolFailure(result.content[0].text if result.content else "MCP tool failed")
    if result.structuredContent:
        structured = result.structuredContent
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool: str, arguments: dict):
    return value(await session.call_tool(tool, arguments))


async def confirm(session, tool: str, arguments: dict) -> dict:
    preview = await call(session, tool, arguments)
    if not preview.get("requires_confirmation"):
        raise AssertionError(f"{tool} did not require confirmation")
    confirmed = {**arguments, "confirm": True}
    if tool in {"update_document", "delete_document"}:
        confirmed["expected_modified"] = preview.get("modified")
    result = await call(session, tool, confirmed)
    return result


def outcome(error: Exception) -> str:
    if isinstance(error, ToolFailure):
        if error.status in {"401", "403"}:
            return f"denied ({error.status} {error.exc_type})"
        return f"failed ({error.status or error.exc_type or 'tool error'})"
    return f"failed ({type(error).__name__})"


def has_system_manager_role(user_document: dict) -> bool:
    """Fail closed unless the User roles child table was returned and is well formed."""
    if not isinstance(user_document, dict) or not isinstance(user_document.get("roles"), list):
        raise ValueError("User roles could not be verified")
    roles = user_document["roles"]
    if any(not isinstance(row, dict) or not isinstance(row.get("role"), str) for row in roles):
        raise ValueError("User roles could not be verified")
    return any(row["role"] == "System Manager" for row in roles)


async def run() -> dict:
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_SITE", "")
    hostname = urlparse(url).hostname or ""
    if os.environ.get("FRAPPE_ALLOW_LIMITED_USER_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_LIMITED_USER_TEST=YES to opt into temporary writes")
    if not (hostname in {"localhost", "127.0.0.1", "::1"} and site.endswith(".localhost")):
        raise RuntimeError("Limited-user acceptance is restricted to loopback .localhost sites")
    if os.environ.get("FRAPPE_USERNAME", "Administrator") != "Administrator":
        raise RuntimeError("The fixture setup requires an Administrator MCP connection")

    root = Path(__file__).resolve().parents[1]
    admin_env = os.environ.copy()
    limited_email = f"mcp-limited-{uuid.uuid4().hex[:12]}@example.com"
    limited_password = f"McpQA-{secrets.token_urlsafe(24)}-9aA!"
    marker = f"MCP limited-user acceptance {uuid.uuid4().hex}"
    user_created = False
    todo_name: str | None = None
    outcomes: dict[str, str] = {}
    system_manager_role_assigned: bool | None = None
    frappe_major_version: int | None = None

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=admin_env,
        cwd=str(root),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as admin:
            await admin.initialize()
            site_info = await call(admin, "get_site_info", {})
            frappe_major_version = site_info.get("frappe_major_version")
            try:
                user_schema = await call(admin, "get_doctype_schema", {"doctype": "User"})
                user_fields = {
                    field["fieldname"]: field
                    for field in user_schema.get("fields", [])
                    if field.get("fieldname")
                }
                # Frappe Cuba's global validate hook iterates optional User child tables; send
                # empty arrays for writable tables that exist on this version. Metadata also
                # differs between v15 and v16, so omit fields absent from the connected schema.
                # In particular `roles` is read-only and must remain unset.
                user_values = {}
                for fieldname, value in {
                    "email": limited_email,
                    "first_name": "MCP Limited User QA",
                    "user_type": "Website User",
                    "send_welcome_email": False,
                    "new_password": limited_password,
                }.items():
                    meta = user_fields.get(fieldname)
                    if meta and not meta.get("read_only"):
                        user_values[fieldname] = value
                for fieldname, meta in user_fields.items():
                    if meta.get("fieldtype") == "Table" and not meta.get("read_only"):
                        user_values[fieldname] = []
                created = await confirm(
                    admin, "create_document", {"doctype": "User", "values": user_values}
                )
                if created.get("name") != limited_email:
                    raise AssertionError("Temporary User creation returned an unexpected identity")
                user_created = True
                outcomes["create_temporary_website_user"] = "pass"
            except Exception as error:
                outcomes["create_temporary_website_user"] = outcome(error)

            if user_created:
                try:
                    created_user = await call(
                        admin,
                        "get_document",
                        {"doctype": "User", "name": limited_email, "fields": ["roles"]},
                    )
                    system_manager_role_assigned = has_system_manager_role(created_user)
                    outcomes["system_manager_role_absent"] = (
                        "fail" if system_manager_role_assigned else "pass"
                    )
                except Exception as error:
                    outcomes["system_manager_role_absent"] = outcome(error)

            if user_created and system_manager_role_assigned is False:
                limited_env = admin_env.copy()
                limited_env["FRAPPE_USERNAME"] = limited_email
                limited_env["FRAPPE_PASSWORD"] = limited_password
                limited_params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "frappe_mcp.standalone"],
                    env=limited_env,
                    cwd=str(root),
                )
                try:
                    async with stdio_client(limited_params) as (limited_read, limited_write):
                        async with ClientSession(limited_read, limited_write) as limited:
                            await limited.initialize()
                            outcomes["limited_user_login"] = "pass"
                            todo_values = {"description": marker, "allocated_to": limited_email}
                            try:
                                created_todo = await confirm(
                                    limited,
                                    "create_document",
                                    {"doctype": "ToDo", "values": todo_values},
                                )
                                todo_name = created_todo.get("name")
                                if not todo_name:
                                    raise AssertionError("ToDo creation returned no name")
                                outcomes["create_todo"] = "pass"
                            except Exception as error:
                                outcomes["create_todo"] = outcome(error)

                            if todo_name:
                                try:
                                    readback = await call(
                                        limited,
                                        "get_document",
                                        {
                                            "doctype": "ToDo",
                                            "name": todo_name,
                                            "fields": ["description"],
                                        },
                                    )
                                    outcomes["read_own_todo"] = (
                                        "pass" if readback.get("description") == marker else "fail"
                                    )
                                except Exception as error:
                                    outcomes["read_own_todo"] = outcome(error)

                                try:
                                    update_values = {"description": f"{marker} updated"}
                                    await confirm(
                                        limited,
                                        "update_document",
                                        {
                                            "doctype": "ToDo",
                                            "name": todo_name,
                                            "values": update_values,
                                        },
                                    )
                                    updated = await call(
                                        limited,
                                        "get_document",
                                        {
                                            "doctype": "ToDo",
                                            "name": todo_name,
                                            "fields": ["description"],
                                        },
                                    )
                                    outcomes["update_own_todo"] = (
                                        "pass"
                                        if updated.get("description")
                                        == update_values["description"]
                                        else "fail"
                                    )
                                except Exception as error:
                                    outcomes["update_own_todo"] = outcome(error)

                                try:
                                    await confirm(
                                        limited,
                                        "delete_document",
                                        {"doctype": "ToDo", "name": todo_name},
                                    )
                                    todo_name = None
                                    outcomes["delete_own_todo"] = "pass"
                                except Exception as error:
                                    outcomes["delete_own_todo"] = outcome(error)
                except Exception as error:
                    outcomes["limited_user_login"] = outcome(error)
            elif user_created:
                outcomes["limited_user_login"] = "skipped (role safety check not passed)"

            # Each resource has its own cleanup attempt; one API failure must not skip the rest.
            try:
                todo_rows = await call(
                    admin,
                    "list_documents",
                    {
                        "doctype": "ToDo",
                        "fields": ["name", "modified"],
                        "filters": [["description", "like", f"{marker}%"]],
                        "limit": 20,
                    },
                )
                for row in todo_rows.get("rows", []):
                    try:
                        preview = await call(
                            admin, "delete_document", {"doctype": "ToDo", "name": row["name"]}
                        )
                        await call(
                            admin,
                            "delete_document",
                            {
                                "doctype": "ToDo",
                                "name": row["name"],
                                "expected_modified": preview.get("modified"),
                                "confirm": True,
                            },
                        )
                    except Exception as error:
                        outcomes["cleanup_todo_delete"] = outcome(error)
            except Exception as error:
                outcomes["cleanup_todo_lookup"] = outcome(error)

            try:
                password_rows = await call(
                    admin,
                    "list_documents",
                    {
                        "doctype": "User Password",
                        "fields": ["name", "modified"],
                        "filters": {"user": limited_email},
                        "limit": 20,
                    },
                )
                for row in password_rows.get("rows", []):
                    try:
                        preview = await call(
                            admin,
                            "delete_document",
                            {"doctype": "User Password", "name": row["name"]},
                        )
                        await call(
                            admin,
                            "delete_document",
                            {
                                "doctype": "User Password",
                                "name": row["name"],
                                "expected_modified": preview.get("modified"),
                                "confirm": True,
                            },
                        )
                    except Exception as error:
                        outcomes["cleanup_password_history_delete"] = outcome(error)
            except Exception as error:
                outcomes["cleanup_password_history_lookup"] = outcome(error)

            try:
                user_rows = await call(
                    admin,
                    "list_documents",
                    {
                        "doctype": "User",
                        "fields": ["name", "first_name", "user_type"],
                        "filters": {"name": limited_email},
                        "limit": 2,
                    },
                )
                if user_rows.get("rows"):
                    if len(user_rows["rows"]) != 1 or (
                        user_rows["rows"][0].get("first_name") != "MCP Limited User QA"
                        or user_rows["rows"][0].get("user_type") != "Website User"
                    ):
                        raise RuntimeError("Temporary User identity did not match the fixture")
                    preview = await call(
                        admin, "delete_document", {"doctype": "User", "name": limited_email}
                    )
                    await call(
                        admin,
                        "delete_document",
                        {
                            "doctype": "User",
                            "name": limited_email,
                            "expected_modified": preview.get("modified"),
                            "confirm": True,
                        },
                    )
            except Exception as error:
                outcomes["cleanup_user_delete"] = outcome(error)

            cleanup_checks = {
                "cleanup_temporary_user": {
                    "doctype": "User",
                    "fields": ["name"],
                    "filters": {"name": limited_email},
                    "limit": 1,
                },
                "cleanup_todo_records": {
                    "doctype": "ToDo",
                    "fields": ["name"],
                    "filters": [["description", "like", f"{marker}%"]],
                    "limit": 2,
                },
                "cleanup_password_history": {
                    "doctype": "User Password",
                    "fields": ["name"],
                    "filters": {"user": limited_email},
                    "limit": 2,
                },
            }
            cleanup_ok = True
            for check, arguments in cleanup_checks.items():
                try:
                    remaining = await call(admin, "list_documents", arguments)
                    passed = not remaining.get("rows")
                    outcomes[check] = "pass" if passed else "fail"
                    cleanup_ok = cleanup_ok and passed
                except Exception as error:
                    outcomes[check] = outcome(error)
                    cleanup_ok = False
            outcomes["cleanup_verified"] = "pass" if cleanup_ok else "fail"

    return {
        "frappe_major_version": frappe_major_version,
        "limited_user_type": "Website User",
        "system_manager_role_assigned": system_manager_role_assigned,
        "welcome_email_sent": False,
        "operations": outcomes,
    }


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    required = {
        "create_temporary_website_user",
        "system_manager_role_absent",
        "limited_user_login",
        "create_todo",
        "read_own_todo",
        "update_own_todo",
        "delete_own_todo",
        "cleanup_temporary_user",
        "cleanup_todo_records",
        "cleanup_password_history",
        "cleanup_verified",
    }
    if any(result["operations"].get(step) != "pass" for step in required):
        raise SystemExit(1)
