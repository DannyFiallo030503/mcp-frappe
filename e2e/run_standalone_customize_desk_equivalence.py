"""Compare customize_doctype's Property Setter with a native Customize Form save on v16."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

DOCTYPE = "ToDo"
PROPERTY = "grid_page_length"
TEST_VALUES = (17, 23)


def unwrap(result):
    if result.isError:
        details = " ".join(item.text for item in result.content if getattr(item, "text", None))
        raise RuntimeError(f"MCP tool call failed: {details[:500]}")
    structured = result.structuredContent or {}
    if structured:
        return structured.get("result", structured)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, name, arguments):
    return unwrap(await session.call_tool(name, arguments))


def native_call(client: httpx.Client, method: str, form: dict):
    response = client.request(
        method,
        "/api/method/frappe.handler.run_doc_method",
        params={"method": "fetch_to_customize", "docs": json.dumps(form)}
        if method == "GET"
        else None,
        json={"method": "save_customization", "docs": json.dumps(form)}
        if method == "POST"
        else None,
    )
    response.raise_for_status()
    result = response.json().get("message") or response.json()
    return result


def normalized_setter(row):
    return {
        "doctype": "Property Setter",
        **{
            key: row.get(key)
            for key in (
                "doc_type",
                "doctype_or_field",
                "field_name",
                "row_name",
                "property",
                "property_type",
                "value",
                "default_value",
            )
        },
    }


async def rows_for_property(session):
    result = await call(
        session,
        "list_documents",
        {
            "doctype": "Property Setter",
            "filters": {"doc_type": DOCTYPE, "property": PROPERTY},
            "fields": [
                "name",
                "doc_type",
                "doctype_or_field",
                "field_name",
                "row_name",
                "property",
                "property_type",
                "value",
                "default_value",
            ],
            "limit": 100,
        },
    )
    return result.get("rows", [])


async def run():
    if os.environ.get("FRAPPE_ALLOW_CUSTOMIZE_DESK_EQUIVALENCE") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_CUSTOMIZE_DESK_EQUIVALENCE=YES")
    endpoint = urlparse(os.environ.get("FRAPPE_URL", ""))
    expected_port = os.environ.get("FRAPPE_E2E_EXPECTED_PORT", "")
    expected_site = os.environ.get("FRAPPE_E2E_EXPECTED_SITE", "")
    if (
        endpoint.hostname not in {"localhost", "127.0.0.1", "::1"}
        or not expected_port.isdecimal()
        or endpoint.port != int(expected_port)
    ):
        raise RuntimeError(
            "Refusing: set FRAPPE_URL to the verified loopback test endpoint and port"
        )
    if not expected_site or os.environ.get("FRAPPE_SITE") != expected_site:
        raise RuntimeError("Refusing: FRAPPE_SITE must match FRAPPE_E2E_EXPECTED_SITE")
    if os.environ.get("FRAPPE_USERNAME", "Administrator") != "Administrator":
        raise RuntimeError("This acceptance runner requires the isolated site's Administrator")

    report = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "doctype": DOCTYPE,
        "property": PROPERTY,
        "paths_compared": [
            "MCP customize_doctype",
            "native Frappe Customize Form save_customization",
        ],
        "frappe_version": None,
        "mcp_setter": None,
        "native_setter": None,
        "equivalent": False,
        "cleanup_verified": False,
        "site_isolated": True,
    }

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            site = await call(session, "get_site_info", {})
            if site.get("frappe_major_version") != 16:
                raise RuntimeError("Refusing: target is not Frappe v16")
            report["frappe_version"] = next(
                (app.get("version") for app in site.get("apps", []) if app.get("app") == "frappe"),
                None,
            )

            client = httpx.Client(
                base_url=os.environ["FRAPPE_URL"], timeout=30, headers={"Host": expected_site}
            )
            baseline = None
            baseline_loaded = False
            try:
                login = client.post(
                    "/api/method/login",
                    data={
                        "usr": os.environ.get("FRAPPE_USERNAME", "Administrator"),
                        "pwd": os.environ["FRAPPE_PASSWORD"],
                    },
                )
                login.raise_for_status()
                original_rows = await rows_for_property(session)
                if original_rows:
                    raise RuntimeError("Refusing: the test Property Setter already exists")
                fetched = native_call(
                    client,
                    "GET",
                    {"doctype": "Customize Form", "name": "Customize Form", "doc_type": DOCTYPE},
                )
                form = (fetched.get("docs") or [None])[0]
                if not isinstance(form, dict):
                    raise RuntimeError("Native fetch_to_customize returned no form")
                baseline = form.get(PROPERTY)
                baseline_loaded = True
                report["baseline_value"] = baseline

                operations = [{"op": "set_property", "property": PROPERTY, "value": TEST_VALUES[0]}]
                preview = await call(
                    session, "customize_doctype", {"doctype": DOCTYPE, "operations": operations}
                )
                if (
                    not preview.get("requires_confirmation")
                    or len(preview.get("property_setters", [])) != 1
                ):
                    raise AssertionError("MCP preview did not describe exactly one setter")
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
                if not saved.get("saved") or saved.get("warnings"):
                    raise AssertionError("MCP customization did not save cleanly")
                mcp_rows = await rows_for_property(session)
                if len(mcp_rows) != 1:
                    raise AssertionError("MCP save did not create exactly one Property Setter")
                report["mcp_setter"] = normalized_setter(mcp_rows[0])

                # Start the native save from its own fetched form and change only this setting.
                desk_fetch = native_call(
                    client,
                    "GET",
                    {"doctype": "Customize Form", "name": "Customize Form", "doc_type": DOCTYPE},
                )
                desk_form = (desk_fetch.get("docs") or [None])[0]
                desk_form[PROPERTY] = TEST_VALUES[1]
                native_call(client, "POST", desk_form)
                native_rows = await rows_for_property(session)
                if len(native_rows) != 1:
                    raise AssertionError("Native save did not leave exactly one Property Setter")
                report["native_setter"] = normalized_setter(native_rows[0])
                comparable = {k: v for k, v in report["mcp_setter"].items() if k not in {"value"}}
                native_comparable = {
                    k: v for k, v in report["native_setter"].items() if k not in {"value"}
                }
                report["equivalent"] = comparable == native_comparable and report["native_setter"][
                    "value"
                ] == str(TEST_VALUES[1])
                if not report["equivalent"]:
                    raise AssertionError(
                        "MCP and native Customize Form created different Property Setter semantics"
                    )

                # Restore the original setting through Frappe and verify no setter remains.
                restore = native_call(
                    client,
                    "GET",
                    {"doctype": "Customize Form", "name": "Customize Form", "doc_type": DOCTYPE},
                )
                restore_form = (restore.get("docs") or [None])[0]
                restore_form[PROPERTY] = baseline
                native_call(client, "POST", restore_form)
                final_rows = await rows_for_property(session)
                report["cleanup_verified"] = not final_rows
                report["steps"] = {
                    "mcp_save": "pass",
                    "native_desk_save": "pass",
                    "setter_identity_and_semantics_match": "pass",
                    "native_restore": "pass" if not final_rows else "fail",
                }
                if final_rows:
                    raise AssertionError("Cleanup left the test Property Setter behind")
            finally:
                if baseline_loaded and not report["cleanup_verified"]:
                    restore = native_call(
                        client,
                        "GET",
                        {
                            "doctype": "Customize Form",
                            "name": "Customize Form",
                            "doc_type": DOCTYPE,
                        },
                    )
                    restore_form = (restore.get("docs") or [None])[0]
                    if not isinstance(restore_form, dict):
                        raise AssertionError("Cleanup could not reload the native Customize Form")
                    restore_form[PROPERTY] = baseline
                    native_call(client, "POST", restore_form)
                    report["cleanup_verified"] = not await rows_for_property(session)
                client.close()

    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
