"""Test submit and cancel as a Website User on an isolated temporary DocType."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import secrets
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def unwrap(result):
    if result.isError:
        raise RuntimeError("MCP tool call failed")
    if result.structuredContent:
        data = result.structuredContent
        return data.get("result", data)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, args):
    return unwrap(await session.call_tool(tool, args))


async def confirm(session, tool, args):
    preview = await call(session, tool, args)
    if not preview.get("requires_confirmation"):
        raise AssertionError(f"{tool} did not return its confirmation preview")
    confirmed = {**args, "confirm": True}
    if tool in {"delete_document", "submit_document", "cancel_document"}:
        confirmed["expected_modified"] = preview.get("modified")
    return await call(session, tool, confirmed)


async def remove_user(admin, email):
    rows = await call(
        admin,
        "list_documents",
        {"doctype": "User Password", "filters": {"user": email}, "fields": ["name"], "limit": 20},
    )
    for row in rows.get("rows", []):
        await confirm(admin, "delete_document", {"doctype": "User Password", "name": row["name"]})
    users = await call(
        admin,
        "list_documents",
        {
            "doctype": "User",
            "filters": {"name": email},
            "fields": ["name", "first_name"],
            "limit": 1,
        },
    )
    if users.get("rows") and users["rows"][0].get("first_name") == "MCP Lifecycle QA":
        await confirm(admin, "delete_document", {"doctype": "User", "name": email})


async def run():
    url = os.environ.get("FRAPPE_URL", "")
    site_name = os.environ.get("FRAPPE_SITE", "")
    if os.environ.get("FRAPPE_ALLOW_LIMITED_LIFECYCLE_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_LIMITED_LIFECYCLE_TEST=YES")
    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"} or not site_name.endswith(
        ".localhost"
    ):
        raise RuntimeError("This runner only changes an isolated loopback .localhost site")
    if os.environ.get("FRAPPE_USERNAME", "Administrator") != "Administrator":
        raise RuntimeError("Fixture creation requires Administrator")

    doctype = "MCP Lifecycle " + uuid.uuid4().hex[:8]
    email = f"mcp-lifecycle-{uuid.uuid4().hex[:12]}@example.com"
    password = f"McpQA-{secrets.token_urlsafe(24)}-9aA!"
    report = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "frappe_version": None,
        "doctype": "temporary custom submittable DocType",
        "permission_role": "All",
        "user_type": "Website User",
        "system_manager_absent": False,
        "create_as_limited_user": False,
        "submit_as_limited_user": False,
        "cancel_as_limited_user": False,
        "cleanup_verified": False,
        "site_isolated": True,
    }
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "frappe_mcp.standalone"], env=env, cwd=str(root)
    )
    document_name = None

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as admin:
            await admin.initialize()
            site = await call(admin, "get_site_info", {})
            if site.get("frappe_major_version") != 16:
                raise RuntimeError("Refusing: runner is pinned to isolated Frappe v16")
            report["frappe_version"] = next(
                (a.get("version") for a in site.get("apps", []) if a.get("app") == "frappe"), None
            )
            doctype_args = {
                "app": "erp_app",
                "module": "CubaZone",
                "name": doctype,
                "is_custom": True,
                "is_submittable": True,
                "fields": [
                    {"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1}
                ],
                "properties": {"autoname": "field:title"},
                "permissions": [
                    {
                        "role": "All",
                        "read": 1,
                        "write": 1,
                        "create": 1,
                        "delete": 1,
                        "submit": 1,
                        "cancel": 1,
                    }
                ],
            }
            try:
                preview = await call(admin, "create_doctype", doctype_args)
                if not preview.get("requires_confirmation"):
                    raise AssertionError("DocType preview was not returned")
                created = await call(admin, "create_doctype", {**doctype_args, "confirm": True})
                if not created.get("created"):
                    raise AssertionError("Frappe did not create the test DocType")

                schema = await call(admin, "get_doctype_schema", {"doctype": "User"})
                fields = {f.get("fieldname"): f for f in schema.get("fields", [])}
                values = {}
                for key, val in {
                    "email": email,
                    "first_name": "MCP Lifecycle QA",
                    "user_type": "Website User",
                    "send_welcome_email": False,
                    "new_password": password,
                }.items():
                    if key in fields and not fields[key].get("read_only"):
                        values[key] = val
                for key, meta in fields.items():
                    if meta.get("fieldtype") == "Table" and not meta.get("read_only"):
                        values[key] = []
                await confirm(admin, "create_document", {"doctype": "User", "values": values})
                user = await call(
                    admin, "get_document", {"doctype": "User", "name": email, "fields": ["roles"]}
                )
                if any(row.get("role") == "System Manager" for row in user.get("roles", [])):
                    raise AssertionError("Limited user unexpectedly has System Manager")
                report["system_manager_absent"] = True

                user_env = env.copy()
                user_env.update({"FRAPPE_USERNAME": email, "FRAPPE_PASSWORD": password})
                user_params = StdioServerParameters(
                    command=sys.executable,
                    args=["-m", "frappe_mcp.standalone"],
                    env=user_env,
                    cwd=str(root),
                )
                async with stdio_client(user_params) as (u_read, u_write):
                    async with ClientSession(u_read, u_write) as user_session:
                        await user_session.initialize()
                        created_doc = await confirm(
                            user_session,
                            "create_document",
                            {"doctype": doctype, "values": {"title": "MCP Lifecycle Acceptance"}},
                        )
                        document_name = created_doc.get("name")
                        if not document_name:
                            raise AssertionError(
                                "Website User could not create the temporary document"
                            )
                        report["create_as_limited_user"] = True
                        submitted = await confirm(
                            user_session,
                            "submit_document",
                            {"doctype": doctype, "name": document_name},
                        )
                        if not submitted.get("submitted"):
                            raise AssertionError("Website User submit failed")
                        status = await call(
                            user_session,
                            "get_document",
                            {"doctype": doctype, "name": document_name, "fields": ["docstatus"]},
                        )
                        if status.get("docstatus") != 1:
                            raise AssertionError("Submitted docstatus was not 1")
                        report["submit_as_limited_user"] = True
                        canceled = await confirm(
                            user_session,
                            "cancel_document",
                            {"doctype": doctype, "name": document_name},
                        )
                        if not canceled.get("cancelled"):
                            raise AssertionError("Website User cancel failed")
                        status = await call(
                            user_session,
                            "get_document",
                            {"doctype": doctype, "name": document_name, "fields": ["docstatus"]},
                        )
                        if status.get("docstatus") != 2:
                            raise AssertionError("Canceled docstatus was not 2")
                        report["cancel_as_limited_user"] = True
            finally:
                try:
                    if document_name:
                        rows = await call(
                            admin,
                            "list_documents",
                            {
                                "doctype": doctype,
                                "filters": {"name": document_name},
                                "fields": ["name", "docstatus"],
                                "limit": 1,
                            },
                        )
                        if rows.get("rows"):
                            if rows["rows"][0].get("docstatus") == 1:
                                await confirm(
                                    admin,
                                    "cancel_document",
                                    {"doctype": doctype, "name": document_name},
                                )
                            await confirm(
                                admin,
                                "delete_document",
                                {"doctype": doctype, "name": document_name},
                            )
                    doctypes = await call(admin, "list_doctypes", {"search": doctype, "limit": 5})
                    if any(row.get("name") == doctype for row in doctypes.get("rows", [])):
                        preview = await call(admin, "delete_doctype", {"doctype": doctype})
                        await call(
                            admin,
                            "delete_doctype",
                            {
                                "doctype": doctype,
                                "expected_modified": preview.get("modified"),
                                "confirm": True,
                            },
                        )
                    await remove_user(admin, email)
                    remaining = await call(admin, "list_doctypes", {"search": doctype, "limit": 5})
                    users = await call(
                        admin,
                        "list_documents",
                        {
                            "doctype": "User",
                            "filters": {"name": email},
                            "fields": ["name"],
                            "limit": 1,
                        },
                    )
                    report["cleanup_verified"] = not remaining.get("rows") and not users.get("rows")
                except Exception as error:
                    raise RuntimeError(
                        f"Test cleanup failed ({type(error).__name__}); "
                        "inspect the unique test records"
                    ) from error

    if not report["cleanup_verified"]:
        raise AssertionError("Temporary DocType or user remains after cleanup")
    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
