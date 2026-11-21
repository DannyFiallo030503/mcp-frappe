"""Run the developer-task evaluations: Claude Code, headless, against a disposable site.

Each task in ``evals/tasks/*.yaml`` has a prompt and a deterministic check. For each task and
configuration the runner:

1. resets the state with the task's ``cleanup`` steps, then runs its ``setup`` steps, through
   this server over MCP stdio (the "harness" session, all toolsets, no metadata cache);
2. runs ``claude -p`` with one configuration: ``dev`` (this server plus the frappe-development
   skill), ``native`` (Frappe's built-in MCP server), or ``both``;
3. runs the task's ``check`` steps through the harness session (the server's read tools and
   file comparisons; a read-only question is checked against what the harness reads itself);
4. runs ``cleanup`` again and restores the test app's checkout with Git.

It records pass or fail, tool calls, tokens and cost (as the CLI reports them), and wall time,
and prints JSON with versions only: no URL, host name, site name, or credential.

It writes to the site and to the test app, and every ``claude -p`` run costs API usage, so it
refuses to start unless ``FRAPPE_ALLOW_EVALS=YES``, the site is loopback or ``.localhost``, and
the test app's checkout is clean. ``--dry-run`` prints the plan and touches nothing.

    FRAPPE_ALLOW_EVALS=YES FRAPPE_TEST_APP=<app> FRAPPE_TEST_MODULE=<module> \\
        uv run python evals/run.py --config dev --config native --model <model>
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = Path(__file__).resolve().parent / "tasks"
SKILL_DIR = ROOT / "plugins" / "frappe-dev" / "skills" / "frappe-development"
CONFIGS = ("dev", "native", "both")
NEEDS = frozenset({"bench", "erpnext"})
STEP_KINDS = frozenset(
    {"call", "write", "write_file", "append_file", "replace_in_file", "delete_matching",
     "file", "answer"}
)
CHECK_KINDS = frozenset({"call", "file", "answer"})
OPERATORS = frozenset(
    {"equals", "not_equals", "contains", "not_contains", "count", "in", "adjacent"}
)
TASK_KEYS = frozenset(
    {"id", "title", "prompt", "needs", "setup", "check", "cleanup", "timeout"}
)
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
PROTECTED_APPS = frozenset({"frappe", "erpnext"})
# Built-in tools the agent gets. File reads let it look at the app checkout, as a developer's
# Claude Code would; nothing that runs commands or writes files, so every change goes through
# an MCP server and the comparison stays about the servers.
DEFAULT_TOOLS = ("Read", "Glob", "Grep", "Skill", "ToolSearch")
SERVER_NAMES = {"dev": ("frappe-dev",), "native": ("frappe",), "both": ("frappe", "frappe-dev")}


# --- Tasks ----------------------------------------------------------------------------------


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    needs: list[str] = field(default_factory=list)
    setup: list[dict[str, Any]] = field(default_factory=list)
    check: list[dict[str, Any]] = field(default_factory=list)
    cleanup: list[dict[str, Any]] = field(default_factory=list)
    timeout: int = 900


def _step_kind(step: Any, where: str, allowed: frozenset[str]) -> str:
    if not isinstance(step, dict):
        raise ValueError(f"{where}: a step must be a mapping")
    kinds = [key for key in step if key in STEP_KINDS]
    if len(kinds) != 1 or kinds[0] not in allowed:
        raise ValueError(f"{where}: needs exactly one of {', '.join(sorted(allowed))}")
    kind = kinds[0]
    extra = set(step) - {kind, "args", "expect", "ignore_errors", "content", "old", "new",
                         "filters", "contains", "exists"}
    if extra:
        raise ValueError(f"{where}: unknown keys {', '.join(sorted(extra))}")
    for index, expectation in enumerate(step.get("expect") or []):
        _check_expectation(expectation, f"{where}.expect[{index}]")
    return kind


def _check_expectation(expectation: Any, where: str) -> None:
    if not isinstance(expectation, dict) or not isinstance(expectation.get("path"), str):
        raise ValueError(f"{where}: needs a `path`")
    ops = set(expectation) - {"path"}
    if len(ops) != 1 or not ops <= OPERATORS:
        raise ValueError(f"{where}: needs exactly one of {', '.join(sorted(OPERATORS))}")


def load_task(path: Path) -> Task:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: not a mapping")
    unknown = set(data) - TASK_KEYS
    if unknown:
        raise ValueError(f"{path.name}: unknown keys {', '.join(sorted(unknown))}")
    if data.get("id") != path.stem:
        raise ValueError(f"{path.name}: id must be {path.stem!r}")
    for key in ("title", "prompt"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"{path.name}: {key} is required")
    needs = data.get("needs") or []
    if not isinstance(needs, list) or not set(needs) <= NEEDS:
        raise ValueError(f"{path.name}: needs may list only {', '.join(sorted(NEEDS))}")
    if not data.get("check"):
        raise ValueError(f"{path.name}: check is required")
    for section, allowed in (("setup", STEP_KINDS - {"answer"}), ("check", CHECK_KINDS),
                             ("cleanup", STEP_KINDS - {"answer", "file"})):
        steps = data.get(section) or []
        if not isinstance(steps, list):
            raise ValueError(f"{path.name}: {section} must be a list")
        for index, step in enumerate(steps):
            _step_kind(step, f"{path.name}: {section}[{index}]", allowed)
    timeout = data.get("timeout", 900)
    if not isinstance(timeout, int) or not 60 <= timeout <= 3600:
        raise ValueError(f"{path.name}: timeout must be 60-3600 seconds")
    return Task(
        id=data["id"], title=data["title"], prompt=data["prompt"], needs=needs,
        setup=data.get("setup") or [], check=data["check"], cleanup=data.get("cleanup") or [],
        timeout=timeout,
    )


def load_tasks(directory: Path = TASKS_DIR, only: list[str] | None = None) -> list[Task]:
    tasks = [load_task(path) for path in sorted(directory.glob("*.yaml"))]
    if only:
        missing = sorted(set(only) - {task.id for task in tasks})
        if missing:
            raise ValueError(f"Unknown task: {', '.join(missing)}")
        tasks = [task for task in tasks if task.id in only]
    return tasks


def substitute(value: Any, names: dict[str, str]) -> Any:
    """Fill ``${name}`` placeholders in every string of a task value."""
    if isinstance(value, str):
        return Template(value).substitute(names)
    if isinstance(value, list):
        return [substitute(item, names) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, names) for key, item in value.items()}
    return value


# --- Expectations ---------------------------------------------------------------------------

_SEGMENT = re.compile(r"^(?P<key>[^\[\]]*)(?:\[(?P<field>[^=\]]+)=(?P<value>[^\]]*)\])?$")
_MISSING = object()


def select(data: Any, path: str) -> Any:
    """Read a value by a dotted path.

    ``fields[fieldname=title].label`` picks the first list item whose ``fieldname`` is ``title``;
    a key applied to a list maps over it (``fields.fieldname`` lists every fieldname). A missing
    key gives ``_MISSING``.
    """
    current = data
    for segment in path.split(".") if path else []:
        match = _SEGMENT.match(segment)
        if not match:
            raise ValueError(f"Bad path segment {segment!r}")
        key = match["key"]
        if key:
            if isinstance(current, list):
                current = [item.get(key, _MISSING) for item in current if isinstance(item, dict)]
                current = [item for item in current if item is not _MISSING]
            elif isinstance(current, dict):
                current = current.get(key, _MISSING)
            else:
                return _MISSING
            if current is _MISSING:
                return _MISSING
        if match["field"]:
            if not isinstance(current, list):
                return _MISSING
            wanted = match["value"]
            current = next(
                (
                    item
                    for item in current
                    if isinstance(item, dict) and str(item.get(match["field"])) == wanted
                ),
                _MISSING,
            )
            if current is _MISSING:
                return _MISSING
    return current


def _same(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return float(actual) == float(expected)
        except (TypeError, ValueError):
            return False
    if isinstance(expected, bool):
        return bool(actual) == expected
    return actual == expected


def evaluate(data: Any, expectation: dict[str, Any]) -> str | None:
    """Return None when the expectation holds, else a one-line reason."""
    path = expectation["path"]
    op, expected = next((key, value) for key, value in expectation.items() if key != "path")
    actual = select(data, path)
    shown = "missing" if actual is _MISSING else json.dumps(actual, default=str)[:200]
    if op == "not_contains" and actual is _MISSING:
        return None
    if actual is _MISSING:
        return f"{path}: missing (expected {op} {expected!r})"
    ok = False
    if op == "equals":
        ok = _same(actual, expected)
    elif op == "not_equals":
        ok = not _same(actual, expected)
    elif op in ("contains", "not_contains"):
        if isinstance(actual, str):
            found = str(expected) in actual
        elif isinstance(actual, list):
            found = any(_same(item, expected) for item in actual)
        else:
            found = False
        ok = found if op == "contains" else not found
    elif op == "count":
        ok = isinstance(actual, (list, dict)) and len(actual) == expected
    elif op == "in":
        ok = any(_same(actual, item) for item in expected)
    elif op == "adjacent":
        first, second = expected
        ok = (
            isinstance(actual, list)
            and first in actual
            and second in actual
            and actual.index(second) == actual.index(first) + 1
        )
    return None if ok else f"{path}: {shown} (expected {op} {expected!r})"


# --- Safety checks --------------------------------------------------------------------------


@dataclass
class Settings:
    app: str
    module: str
    apps_path: Path
    native_url: str | None
    bench: bool


def _local_host(hostname: str) -> bool:
    return hostname in LOOPBACK or hostname.endswith(".localhost")


def scrub(name: str) -> str:
    """Frappe's ``scrub``: lower case, spaces and hyphens to underscores."""
    return name.replace(" ", "_").replace("-", "_").lower()


