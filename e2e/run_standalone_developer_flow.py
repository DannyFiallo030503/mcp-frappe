"""Run the whole developer workflow against a disposable development site.

The flow, over a real MCP stdio session:

1. create a standard child table and a submittable DocType in the test app, then check
   that ``diff_doctype`` reports ``in_sync``;
2. add, move, change, and remove fields with ``update_doctype``, then check the app JSON
   and ``diff_doctype`` again;
3. add a Custom Field to a ``frappe`` DocType, export it, check the ``custom/`` file, and
   roll the export back;
4. create a site-custom DocType and promote it into the test app;
5. create, submit, cancel, and delete a test document;
6. add a Workflow, a Notification, a Report Builder report, and a Workspace;
7. remove everything, restore the app's files, and check ``list_app_drift``.

It writes site metadata and app files, so it needs explicit opt-in and refuses anything but
a loopback ``.localhost`` site:

    FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES
    FRAPPE_APPS_PATH=<the site's Bench apps directory>
    FRAPPE_TEST_APP=<a disposable test app installed on the site>
    FRAPPE_TEST_MODULE=<a module of that app>
    FRAPPE_TEST_CUSTOMIZE_DOCTYPE=ToDo   # optional: the frappe DocType to customize

The site needs developer mode and an Administrator login (deleting standard DocTypes
needs it). Every object gets a random suffix; cleanup runs in a ``finally`` block, and
leftover folders named with that suffix are removed from the test module. The output is
aggregate pass/fail per area, versions, and counts: no record values, paths, or hostnames.
Redirect it to a file to keep it as evidence.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import traceback
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
TRANSIENT = (
    "peer closed",
    "connecterror",
    "connection refused",
    "server disconnected",
    "reset by peer",
    "incomplete read",
    "remoteprotocolerror",
    "502",
    "503",
)


def value(result):
    if result.isError:
        raise RuntimeError(result.content[0].text if result.content else "MCP tool failed")
    structured = result.structuredContent or {}
    if isinstance(structured, dict) and set(structured) == {"result"}:
        return structured["result"]
    if result.content:
        try:
            return json.loads(result.content[0].text)
        except json.JSONDecodeError:
            return result.content[0].text
    return None


def transient(error: Exception) -> bool:
    return any(token in str(error).lower() for token in TRANSIENT)


def scrub(name: str) -> str:
    """Frappe's folder name for a DocType, Report, or Workspace name."""
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def settings() -> dict[str, str]:
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_SITE", "")
    hostname = urlparse(url).hostname or ""
    if os.environ.get("FRAPPE_ALLOW_DEVELOPER_FLOW_TEST") != "YES":
        raise SystemExit(
            "Set FRAPPE_ALLOW_DEVELOPER_FLOW_TEST=YES to opt into schema and app-file writes"
        )
    if hostname not in {"localhost", "127.0.0.1", "::1"} or not site.endswith(".localhost"):
        raise SystemExit("The developer flow is restricted to loopback .localhost sites")
    required = {
        "apps_path": "FRAPPE_APPS_PATH",
        "app": "FRAPPE_TEST_APP",
        "module": "FRAPPE_TEST_MODULE",
    }
    values = {key: os.environ.get(name, "") for key, name in required.items()}
    missing = [required[key] for key, item in values.items() if not item]
    if missing:
        raise SystemExit(f"Set {', '.join(missing)}")
    if values["app"] in {"frappe", "erpnext"}:
        raise SystemExit("FRAPPE_TEST_APP must be a disposable test app, not frappe or erpnext")
    values["customize"] = os.environ.get("FRAPPE_TEST_CUSTOMIZE_DOCTYPE", "ToDo")
    return values


def snapshot(folder: Path) -> set[str]:
    if not folder.is_dir():
        return set()
    return {
        str(path.relative_to(folder))
        for path in folder.rglob("*")
        if "__pycache__" not in path.parts
    }


