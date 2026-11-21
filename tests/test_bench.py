"""Opt-in Bench commands: FRAPPE_BENCH_PATH and the run_bench tool. No real Bench is run."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from frappe_mcp import standalone

APP = "lab_app"
MODULE = "Lab"
DOCTYPE = "Lab Sample"
SITE = "lab.localhost"
TEST_MODULE = "lab_app.lab.doctype.lab_sample.test_lab_sample"
SECRET = "api-secret-value-1234"


class FakeRun:
    """Stands in for `subprocess.run` and records every call."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict]] = []
        self.output = b"done\n"
        self.returncode: int | BaseException = 0
        self.effect = None

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.effect is not None:
            self.effect()
        if isinstance(self.returncode, BaseException):
            raise self.returncode
        return subprocess.CompletedProcess(argv, self.returncode, self.output, None)


@pytest.fixture
def bench(monkeypatch, tmp_path, fake_site):
    """A configured local Bench with a fake `subprocess.run`, drift, and pending patches."""
    monkeypatch.setattr(
        standalone.server._tool_manager, "_tools", dict(standalone.server._tool_manager._tools)
    )
    monkeypatch.setattr(standalone, "_TOOLSETS", copy.deepcopy(standalone._TOOLSETS))
    for name in ("FRAPPE_BENCH_COMMAND", "FRAPPE_BENCH_TIMEOUT", "FRAPPE_SITE",
                 "FRAPPE_MCP_TOOLSETS", "FRAPPE_MCP_READ_ONLY", "FRAPPE_MCP_METHODS",
                 "FRAPPE_APPS_PATH", "FRAPPE_API_KEY", "FRAPPE_PASSWORD", "CI"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "bench"
    (root / "sites" / SITE).mkdir(parents=True)
    (root / "sites" / SITE / "site_config.json").write_text(
        json.dumps({"db_name": "lab", "db_password": "db-password-value", "allow_tests": 1})
    )
    (root / "sites" / "common_site_config.json").write_text("{}")
    monkeypatch.setenv("FRAPPE_URL", "http://lab.localhost:8000")
    monkeypatch.setenv("FRAPPE_BENCH_PATH", str(root))
    monkeypatch.setenv("FRAPPE_BENCH_SITE", SITE)
    monkeypatch.setenv("FRAPPE_API_SECRET", SECRET)
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr(standalone.shutil, "which", lambda name: f"/usr/bin/{name}")
    runner = FakeRun()
    monkeypatch.setattr(standalone.subprocess, "run", runner)
    ready: list[bool] = []
    monkeypatch.setattr(
        standalone, "_wait_for_server_ready", lambda *args, **kwargs: ready.append(True)
    )
    state = {"verdict": "missing_on_site", "pending": [{"patch": "lab_app.patches.v1"}]}
    drift_calls: list[tuple] = []

    def drift(app, module=None, limit=50, offset=0):
        drift_calls.append((app, module))
        return {
            "app": app,
            "total": 1,
            "counts": {state["verdict"]: 1},
            "items": [{"doctype": DOCTYPE, "module": MODULE, "verdict": state["verdict"]}],
            "next_offset": None,
        }

    def patches(app):
        return {"app": app, "pending_count": len(state["pending"]), "pending": state["pending"]}

    monkeypatch.setattr(standalone, "list_app_drift", drift)
    monkeypatch.setattr(standalone, "list_pending_patches", patches)
    fake_site.add(
        "GET",
        standalone._resource("Module Def"),
        [{"name": MODULE, "app_name": APP, "custom": 0}],
    )
    fake_site.add(
        "GET", standalone._resource("DocType"), [{"name": DOCTYPE, "module": MODULE}]
    )
    standalone._bench_config.clear()
    standalone._bench_settings()
    yield {"root": root, "run": runner, "state": state, "ready": ready, "drift": drift_calls,
           "site": fake_site, "env": monkeypatch, "tmp": tmp_path}
    standalone._bench_config.clear()


def _apps(bench) -> Path:
    """Create the lab_app checkout with the Lab Sample DocType file; return the module folder."""
    apps = bench["tmp"] / "apps"
    folder = apps / APP / APP / "lab" / "doctype" / "lab_sample"
    folder.mkdir(parents=True)
    (folder / "lab_sample.json").write_text(json.dumps({"doctype": "DocType", "name": DOCTYPE}))
    bench["env"].setenv("FRAPPE_APPS_PATH", str(apps))
    return apps / APP / APP


def _confirm(**kwargs):
    preview = standalone.run_bench(**kwargs)
    return standalone.run_bench(**kwargs, confirm=True, expected_preview=preview["preview_id"])


# Configuration


def test_without_the_variable_there_is_no_bench_tool(bench) -> None:
    bench["env"].delenv("FRAPPE_BENCH_PATH")
    standalone.server.remove_tool("run_bench")
    standalone._TOOLSETS["bench"].clear()

    standalone._bench_settings()

    assert "run_bench" not in standalone.server._tool_manager._tools
    assert not standalone._TOOLSETS["bench"]
    assert standalone._bench_config == {}


def test_the_variable_registers_run_bench_in_the_bench_toolset(bench) -> None:
    tool = standalone.server._tool_manager._tools["run_bench"]

    assert standalone._TOOLSETS["bench"] == {"run_bench"}
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.destructiveHint is True


def test_the_default_tool_list_has_no_bench_tool() -> None:
    assert "run_bench" not in standalone.server._tool_manager._tools
    assert not standalone._TOOLSETS["bench"]


@pytest.mark.parametrize("url", ["https://erp.example.com", "http://10.0.0.5:8000", ""])
def test_a_remote_site_refuses_the_bench_tool(bench, url) -> None:
    bench["env"].setenv("FRAPPE_URL", url)

    with pytest.raises(RuntimeError, match="only with a local site"):
        standalone._bench_settings()
    with pytest.raises(ValueError, match="only with a local site"):
        standalone.run_bench("clear-cache")
    assert bench["run"].calls == []


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"FRAPPE_BENCH_PATH": "/nowhere/at/all"}, "FRAPPE_BENCH_PATH does not exist"),
        ({"FRAPPE_BENCH_SITE": ""}, "Set FRAPPE_BENCH_SITE"),
        ({"FRAPPE_BENCH_SITE": "-x"}, "not a valid site name"),
        ({"FRAPPE_BENCH_SITE": "../lab"}, "not a valid site name"),
        ({"FRAPPE_BENCH_SITE": "other.localhost"}, "has no sites/other.localhost/site_config"),
        ({"FRAPPE_BENCH_COMMAND": "bench migrate"}, "must be a JSON list"),
        ({"FRAPPE_BENCH_COMMAND": "[]"}, "must be a JSON list"),
        ({"FRAPPE_BENCH_COMMAND": '["docker", 1]'}, "must be a JSON list"),
        ({"FRAPPE_BENCH_COMMAND": '["docker", ""]'}, "must be a JSON list"),
        ({"FRAPPE_BENCH_TIMEOUT": "soon"}, "FRAPPE_BENCH_TIMEOUT"),
        ({"FRAPPE_BENCH_TIMEOUT": "5"}, "FRAPPE_BENCH_TIMEOUT"),
    ],
)
def test_bad_bench_settings_fail_clearly(bench, env, message) -> None:
    for name, value in env.items():
        bench["env"].setenv(name, value)

    with pytest.raises(RuntimeError, match=message):
        standalone._bench_settings()


