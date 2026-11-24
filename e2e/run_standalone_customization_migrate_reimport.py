"""Export, remove, and re-import a Custom Field through Frappe's Bench migrate."""

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
        detail = result.content[0].text if result.content else "MCP tool failed"
        raise RuntimeError(detail[:500])
    if result.structuredContent:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, args):
    return value(await session.call_tool(tool, args))


async def retry_call(session, tool, args, retries=10):
    for attempt in range(retries + 1):
        try:
            return await call(session, tool, args)
        except RuntimeError as error:
            transient = any(
                word in str(error).lower()
                for word in (
                    "peer closed",
                    "connection refused",
                    "server disconnected",
                    "reset by peer",
                    "incomplete read",
                    "502",
                    "503",
                )
            )
            if not transient or attempt == retries:
                raise
            await asyncio.sleep(2)
    raise AssertionError("unreachable")


async def confirm(session, tool, args, preview_key="preview_id"):
    preview = await retry_call(session, tool, args)
    if not preview.get("requires_confirmation") or not preview.get(preview_key):
        raise AssertionError(f"{tool} did not return a confirmation preview")
    result = await retry_call(
        session,
        tool,
        {**args, "confirm": True, "expected_preview": preview[preview_key]},
    )
    return preview, result


async def schema_field(session, doctype, fieldname):
    schema = await retry_call(session, "get_doctype_schema", {"doctype": doctype})
    return next((f for f in schema.get("fields", []) if f.get("fieldname") == fieldname), None)


async def delete_field(session, name):
    rows = await retry_call(
        session,
        "list_documents",
        {"doctype": "Custom Field", "filters": {"name": name}, "fields": ["name"], "limit": 1},
    )
    if not rows.get("rows"):
        return True
    args = {"doctype": "Custom Field", "name": name}
    preview = await retry_call(session, "delete_document", args)
    if not preview.get("requires_confirmation") or not preview.get("modified"):
        raise AssertionError("Custom Field delete did not return a modified-bound preview")
    deleted = await retry_call(
        session,
        "delete_document",
        {**args, "confirm": True, "expected_modified": preview["modified"]},
    )
    return bool(deleted.get("deleted"))


async def rollback(session, app, module, rollback_id):
    if not rollback_id:
        return True
    preview = await retry_call(
        session,
        "rollback_export",
        {"app": app, "module": module, "rollback_id": rollback_id},
    )
    if preview.get("conflict"):
        raise RuntimeError("Export rollback reported a conflict; preserve the export for review")
    result = await retry_call(
        session,
        "rollback_export",
        {
            "app": app,
            "module": module,
            "rollback_id": rollback_id,
            "confirm": True,
            "expected_preview": preview["preview_id"],
        },
    )
    return bool(result.get("rolled_back"))


