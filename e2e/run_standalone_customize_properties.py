"""Exercise reversible DocType-level Check and Data properties on isolated Frappe v16."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROPERTIES = {
    "track_changes": "Check",
    "sort_order": "Data",
    "grid_page_length": "Int",
    "default_view": "Select",
}
ALL_TEST_PROPERTIES = set(PROPERTIES)


def value(result):
    if result.isError:
        raise RuntimeError("MCP tool call failed")
    structured = result.structuredContent or {}
    if structured:
        return structured.get("result", structured)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, arguments):
    return value(await session.call_tool(tool, arguments))


async def setters(session):
    found = []
    offset = 0
    while True:
        result = await call(
            session,
            "list_documents",
            {
                "doctype": "Property Setter",
                "filters": {"doc_type": "ToDo"},
                "fields": ["name", "doc_type", "field_name", "property", "value"],
                "limit": 100,
                "offset": offset,
            },
        )
        found.extend(
            row
            for row in result.get("rows", [])
            if row.get("property") in ALL_TEST_PROPERTIES and not row.get("field_name")
        )
        if not result.get("has_more"):
            return found
        if not result.get("rows"):
            raise RuntimeError("Property Setter page was empty while has_more was true")
        offset += len(result["rows"])


async def set_properties(session, operations):
    preview = await call(
        session, "customize_doctype", {"doctype": "ToDo", "operations": operations}
    )
    if not preview.get("requires_confirmation") or not preview.get("preview_id"):
        raise AssertionError("Customize Form did not return a preview token")
    saved = await call(
        session,
        "customize_doctype",
        {
            "doctype": "ToDo",
            "operations": operations,
            "expected_preview": preview["preview_id"],
            "confirm": True,
        },
    )
    if not saved.get("saved") or saved.get("warnings"):
        raise AssertionError("Frappe did not confirm every customized property")
    return preview, saved


async def delete_setter(session, name):
    preview = await call(session, "delete_document", {"doctype": "Property Setter", "name": name})
    if not preview.get("requires_confirmation"):
        raise AssertionError("Property Setter cleanup lacked a confirmation preview")
    result = await call(
        session,
        "delete_document",
        {
            "doctype": "Property Setter",
            "name": name,
            "confirm": True,
            "expected_modified": preview.get("modified"),
        },
    )
    if not result.get("deleted"):
        raise AssertionError("Property Setter cleanup was not confirmed")


async def run():
    if os.environ.get("FRAPPE_ALLOW_CUSTOMIZE_PROPERTIES_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_CUSTOMIZE_PROPERTIES_TEST=YES")
    endpoint = urlparse(os.environ.get("FRAPPE_URL", ""))
    if endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("This acceptance runner only supports a loopback test site")
    expected_port = os.environ.get("FRAPPE_E2E_EXPECTED_PORT", "")
    if not expected_port.isdecimal() or endpoint.port != int(expected_port):
        raise RuntimeError("Set FRAPPE_E2E_EXPECTED_PORT to the verified test-site port")
    expected_site = os.environ.get("FRAPPE_E2E_EXPECTED_SITE", "")
    if not expected_site or os.environ.get("FRAPPE_SITE") != expected_site:
        raise RuntimeError("Set FRAPPE_E2E_EXPECTED_SITE to the verified test site")
    if os.environ.get("FRAPPE_USERNAME", "Administrator") != "Administrator":
        raise RuntimeError("This test requires the isolated site's Administrator")

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    report = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "doctype": "ToDo",
        "operation": "customize_doctype DocType Check/Data/Int/Select properties, then revert",
        "frappe_major_version": None,
        "frappe_version": None,
        "property_types": PROPERTIES,
        "steps": {},
        "cleanup_verified": False,
        "site_isolated": True,
    }
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            site = await call(session, "get_site_info", {})
            report["frappe_major_version"] = site.get("frappe_major_version")
            frappe_app = next(
                (app for app in site.get("apps", []) if app.get("app") == "frappe"), {}
            )
            report["frappe_version"] = frappe_app.get("version")
            if report["frappe_major_version"] != 16 or report["frappe_version"] != "16.34.0":
                raise RuntimeError(
                    "Refusing: runner is pinned to the isolated Frappe 16.34.0 target"
                )

            mutation_attempted = False
            restored_ok = False
            existing = await setters(session)
            if existing:
                raise RuntimeError("Refusing: test properties already have Property Setters")
            report["steps"]["preflight_no_existing_test_setters"] = "pass"
            try:
                operations = [
                    {"op": "set_property", "property": "track_changes", "value": 0},
                    {"op": "set_property", "property": "sort_order", "value": "ASC"},
                    {"op": "set_property", "property": "grid_page_length", "value": 25},
                    {"op": "set_property", "property": "default_view", "value": "List"},
                ]
                changed_preview = await call(
                    session,
                    "customize_doctype",
                    {
                        "doctype": "ToDo",
                        "operations": operations,
                    },
                )
                if not changed_preview.get("requires_confirmation") or not changed_preview.get(
                    "preview_id"
                ):
                    raise AssertionError("Customize Form did not return a preview token")
                preview_setters = changed_preview.get("property_setters", [])
                created = {
                    item.get("property")
                    for item in preview_setters
                    if item.get("action") == "create"
                }
                if (
                    created != set(PROPERTIES)
                    or len(preview_setters) != len(PROPERTIES)
                    or changed_preview.get("custom_fields")
                    or changed_preview.get("warnings")
                ):
                    raise RuntimeError(
                        "Refusing: unexpected Property Setter state for tested properties"
                    )
                mutation_attempted = True
                changed = await call(
                    session,
                    "customize_doctype",
                    {
                        "doctype": "ToDo",
                        "operations": operations,
                        "expected_preview": changed_preview["preview_id"],
                        "confirm": True,
                    },
                )
                if not changed.get("saved") or changed.get("warnings"):
                    raise AssertionError("Frappe did not apply every previewed property")
                applied = {
                    item.get("property"): str(item.get("to"))
                    for item in changed.get("property_setters", [])
                }
                expected_values = {
                    "track_changes": "0",
                    "sort_order": "ASC",
                    "grid_page_length": "25",
                    "default_view": "List",
                }
                if applied != expected_values:
                    raise AssertionError("Frappe readback did not confirm all requested values")
                persisted = await setters(session)
                readback = {row["property"]: row.get("value") for row in persisted}
                if readback != expected_values:
                    raise AssertionError(
                        "Property Setter records do not contain the requested values"
                    )
                report["steps"]["all_four_property_types_persisted"] = "pass"
                report["steps"]["preview_confirmation_save"] = "pass"

                restore_ops = [
                    {"op": "set_property", "property": "track_changes", "value": 1},
                    {"op": "set_property", "property": "sort_order", "value": "DESC"},
                ]
                restore_preview, restored = await set_properties(session, restore_ops)
                removed = {
                    item.get("property")
                    for item in restored.get("property_setters", [])
                    if item.get("action") == "remove"
                }
                if removed != {"track_changes", "sort_order"}:
                    raise AssertionError(
                        "Restoring Frappe defaults did not remove Check/Data setters"
                    )
                restored_ok = True
                report["steps"]["native_defaults_restored"] = "pass"
                report["steps"]["revert_preview_confirmation"] = "pass"
            finally:
                if mutation_attempted and not restored_ok:
                    try:
                        _, cleanup = await set_properties(
                            session,
                            [
                                {"op": "set_property", "property": "track_changes", "value": 1},
                                {"op": "set_property", "property": "sort_order", "value": "DESC"},
                            ],
                        )
                        restored_ok = not cleanup.get("warnings") and all(
                            item.get("action") == "remove"
                            for item in cleanup.get("property_setters", [])
                        )
                    except Exception:
                        restored_ok = False
                try:
                    residual = await setters(session)
                    if residual:
                        for setter in residual:
                            await delete_setter(session, setter["name"])
                    final_residual = await setters(session)
                    report["cleanup_verified"] = restored_ok and not final_residual
                except Exception:
                    report["cleanup_verified"] = False
                report["steps"]["property_setter_query_empty"] = (
                    "pass" if report["cleanup_verified"] else "failed"
                )
    return report


if __name__ == "__main__":
    outcome = asyncio.run(run())
    print(json.dumps(outcome, indent=2, sort_keys=True))
    if not outcome.get("cleanup_verified"):
        raise SystemExit("Temporary Property Setter cleanup was not verified")
