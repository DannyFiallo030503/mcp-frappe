"""Validate Frappe's document rename path on a temporary Salutation record (v16 only)."""

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

DOCTYPE = "Salutation"


def value(result):
    if result.isError:
        detail = result.content[0].text if result.content else "MCP tool call failed"
        raise RuntimeError(detail.splitlines()[0][:300])
    if result.structuredContent:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, arguments):
    return value(await session.call_tool(tool, arguments))


async def rows_for(session, name):
    result = await call(
        session,
        "list_documents",
        {
            "doctype": DOCTYPE,
            "filters": {"name": name},
            "fields": ["name", "salutation", "modified"],
            "limit": 2,
        },
    )
    return result.get("rows", [])


async def rows_for_marker(session, token):
    result = await call(
        session,
        "list_documents",
        {
            "doctype": DOCTYPE,
            "filters": {"salutation": ["like", f"%{token}%"]},
            "fields": ["name", "salutation", "owner"],
            "limit": 5,
        },
    )
    return result.get("rows", [])


async def delete_if_present(session, name):
    rows = await rows_for(session, name)
    if len(rows) > 1:
        raise RuntimeError(f"Multiple records matched the temporary name {name}")
    if rows:
        preview = await call(session, "delete_document", {"doctype": DOCTYPE, "name": name})
        if not preview.get("requires_confirmation"):
            raise RuntimeError("Delete did not require confirmation")
        deleted = await call(
            session,
            "delete_document",
            {
                "doctype": DOCTYPE,
                "name": name,
                "confirm": True,
                "expected_modified": preview.get("modified"),
            },
        )
        if not deleted.get("deleted"):
            raise RuntimeError(f"Delete was not confirmed for {name}")


