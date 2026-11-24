"""Exercise Customize Form move/add/related-type-change operations on isolated Frappe v16."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

DOCTYPE = "Territory"


def value(result):
    if result.isError:
        text = result.content[0].text if result.content else ""
        match = re.search(r"Frappe HTTP (\d+) ([A-Za-z]+)", text)
        if match:
            detail = text.split(f"Frappe HTTP {match.group(1)} {match.group(2)}", 1)[-1]
            detail = detail.splitlines()[0].strip(" :")[:180]
            raise RuntimeError(f"Frappe HTTP {match.group(1)} {match.group(2)}: {detail}")
        detail = text.splitlines()[0][:240] if text else "no MCP error detail"
        raise RuntimeError(f"MCP tool call failed: {detail}")
    structured = result.structuredContent or {}
    if structured:
        return structured.get("result", structured)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, arguments):
    return value(await session.call_tool(tool, arguments))


async def customization(session, operations):
    preview = await call(
        session, "customize_doctype", {"doctype": DOCTYPE, "operations": operations}
    )
    if not preview.get("requires_confirmation") or not preview.get("preview_id"):
        raise AssertionError("Customize Form did not return a preview token")
    if preview.get("warnings"):
        raise AssertionError("Customize Form preview returned unexpected warnings")
    saved = await call(
        session,
        "customize_doctype",
        {
            "doctype": DOCTYPE,
            "operations": operations,
            "expected_preview": preview["preview_id"],
            "confirm": True,
        },
    )
    if not saved.get("saved"):
        raise AssertionError(f"Customize Form save failed: {saved}")
    return preview, saved


async def schema(session):
    result = await call(session, "get_doctype_schema", {"doctype": DOCTYPE, "detail": "full"})
    doctype = result.get("doctype", {})
    fields = doctype.get("fields", [])
    names = [row.get("fieldname") for row in fields if row.get("fieldname")]
    return doctype, fields, names


async def property_setters(session):
    rows = []
    offset = 0
    while True:
        result = await call(
            session,
            "list_documents",
            {
                "doctype": "Property Setter",
                "filters": {"doc_type": DOCTYPE},
                "fields": ["name", "doc_type", "field_name", "property", "value"],
                "limit": 100,
                "offset": offset,
            },
        )
        rows.extend(result.get("rows", []))
        if not result.get("has_more"):
            return rows
        if not result.get("rows"):
            raise RuntimeError("Property Setter page was empty while has_more was true")
        offset += len(result["rows"])


async def delete_document(session, doctype, name):
    preview = await call(session, "delete_document", {"doctype": doctype, "name": name})
    if not preview.get("requires_confirmation"):
        raise AssertionError(f"{doctype} cleanup lacked a confirmation preview")
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
        raise AssertionError(f"{doctype} cleanup was not confirmed")


async def delete_test_custom_field(session, fieldname):
    result = await call(
        session,
        "list_documents",
        {
            "doctype": "Custom Field",
            "filters": {"dt": DOCTYPE, "fieldname": fieldname},
            "fields": ["name", "fieldname"],
            "limit": 2,
        },
    )
    rows = result.get("rows", [])
    if len(rows) > 1:
        raise AssertionError("Multiple test Custom Fields exist")
    for row in rows:
        await delete_document(session, "Custom Field", row["name"])


async def restore_order(session, names):
    _, _, current = await schema(session)
    if current == names:
        return
    operations = [
        {"op": "move_field", "fieldname": name, "insert_after": names[index - 1] if index else None}
        for index, name in enumerate(names)
    ]
    await customization(session, operations)


async def run():
    if os.environ.get("FRAPPE_ALLOW_CUSTOMIZE_FIELD_OPERATIONS_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_CUSTOMIZE_FIELD_OPERATIONS_TEST=YES")
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
    field_suffix = uuid.uuid4().hex[:12]
    fieldname = f"custom_mcp_qa_{field_suffix}"
    evidence = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "doctype": DOCTYPE,
        "operation": "customize_doctype move_field, add_custom_field and Data-to-Select change",
        "frappe_major_version": None,
        "frappe_version": None,
        "steps": {},
        "cleanup_verified": False,
        "site_isolated": True,
    }
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            site = await call(session, "get_site_info", {})
            evidence["frappe_major_version"] = site.get("frappe_major_version")
            frappe_app = next(
                (app for app in site.get("apps", []) if app.get("app") == "frappe"), {}
            )
            evidence["frappe_version"] = frappe_app.get("version")
            if evidence["frappe_major_version"] != 16 or evidence["frappe_version"] != "16.34.0":
                raise RuntimeError("Refusing: runner is pinned to isolated Frappe 16.34.0")

            _, _, original_order = await schema(session)
            if fieldname in original_order:
                raise RuntimeError("Generated test fieldname already exists")
            if (
                not original_order
                or "territory_name" not in original_order
                or "parent_territory" not in original_order
            ):
                raise RuntimeError(
                    "Territory schema differs from this runner's field-order fixture"
                )
            existing_fields = await call(
                session,
                "list_documents",
                {
                    "doctype": "Custom Field",
                    "filters": {"dt": DOCTYPE},
                    "fields": ["name", "fieldname"],
                    "limit": 100,
                },
            )
            if existing_fields.get("rows"):
                raise RuntimeError("Refusing: Territory already has Custom Fields")
            old_order_setters = [
                row
                for row in await property_setters(session)
                if row.get("property") == "field_order" and not row.get("field_name")
            ]
            if old_order_setters:
                raise RuntimeError(
                    f"Refusing: {DOCTYPE} already has a DocType-level field_order Property Setter"
                )
            evidence["steps"]["preflight_schema_and_no_field_order_setter"] = "pass"

            order_restored = False
            try:
                move_preview, moved = await customization(
                    session,
                    [
                        {
                            "op": "move_field",
                            "fieldname": "parent_territory",
                            "insert_after": "is_group",
                        }
                    ],
                )
                if not any(
                    row.get("property") == "field_order"
                    for row in move_preview.get("property_setters", [])
                ):
                    raise AssertionError("Move preview omitted the field_order Property Setter")
                moved_order_setters = [
                    row
                    for row in moved.get("property_setters", [])
                    if row.get("property") == "field_order" and row.get("to")
                ]
                if len(moved_order_setters) != 1:
                    raise AssertionError("Frappe readback omitted the moved field_order setter")
                moved_order = json.loads(moved_order_setters[0]["to"])
                if moved_order.index("parent_territory") != moved_order.index("is_group") + 1:
                    raise AssertionError("Moved field was not persisted in the requested position")
                evidence["steps"]["move_field_preview_confirm_readback"] = "pass"
                await restore_order(session, original_order)
                order_restored = True
                evidence["steps"]["move_field_order_restored"] = "pass"

                add_ops = [
                    {
                        "op": "add_custom_field",
                        "field": {
                            "fieldname": fieldname,
                            "fieldtype": "Data",
                            "label": f"MCP QA {field_suffix}",
                        },
                        "insert_after": "territory_name",
                    }
                ]
                add_preview, added = await customization(session, add_ops)
                evidence["steps"]["add_custom_field_save_warnings"] = added.get("warnings", [])
                evidence["steps"]["add_custom_field_frappe_messages"] = added.get(
                    "frappe_messages", []
                )
                if not any(
                    row.get("fieldname") == fieldname
                    for row in add_preview.get("custom_fields", [])
                ):
                    raise AssertionError("Add preview omitted the temporary Custom Field")
                after_add = await call(
                    session,
                    "list_documents",
                    {
                        "doctype": "Custom Field",
                        "filters": {"dt": DOCTYPE, "fieldname": fieldname},
                        "fields": ["name", "fieldname", "dt"],
                        "limit": 2,
                    },
                )
                evidence["steps"]["custom_field_rows_immediately_after_save"] = after_add.get(
                    "rows", []
                )
                _, added_fields, _ = await schema(session)
                added_row = next(
                    (row for row in added_fields if row.get("fieldname") == fieldname), None
                )
                if not added_row or added_row.get("fieldtype") != "Data":
                    raise AssertionError(
                        "Temporary Data Custom Field did not persist; "
                        f"warnings={added.get('warnings')}; "
                        f"messages={added.get('frappe_messages')}; "
                        f"rows={after_add.get('rows')}"
                    )
                evidence["steps"]["add_custom_field_preview_confirm_readback"] = "pass"

                type_ops = [
                    {"op": "change_fieldtype", "fieldname": fieldname, "fieldtype": "Select"},
                    {
                        "op": "set_property",
                        "fieldname": fieldname,
                        "property": "options",
                        "value": "Open\nClosed",
                    },
                ]
                type_preview, changed = await customization(session, type_ops)
                if not any(
                    row.get("fieldname") == fieldname
                    for row in type_preview.get("custom_fields", [])
                ):
                    raise AssertionError("Type-change preview omitted the temporary Custom Field")
                _, changed_fields, _ = await schema(session)
                changed_row = next(
                    (row for row in changed_fields if row.get("fieldname") == fieldname), None
                )
                if (
                    not changed_row
                    or changed_row.get("fieldtype") != "Select"
                    or changed_row.get("options") != "Open\nClosed"
                ):
                    raise AssertionError("Related Data-to-Select change/options did not persist")
                evidence["steps"]["related_type_change_and_options_readback"] = "pass"
            except Exception as error:
                evidence["failure"] = str(error)[:500]
            finally:
                cleanup_ok = True
                try:
                    await delete_test_custom_field(session, fieldname)
                    evidence["steps"]["temporary_custom_field_deleted"] = "pass"
                except Exception:
                    cleanup_ok = False
                    evidence["steps"]["temporary_custom_field_deleted"] = "failed"
                try:
                    await restore_order(session, original_order)
                    order_restored = True
                except Exception:
                    cleanup_ok = False
                    order_restored = False
                try:
                    for setter in await property_setters(session):
                        if setter.get("property") == "field_order" and not setter.get("field_name"):
                            await delete_document(session, "Property Setter", setter["name"])
                except Exception:
                    cleanup_ok = False
                try:
                    _, _, final_order = await schema(session)
                    final_field = fieldname in final_order
                    remaining_order_setters = [
                        row
                        for row in await property_setters(session)
                        if row.get("property") == "field_order" and not row.get("field_name")
                    ]
                    evidence["cleanup_verified"] = (
                        cleanup_ok
                        and order_restored
                        and not final_field
                        and final_order == original_order
                        and not remaining_order_setters
                    )
                except Exception:
                    evidence["cleanup_verified"] = False
                evidence["steps"]["final_field_order_and_property_setter_cleanup"] = (
                    "pass" if evidence["cleanup_verified"] else "failed"
                )
    return evidence


if __name__ == "__main__":
    outcome = asyncio.run(run())
    print(json.dumps(outcome, indent=2, sort_keys=True))
    if outcome.get("failure") or not outcome.get("cleanup_verified"):
        raise SystemExit("Customize Form field-operation acceptance failed; inspect evidence JSON")
