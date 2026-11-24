"""Verify run_bench reports a deliberately failing test on isolated Frappe v16."""

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

TEST_TEMPLATE = '''"""Temporary run_bench failure-reporting acceptance test."""

try:
    from frappe.tests import IntegrationTestCase as TestCase
except ImportError:
    from frappe.tests.utils import FrappeTestCase as TestCase


class Test{class_name}(TestCase):
    def test_intentional_failure(self):
        self.fail("MCP intentional failure {marker}")
'''


def value(result):
    if result.isError:
        detail = result.content[0].text if result.content else "MCP tool call failed"
        raise RuntimeError(detail[:500])
    if result.structuredContent:
        data = result.structuredContent
        return data.get("result", data)
    return json.loads(result.content[0].text) if result.content else None


async def call(session, tool, args, *, retry_transport=False):
    """Retry only read-only calls/previews after a Frappe worker restart."""
    attempts = 12 if retry_transport else 1
    for attempt in range(attempts):
        try:
            return value(await session.call_tool(tool, args))
        except RuntimeError as error:
            retryable = any(
                phrase in str(error).lower()
                for phrase in ("connection was closed", "cannot reach", "connection refused")
            )
            if not retryable or attempt + 1 == attempts:
                raise
            await asyncio.sleep(min(1 + attempt, 5))


async def bench(session, args):
    preview = await call(session, "run_bench", args, retry_transport=True)
    if not preview.get("requires_confirmation") or not preview.get("preview_id"):
        raise AssertionError(f"run_bench {args['command']} did not return a preview")
    return await call(
        session,
        "run_bench",
        {**args, "confirm": True, "expected_preview": preview["preview_id"]},
    )


async def run():
    url = os.environ.get("FRAPPE_URL", "")
    site = os.environ.get("FRAPPE_SITE", "")
    if os.environ.get("FRAPPE_ALLOW_BENCH_FAILURE_TEST") != "YES":
        raise RuntimeError("Set FRAPPE_ALLOW_BENCH_FAILURE_TEST=YES")
    if urlparse(url).hostname not in {"localhost", "127.0.0.1", "::1"} or not site.endswith(
        ".localhost"
    ):
        raise RuntimeError("This runner only changes an isolated loopback .localhost site")
    app = os.environ.get("FRAPPE_TEST_APP", "")
    module = os.environ.get("FRAPPE_TEST_MODULE", "")
    apps_path = os.environ.get("FRAPPE_APPS_PATH", "")
    bench_path = os.environ.get("FRAPPE_BENCH_PATH", "")
    if not app or app in {"frappe", "erpnext"} or not module or not apps_path or not bench_path:
        raise RuntimeError("Set FRAPPE_TEST_APP, FRAPPE_TEST_MODULE, FRAPPE_APPS_PATH, and Bench")
    if os.environ.get("FRAPPE_USERNAME", "Administrator") != "Administrator":
        raise RuntimeError("This acceptance runner requires the isolated site's Administrator")

    marker = uuid.uuid4().hex[:8]
    slug = "test_mcp_failure_reporting_" + marker
    test_module = f"{app}.{module.lower().replace(' ', '_')}.{slug}"
    class_name = "TestMcpFailureReporting" + marker.title()
    test_path = Path(apps_path) / app / app / module.lower().replace(" ", "_") / f"{slug}.py"
    if test_path.exists():
        raise RuntimeError("Refusing to overwrite an existing test file")
    report = {
        "date": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "frappe_version": None,
        "command": "run-tests",
        "deliberate_failure_marker": "MCP intentional failure",
        "bench_exit_code": None,
        "bench_tests_status": None,
        "bench_reported_failure": False,
        "cleanup_verified": False,
        "site_isolated": True,
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "frappe_mcp.standalone"],
        env=os.environ.copy(),
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    file_created = False
    try:
        test_path.write_text(
            TEST_TEMPLATE.format(class_name=class_name, marker=marker), encoding="utf-8"
        )
        file_created = True
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {item.name for item in (await session.list_tools()).tools}
                if "run_bench" not in tools:
                    raise RuntimeError("run_bench is not enabled")
                site_info = await call(session, "get_site_info", {}, retry_transport=True)
                if site_info.get("frappe_major_version") != 16:
                    raise RuntimeError(
                        "Refusing: this acceptance runner is pinned to isolated Frappe v16"
                    )
                report["frappe_version"] = next(
                    (
                        item.get("version")
                        for item in site_info.get("apps", [])
                        if item.get("app") == "frappe"
                    ),
                    None,
                )
                failed = await bench(
                    session, {"command": "run-tests", "app": app, "test_module": test_module}
                )
                report["bench_exit_code"] = failed.get("exit_code")
                report["bench_tests_status"] = (failed.get("tests") or {}).get("status")
                output = str(failed.get("output", ""))
                report["bench_reported_failure"] = (
                    failed.get("ok") is False
                    and failed.get("exit_code") not in (0, None)
                    and report["bench_tests_status"] == "FAILED"
                    and "MCP intentional failure" in output
                )
                if not report["bench_reported_failure"]:
                    raise AssertionError("run_bench did not surface the deliberate failing test")
    finally:
        if file_created:
            test_path.unlink(missing_ok=True)
        report["test_file_absent"] = not test_path.exists()
        report["cleanup_verified"] = report["test_file_absent"]
        if not report["cleanup_verified"]:
            raise AssertionError("Temporary test file remains")

    print(json.dumps(report, sort_keys=True, indent=2))


if __name__ == "__main__":
    asyncio.run(run())
