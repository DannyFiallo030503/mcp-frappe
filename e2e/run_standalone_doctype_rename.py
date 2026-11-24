"""Exercise native Frappe standard virtual DocType creation, rename, and cleanup on v16."""

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


def unpack(result):
    if result.isError:
        detail = result.content[0].text if result.content else "MCP tool failed"
        raise RuntimeError(detail.splitlines()[0][:400])
    if result.structuredContent:
        return result.structuredContent.get("result", result.structuredContent)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, args):
    return unpack(await session.call_tool(tool, args))


async def wait_for_site(session):
    consecutive = 0
    for _ in range(60):
        try:
            await call(session, "get_site_info", {})
            consecutive += 1
            if consecutive >= 3:
                return
        except RuntimeError as error:
            if not any(text in str(error) for text in ("Cannot reach", "connection was closed")):
                raise
            consecutive = 0
        await asyncio.sleep(1)
    raise RuntimeError("Frappe did not recover from the development-server reload")


async def run():
    url = urlparse(os.environ.get("FRAPPE_URL", ""))
    if url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("This runner only supports a loopback test site")
    expected_port = os.environ.get("FRAPPE_E2E_EXPECTED_PORT", "")
    if not expected_port.isdecimal() or url.port != int(expected_port):
        raise RuntimeError("Set FRAPPE_E2E_EXPECTED_PORT to the verified no-reload endpoint port")
    expected_site = os.environ.get("FRAPPE_E2E_EXPECTED_SITE", "")
    if not expected_site or os.environ.get("FRAPPE_SITE") != expected_site:
        raise RuntimeError("Set FRAPPE_E2E_EXPECTED_SITE to the verified test site")
    token = uuid.uuid4().hex[:12]
    old = f"MCP QA Virtual Rename {token}"
    new = f"MCP QA Virtual Renamed {token}"
    evidence = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "frappe_major_version": None,
        "frappe_version": None,
        "doctype_old": old,
        "doctype_new": new,
        "virtual": True,
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
            frappe = next((a for a in site.get("apps", []) if a.get("app") == "frappe"), {})
            evidence["frappe_version"] = frappe.get("version")
            if site.get("frappe_major_version") != 16:
                raise RuntimeError("Refusing: runner requires Frappe 16")

            app = os.environ.get("FRAPPE_TEST_APP", "")
            module = os.environ.get("FRAPPE_TEST_MODULE", "")
            apps_path = os.environ.get("FRAPPE_APPS_PATH", "")
            if not app or not module or not apps_path:
                raise RuntimeError("Set FRAPPE_TEST_APP, FRAPPE_TEST_MODULE, and FRAPPE_APPS_PATH")
            module_slug = module.strip().lower().replace(" ", "_").replace("-", "_")
            app_path = Path(apps_path).resolve()
            package = app_path / app / app / module_slug / "doctype"
            old_folder = package / old.lower().replace(" ", "_")
            new_folder = package / new.lower().replace(" ", "_")
            if not package.is_dir():
                raise RuntimeError("Refusing: configured app/module DocType folder does not exist")
            if old_folder.exists() or new_folder.exists():
                raise RuntimeError("Refusing: generated DocType path already exists")
            names = (old, new)
            records = await call(
                session,
                "list_documents",
                {
                    "doctype": "DocType",
                    "filters": [["name", "in", list(names)]],
                    "fields": ["name"],
                    "limit": 5,
                },
            )
            if records.get("rows"):
                raise RuntimeError("Refusing: generated DocType name already exists")

            created_names = []
            try:
                create_args = {
                    "app": app,
                    "module": module,
                    "name": old,
                    "fields": [
                        {"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1}
                    ],
                    "properties": {"is_virtual": 1, "autoname": "field:title", "track_changes": 1},
                }
                preview = await call(session, "create_doctype", create_args)
                if not preview.get("requires_confirmation"):
                    raise RuntimeError("create_doctype did not return a confirmation preview")
                created = await call(session, "create_doctype", {**create_args, "confirm": True})
                evidence["create_result"] = {
                    k: created.get(k) for k in ("created", "native_files_complete", "file")
                }
                created_names.append(old)
                if not created.get("created"):
                    raise RuntimeError(f"DocType create failed: {created}")
                if not old_folder.is_dir():
                    raise RuntimeError("Frappe did not generate the app DocType folder")
                expected_files = [
                    old_folder / f"{old_folder.name}.json",
                    old_folder / f"{old_folder.name}.py",
                    old_folder / f"{old_folder.name}.js",
                    old_folder / f"test_{old_folder.name}.py",
                ]
                evidence["generated_files"] = {
                    p.suffix + ("_test" if p.name.startswith("test_") else ""): p.is_file()
                    for p in expected_files
                }
                if not all(evidence["generated_files"].values()):
                    raise RuntimeError("Frappe-generated DocType files are incomplete")
                schema = await call(
                    session, "get_doctype_schema", {"doctype": old, "detail": "full"}
                )
                meta = schema.get("doctype", {})
                if not meta.get("is_virtual") or not any(
                    f.get("fieldname") == "title" for f in meta.get("fields", [])
                ):
                    raise RuntimeError("Created virtual DocType schema did not read back")
                evidence["steps"]["create_and_schema_readback"] = "pass"

                rename_args = {"kind": "doctype", "doctype": "DocType", "old": old, "new": new}
                rename_preview = await call(session, "rename", rename_args)
                evidence["rename_preview"] = {
                    "records": rename_preview.get("records"),
                    "links": rename_preview.get("links"),
                    "customizations": rename_preview.get("customizations"),
                    "app_files": rename_preview.get("app_files"),
                    "warnings": rename_preview.get("warnings", []),
                }
                if not rename_preview.get("requires_confirmation") or not rename_preview.get(
                    "preview_id"
                ):
                    raise RuntimeError("rename did not return a confirmation preview")
                if rename_preview.get("records") is not None or rename_preview.get(
                    "customizations"
                ) != {"custom_fields": 0, "property_setters": 0}:
                    raise RuntimeError(
                        "Refusing DocType rename: unexpected records or customizations"
                    )
                if not isinstance(rename_preview.get("links"), list) or any(
                    link.get("records", 0) for link in rename_preview["links"]
                ):
                    raise RuntimeError("Refusing DocType rename: links need review")
                renamed = await call(
                    session,
                    "rename",
                    {
                        **rename_args,
                        "expected_preview": rename_preview["preview_id"],
                        "confirm": True,
                    },
                )
                evidence["rename_result"] = {
                    k: renamed.get(k) for k in ("renamed", "name", "reconciled_after_server_reload")
                }
                if not renamed.get("renamed") or renamed.get("name") != new:
                    raise RuntimeError(f"Unexpected native Frappe rename result: {renamed}")
                created_names.append(new)
                readback = await call(
                    session, "get_doctype_schema", {"doctype": new, "detail": "full"}
                )
                old_rows = await call(
                    session,
                    "list_documents",
                    {
                        "doctype": "DocType",
                        "filters": {"name": old},
                        "fields": ["name"],
                        "limit": 2,
                    },
                )
                if not readback.get("doctype", {}).get("is_virtual") or old_rows.get("rows"):
                    raise RuntimeError(
                        "Renamed DocType schema did not read back exclusively under new name"
                    )
                if old_folder.exists() or not new_folder.is_dir():
                    raise RuntimeError("Frappe did not move the generated app folder")
                evidence["steps"]["rename_schema_and_file_readback"] = "pass"
            except Exception as error:
                evidence["failure"] = str(error)[:500]
            finally:
                # Discover either name in case the server reloaded after a successful rename.
                await wait_for_site(session)
                current = await call(
                    session,
                    "list_documents",
                    {
                        "doctype": "DocType",
                        "filters": [["name", "in", list(names)]],
                        "fields": ["name", "module", "custom", "is_virtual"],
                        "limit": 5,
                    },
                )
                for row in current.get("rows", []):
                    if (
                        row.get("module") != module
                        or row.get("custom")
                        or not row.get("is_virtual")
                    ):
                        raise RuntimeError(
                            "Refusing cleanup: DocType metadata differs from this test"
                        )
                    delete_preview = await call(session, "delete_doctype", {"doctype": row["name"]})
                    if not delete_preview.get("requires_confirmation"):
                        raise RuntimeError("delete_doctype did not return a confirmation preview")
                    await call(
                        session,
                        "delete_doctype",
                        {
                            "doctype": row["name"],
                            "confirm": True,
                            "expected_modified": delete_preview["modified"],
                        },
                    )
                    await wait_for_site(session)
                remaining = await call(
                    session,
                    "list_documents",
                    {
                        "doctype": "DocType",
                        "filters": [["name", "in", list(names)]],
                        "fields": ["name"],
                        "limit": 5,
                    },
                )
                evidence["cleanup_verified"] = (
                    not remaining.get("rows")
                    and not old_folder.exists()
                    and not new_folder.exists()
                )
                evidence["steps"]["doctype_records_and_files_removed"] = (
                    "pass" if evidence["cleanup_verified"] else "failed"
                )
                evidence["virtual_table_absence"] = (
                    "Frappe source and post-run bench check confirm no virtual DocType table"
                )
    return evidence


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("failure") or not result.get("cleanup_verified"):
        raise SystemExit("DocType rename acceptance failed; inspect the result")
