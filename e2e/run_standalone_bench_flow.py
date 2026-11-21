"""Run the Bench command flow against a disposable development site.

The flow, over a real MCP stdio session with ``run_bench`` registered:

1. write a standard DocType's files (JSON, controller, a passing test) into the test module
   without creating it on the site, and check that ``list_app_drift`` says ``missing_on_site``;
2. ``run_bench(command="reload-doc")``, then check that the DocType is ``in_sync``;
3. ``run_bench(command="run-tests")`` for that DocType (skipped when the site has no
   ``allow_tests``);
4. ``run_bench(command="clear-cache")``;
5. delete the DocType and its folder.

It runs Bench and writes site metadata and app files, so it needs explicit opt-in and refuses
anything but a loopback ``.localhost`` site:

    FRAPPE_ALLOW_BENCH_TEST=YES
    FRAPPE_BENCH_PATH=<the site's Bench folder>
    FRAPPE_APPS_PATH=<the site's Bench apps directory>
    FRAPPE_TEST_APP=<a disposable test app installed on the site>
    FRAPPE_TEST_MODULE=<a module of that app>

The site needs developer mode and an Administrator login. The DocType gets a random suffix;
cleanup runs in a ``finally`` block. The output is aggregate pass/fail per step, versions,
and exit codes: no record values, paths, hostnames, or Bench output. Redirect it to a file to
keep it as evidence.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import shutil
import sys
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlparse

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
TRANSIENT = ("peer closed", "connecterror", "connection refused", "server disconnected",
             "reset by peer", "incomplete read", "remoteprotocolerror", "502", "503")
TEST_FILE = '''import frappe

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:  # Frappe v15
    from frappe.tests.utils import FrappeTestCase as TestCase


class Test{classname}(TestCase):
    def test_insert(self):
        doc = frappe.get_doc({{"doctype": "{doctype}", "title": "bench flow"}}).insert()
        self.assertTrue(doc.name)
'''


def value(result):
    if result.isError:
        raise RuntimeError(result.content[0].text if result.content else "MCP tool failed")
    if result.content:
        try:
            return json.loads(result.content[0].text)
        except json.JSONDecodeError:
            return result.content[0].text
    return None


def transient(error: Exception) -> bool:
    return any(token in str(error).lower() for token in TRANSIENT)


def scrub(name: str) -> str:
    return name.strip().lower().replace(" ", "_").replace("-", "_")


def settings() -> dict[str, str]:
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_BENCH_SITE") or os.environ.get("FRAPPE_SITE", "")
    hostname = urlparse(url).hostname or ""
    if os.environ.get("FRAPPE_ALLOW_BENCH_TEST") != "YES":
        raise SystemExit("Set FRAPPE_ALLOW_BENCH_TEST=YES to opt into running Bench commands")
    if hostname not in {"localhost", "127.0.0.1", "::1"} or not site.endswith(".localhost"):
        raise SystemExit("The Bench flow is restricted to loopback .localhost sites")
    required = {
        "bench": "FRAPPE_BENCH_PATH",
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
    return values


class Flow:
    def __init__(self, session: ClientSession, config: dict[str, str]):
        self.session = session
        self.app = config["app"]
        self.module = config["module"]
        self.suffix = uuid.uuid4().hex[:8]
        self.doctype = f"MCP Bench {self.suffix}"
        self.slug = scrub(self.doctype)
        self.folder = (
            Path(config["apps_path"]) / self.app / self.app / scrub(self.module) / "doctype"
            / self.slug
        )
        self.steps: dict[str, object] = {}
        self.details: dict[str, object] = {}

    async def call(self, tool: str, arguments: dict):
        return value(await self.session.call_tool(tool, arguments))

    async def read(self, tool: str, arguments: dict, retries: int = 10):
        for attempt in range(retries + 1):
            try:
                return await self.call(tool, arguments)
            except RuntimeError as error:
                if not transient(error) or attempt == retries:
                    raise
                await asyncio.sleep(2)
        raise AssertionError("unreachable")

    async def bench(self, command: str, **arguments) -> dict:
        args = {"command": command, **arguments}
        preview = await self.read("run_bench", args)
        if not preview.get("requires_confirmation") or not preview.get("preview_id"):
            raise AssertionError(f"run_bench {command} did not return a preview")
        result = await self.call(
            "run_bench", {**args, "confirm": True, "expected_preview": preview["preview_id"]}
        )
        self.details[command] = {
            "exit_code": result.get("exit_code"),
            "ok": result.get("ok"),
            "timed_out": result.get("timed_out"),
            "seconds": result.get("seconds"),
            "server_ready": result.get("server_ready"),
            **({"tests": result["tests"]} if "tests" in result else {}),
        }
        return result

    async def verdict(self) -> str | None:
        drift = await self.read("list_app_drift", {"app": self.app, "module": self.module,
                                                   "limit": 200})
        for item in drift.get("items", []):
            if item.get("doctype") == self.doctype:
                return item.get("verdict")
        return None

    def write_files(self) -> None:
        self.folder.mkdir(parents=True)
        now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        doc = {
            "doctype": "DocType",
            "name": self.doctype,
            "module": self.module,
            "custom": 0,
            "autoname": "hash",
            "creation": now,
            "modified": now,
            "fields": [{"fieldname": "title", "fieldtype": "Data", "label": "Title"}],
            "permissions": [
                {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}
            ],
        }
        classname = self.doctype.replace(" ", "")
        (self.folder / f"{self.slug}.json").write_text(json.dumps(doc, indent=1) + "\n")
        (self.folder / "__init__.py").write_text("")
        (self.folder / f"{self.slug}.py").write_text(
            "from frappe.model.document import Document\n\n\n"
            f"class {classname}(Document):\n    pass\n"
        )
        (self.folder / f"test_{self.slug}.py").write_text(
            TEST_FILE.format(classname=classname, doctype=self.doctype)
        )

    async def step(self, name: str, action) -> None:
        try:
            self.steps[name] = await action()
        except Exception as error:  # noqa: BLE001 - recorded as evidence
            self.steps[name] = "fail"
            self.details[f"{name}_error"] = type(error).__name__
            traceback.print_exc(file=sys.stderr)

    async def missing_first(self) -> str:
        self.write_files()
        verdict = await self.verdict()
        self.details["verdict_before"] = verdict
        return "pass" if verdict == "missing_on_site" else "fail"

    async def reload_doc(self) -> str:
        result = await self.bench(
            "reload-doc", app=self.app, module=self.module, doctype=self.doctype
        )
        verdict = await self.verdict()
        self.details["verdict_after"] = verdict
        return "pass" if result.get("ok") and verdict == "in_sync" else "fail"

    async def run_tests(self) -> str:
        try:
            result = await self.bench("run-tests", app=self.app, doctype=self.doctype)
        except RuntimeError as error:
            if "allow_tests" in str(error):
                return "skipped: the site has no allow_tests"
            raise
        return "pass" if result.get("ok") else "fail"

    async def clear_cache(self) -> str:
        return "pass" if (await self.bench("clear-cache")).get("ok") else "fail"

    async def cleanup(self) -> bool:
        removed = False
        for _ in range(3):
            try:
                preview = await self.read("delete_doctype", {"doctype": self.doctype})
                if preview.get("requires_confirmation"):
                    await self.call("delete_doctype", {
                        "doctype": self.doctype, "confirm": True,
                        "expected_modified": preview.get("modified"),
                    })
                removed = True
                break
            except RuntimeError as error:
                if "not found" in str(error).lower() or "does not exist" in str(error).lower():
                    removed = True
                    break
                if not transient(error):
                    traceback.print_exc(file=sys.stderr)
                    break
                await asyncio.sleep(2)
        if self.folder.exists():
            shutil.rmtree(self.folder)
        return removed and not self.folder.exists()


async def run() -> dict[str, object]:
    config = settings()
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(ROOT),
    )
    from frappe_mcp import __version__

    evidence: dict[str, object] = {
        "runner": "bench_flow",
        "mcp_version": __version__,
        "date": dt.date.today().isoformat(),
    }
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name for tool in (await session.list_tools()).tools}
            if "run_bench" not in tools:
                raise SystemExit("run_bench is not listed: check FRAPPE_BENCH_PATH")
            flow = Flow(session, config)
            info = await flow.read("get_site_info", {})
            evidence["versions"] = {
                row.get("app"): row.get("version") for row in info.get("apps", [])
                if row.get("app") in ("frappe", "erpnext")
            }
            try:
                await flow.step("missing_on_site_before", flow.missing_first)
                await flow.step("reload_doc_syncs_it", flow.reload_doc)
                await flow.step("run_tests", flow.run_tests)
                await flow.step("clear_cache", flow.clear_cache)
            finally:
                cleaned = await flow.cleanup()
            evidence["steps"] = flow.steps
            evidence["details"] = flow.details
            evidence["cleanup"] = {"doctype_and_folder_removed": cleaned}
            evidence["passed"] = cleaned and all(
                result == "pass" or str(result).startswith("skipped")
                for result in flow.steps.values()
            )
            evidence["secrets_paths_or_output_recorded"] = False
    return evidence


if __name__ == "__main__":
    result = asyncio.run(run())
    print(json.dumps(result, indent=2))
    sys.exit(0 if result.get("passed") else 1)
