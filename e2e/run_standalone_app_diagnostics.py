"""Read-only live checks for app-aware Frappe diagnostics over MCP stdio.

Requires the usual FRAPPE_URL and authentication environment plus
FRAPPE_APPS_PATH. The report contains aggregate counts only: no site name,
document data, patch names, method names, or credentials.
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


def app_versions(site_info: dict, app: str) -> dict[str, str]:
    wanted = {"frappe", "erpnext", app}
    return {
        row["app"]: str(row.get("version", "unknown"))
        for row in site_info.get("apps", [])
        if row.get("app") in wanted
    }


async def list_all_method_rows(session, app: str, as_config: bool = False) -> tuple[int, dict]:
    offset = 0
    total_rows = 0
    first_page: dict = {}
    while True:
        page = result_value(
            await session.call_tool(
                "list_app_methods",
                {"app": app, "limit": 200, "offset": offset, "as_config": as_config},
            )
        )
        if not first_page:
            first_page = page
        total_rows += len(page.get("rows", []))
        if not page.get("has_more"):
            return total_rows, first_page
        offset += len(page.get("rows", []))
        if not page.get("rows"):
            raise RuntimeError("list_app_methods returned an empty page with has_more=true")


async def run() -> dict:
    root = Path(__file__).resolve().parents[1]
    app = os.environ.get("FRAPPE_DIAGNOSTIC_APP", "")
    if not app:
        raise RuntimeError("Set FRAPPE_DIAGNOSTIC_APP to the installed application under review")
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
            site = result_value(await session.call_tool("get_site_info", {}))
            patches = result_value(await session.call_tool("list_pending_patches", {"app": app}))
            drift_counts: dict[str, int] = {}
            drift_total = 0
            drift_offset = 0
            drift_missing_local_modules = 0
            site_ahead_doctypes: list[str] = []
            while True:
                drift = result_value(
                    await session.call_tool(
                        "list_app_drift", {"app": app, "limit": 200, "offset": drift_offset}
                    )
                )
                drift_total = drift.get("total", drift_total)
                drift_missing_local_modules = len(drift.get("modules_missing_locally", []))
                for verdict, count in drift.get("counts", {}).items():
                    drift_counts[verdict] = drift_counts.get(verdict, 0) + count
                site_ahead_doctypes.extend(
                    row["doctype"]
                    for row in drift.get("items", [])
                    if row.get("verdict") == "site_ahead"
                )
                next_offset = drift.get("next_offset")
                if next_offset is None:
                    break
                if next_offset <= drift_offset:
                    raise RuntimeError("list_app_drift returned a non-advancing next_offset")
                drift_offset = next_offset
            site_ahead_details = []
            for doctype in site_ahead_doctypes:
                detail = result_value(
                    await session.call_tool("diff_doctype", {"doctype": doctype, "app": app})
                )
                file_diff = detail.get("doctype_file") or {}
                changes = file_diff.get("changes", {})
                site_ahead_details.append(
                    {
                        "section_change_counts": {
                            key: len(value) if isinstance(value, dict | list) else int(bool(value))
                            for key, value in changes.items()
                        },
                        "changed_doc_type_property_names": sorted(
                            (changes.get("properties") or {}).keys()
                        ),
                        "newer_side": file_diff.get("newer"),
                        "migrate_imports_file": file_diff.get("migrate_imports_file"),
                        "diff_truncated": file_diff.get("truncated"),
                    }
                )
            method_count, methods = await list_all_method_rows(session, app)
            suggestion_count, suggestions = await list_all_method_rows(session, app, as_config=True)
            return {
                "server_name": initialized.serverInfo.name,
                "tool_count": len(tools.tools),
                "frappe_major_version": site.get("frappe_major_version"),
                "app_versions": app_versions(site, app),
                "patches": {
                    "format": patches.get("format"),
                    "pending_count": patches.get("pending_count"),
                    "applied_count": patches.get("applied_count"),
                    "unknown_in_log_count": patches.get("unknown_in_log_count"),
                    "warning_count_reported": len(patches.get("warnings", [])),
                    "warning_list_at_limit": len(patches.get("warnings", [])) >= 20,
                },
                "drift": {
                    "total": drift_total,
                    "counts": drift_counts,
                    "missing_local_module_count": drift_missing_local_modules,
                    "site_ahead_details": site_ahead_details,
                },
                "app_methods": {
                    "files_scanned": methods.get("files_scanned"),
                    "files_truncated": methods.get("files_truncated"),
                    "method_count": method_count,
                    "skipped_file_count_reported": len(methods.get("skipped", [])),
                    "skipped_file_list_at_limit": len(methods.get("skipped", [])) >= 50,
                    "config_suggestion_count": suggestion_count,
                },
                "read_only": True,
            }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))
