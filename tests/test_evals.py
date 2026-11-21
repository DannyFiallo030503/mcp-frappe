"""The developer-task evaluation runner in evals/run.py, without a site or Claude Code."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "evals" / "run.py"

# The sdist ships tests/ but not evals/.
pytestmark = pytest.mark.skipif(not RUNNER.is_file(), reason="evals/ not present")


@pytest.fixture(scope="module")
def run():
    spec = importlib.util.spec_from_file_location("evals_run", RUNNER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["evals_run"] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("evals_run", None)


NAMES = {"app": "lab_app", "module": "Lab", "module_dir": "lab", "run": "abcd1234"}


def _environ(tmp_path: Path, **overrides: str) -> dict[str, str]:
    (tmp_path / "lab_app").mkdir(exist_ok=True)
    environ = {
        "FRAPPE_ALLOW_EVALS": "YES",
        "FRAPPE_URL": "http://127.0.0.1:8000",
        "FRAPPE_SITE": "dev.localhost",
        "FRAPPE_TEST_APP": "lab_app",
        "FRAPPE_TEST_MODULE": "Lab",
        "FRAPPE_APPS_PATH": str(tmp_path),
        "FRAPPE_API_KEY": "key-value",
        "FRAPPE_API_SECRET": "secret-value",
    }
    environ.update(overrides)
    return {key: value for key, value in environ.items() if value is not None}


# Tasks


def test_every_task_loads_and_fills_its_placeholders(run) -> None:
    tasks = run.load_tasks()
    assert 10 <= len(tasks) <= 15
    assert len({task.id for task in tasks}) == len(tasks)
    for task in tasks:
        for part in (task.prompt, task.setup, task.check, task.cleanup):
            filled = run.substitute(part, NAMES)
            assert "${" not in json.dumps(filled), task.id
        assert task.check, task.id


def test_tasks_never_check_the_agent_claiming_success(run) -> None:
    for task in run.load_tasks():
        kinds = [next(k for k in step if k in run.STEP_KINDS) for step in task.check]
        # A task may check an answer only next to, or instead of, a site read on a question.
        assert any(kind in ("call", "file") for kind in kinds) or "?" in task.prompt, task.id


def test_task_loader_refuses_unknown_steps_and_keys(run, tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("id: bad\ntitle: t\nprompt: p\ncheck:\n  - shell: rm -rf /\n")
    with pytest.raises(ValueError, match="needs exactly one of"):
        run.load_task(path)
    path.write_text("id: bad\ntitle: t\nprompt: p\nmodel: x\ncheck:\n  - answer: [a]\n")
    with pytest.raises(ValueError, match="unknown keys"):
        run.load_task(path)
    path.write_text("id: other\ntitle: t\nprompt: p\ncheck:\n  - answer: [a]\n")
    with pytest.raises(ValueError, match="id must be"):
        run.load_task(path)
    path.write_text(
        "id: bad\ntitle: t\nprompt: p\ncheck:\n  - call: x\n    expect: [{path: a, like: b}]\n"
    )
    with pytest.raises(ValueError, match="needs exactly one of"):
        run.load_task(path)


def test_unknown_task_ids_are_refused(run) -> None:
    with pytest.raises(ValueError, match="Unknown task"):
        run.load_tasks(only=["no-such-task"])


# Expectations


def test_select_reads_paths_filters_and_projections(run) -> None:
    data = {
        "fields": [{"fieldname": "title", "label": "Title"}, {"fieldname": "qty", "reqd": 1}],
        "child_tables": {"Lab Reading": [{"fieldname": "value"}]},
    }
    assert run.select(data, "fields[fieldname=qty].reqd") == 1
    assert run.select(data, "fields.fieldname") == ["title", "qty"]
    assert run.select(data, "child_tables.Lab Reading.fieldname") == ["value"]
    assert run.select(data, "fields[fieldname=nope].label") is run._MISSING
    assert run.select(data, "missing.key") is run._MISSING


def test_evaluate_reports_why_an_expectation_fails(run) -> None:
    data = {"verdict": "app_ahead", "count": 3, "names": ["a", "b", "c"], "flag": 1}
    assert run.evaluate(data, {"path": "verdict", "equals": "app_ahead"}) is None
    assert "expected equals" in run.evaluate(data, {"path": "verdict", "equals": "in_sync"})
    assert run.evaluate(data, {"path": "count", "equals": 3}) is None
    assert run.evaluate(data, {"path": "flag", "equals": True}) is None
    assert run.evaluate(data, {"path": "names", "count": 3}) is None
    assert run.evaluate(data, {"path": "names", "contains": "b"}) is None
    assert run.evaluate(data, {"path": "names", "not_contains": "z"}) is None
    assert run.evaluate(data, {"path": "names", "adjacent": ["a", "b"]}) is None
    assert run.evaluate(data, {"path": "names", "adjacent": ["a", "c"]})
    assert run.evaluate(data, {"path": "verdict", "in": ["in_sync", "app_ahead"]}) is None
    assert run.evaluate(data, {"path": "absent", "not_contains": "x"}) is None
    assert "missing" in run.evaluate(data, {"path": "absent", "equals": 1})


# Safety checks


def test_settings_accept_a_local_opted_in_target(run, tmp_path) -> None:
    settings = run.check_settings(_environ(tmp_path), ["dev", "native"])
    assert settings.app == "lab_app" and settings.module == "Lab"
    assert settings.native_url == "http://dev.localhost:8000/api/v2/mcp"
    assert settings.bench is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"FRAPPE_ALLOW_EVALS": None}, "FRAPPE_ALLOW_EVALS=YES"),
        ({"FRAPPE_ALLOW_EVALS": "yes"}, "FRAPPE_ALLOW_EVALS=YES"),
        ({"FRAPPE_URL": "https://erp.example.com"}, "loopback"),
        ({"FRAPPE_URL": "http://10.0.0.5:8000"}, "loopback"),
        ({"FRAPPE_SITE": "erp.example.com"}, ".localhost site"),
        ({"FRAPPE_TEST_APP": "erpnext"}, "disposable test app"),
        ({"FRAPPE_TEST_MODULE": None}, "FRAPPE_TEST_MODULE"),
        ({"FRAPPE_TEST_APP": "other_app"}, "FRAPPE_APPS_PATH"),
        ({"FRAPPE_API_SECRET": None}, "FRAPPE_API_KEY"),
        ({"FRAPPE_NATIVE_MCP_URL": "https://erp.example.com/api/v2/mcp"}, "native MCP URL"),
    ],
)
def test_settings_refuse_anything_but_a_disposable_local_site(
    run, tmp_path, overrides, message
) -> None:
    with pytest.raises(SystemExit, match=message):
        run.check_settings(_environ(tmp_path, **overrides), ["native"])


def test_app_files_stay_inside_the_test_app(run, tmp_path) -> None:
    settings = run.check_settings(_environ(tmp_path), ["dev"])
    assert run.app_file(settings, "lab_app/patches.txt").parent.name == "lab_app"
    with pytest.raises(ValueError, match="leaves the test app"):
        run.app_file(settings, "../other_app/hooks.py")


# The agent run


def test_server_configs_forward_only_connection_settings(run, tmp_path) -> None:
    environ = _environ(
        tmp_path, FRAPPE_BENCH_PATH=str(tmp_path), FRAPPE_MCP_TOOLSETS="read",
        FRAPPE_MCP_READ_ONLY="YES",
    )
    settings = run.check_settings(environ, ["both"])

    dev = run.mcp_config("dev", settings, environ)["mcpServers"]
    assert set(dev) == {"frappe-dev"}
    env = dev["frappe-dev"]["env"]
    assert env["FRAPPE_URL"] == "http://127.0.0.1:8000" and env["FRAPPE_API_KEY"] == "key-value"
    assert env["FRAPPE_MCP_JOURNAL_RECORDS"] == "YES"
    for key in ("FRAPPE_MCP_TOOLSETS", "FRAPPE_MCP_READ_ONLY", "FRAPPE_ALLOW_EVALS",
                "FRAPPE_TEST_APP"):
        assert key not in env
    agent = run.agent_env({**environ, "PATH": "/usr/bin"})
    assert agent == {"PATH": "/usr/bin"}

    both = run.mcp_config("both", settings, environ)["mcpServers"]
    assert set(both) == {"frappe", "frappe-dev"}
    assert both["frappe-dev"]["env"]["FRAPPE_MCP_TOOLSETS"] == "read,customize,developer,bench"
    assert both["frappe"]["headers"]["Authorization"] == "token key-value:secret-value"
    assert both["frappe"]["url"] == "http://dev.localhost:8000/api/v2/mcp"


def test_claude_argv_limits_servers_and_tools_and_carries_no_secret(run, tmp_path) -> None:
    environ = _environ(tmp_path)
    settings = run.check_settings(environ, ["dev"])
    argv = run.claude_argv(
        "claude", "Do it", "dev", tmp_path / "mcp.json", settings, "some-model", 2.5,
        run.DEFAULT_TOOLS,
    )
    joined = " ".join(argv)
    assert argv[:3] == ["claude", "-p", "Do it"]
    for flag in ("--strict-mcp-config", "--no-session-persistence", "--verbose"):
        assert flag in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep,Skill,ToolSearch"
    assert "mcp__frappe-dev" in argv and "mcp__frappe" not in argv
    assert argv[argv.index("--max-budget-usd") + 1] == "2.5"
    assert "key-value" not in joined and "secret-value" not in joined


def test_parse_stream_counts_tool_calls_and_reads_the_result(run) -> None:
    events = [
        {"type": "system", "subtype": "init", "model": "m", "claude_code_version": "2.1.0",
         "mcp_servers": [{"name": "frappe-dev", "status": "connected"}]},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Looking"},
            {"type": "tool_use", "name": "mcp__frappe-dev__get_site_info", "input": {}},
            {"type": "tool_use", "name": "ToolSearch", "input": {}},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "mcp__frappe-dev__get_site_info", "input": {}},
        ]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Done: X",
         "num_turns": 3, "total_cost_usd": 0.12,
         "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}},
    ]
    lines = [json.dumps(event) for event in events] + ["not json", "[]"]
    summary = run.parse_stream(lines)
    assert summary["tool_calls"] == {"mcp__frappe-dev__get_site_info": 2, "ToolSearch": 1}
    assert summary["tool_call_count"] == 3 and summary["mcp_tool_calls"] == 2
    assert summary["answer"] == "Done: X" and summary["stop"] == "success"
    assert summary["tokens"] == {
        "input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100,
    }
    assert summary["mcp_servers"] == {"frappe-dev": "connected"}
    assert summary["model"] == "m" and summary["cost_usd"] == 0.12


def test_the_skill_is_copied_only_for_configurations_with_this_server(run) -> None:
    import shutil

    for config, expected in (("dev", True), ("both", True), ("native", False)):
        folder = run.workdir(config, skill=True)
        try:
            assert (folder / ".claude" / "skills" / "frappe-development" / "SKILL.md").is_file() \
                is expected
        finally:
            shutil.rmtree(folder)
    folder = run.workdir("dev", skill=False)
    try:
        assert not (folder / ".claude").exists()
    finally:
        shutil.rmtree(folder)


# The harness


class FakeSession:
    def __init__(self, answers: dict[tuple[str, bool], object]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool: str, args: dict):
        self.calls.append((tool, args))
        value = self.answers[(tool, bool(args.get("confirm")))]
        if isinstance(value, Exception):
            return SimpleNamespace(isError=True, content=[SimpleNamespace(text=str(value))])
        return SimpleNamespace(isError=False, content=[SimpleNamespace(text=json.dumps(value))])


def test_harness_confirms_writes_with_the_token_the_preview_returned(run, tmp_path) -> None:
    settings = run.check_settings(_environ(tmp_path), ["dev"])
    session = FakeSession({
        ("rename", False): {"preview_id": "p1"},
        ("rename", True): {"ok": True},
        ("delete_doctype", False): {"modified": "2026-01-01 00:00:00"},
        ("delete_doctype", True): {"deleted": True},
        ("create_doctype", False): {"name": "X"},
        ("create_doctype", True): {"name": "X"},
    })
    harness = run.Harness(session, settings)
    asyncio.run(harness.write("rename", {"kind": "field"}))
    asyncio.run(harness.write("delete_doctype", {"doctype": "X"}))
    asyncio.run(harness.write("create_doctype", {"name": "X"}))
    confirmed = [args for tool, args in session.calls if args.get("confirm")]
    assert confirmed[0]["expected_preview"] == "p1"
    assert confirmed[1]["expected_modified"] == "2026-01-01 00:00:00"
    assert "expected_preview" not in confirmed[2] and "expected_modified" not in confirmed[2]


def test_harness_steps_check_expectations_files_and_answers(run, tmp_path) -> None:
    settings = run.check_settings(_environ(tmp_path), ["dev"])
    session = FakeSession({
        ("diff_doctype", False): {"verdict": "app_ahead"},
        ("list_documents", False): {"rows": [{"name": "A"}]},
        ("delete_document", False): {"modified": "m"},
        ("delete_document", True): {"deleted": True},
        ("get_doctype_schema", False): RuntimeError("DocType not found"),
    })
    harness = run.Harness(session, settings)
    steps = [
        {"write_file": "lab_app/patches.txt", "content": "lab_app.patches.one"},
        {"append_file": "lab_app/patches.txt", "content": "lab_app.patches.two\n"},
        {"replace_in_file": "lab_app/patches.txt", "old": "one", "new": "uno"},
        {"file": "lab_app/patches.txt", "contains": "patches.uno\nlab_app.patches.two"},
        {"file": "lab_app/missing.json", "exists": False},
        {"call": "diff_doctype", "args": {"doctype": "X"},
         "expect": [{"path": "verdict", "equals": "in_sync"}]},
        {"delete_matching": "ToDo", "filters": [["description", "like", "%x%"]]},
        {"answer": ["lab_app.patches.TWO", "not mentioned"]},
        {"call": "get_doctype_schema", "args": {"doctype": "Gone"}},
        {"call": "get_doctype_schema", "args": {"doctype": "Gone"}, "ignore_errors": True},
    ]
    problems = asyncio.run(harness.run_steps(steps, answer="Pending: lab_app.patches.two"))
    assert len(problems) == 3, problems
    assert problems[0].startswith("verdict: \"app_ahead\"")
    assert problems[1] == "answer does not mention 'not mentioned'"
    assert "DocType not found" in problems[2]
    assert ("delete_document", {"doctype": "ToDo", "name": "A", "expected_modified": "m",
                                "confirm": True}) in session.calls
    assert asyncio.run(harness.run_steps(steps[-2:], tolerant=True)) == []


def test_dry_run_prints_the_plan_and_touches_nothing(run, capsys, monkeypatch) -> None:
    monkeypatch.delenv("FRAPPE_ALLOW_EVALS", raising=False)
    assert run.main(["--dry-run", "--config", "dev", "--task", "pending-patches"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["runs"] == 1 and plan["configs"] == ["dev"]
    assert plan["tasks"][0]["id"] == "pending-patches"