def native_url(environ: dict[str, str]) -> str | None:
    """The built-in MCP endpoint: FRAPPE_NATIVE_MCP_URL, or built from the site settings."""
    explicit = (environ.get("FRAPPE_NATIVE_MCP_URL") or "").strip()
    if explicit:
        return explicit
    base = urlparse(environ.get("FRAPPE_URL", ""))
    if not base.scheme or not base.hostname:
        return None
    host = (environ.get("FRAPPE_SITE") or "").strip() or base.hostname
    port = f":{base.port}" if base.port else ""
    return f"{base.scheme}://{host}{port}/api/v2/mcp"


def check_settings(environ: dict[str, str], configs: list[str]) -> Settings:
    """Refuse anything but an opted-in, local, disposable target."""
    if environ.get("FRAPPE_ALLOW_EVALS") != "YES":
        raise SystemExit("Set FRAPPE_ALLOW_EVALS=YES to opt into the evaluation runs")
    url = environ.get("FRAPPE_URL", "")
    if not _local_host(urlparse(url).hostname or ""):
        raise SystemExit("Evaluations run only against a loopback or .localhost FRAPPE_URL")
    site = (environ.get("FRAPPE_SITE") or "").strip()
    if site and not site.endswith(".localhost"):
        raise SystemExit("FRAPPE_SITE must be a .localhost site")
    app = (environ.get("FRAPPE_TEST_APP") or "").strip()
    module = (environ.get("FRAPPE_TEST_MODULE") or "").strip()
    if not app or not module:
        raise SystemExit("Set FRAPPE_TEST_APP and FRAPPE_TEST_MODULE to a disposable test app")
    if app in PROTECTED_APPS:
        raise SystemExit("FRAPPE_TEST_APP must be a disposable test app, not frappe or erpnext")
    apps_value = (environ.get("FRAPPE_APPS_PATH") or "").strip()
    if not apps_value or not (Path(apps_value) / app).is_dir():
        raise SystemExit("FRAPPE_APPS_PATH must be the Bench apps folder holding the test app")
    if not (environ.get("FRAPPE_API_KEY") and environ.get("FRAPPE_API_SECRET")):
        raise SystemExit("Set FRAPPE_API_KEY and FRAPPE_API_SECRET (the native server needs them)")
    endpoint = native_url(environ) if {"native", "both"} & set(configs) else None
    if endpoint is not None and not _local_host(urlparse(endpoint).hostname or ""):
        raise SystemExit("The native MCP URL must be loopback or .localhost too")
    return Settings(
        app=app,
        module=module,
        apps_path=Path(apps_value).resolve(),
        native_url=endpoint,
        bench=bool((environ.get("FRAPPE_BENCH_PATH") or "").strip()),
    )