def file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def git_changes(folder: Path) -> int | None:
    """Count changed paths in the app's Git checkout, or None without Git."""
    try:
        done = subprocess.run(
            ["git", "-C", str(folder), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return len([line for line in done.stdout.splitlines() if line.strip()])


def mcp_commit() -> dict[str, object]:
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}
    return {"commit": commit, "dirty": dirty}


class Flow:
    def __init__(self, session: ClientSession, config: dict[str, str]):
        self.session = session
        self.app = config["app"]
        self.module = config["module"]
        self.customize = config["customize"]
        self.app_root = Path(config["apps_path"]) / self.app
        self.module_dir = self.app_root / self.app / scrub(self.module)
        self.suffix = uuid.uuid4().hex[:8]
        self.token = f"mcp_flow_{self.suffix}"
        prefix = f"MCP Flow {self.suffix}"
        self.names = {
            "child": f"{prefix} Item",
            "parent": f"{prefix}",
            "custom": f"{prefix} Note",
            "custom_field": f"mcp_flow_{self.suffix}",
            "state_draft": f"MCP {self.suffix} Draft",
            "state_approved": f"MCP {self.suffix} Approved",
            "action": f"MCP {self.suffix} Approve",
            "workflow": f"{prefix} Approval",
            "notification": f"{prefix} Alert",
            "report": f"{prefix} Open",
            "workspace": f"{prefix} Desk",
        }
        self.areas: dict[str, object] = {}
        self.details: dict[str, object] = {}
        self.cleanup: list[tuple[str, Callable[[], Awaitable[bool]]]] = []

    # -- MCP calls -------------------------------------------------------------------------

    async def call(self, tool: str, arguments: dict):
        return value(await self.session.call_tool(tool, arguments))

    async def read(self, tool: str, arguments: dict, retries: int = 10):
        """Call a read tool, waiting out a developer-mode Bench reload."""
        for attempt in range(retries + 1):
            try:
                return await self.call(tool, arguments)
            except RuntimeError as error:
                if not transient(error) or attempt == retries:
                    raise
                await asyncio.sleep(2)
        raise AssertionError("unreachable")

    async def preview_confirm(self, tool: str, arguments: dict, token: str | None = None):
        """Preview, check it asks for confirmation, then confirm with its token."""
        preview = await self.read(tool, arguments)
        if not preview.get("requires_confirmation"):
            raise AssertionError(f"{tool} did not return a preview")
        extra: dict[str, object] = {"confirm": True}
        if token == "modified" and preview.get("modified"):
            extra["expected_modified"] = preview["modified"]
        if token == "preview_id":
            extra["expected_preview"] = preview["preview_id"]
        return preview, await self.call(tool, {**arguments, **extra})

    async def exists(self, doctype: str, name: str) -> bool:
        page = await self.read(
            "list_documents",
            {"doctype": doctype, "filters": {"name": name}, "fields": ["name"], "limit": 1},
        )
        return bool(page["rows"])

    async def delete_document(self, doctype: str, name: str) -> bool:
        for _ in range(3):
            if not await self.exists(doctype, name):
                return True
            try:
                await self.preview_confirm(
                    "delete_document", {"doctype": doctype, "name": name}, "modified"
                )
            except RuntimeError as error:
                if not transient(error):
                    raise
                await asyncio.sleep(2)
        return not await self.exists(doctype, name)

    async def delete_doctype(self, name: str) -> bool:
        for _ in range(3):
            if not await self.exists("DocType", name):
                return True
            try:
                await self.preview_confirm("delete_doctype", {"doctype": name}, "modified")
            except RuntimeError as error:
                if not transient(error):
                    raise
                await asyncio.sleep(2)
        return not await self.exists("DocType", name)

    def on_cleanup(self, label: str, action: Callable[[], Awaitable[bool]]) -> None:
        self.cleanup.append((label, action))

    async def diff_verdict(self, doctype: str) -> str:
        return (await self.read("diff_doctype", {"doctype": doctype, "app": self.app}))["verdict"]

    def doctype_json(self, doctype: str) -> dict:
        slug = scrub(doctype)
        path = self.module_dir / "doctype" / slug / f"{slug}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    # -- Steps -----------------------------------------------------------------------------

    async def step_create(self) -> None:
        child, parent = self.names["child"], self.names["parent"]
        base = {"app": self.app, "module": self.module}
        _, created = await self.preview_confirm(
            "create_doctype",
            {
                **base,
                "name": child,
                "is_child_table": True,
                "fields": [
                    {"fieldname": "item_name", "fieldtype": "Data", "label": "Item",
                     "in_list_view": 1},
                    {"fieldname": "qty", "fieldtype": "Int", "label": "Qty", "in_list_view": 1},
                ],
            },
        )
        self.on_cleanup("child_doctype", lambda: self.delete_doctype(child))
        child_files = created.get("native_files_complete")
        _, created = await self.preview_confirm(
            "create_doctype",
            {
                **base,
                "name": parent,
                "is_submittable": True,
                "fields": [
                    {"fieldname": "title", "fieldtype": "Data", "label": "Title", "reqd": 1,
                     "in_list_view": 1},
                    {"fieldname": "legacy_code", "fieldtype": "Data", "label": "Legacy Code"},
                    {"fieldname": "status", "fieldtype": "Select", "label": "Status",
                     "options": "Open\nClosed", "default": "Open"},
                    {"fieldname": "notes", "fieldtype": "Small Text", "label": "Notes"},
                    {"fieldname": "amount", "fieldtype": "Currency", "label": "Amount"},
                    {"fieldname": "workflow_state", "fieldtype": "Link", "label": "State",
                     "options": "Workflow State", "hidden": 1, "allow_on_submit": 1},
                    {"fieldname": "items", "fieldtype": "Table", "label": "Items",
                     "options": child},
                ],
                "permissions": [
                    {"role": "System Manager", "read": 1, "write": 1, "create": 1,
                     "delete": 1, "submit": 1, "cancel": 1},
                ],
            },
        )
        self.on_cleanup("parent_doctype", lambda: self.delete_doctype(parent))
        self.details["native_files_complete"] = bool(
            child_files and created.get("native_files_complete")
        )
        verdicts = [await self.diff_verdict(child), await self.diff_verdict(parent)]
        self.details["diff_after_create"] = verdicts
        if verdicts != ["in_sync", "in_sync"]:
            raise AssertionError(f"diff_doctype after create: {verdicts}")

    async def step_update(self) -> None:
        parent = self.names["parent"]
        operations = [
            {"op": "add_field", "insert_after": "title",
             "field": {"fieldname": "priority", "fieldtype": "Select", "label": "Priority",
                       "options": "Low\nHigh"}},
            {"op": "move_field", "fieldname": "notes", "insert_after": "priority"},
            {"op": "update_field", "fieldname": "amount", "set": {"label": "Total Amount"}},
            {"op": "remove_field", "fieldname": "legacy_code"},
        ]
        preview, updated = await self.preview_confirm(
            "update_doctype", {"doctype": parent, "operations": operations}, "modified"
        )
        if not updated.get("updated"):
            raise AssertionError("update_doctype did not report an update")
        self.details["update_reconciled_after_reload"] = bool(
            updated.get("reconciled_after_server_reload")
        )
        document = self.doctype_json(parent)
        order = document.get("field_order", [])
        labels = {row.get("fieldname"): row.get("label") for row in document.get("fields", [])}
        checks = {
            "added": "priority" in order,
            "removed": "legacy_code" not in order and "legacy_code" not in labels,
            "moved": "notes" in order and order.index("notes") == order.index("priority") + 1,
            "changed": labels.get("amount") == "Total Amount",
        }
        self.details["app_json_after_update"] = checks
        verdict = await self.diff_verdict(parent)
        self.details["diff_after_update"] = verdict
        if not all(checks.values()) or verdict != "in_sync":
            raise AssertionError(f"app JSON {checks}, diff_doctype {verdict}")

    async def step_customize(self) -> None:
        doctype, fieldname = self.customize, self.names["custom_field"]
        base = {"app": self.app, "module": self.module, "doctype": doctype}
        _, created = await self.preview_confirm(
            "create_custom_field",
            {**base, "field": {"fieldname": fieldname, "fieldtype": "Data",
                               "label": "MCP Flow Field"}},
        )
        name = created.get("name") or f"{doctype}-{fieldname}"
        self.on_cleanup("custom_field", lambda: self.delete_document("Custom Field", name))
        custom_file = self.module_dir / "custom" / f"{scrub(doctype)}.json"
        before = file_hash(custom_file)
        export_args = {"app": self.app, "module": self.module, "doctypes": [doctype],
                       "apply_module_filter": True}
        _, exported = await self.preview_confirm("export_customizations", export_args,
                                                 "preview_id")
        rollback_id = exported["rollback_id"]
        rollback_args = {"app": self.app, "module": self.module, "rollback_id": rollback_id}

        async def rollback() -> bool:
            await self.preview_confirm("rollback_export", rollback_args, "preview_id")
            return file_hash(custom_file) == before

        self.on_cleanup("export_rollback", rollback)
        exported_rows = json.loads(custom_file.read_text(encoding="utf-8"))
        holds = fieldname in {row.get("fieldname") for row in exported_rows["custom_fields"]}
        restored = await rollback()
        self.cleanup.pop()  # rolled back; a failed rollback stays queued for cleanup
        self.details["custom_file_holds_field"] = holds
        self.details["export_rolled_back"] = restored
        if not (holds and restored):
            raise AssertionError("custom/ file did not hold the field, or rollback failed")

    async def step_promote(self) -> None:
        name = self.names["custom"]
        await self.preview_confirm(
            "create_doctype",
            {"app": self.app, "module": self.module, "name": name, "is_custom": True,
             "fields": [{"fieldname": "note", "fieldtype": "Data", "label": "Note"}]},
        )
        self.on_cleanup("custom_doctype", lambda: self.delete_doctype(name))
        before = await self.diff_verdict(name)
        _, promoted = await self.preview_confirm(
            "promote_custom_doctype",
            {"doctype": name, "app": self.app, "module": self.module},
            "modified",
        )
        after = await self.diff_verdict(name)
        self.details["promote"] = {
            "verdict_before": before,
            "native_files_complete": bool(promoted.get("native_files_complete")),
            "verdict_after": after,
        }
        if before != "site_custom" or not promoted.get("promoted") or after != "in_sync":
            raise AssertionError(f"promote: {self.details['promote']}")

    async def step_lifecycle(self) -> None:
        parent = self.names["parent"]
        _, created = await self.preview_confirm(
            "create_document",
            {"doctype": parent, "values": {"title": "MCP flow record",
                                           "items": [{"item_name": "Sample", "qty": 1}]}},
        )
        name = created.get("name") or created.get("document", {}).get("name")
        if not name:
            raise AssertionError("create_document returned no name")
        self.on_cleanup("document", lambda: self.delete_document(parent, name))
        _, submitted = await self.preview_confirm(
            "submit_document", {"doctype": parent, "name": name}, "modified"
        )
        _, cancelled = await self.preview_confirm(
            "cancel_document", {"doctype": parent, "name": name}, "modified"
        )
        deleted = await self.delete_document(parent, name)
        self.details["lifecycle_docstatus"] = [
            submitted.get("docstatus"), cancelled.get("docstatus")
        ]
        if submitted.get("docstatus") != 1 or cancelled.get("docstatus") != 2 or not deleted:
            raise AssertionError(f"lifecycle: {self.details['lifecycle_docstatus']}")

    async def step_workflow(self) -> None:
        parent, names = self.names["parent"], self.names
        preview, saved = await self.preview_confirm(
            "upsert_workflow",
            {
                "doctype": parent,
                "name": names["workflow"],
                "states": [
                    {"state": names["state_draft"], "doc_status": 0,
                     "allow_edit": "System Manager"},
                    {"state": names["state_approved"], "doc_status": 1,
                     "allow_edit": "System Manager"},
                ],
                "transitions": [
                    {"state": names["state_draft"], "action": names["action"],
                     "next_state": names["state_approved"], "allowed": "System Manager"},
                ],
            },
        )
        for doctype, created in preview.get("creates", {}).items():
            for master in created:
                self.on_cleanup(
                    "workflow_master",
                    lambda doctype=doctype, master=master: self.delete_document(doctype, master),
                )
        self.on_cleanup(
            "workflow", lambda: self.delete_document("Workflow", names["workflow"])
        )
        active = (await self.read("get_workflow", {"doctype": parent})).get("workflow") or {}
        self.details["workflow_masters_created"] = sum(
            len(rows) for rows in preview.get("creates", {}).values()
        )
        if not saved.get("saved") or active.get("name") != names["workflow"]:
            raise AssertionError("Workflow was not saved or is not active")

    async def step_notification(self) -> None:
        name = self.names["notification"]
        _, saved = await self.preview_confirm(
            "upsert_notification",
            {
                "name": name,
                "document_type": self.names["parent"],
                "event": "New",
                "channel": "System Notification",
                "recipients": [{"receiver_by_role": "System Manager"}],
                "subject": "New {{ doc.name }}",
                "message": "{{ doc.title }} was created",
            },
        )
        self.on_cleanup("notification", lambda: self.delete_document("Notification", name))
        if not saved.get("saved"):
            raise AssertionError("Notification was not saved")

    async def step_report(self) -> None:
        name = self.names["report"]
        _, saved = await self.preview_confirm(
            "upsert_report",
            {"name": name, "ref_doctype": self.names["parent"],
             "columns": ["name", "title", "amount"], "filters": {"status": "Open"},
             "order_by": "modified desc"},
        )
        self.on_cleanup("report", lambda: self.delete_document("Report", name))
        sample = saved.get("sample") or {}
        self.details["report_sample_ran"] = "error" not in sample
        if not saved.get("saved") or "error" in sample:
            raise AssertionError("Report was not saved or its sample failed")

    async def step_workspace(self) -> None:
        name, parent = self.names["workspace"], self.names["parent"]
        _, saved = await self.preview_confirm(
            "upsert_workspace",
            {
                "name": name,
                "module": self.module,
                "shortcuts": [
                    {"label": "Records", "type": "DocType", "link_to": parent,
                     "doc_view": "List"},
                    {"label": "Open Records", "type": "Report",
                     "link_to": self.names["report"]},
                ],
                "link_cards": [
                    {"label": "Masters",
                     "links": [{"label": "Records", "type": "DocType", "link_to": parent}]},
                ],
            },
        )
        workspace = saved.get("workspace") or name
        self.on_cleanup("workspace", lambda: self.delete_document("Workspace", workspace))
        slug = scrub(name)
        written = (self.module_dir / "workspace" / slug / f"{slug}.json").is_file()
        layout = await self.read("get_workspace", {"name": workspace})
        self.details["workspace_file_written"] = written
        if not saved.get("saved") or len(layout.get("shortcuts", [])) != 2:
            raise AssertionError("Workspace was not saved with its shortcuts")

    # -- Cleanup ---------------------------------------------------------------------------

    async def run_cleanup(self) -> dict[str, object]:
        results: dict[str, object] = {}
        for label, action in reversed(self.cleanup):
            key = label if label not in results else f"{label}_{len(results)}"
            try:
                results[key] = bool(await action())
            except Exception as error:
                print(f"cleanup {label} failed: {error}", file=sys.stderr)
                results[key] = False
        self.cleanup.clear()
        return results

    def remove_leftovers(self, before: set[str]) -> int:
        """Remove folders this run created that Frappe left behind in the test module."""
        removed = 0
        for relative in sorted(snapshot(self.module_dir) - before, key=len):
            path = self.module_dir / relative
            if not path.exists() or path.is_symlink():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.module_dir.resolve()):
                continue
            ours = self.suffix in path.name
            scaffold = path.is_dir() and all(
                child.name in {"__init__.py", "__pycache__"} for child in path.iterdir()
            )
            if path.is_dir() and (ours or scaffold):
                shutil.rmtree(path)
                removed += 1
            elif path.is_file() and ours:
                path.unlink()
                removed += 1
        return removed