async def run():
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
        raise RuntimeError("This acceptance runner requires the isolated Administrator user")

    token = uuid.uuid4().hex[:12]
    old = f"MCP QA Salutation {token}"
    new = f"MCP QA Salutation Renamed {token}"
    evidence = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "doctype": DOCTYPE,
        "frappe_major_version": None,
        "frappe_version": None,
        "old_name": old,
        "new_name": new,
        "steps": {},
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
            evidence["frappe_major_version"] = site.get("frappe_major_version")
            frappe = next((app for app in site.get("apps", []) if app.get("app") == "frappe"), {})
            evidence["frappe_version"] = frappe.get("version")
            if site.get("frappe_major_version") != 16 or frappe.get("version") != "16.34.0":
                raise RuntimeError("Refusing: runner is pinned to isolated Frappe 16.34.0")

            schema = await call(
                session, "get_doctype_schema", {"doctype": DOCTYPE, "detail": "full"}
            )
            doctype = schema.get("doctype", {})
            if not doctype.get("allow_rename") or doctype.get("autoname") != "field:salutation":
                raise RuntimeError("Salutation metadata no longer matches this runner")
            if await rows_for(session, old) or await rows_for(session, new):
                raise RuntimeError("Refusing: generated test name already exists")
            evidence["steps"]["schema_and_name_preflight"] = "pass"

            created = False
            created_name = None
            renamed_name = None
            rename_attempted = False
            try:
                create_payload = {"doctype": DOCTYPE, "values": {"salutation": old}}
                create_preview = await call(session, "create_document", create_payload)
                if not create_preview.get("requires_confirmation"):
                    raise RuntimeError("Create did not return a confirmation preview")
                create_result = await call(
                    session, "create_document", {**create_payload, "confirm": True}
                )
                created_name = create_result.get("name")
                if create_result.get("name") != old:
                    raise RuntimeError(f"Frappe did not create the record: {create_result}")
                old = create_result["name"]
                evidence["old_name"] = old
                created = True
                created_rows = await rows_for(session, old)
                if len(created_rows) != 1 or created_rows[0].get("salutation") != old:
                    raise AssertionError("Created Salutation did not read back")
                evidence["steps"]["create_and_readback"] = "pass"

                rename_args = {
                    "kind": "document",
                    "doctype": DOCTYPE,
                    "old": old,
                    "new": new,
                    "merge": False,
                }
                rename_preview = await call(session, "rename", rename_args)
                if not rename_preview.get("requires_confirmation"):
                    raise RuntimeError("Rename did not return a confirmation preview")
                if not rename_preview.get("preview_id"):
                    raise RuntimeError("Rename preview did not include a preview_id")
                incoming_links = {
                    "links": rename_preview.get("links"),
                    "links_truncated": rename_preview.get("links_truncated"),
                    "linked_records": rename_preview.get("linked_records"),
                }
                evidence["rename_preview"] = {
                    "frappe_updates": rename_preview.get("frappe_updates", []),
                    "incoming_links": incoming_links,
                    "notes": rename_preview.get("notes", []),
                }
                links = incoming_links["links"]
                if (
                    not isinstance(links, list)
                    or incoming_links["links_truncated"] is not False
                    or incoming_links["linked_records"] != 0
                    or any(link.get("records") != 0 for link in links)
                ):
                    raise RuntimeError(
                        "Refusing to rename: the temporary Salutation has incoming links"
                    )
                rename_attempted = True
                renamed = await call(
                    session,
                    "rename",
                    {
                        **rename_args,
                        "expected_preview": rename_preview.get("preview_id"),
                        "confirm": True,
                    },
                )
                renamed_name = renamed.get("name") or renamed.get("new")
                evidence["renamed_name_actual"] = renamed_name
                if not renamed.get("renamed") or renamed.get("name") != new:
                    raise AssertionError(f"Unexpected Frappe rename result: {renamed}")
                new_rows = await rows_for(session, new)
                old_rows = await rows_for(session, old)
                if len(new_rows) != 1 or old_rows or new_rows[0].get("salutation") != new:
                    raise AssertionError("Renamed document did not read back under its new name")
                evidence["steps"]["rename_preview_confirm_and_readback"] = "pass"
            except Exception as error:
                evidence["failure"] = str(error)[:500]
            finally:
                try:
                    cleanup_names = dict.fromkeys(
                        name for name in (new, old, created_name, renamed_name) if name
                    )
                    marker_rows = await rows_for_marker(session, token)
                    if len(marker_rows) > 1:
                        raise RuntimeError("Multiple records matched the unique cleanup marker")
                    for row in marker_rows:
                        if row.get("owner") != "Administrator" or token not in row.get(
                            "salutation", ""
                        ):
                            raise RuntimeError("Marker lookup returned a record outside this test")
                        cleanup_names[row["name"]] = None
                        if rename_attempted and renamed_name is None:
                            renamed_name = row["name"]
                    for name in cleanup_names:
                        await delete_if_present(session, name)
                    named_remaining = []
                    for name in cleanup_names:
                        named_remaining.extend(await rows_for(session, name))
                    remaining_markers = await rows_for_marker(session, token)
                    rename_state_known = not rename_attempted or bool(renamed_name)
                    evidence["cleanup_verified"] = (
                        not named_remaining and not remaining_markers and rename_state_known
                    )
                    evidence["steps"]["temporary_document_deleted"] = (
                        "pass" if evidence["cleanup_verified"] else "failed"
                    )
                except Exception as error:
                    evidence["cleanup_verified"] = False
                    evidence["steps"]["temporary_document_deleted"] = str(error)[:300]
            if not created and not evidence.get("failure"):
                raise RuntimeError("Temporary Salutation was not created")
    return evidence


if __name__ == "__main__":
    outcome = asyncio.run(run())
    print(json.dumps(outcome, indent=2, sort_keys=True))
    if outcome.get("failure") or not outcome.get("cleanup_verified"):
        raise SystemExit("Document rename acceptance failed; inspect evidence JSON")