def app_file(settings: Settings, relative: str) -> Path:
    """A path inside the test app's checkout; refuses anything that leaves it."""
    root = (settings.apps_path / settings.app).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"{relative!r} leaves the test app")
    return target


def git_status(repo: Path) -> list[str]:
    output = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
        check=True, capture_output=True, text=True,
    ).stdout
    return [line for line in output.splitlines() if line.strip()]


def git_restore(repo: Path) -> list[str]:
    """Put the test app back to its last commit; return what had changed."""
    changed = git_status(repo)
    if changed:
        subprocess.run(["git", "-C", str(repo), "checkout", "--", "."], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(repo), "clean", "-fdq"], check=True,
                       capture_output=True)
    return changed


# --- The agent run --------------------------------------------------------------------------


# Settings the agent's frappe-dev server gets; everything else (the eval opt-ins, toolset
# overrides from the caller's shell) stays out.
FORWARDED = (
    "FRAPPE_URL", "FRAPPE_SITE", "FRAPPE_API_KEY", "FRAPPE_API_SECRET", "FRAPPE_APPS_PATH",
    "FRAPPE_BENCH_PATH", "FRAPPE_BENCH_SITE", "FRAPPE_BENCH_COMMAND", "FRAPPE_BENCH_TIMEOUT",
    "FRAPPE_TIMEOUT", "FRAPPE_SITE_LABEL",
)