async def run() -> dict:
    config = settings()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(ROOT),
    )
    from frappe_mcp import __version__

    evidence: dict[str, object] = {
        "runner": "developer_flow",
        "run_date": dt.date.today().isoformat(),
        "mcp_version": __version__,
        "mcp": mcp_commit(),
        "transport": "MCP stdio to the standalone server, then Frappe HTTP API",
        "site": "loopback .localhost development site; URL and credentials omitted",
    }
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            flow = Flow(session, config)
            info = await flow.read("get_site_info", {})
            evidence["versions"] = {
                row.get("app"): row.get("version") for row in info.get("apps", [])
            }
            evidence["frappe_major_version"] = info.get("frappe_major_version")
            evidence["identity"] = (
                "Administrator" if info.get("user") == "Administrator" else "other user"
            )
            if config["app"] not in evidence["versions"]:
                raise SystemExit("FRAPPE_TEST_APP is not installed on the site")
            files_before = snapshot(flow.module_dir)
            git_before = git_changes(flow.app_root)
            drift_args = {"app": flow.app, "module": flow.module, "limit": 200}
            drift_before = (await flow.read("list_app_drift", drift_args)).get("counts")

            steps = [
                ("doctype_create_and_diff", flow.step_create),
                ("doctype_update_and_diff", flow.step_update),
                ("custom_field_export_rollback", flow.step_customize),
                ("custom_doctype_promote", flow.step_promote),
                ("document_submit_cancel", flow.step_lifecycle),
                ("workflow", flow.step_workflow),
                ("notification", flow.step_notification),
                ("report_builder_report", flow.step_report),
                ("workspace", flow.step_workspace),
            ]
            failed = False
            try:
                for area, step in steps:
                    if failed:
                        flow.areas[area] = "skipped"
                        continue
                    try:
                        await step()
                        flow.areas[area] = "pass"
                    except Exception as error:
                        failed = True
                        flow.areas[area] = f"fail: {type(error).__name__}"
                        traceback.print_exc(file=sys.stderr)
            finally:
                cleanup = await flow.run_cleanup()
            leftovers = flow.remove_leftovers(files_before)
            drift = await flow.read("list_app_drift", drift_args)
            ours = [item for item in drift.get("items", []) if flow.suffix in item["doctype"]]
            git_after = git_changes(flow.app_root)
            evidence["developer_mode"] = (
                "on (Frappe wrote standard DocType files)"
                if flow.details.get("native_files_complete")
                else "not confirmed"
            )
            evidence["areas"] = flow.areas
            evidence["details"] = flow.details
            evidence["cleanup"] = {
                "steps": cleanup,
                "all_removed": all(cleanup.values()),
                "leftover_paths_removed": leftovers,
                "module_files_restored": snapshot(flow.module_dir) == files_before,
                "app_git_changes_unchanged": (
                    None if git_before is None else git_before == git_after
                ),
                "drift_counts_unchanged": drift.get("counts") == drift_before,
                "test_doctypes_left_in_drift": len(ours),
            }
            evidence["passed"] = (
                all(result == "pass" for result in flow.areas.values())
                and evidence["cleanup"]["all_removed"]
                and evidence["cleanup"]["module_files_restored"]
                and evidence["cleanup"]["drift_counts_unchanged"]
                and not ours
            )
            evidence["secrets_or_record_values_recorded"] = False
    return evidence


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("passed") else 1)
