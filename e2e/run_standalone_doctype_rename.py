"""Exercise native virtual DocType rename and safe recovery on an isolated Frappe 16 site.

Set FRAPPE_ALLOW_DOCTYPE_RENAME_TEST=YES, FRAPPE_E2E_EXPECTED_PORT, and
FRAPPE_E2E_EXPECTED_SITE. For the default-reloader recovery case, also set
FRAPPE_EXPECT_DOCTYPE_RENAME_RECOVERY=YES and point FRAPPE_E2E_CLEANUP_URL/PORT
at a stable HTTP worker for the same site so cleanup remains controlled by MCP.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

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


async def _delete_on_cleanup_endpoint(
    environment: dict[str, str], names: tuple[str, ...], module: str
) -> None:
    """Remove the test DocType through MCP on a stable worker after reload recovery."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=environment,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            site = await call(session, "get_site_info", {})
            if site.get("frappe_major_version") != 16:
                raise RuntimeError("Cleanup endpoint is not a Frappe 16 site")
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
                if row.get("module") != module or row.get("custom") or not row.get("is_virtual"):
                    raise RuntimeError("Refusing cleanup: DocType metadata differs from this test")
                preview = await call(session, "delete_doctype", {"doctype": row["name"]})
                if not preview.get("requires_confirmation"):
                    raise RuntimeError("delete_doctype did not return a confirmation preview")
                await call(
                    session,
                    "delete_doctype",
                    {
                        "doctype": row["name"],
                        "confirm": True,
                        "expected_modified": preview["modified"],
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
            if remaining.get("rows"):
                raise RuntimeError("Cleanup endpoint left test DocType metadata behind")


def _test_only_tree(root: Path, slugs: tuple[str, ...]) -> bool:
    """Allow only generated files for this run before removing retained recovery copies."""
    for path in root.rglob("*"):
        if path.is_dir() and path.name == "__pycache__":
            continue
        if (
            path.name in {"__init__.py", "original", "moved-doctype-folder"}
            or path.suffix == ".pyc"
        ):
            continue
        if not path.name.startswith(slugs):
            return False
    return True


def _recovery_paths(error: str) -> tuple[Path | None, Path | None]:
    snapshot_match = re.search(r"original snapshot retained at ([^;]+)", error)
    moved_match = re.search(r"moved files quarantined at ([^;]+)", error)
    snapshot = Path(snapshot_match.group(1)) if snapshot_match else None
    moved = Path(moved_match.group(1)) if moved_match else None
    return snapshot, moved


async def run():
    if os.environ.get("FRAPPE_ALLOW_DOCTYPE_RENAME_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_DOCTYPE_RENAME_TEST=YES for temporary schema writes")
    url = urlparse(os.environ.get("FRAPPE_URL", ""))
    if url.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("This runner only supports a loopback test site")
    expected_port = os.environ.get("FRAPPE_E2E_EXPECTED_PORT", "")
    if not expected_port.isdecimal() or url.port != int(expected_port):
        raise RuntimeError("Set FRAPPE_E2E_EXPECTED_PORT to the verified test endpoint port")
    expected_site = os.environ.get("FRAPPE_E2E_EXPECTED_SITE", "")
    if not expected_site or os.environ.get("FRAPPE_SITE") != expected_site:
        raise RuntimeError("Set FRAPPE_E2E_EXPECTED_SITE to the verified test site")
    cleanup_environment = None
    if os.environ.get("FRAPPE_EXPECT_DOCTYPE_RENAME_RECOVERY") == "YES":
        cleanup_url = os.environ.get("FRAPPE_E2E_CLEANUP_URL", "")
        cleanup_port = os.environ.get("FRAPPE_E2E_CLEANUP_PORT", "")
        cleanup_endpoint = urlparse(cleanup_url)
        if (
            cleanup_endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not cleanup_port.isdecimal()
            or cleanup_endpoint.port != int(cleanup_port)
            or cleanup_endpoint.port == url.port
        ):
            raise RuntimeError(
                "Set FRAPPE_E2E_CLEANUP_URL/PORT to a separate loopback endpoint for the same site"
            )
        ping = Request(
            cleanup_url.rstrip("/") + "/api/method/ping",
            headers={"Host": expected_site},
        )
        with urlopen(ping, timeout=5) as response:
            if response.status != 200 or json.load(response).get("message") != "pong":
                raise RuntimeError("Cleanup endpoint did not answer the Frappe ping")
        cleanup_environment = os.environ.copy()
        cleanup_environment["FRAPPE_URL"] = cleanup_url
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
    expect_reloader_recovery = os.environ.get("FRAPPE_EXPECT_DOCTYPE_RENAME_RECOVERY") == "YES"
    recovery_snapshot: Path | None = None
    recovery_moved: Path | None = None
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
            doctype_root = package.resolve()
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
                    k: created.get(k) for k in ("created", "native_files_complete")
                }
                created_names.append(old)
                if not created.get("created"):
                    raise RuntimeError("DocType creation did not complete")
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
                    "app_files_reported": bool(rename_preview.get("app_files")),
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
                try:
                    renamed = await call(
                        session,
                        "rename",
                        {
                            **rename_args,
                            "expected_preview": rename_preview["preview_id"],
                            "confirm": True,
                        },
                    )
                except RuntimeError as error:
                    message = str(error)
                    expected_error = (
                        "Frappe disconnected during the rename and the DocType was not renamed"
                    )
                    if not expect_reloader_recovery or expected_error not in message:
                        raise
                    recovery_snapshot, recovery_moved = _recovery_paths(message)
                    if recovery_snapshot is None or recovery_moved is None:
                        raise RuntimeError(
                            "Expected reload recovery did not retain both safety copies"
                        ) from error
                    evidence["rename_recovery"] = {
                        "interruption_observed": True,
                        "original_snapshot_retained": True,
                        "renamed_files_quarantined": True,
                    }
                    renamed = None
                evidence["rename_result"] = (
                    {
                        k: renamed.get(k)
                        for k in ("renamed", "name", "reconciled_after_server_reload")
                    }
                    if renamed is not None
                    else {"renamed": False}
                )
                if renamed is None:
                    await wait_for_site(session)
                    old_schema = await call(
                        session, "get_doctype_schema", {"doctype": old, "detail": "full"}
                    )
                    new_rows = await call(
                        session,
                        "list_documents",
                        {
                            "doctype": "DocType",
                            "filters": {"name": new},
                            "fields": ["name"],
                            "limit": 2,
                        },
                    )
                    if not old_schema.get("doctype", {}).get("is_virtual") or new_rows.get("rows"):
                        raise RuntimeError("Reload recovery did not preserve old metadata only")
                    if not old_folder.is_dir() or new_folder.exists():
                        raise RuntimeError(
                            "Reload recovery did not restore the original app folder"
                        )
                    if (
                        recovery_moved.parent.parent.resolve() != doctype_root
                        or recovery_moved.name != "moved-doctype-folder"
                        or not recovery_moved.is_dir()
                        or not recovery_snapshot.is_dir()
                        or not recovery_snapshot.joinpath("original").is_dir()
                    ):
                        raise RuntimeError(
                            "Reload recovery artifacts are outside the expected app/state folders"
                        )
                    evidence["steps"]["reloader_interruption_recovered"] = "pass"
                else:
                    if not renamed.get("renamed") or renamed.get("name") != new:
                        raise RuntimeError("Unexpected native Frappe rename result")
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
                evidence["failure"] = type(error).__name__
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
                    if expect_reloader_recovery and recovery_snapshot and recovery_moved:
                        if cleanup_environment is None:
                            raise RuntimeError(
                                "A stable cleanup endpoint is required after reload recovery"
                            )
                        await _delete_on_cleanup_endpoint(cleanup_environment, names, module)
                        evidence["cleanup_method"] = (
                            "native MCP delete on the same site's stable endpoint"
                        )
                        await wait_for_site(session)
                    else:
                        delete_preview = await call(
                            session, "delete_doctype", {"doctype": row["name"]}
                        )
                        if not delete_preview.get("requires_confirmation"):
                            raise RuntimeError(
                                "delete_doctype did not return a confirmation preview"
                            )
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
                evidence["virtual_table_expectation"] = (
                    "Frappe virtual DocTypes skip schema table creation"
                )
                if evidence["cleanup_verified"] and recovery_snapshot and recovery_moved:
                    quarantine = recovery_moved.parent
                    snapshot_root = recovery_snapshot
                    expected_slugs = (
                        old_folder.name,
                        new_folder.name,
                        f"test_{old_folder.name}",
                        f"test_{new_folder.name}",
                    )
                    allowed_snapshot_parent = (
                        Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state"))
                        / "mcp-frappe"
                        / "doctype-recoveries"
                    ).resolve()
                    if (
                        quarantine.parent.resolve() != doctype_root
                        or snapshot_root.parent.resolve() != allowed_snapshot_parent
                        or not _test_only_tree(quarantine, expected_slugs)
                        or not _test_only_tree(snapshot_root, expected_slugs)
                    ):
                        raise RuntimeError(
                            "Refusing to remove recovery artifacts outside this test"
                        )
                    shutil.rmtree(quarantine)
                    shutil.rmtree(snapshot_root)
                    evidence["recovery_artifacts_removed"] = (
                        not quarantine.exists() and not snapshot_root.exists()
                    )
    return evidence


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("failure") or not result.get("cleanup_verified"):
        raise SystemExit("DocType rename acceptance failed; inspect the result")