def mcp_config(config: str, settings: Settings, environ: dict[str, str]) -> dict[str, Any]:
    """The --mcp-config for a configuration. It holds the API key, so the runner writes it to a
    0600 file in the run's temporary folder and deletes it after the run."""
    servers: dict[str, Any] = {}
    if config in ("native", "both"):
        servers["frappe"] = {
            "type": "http",
            "url": settings.native_url,
            "headers": {
                "Authorization": (
                    f"token {environ['FRAPPE_API_KEY']}:{environ['FRAPPE_API_SECRET']}"
                )
            },
        }
    if config in ("dev", "both"):
        env = {key: environ[key] for key in FORWARDED if environ.get(key)}
        env["FRAPPE_MCP_JOURNAL_RECORDS"] = "YES"
        if config == "both":
            # As docs/native-frappe-mcp.md recommends: record writes go to the native server.
            env["FRAPPE_MCP_TOOLSETS"] = "read,customize,developer" + (
                ",bench" if settings.bench else ""
            )
        servers["frappe-dev"] = {
            "command": sys.executable,
            "args": ["-m", "frappe_mcp.standalone"],
            "env": env,
        }
    return {"mcpServers": servers}


def agent_env(environ: dict[str, str]) -> dict[str, str]:
    """Claude Code's own environment: the caller's, without any FRAPPE_* setting."""
    return {key: value for key, value in environ.items() if not key.startswith("FRAPPE_")}


def claude_argv(
    claude: str,
    prompt: str,
    config: str,
    config_file: Path,
    settings: Settings,
    model: str | None,
    budget: float | None,
    tools: tuple[str, ...],
) -> list[str]:
    allowed = [f"mcp__{name}" for name in SERVER_NAMES[config]] + list(tools)
    argv = [
        claude, "-p", prompt,
        "--output-format", "stream-json", "--verbose",
        "--strict-mcp-config", "--mcp-config", str(config_file),
        "--setting-sources", "project",
        "--permission-mode", "dontAsk",
        "--tools", ",".join(tools),
        "--allowedTools", *allowed,
        "--add-dir", str(settings.apps_path / settings.app),
        "--no-session-persistence",
    ]
    if model:
        argv += ["--model", model]
    if budget:
        argv += ["--max-budget-usd", f"{budget:g}"]
    return argv