async def run():
    url = os.environ.get("FRAPPE_URL", "")
    site_name = os.environ.get("FRAPPE_SITE", "")
    parsed = urlparse(url)
    if os.environ.get("FRAPPE_ALLOW_MIGRATE_REIMPORT_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_MIGRATE_REIMPORT_TEST=YES")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or not site_name.endswith(
        ".localhost"
    ):
        raise RuntimeError(
            "Refusing: this acceptance runner is restricted to an isolated loopback .localhost site"
        )
    app = os.environ.get("FRAPPE_TEST_APP", "")
    module = os.environ.get("FRAPPE_TEST_MODULE", "")
    if not app or app in {"frappe", "erpnext"} or not module:
        raise RuntimeError("Set an installed non-core FRAPPE_TEST_APP and its FRAPPE_TEST_MODULE")
    if not os.environ.get("FRAPPE_BENCH_PATH") or not os.environ.get("FRAPPE_APPS_PATH"):
        raise RuntimeError("Set FRAPPE_BENCH_PATH and FRAPPE_APPS_PATH for the existing Bench")

    doctype = "ToDo"
    fieldname = "mcp_migrate_" + uuid.uuid4().hex[:12]
    field = {
        "fieldname": fieldname,
        "fieldtype": "Data",
        "label": "MCP Migrate Reimport",
        "insert_after": "description",
    }
    report = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "doctype": doctype,
        "fieldtype": "Data",
        "frappe_version": None,
        "export_then_delete_then_migrate_reimport": False,
        "cleanup_verified": False,
        "site_isolated": True,
    }
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            site = await call(session, "get_site_info", {})
            if site.get("frappe_major_version") != 16:
                raise RuntimeError("Refusing: this runner is pinned to isolated Frappe v16")
            report["frappe_version"] = next(
                (
                    item.get("version")
                    for item in site.get("apps", [])
                    if item.get("app") == "frappe"
                ),
                None,
            )
            if not any(item.get("app") == app for item in site.get("apps", [])):
                raise RuntimeError("The selected test app is not installed on this site")

            custom_field_name = None
            rollback_id = None
            exported = False
            field_removed = False
            rollback_done = False
            try:
                create_args = {
                    "app": app,
                    "module": module,
                    "doctype": doctype,
                    "field": field,
                }
                create_preview = await retry_call(session, "create_custom_field", create_args)
                if not create_preview.get("requires_confirmation"):
                    raise AssertionError("Custom Field create did not return a preview")
                created = await retry_call(
                    session, "create_custom_field", {**create_args, "confirm": True}
                )
                custom_field_name = created.get("name")
                if not custom_field_name:
                    raise AssertionError("Frappe did not return the Custom Field name")
                if not await schema_field(session, doctype, fieldname):
                    raise AssertionError("Created Custom Field is absent from the schema")

                export_args = {
                    "app": app,
                    "module": module,
                    "doctypes": [doctype],
                    "apply_module_filter": True,
                    "sync_on_migrate": True,
                }
                _, export_result = await confirm(session, "export_customizations", export_args)
                rollback_id = export_result.get("rollback_id")
                if not export_result.get("exported") or not rollback_id:
                    raise AssertionError("Customization export did not return a rollback manifest")
                exported = True

                field_removed = await delete_field(session, custom_field_name)
                if not field_removed or await schema_field(session, doctype, fieldname):
                    raise AssertionError("Pre-migrate deletion did not remove the Custom Field")

                migrate_preview, migrate_result = await confirm(
                    session, "run_bench", {"command": "migrate", "app": app}
                )
                if not migrate_result.get("ok") or migrate_result.get("exit_code") != 0:
                    raise AssertionError("run_bench migrate did not finish successfully")
                imported_field = await schema_field(session, doctype, fieldname)
                if not imported_field or imported_field.get("fieldtype") != "Data":
                    raise AssertionError("bench migrate did not restore the exported Custom Field")
                report["bench_preflight_included"] = bool(migrate_preview.get("preflight"))
                report["bench_migrate_exit_code"] = migrate_result.get("exit_code")
                report["reimported_fieldtype"] = imported_field.get("fieldtype")
                report["export_then_delete_then_migrate_reimport"] = True

                field_removed = await delete_field(session, custom_field_name)
                rollback_done = await rollback(session, app, module, rollback_id)
                rollback_id = None
                absent = await schema_field(session, doctype, fieldname) is None
                report["cleanup_verified"] = field_removed and rollback_done and absent
                report["cleanup"] = {
                    "custom_field_absent": absent,
                    "export_rollback_removed_test_file": rollback_done,
                }
                if not report["cleanup_verified"]:
                    raise AssertionError("Test field or exported file remains after cleanup")
            finally:
                if custom_field_name and not field_removed:
                    field_removed = await delete_field(session, custom_field_name)
                if rollback_id and exported and not rollback_done:
                    rollback_done = await rollback(session, app, module, rollback_id)
                if custom_field_name and field_removed and rollback_done:
                    report["cleanup_verified"] = (
                        await schema_field(session, doctype, fieldname) is None
                    )

    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