def test_the_site_falls_back_to_frappe_site(bench) -> None:
    bench["env"].delenv("FRAPPE_BENCH_SITE")
    bench["env"].setenv("FRAPPE_SITE", SITE)

    standalone._bench_settings()

    assert standalone._bench_config["site"] == SITE


def test_a_command_not_on_path_fails_clearly(bench) -> None:
    bench["env"].setattr(standalone.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="bench is not on PATH"):
        standalone._bench_settings()


def test_a_container_command_prefixes_the_argv(bench) -> None:
    bench["env"].setenv(
        "FRAPPE_BENCH_COMMAND", '["docker", "compose", "exec", "-T", "backend", "bench"]'
    )
    standalone._bench_settings()

    preview = standalone.run_bench("clear-cache")

    assert preview["argv"] == [
        "docker", "compose", "exec", "-T", "backend", "bench", "--site", SITE, "clear-cache"
    ]


def test_a_bench_without_a_local_sites_folder_skips_the_site_config_check(bench) -> None:
    compose = bench["tmp"] / "compose"
    compose.mkdir()
    bench["env"].setenv("FRAPPE_BENCH_PATH", str(compose))

    standalone._bench_settings()

    assert standalone._bench_config["site"] == SITE


# Commands and their arguments


@pytest.mark.parametrize(
    ("kwargs", "tail"),
    [
        ({"command": "migrate"}, ["migrate"]),
        ({"command": "migrate", "app": APP}, ["migrate"]),
        (
            {"command": "reload-doc", "app": APP, "module": MODULE, "doctype": DOCTYPE},
            ["reload-doc", "lab", "doctype", "lab_sample"],
        ),
        ({"command": "export-fixtures", "app": APP}, ["export-fixtures", "--app", APP]),
        ({"command": "clear-cache"}, ["clear-cache"]),
        (
            {"command": "run-tests", "app": APP, "module": MODULE},
            ["run-tests", "--app", APP, "--module-def", MODULE],
        ),
        (
            {"command": "run-tests", "app": APP, "doctype": DOCTYPE},
            ["run-tests", "--app", APP, "--doctype", DOCTYPE],
        ),
        (
            {"command": "run-tests", "app": APP, "test_module": TEST_MODULE},
            ["run-tests", "--app", APP, "--module", TEST_MODULE],
        ),
    ],
)
def test_each_command_builds_a_fixed_argv(bench, kwargs, tail) -> None:
    _apps(bench)

    preview = standalone.run_bench(**kwargs)

    assert preview["preview"] is True and preview["requires_confirmation"] is True
    assert preview["argv"] == ["bench", "--site", SITE, *tail]
    assert preview["site"] == SITE
    assert len(preview["preview_id"]) == 64
    assert bench["run"].calls == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"command": "build"}, "command must be one of"),
        ({"command": "migrate --skip-failing"}, "command must be one of"),
        ({"command": "migrate", "module": MODULE}, "migrate takes only app"),
        ({"command": "clear-cache", "app": APP}, "clear-cache takes no arguments"),
        ({"command": "reload-doc", "app": APP, "module": MODULE}, "reload-doc needs app, module"),
        ({"command": "export-fixtures"}, "export-fixtures needs app"),
        ({"command": "export-fixtures", "app": APP, "doctype": DOCTYPE}, "takes only app"),
        ({"command": "export-fixtures", "app": "frappe"}, "protected app"),
        ({"command": "run-tests", "app": APP}, "exactly one of module, doctype, test_module"),
        (
            {"command": "run-tests", "app": APP, "module": MODULE, "doctype": DOCTYPE},
            "exactly one of module, doctype, test_module",
        ),
        ({"command": "run-tests", "module": MODULE}, "run-tests needs app"),
        (
            {"command": "run-tests", "app": APP, "test_module": "other_app.tests.test_x"},
            "test_module must be a module of lab_app",
        ),
        (
            {"command": "run-tests", "app": APP, "test_module": "lab_app.tests; rm -rf /"},
            "test_module must be a module of lab_app",
        ),
        ({"command": "migrate", "app": "--help"}, "Invalid app name"),
        ({"command": "migrate", "app": "lab app; ls"}, "Invalid app name"),
        (
            {"command": "reload-doc", "app": APP, "module": "-x", "doctype": DOCTYPE},
            "Invalid app or module name",
        ),
    ],
)
def test_other_argument_combinations_are_refused(bench, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        standalone.run_bench(**kwargs)
    assert bench["run"].calls == []


def test_reload_doc_needs_the_doctype_file_in_the_checkout(bench) -> None:
    package = _apps(bench)
    (package / "lab" / "doctype" / "lab_sample" / "lab_sample.json").unlink()

    with pytest.raises(ValueError, match="no DocType file for Lab Sample"):
        standalone.run_bench("reload-doc", app=APP, module=MODULE, doctype=DOCTYPE)


def test_reload_doc_refuses_a_file_that_names_another_doctype(bench) -> None:
    package = _apps(bench)
    (package / "lab" / "doctype" / "lab_sample" / "lab_sample.json").write_text(
        json.dumps({"doctype": "DocType", "name": "Lab Other"})
    )

    with pytest.raises(ValueError, match="does not hold the DocType Lab Sample"):
        standalone.run_bench("reload-doc", app=APP, module=MODULE, doctype=DOCTYPE)


def test_reload_doc_refuses_a_module_of_another_app(bench) -> None:
    _apps(bench)
    bench["site"].add(
        "GET",
        standalone._resource("Module Def"),
        [{"name": MODULE, "app_name": "other_app", "custom": 0}],
    )

    with pytest.raises(ValueError, match="does not belong to app lab_app"):
        standalone.run_bench("reload-doc", app=APP, module=MODULE, doctype=DOCTYPE)


def test_run_tests_refuses_a_doctype_of_another_app(bench) -> None:
    bench["site"].add(
        "GET",
        standalone._resource("Module Def"),
        [{"name": MODULE, "app_name": "other_app", "custom": 0}],
        params=lambda params: "name" in params["filters"],
    )

    with pytest.raises(ValueError, match="Lab Sample belongs to other_app, not lab_app"):
        standalone.run_bench("run-tests", app=APP, doctype=DOCTYPE)


def test_an_app_without_modules_on_the_site_is_refused(bench) -> None:
    bench["site"].add("GET", standalone._resource("Module Def"), [])

    with pytest.raises(ValueError, match="no modules of app lab_app"):
        standalone.run_bench("migrate", app=APP)


@pytest.mark.parametrize(
    ("site_config", "common", "allowed"),
    [
        ({"allow_tests": 0}, {}, False),
        ({}, {}, False),
        ({}, {"allow_tests": True}, True),
        ({"allow_tests": 0}, {"allow_tests": 1}, False),
    ],
)
def test_run_tests_needs_allow_tests(bench, site_config, common, allowed) -> None:
    sites = bench["root"] / "sites"
    (sites / SITE / "site_config.json").write_text(json.dumps(site_config))
    (sites / "common_site_config.json").write_text(json.dumps(common))

    if allowed:
        assert standalone.run_bench("run-tests", app=APP, module=MODULE)["preview"] is True
    else:
        with pytest.raises(ValueError, match="allow_tests"):
            standalone.run_bench("run-tests", app=APP, module=MODULE)


# Preview and confirmation


def test_the_preview_shows_drift_and_pending_patches_and_warns_for_migrate(bench) -> None:
    preview = standalone.run_bench("migrate", app=APP)

    assert preview["preflight"]["drift"]["counts"] == {"missing_on_site": 1}
    assert preview["preflight"]["drift"]["items"] == [
        {"doctype": DOCTYPE, "module": MODULE, "verdict": "missing_on_site"}
    ]
    assert preview["preflight"]["pending_patches"]["pending_count"] == 1
    assert "patches" in preview["warning"] and "data" in preview["warning"]
    # Nothing in the preview names the Bench folder.
    assert str(bench["root"]) not in json.dumps(preview)


def test_drift_unavailable_without_a_checkout_is_said_not_hidden(bench) -> None:
    def no_checkout(*args, **kwargs):
        raise RuntimeError("Set FRAPPE_APPS_PATH to the local Bench apps directory for exports")

    bench["env"].setattr(standalone, "list_app_drift", no_checkout)

    preview = standalone.run_bench("migrate", app=APP)

    assert "FRAPPE_APPS_PATH" in preview["preflight"]["drift"]["unavailable"]


def test_confirm_needs_the_current_preview_id(bench) -> None:
    preview = standalone.run_bench("migrate", app=APP)

    with pytest.raises(ValueError, match="preview is missing or stale"):
        standalone.run_bench("migrate", app=APP, confirm=True)
    with pytest.raises(ValueError, match="preview is missing or stale"):
        standalone.run_bench("migrate", app=APP, confirm=True, expected_preview="0" * 64)
    # The site changed after the preview: the same id is now stale.
    bench["state"]["pending"] = []
    with pytest.raises(ValueError, match="preview is missing or stale"):
        standalone.run_bench(
            "migrate", app=APP, confirm=True, expected_preview=preview["preview_id"]
        )
    assert bench["run"].calls == []


def test_a_preview_id_of_another_command_is_refused(bench) -> None:
    preview = standalone.run_bench("clear-cache")

    with pytest.raises(ValueError, match="preview is missing or stale"):
        standalone.run_bench("migrate", confirm=True, expected_preview=preview["preview_id"])


def test_confirm_runs_the_argv_without_a_shell_or_secrets(bench) -> None:
    bench["env"].setenv("FRAPPE_PASSWORD", "password-value-123")
    bench["env"].setenv("FRAPPE_API_KEY", "api-key-value-123")

    result = _confirm(command="clear-cache")

    ((argv, kwargs),) = bench["run"].calls
    assert argv == ["bench", "--site", SITE, "clear-cache"]
    assert "shell" not in kwargs
    assert kwargs["cwd"] == bench["root"].resolve()
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["timeout"] == 900
    env = kwargs["env"]
    assert not [name for name in env if name.startswith("FRAPPE_")]
    assert "CI" not in env
    assert SECRET not in json.dumps(env)
    assert "PATH" in env
    assert result["exit_code"] == 0 and result["ok"] is True
    assert result["command"] == "clear-cache"


def test_the_timeout_setting_reaches_the_run(bench) -> None:
    bench["env"].setenv("FRAPPE_BENCH_TIMEOUT", "120")
    standalone._bench_settings()

    _confirm(command="clear-cache")

    assert bench["run"].calls[0][1]["timeout"] == 120


def test_output_is_redacted_capped_and_plain(bench) -> None:
    lines = [f"line {index}" for index in range(500)]
    lines[-2] = f"\x1b[32mtoken {SECRET} in {bench['root']}/sites\x1b[0m"
    lines[-3] = "x" * 2000
    bench["run"].output = ("\n".join(lines) + "\n").encode()

    result = _confirm(command="clear-cache")

    output = result["output"]
    assert SECRET not in output and "***" in output
    assert str(bench["root"]) not in output and "<bench>/sites" in output
    assert "\x1b" not in output
    assert output.splitlines()[-1] == "line 499"
    assert len(output.splitlines()) <= standalone._BENCH_OUTPUT_LINES
    assert max(len(line) for line in output.splitlines()) <= standalone._BENCH_LINE_CHARS + 20
    assert len(output) <= standalone._BENCH_OUTPUT_CHARS
    assert result["output_truncated"] is True
    assert result["output_lines"] == 500


def test_a_timeout_is_reported_and_the_post_check_still_runs(bench) -> None:
    bench["run"].returncode = subprocess.TimeoutExpired(["bench"], 900, output=b"half way\n")

    result = _confirm(command="migrate", app=APP)

    assert result["timed_out"] is True and result["exit_code"] is None
    assert result["ok"] is False
    assert "half way" in result["output"]
    assert bench["ready"]
    assert result["after"]["drift"]["counts"] == {"missing_on_site": 1}


def test_a_failed_command_still_runs_the_post_check(bench) -> None:
    bench["run"].returncode = 1
    bench["run"].output = b"Traceback (most recent call last):\nboom\n"

    result = _confirm(command="migrate", app=APP)

    assert result["exit_code"] == 1 and result["ok"] is False
    assert "boom" in result["output"]
    assert bench["ready"]
    assert "after" in result and "pending_patches" in result["after"]


def test_a_command_that_cannot_start_is_a_clear_error(bench) -> None:
    bench["run"].returncode = FileNotFoundError(2, "No such file or directory")

    with pytest.raises(RuntimeError, match="Could not start bench"):
        _confirm(command="clear-cache")


def test_the_metadata_cache_is_dropped_after_a_run(bench) -> None:
    standalone._meta_cache["Lab Sample"] = (0.0, {"doctype": {}})

    _confirm(command="clear-cache")

    assert standalone._meta_cache == {}


def test_a_missing_doctype_syncs_in_one_preview_and_confirmation(bench) -> None:
    _apps(bench)

    def migrate_file() -> None:
        bench["state"]["verdict"] = "in_sync"

    bench["run"].effect = migrate_file

    preview = standalone.run_bench("reload-doc", app=APP, module=MODULE, doctype=DOCTYPE)
    result = standalone.run_bench(
        "reload-doc", app=APP, module=MODULE, doctype=DOCTYPE, confirm=True,
        expected_preview=preview["preview_id"],
    )

    assert preview["preflight"]["drift"]["counts"] == {"missing_on_site": 1}
    assert "reload-doc" in preview["warning"]
    assert result["ok"] is True
    assert result["after"]["drift"]["counts"] == {"in_sync": 1}
    assert result["after"]["drift"]["items"] == []
    # Drift is scoped to the DocType's module.
    assert set(bench["drift"]) == {(APP, MODULE)}


# run-tests results


def test_run_tests_reads_the_unittest_summary(bench) -> None:
    bench["run"].output = b"Running 3 tests\n...\nRan 3 tests in 0.2s\n\nOK\n"

    result = _confirm(command="run-tests", app=APP, doctype=DOCTYPE)

    assert result["tests"] == {"ran": 3, "status": "OK"}
    assert result["ok"] is True


def test_failed_tests_are_not_ok_even_with_exit_code_zero(bench) -> None:
    # Frappe v15 exits 0 on test failures unless CI is set.
    bench["run"].output = b"Ran 4 tests in 1.0s\n\nFAILED (failures=1, errors=1)\n"

    result = _confirm(command="run-tests", app=APP, module=MODULE)

    assert result["exit_code"] == 0
    assert result["tests"] == {"ran": 4, "status": "FAILED", "detail": "failures=1, errors=1"}
    assert result["ok"] is False


def test_tests_disabled_by_the_site_are_not_ok(bench) -> None:
    bench["run"].output = b"Testing is disabled for the site!\nYou can enable tests ...\n"

    result = _confirm(command="run-tests", app=APP, module=MODULE)

    assert result["tests"]["status"] == "disabled"
    assert result["ok"] is False


# export-fixtures


def test_export_fixtures_previews_the_fixture_files_and_reports_changes(bench) -> None:
    package = _apps(bench)
    fixtures = package / "fixtures"
    fixtures.mkdir()
    (fixtures / "role.json").write_text("[]")
    (fixtures / "custom_field.json").write_text("[]")

    def export() -> None:
        (fixtures / "role.json").write_text('[{"name": "Lab Manager"}]')
        (fixtures / "workflow.json").write_text("[]")

    bench["run"].effect = export

    preview = standalone.run_bench("export-fixtures", app=APP)
    result = standalone.run_bench(
        "export-fixtures", app=APP, confirm=True, expected_preview=preview["preview_id"]
    )

    assert [row["file"] for row in preview["preflight"]["fixture_files"]["files"]] == [
        "fixtures/custom_field.json", "fixtures/role.json"
    ]
    assert result["changed_files"] == {
        "added": ["fixtures/workflow.json"],
        "changed": ["fixtures/role.json"],
        "removed": [],
    }


# Hints in other tools


def test_drift_and_pending_patches_point_to_run_bench(bench) -> None:
    assert standalone._bench_config

    assert "run_bench(command='reload-doc'" in standalone._missing_on_site_hint()
    assert "run_bench(command='migrate')" in standalone._pending_patches_hint()

    bench["env"].setattr(standalone, "_bench_config", {})
    assert "bench" in standalone._missing_on_site_hint()
    assert "run_bench" not in standalone._missing_on_site_hint()
    assert "run_bench" not in standalone._pending_patches_hint()


# Startup and `check`


def test_main_registers_run_bench_and_exits_on_a_bad_setting(bench) -> None:
    standalone.server.remove_tool("run_bench")
    standalone._TOOLSETS["bench"].clear()
    served: list[set] = []
    bench["env"].setattr(
        standalone.server, "run",
        lambda **kwargs: served.append(set(standalone.server._tool_manager._tools)),
    )

    standalone.main([])

    assert "run_bench" in served[0]
    bench["env"].setenv("FRAPPE_BENCH_SITE", "other.localhost")
    with pytest.raises(SystemExit, match="other.localhost"):
        standalone.main([])


def test_read_only_mode_leaves_the_bench_tool_out(bench) -> None:
    bench["env"].setenv("FRAPPE_MCP_READ_ONLY", "YES")
    served: list[set] = []
    bench["env"].setattr(
        standalone.server, "run",
        lambda **kwargs: served.append(set(standalone.server._tool_manager._tools)),
    )

    standalone.main([])

    assert "run_bench" not in served[0]


def test_check_reports_the_bench_without_its_path(bench) -> None:
    rows = standalone._bench_check_rows({"bench"})

    ((status, step, detail),) = rows
    assert (status, step) == ("ok", "bench")
    assert SITE in detail and "bench" in detail and "run-tests allowed" in detail
    assert str(bench["root"]) not in detail


def test_check_fails_a_bad_bench_setting(bench) -> None:
    bench["env"].setenv("FRAPPE_BENCH_TIMEOUT", "0")

    ((status, step, detail),) = standalone._bench_check_rows({"bench"})

    assert (status, step) == ("FAIL", "bench")
    assert "FRAPPE_BENCH_TIMEOUT" in detail


def test_check_lists_the_bench_toolset_when_it_is_configured(bench, capsys) -> None:
    bench["env"].setenv("FRAPPE_API_KEY", "api-key-value-123")
    bench["site"].add(
        "GET", "/api/method/frappe.auth.get_logged_user", "Administrator"
    )
    bench["site"].add("GET", "/api/method/frappe.utils.change_log.get_versions", {})

    rows = standalone._setup_check()

    toolsets = [detail for _, step, detail in rows if step == "toolsets"]
    assert toolsets and toolsets[0].endswith("developer, bench")
    assert ("ok", "bench") in [(status, step) for status, step, _ in rows]