def parse_stream(lines: list[str]) -> dict[str, Any]:
    """Summarize ``claude -p --output-format stream-json`` output."""
    calls: dict[str, int] = {}
    summary: dict[str, Any] = {"tool_calls": calls, "answer": "", "mcp_servers": {}}
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            summary["model"] = event.get("model")
            summary["claude_code"] = event.get("claude_code_version")
            for server in event.get("mcp_servers") or []:
                if isinstance(server, dict):
                    summary["mcp_servers"][server.get("name")] = server.get("status")
        elif kind == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name"))
                    calls[name] = calls.get(name, 0) + 1
        elif kind == "result":
            summary["answer"] = event.get("result") or ""
            summary["is_error"] = bool(event.get("is_error"))
            summary["stop"] = event.get("subtype")
            summary["num_turns"] = event.get("num_turns")
            summary["cost_usd"] = event.get("total_cost_usd")
            usage = event.get("usage") or {}
            summary["tokens"] = {
                key: usage.get(key)
                for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                            "cache_read_input_tokens")
                if usage.get(key) is not None
            } or None
    summary["tool_call_count"] = sum(calls.values())
    summary["mcp_tool_calls"] = sum(n for name, n in calls.items() if name.startswith("mcp__"))
    return summary


def run_agent(
    argv: list[str], cwd: Path, timeout: int, env: dict[str, str]
) -> tuple[list[str], float, bool]:
    started = time.monotonic()
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              stdin=subprocess.DEVNULL, env=env)
        lines, timed_out = done.stdout.splitlines(), False
    except subprocess.TimeoutExpired as error:
        out = error.stdout or ""
        lines = (out.decode() if isinstance(out, bytes) else out).splitlines()
        timed_out = True
    return lines, round(time.monotonic() - started, 1), timed_out


def workdir(config: str, skill: bool) -> Path:
    """A fresh project folder for one run, with the skill when the configuration uses it."""
    folder = Path(tempfile.mkdtemp(prefix="mcp-frappe-eval-"))
    if skill and config in ("dev", "both"):
        shutil.copytree(SKILL_DIR, folder / ".claude" / "skills" / SKILL_DIR.name)
    return folder


# --- The harness session --------------------------------------------------------------------


class Harness:
    """Setup, checks, and cleanup through this server, with every toolset and no cache."""

    def __init__(self, session: Any, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def call(self, tool: str, args: dict[str, Any]) -> Any:
        result = await self.session.call_tool(tool, args)
        text = result.content[0].text if result.content else ""
        if result.isError:
            raise RuntimeError(f"{tool}: {text[:300]}")
        return json.loads(text) if text else None

    async def write(self, tool: str, args: dict[str, Any]) -> Any:
        preview = await self.call(tool, args)
        token: dict[str, Any] = {}
        if isinstance(preview, dict) and preview.get("preview_id"):
            token = {"expected_preview": preview["preview_id"]}
        elif isinstance(preview, dict) and preview.get("modified"):
            token = {"expected_modified": preview["modified"]}
        return await self.call(tool, {**args, **token, "confirm": True})

    async def step(self, step: dict[str, Any], answer: str = "") -> list[str]:
        """Run one step; return the failed expectations."""
        kind = _step_kind(step, "step", STEP_KINDS)
        args = step.get("args") or {}
        if kind == "call":
            data = await self.call(step["call"], args)
        elif kind == "write":
            data = await self.write(step["write"], args)
        elif kind == "delete_matching":
            doctype = step["delete_matching"]
            page = await self.call("list_documents", {
                "doctype": doctype, "filters": step.get("filters"), "fields": ["name"],
                "limit": 100,
            })
            for row in page["rows"]:
                await self.write("delete_document", {"doctype": doctype, "name": row["name"]})
            return []
        elif kind == "answer":
            return [
                f"answer does not mention {text!r}"
                for text in step["answer"]
                if text.lower() not in answer.lower()
            ]
        else:
            return self.file_step(kind, step)
        return [reason for e in step.get("expect") or [] if (reason := evaluate(data, e))]

    def file_step(self, kind: str, step: dict[str, Any]) -> list[str]:
        path = app_file(self.settings, step[kind])
        if kind == "write_file":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(step.get("content", ""), encoding="utf-8")
        elif kind == "append_file":
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            if text and not text.endswith("\n"):
                text += "\n"
            path.write_text(text + step.get("content", ""), encoding="utf-8")
        elif kind == "replace_in_file":
            text = path.read_text(encoding="utf-8")
            if step["old"] not in text:
                raise RuntimeError(f"{step[kind]}: text to replace not found")
            path.write_text(text.replace(step["old"], step["new"], 1), encoding="utf-8")
        elif kind == "file":
            if not path.exists():
                return [] if step.get("exists") is False else [f"{step[kind]}: missing"]
            if step.get("exists") is False:
                return [f"{step[kind]}: exists"]
            if "contains" in step and step["contains"] not in path.read_text(encoding="utf-8"):
                return [f"{step[kind]}: does not contain {step['contains']!r}"]
        return []

    async def run_steps(
        self, steps: list[dict[str, Any]], answer: str = "", tolerant: bool = False
    ) -> list[str]:
        problems: list[str] = []
        for index, step in enumerate(steps):
            try:
                problems += await self.step(step, answer)
            except Exception as error:  # every failure is a result here
                if not (tolerant or step.get("ignore_errors")):
                    problems.append(f"step {index}: {error}")
        return problems

    async def drift(self) -> set[str]:
        page = await self.call(
            "list_app_drift", {"app": self.settings.app, "limit": 200}
        )
        return {
            item["doctype"] for item in page.get("items", []) if item.get("verdict") != "in_sync"
        }


# --- Main -----------------------------------------------------------------------------------


def _version(argv: list[str]) -> str | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


async def run_task(
    harness: Harness, task: Task, config: str, args: argparse.Namespace, names: dict[str, str],
    environ: dict[str, str], baseline_drift: set[str],
) -> dict[str, Any]:
    settings = harness.settings
    names = {**names, "run": uuid.uuid4().hex[:8]}
    setup = substitute(task.setup, names)
    check = substitute(task.check, names)
    cleanup = substitute(task.cleanup, names)
    row: dict[str, Any] = {"task": task.id, "config": config}

    await harness.run_steps(cleanup, tolerant=True)
    git_restore(settings.apps_path / settings.app)
    problems = await harness.run_steps(setup)
    if problems:
        row.update(status="setup_failed", problems=problems)
    else:
        folder = workdir(config, not args.no_skill)
        config_file = folder / "mcp.json"
        config_file.touch(mode=0o600)
        config_file.write_text(json.dumps(mcp_config(config, settings, environ)))
        argv = claude_argv(
            args.claude, substitute(task.prompt, names).strip(), config, config_file, settings,
            args.model, args.max_budget_usd, tuple(args.tools.split(",")),
        )
        try:
            lines, seconds, timed_out = await asyncio.to_thread(
                run_agent, argv, folder, task.timeout, agent_env(environ)
            )
        finally:
            shutil.rmtree(folder, ignore_errors=True)
        summary = parse_stream(lines)
        answer = summary.pop("answer", "")
        failed_servers = sorted(
            name for name, status in summary["mcp_servers"].items() if status != "connected"
        )
        problems = await harness.run_steps(check, answer=answer)
        row.update(
            status="pass" if not problems else "fail",
            problems=problems,
            wall_seconds=seconds,
            timed_out=timed_out,
            **summary,
        )
        if failed_servers:
            row["status"] = "invalid"
            row["problems"] = [f"MCP server not connected: {', '.join(failed_servers)}"]
    await harness.run_steps(cleanup, tolerant=True)
    row["app_files_restored"] = len(git_restore(settings.apps_path / settings.app))
    leftovers = sorted(await harness.drift() - baseline_drift)
    if leftovers:
        row["leftover_drift"] = leftovers
    return row


async def main_async(args: argparse.Namespace) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    environ = dict(os.environ)
    configs: list[str] = list(args.config or CONFIGS)
    settings = check_settings(environ, configs)
    tasks = load_tasks(only=args.task)
    repo = settings.apps_path / settings.app
    if git_status(repo):
        raise SystemExit(f"Commit or stash the changes in {settings.app} first; runs reset it")
    names = {"app": settings.app, "module": settings.module, "module_dir": scrub(settings.module)}

    env = {**environ, "FRAPPE_META_TTL": "0"}
    for key in ("FRAPPE_MCP_TOOLSETS", "FRAPPE_MCP_READ_ONLY", "FRAPPE_MCP_JOURNAL_RECORDS"):
        env.pop(key, None)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "frappe_mcp.standalone"], env=env, cwd=str(ROOT)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        harness = Harness(session, settings)
        info = await harness.call("get_site_info", {})
        apps = {app.get("app"): app.get("version") for app in info.get("apps", [])}
        if settings.app not in apps:
            raise SystemExit("FRAPPE_TEST_APP is not installed on the site")
        baseline = await harness.drift()
        rows = []
        for task in tasks:
            missing = [need for need in task.needs if
                       (need == "bench" and not settings.bench)
                       or (need == "erpnext" and "erpnext" not in apps)]
            for config in configs:
                if missing:
                    rows.append({"task": task.id, "config": config, "status": "skipped",
                                 "problems": [f"needs {', '.join(missing)}"]})
                    continue
                print(f"{task.id} [{config}] ...", file=sys.stderr, flush=True)
                row = await run_task(harness, task, config, args, names, environ, baseline)
                print(f"{task.id} [{config}] {row['status']}", file=sys.stderr, flush=True)
                rows.append(row)
    commit = _version(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"])
    from frappe_mcp import __version__

    return {
        "date": dt.date.today().isoformat(),
        "mcp_frappe": __version__,
        "commit": commit,
        "claude_code": _version([args.claude, "--version"]),
        "model": args.model,
        "skill": not args.no_skill,
        "tools": args.tools,
        "site": "loopback .localhost development site; URL, site name, and credentials omitted",
        "apps": {name: apps.get(name) for name in ("frappe", "erpnext") if apps.get(name)},
        "results": rows,
    }


def plan(args: argparse.Namespace) -> dict[str, Any]:
    tasks = load_tasks(only=args.task)
    configs: list[str] = list(args.config or CONFIGS)
    return {
        "tasks": [{"id": task.id, "title": task.title, "needs": task.needs} for task in tasks],
        "configs": configs,
        "runs": len(tasks) * len(configs),
        "model": args.model,
        "tools": args.tools,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--config", action="append", choices=CONFIGS,
                        help="dev, native, or both; repeat for several (default: all three)")
    parser.add_argument("--task", action="append", help="a task id; repeat (default: all)")
    parser.add_argument("--model", help="passed to claude --model")
    parser.add_argument("--max-budget-usd", type=float, help="per run, passed to claude")
    parser.add_argument("--tools", default=",".join(DEFAULT_TOOLS),
                        help="built-in tools for the agent (comma-separated)")
    parser.add_argument("--no-skill", action="store_true",
                        help="run dev and both without the frappe-development skill")
    parser.add_argument("--claude", default="claude", help="the Claude Code executable")
    parser.add_argument("--out", type=Path, help="also write the JSON result to this file")
    parser.add_argument("--dry-run", action="store_true",
                        help="list the tasks and runs; touch nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = plan(args) if args.dry_run else asyncio.run(main_async(args))
    text = json.dumps(result, indent=2, default=str)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    rows = result.get("results", [])
    return 1 if any(row["status"] in ("fail", "setup_failed", "invalid") for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
