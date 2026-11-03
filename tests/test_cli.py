"""The `mcp-frappe` command line: `--version`, `check`, and a clean stdout in server mode."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import httpx
import pytest

import frappe_mcp
from frappe_mcp import standalone

ROOT = Path(__file__).resolve().parents[1]
KEY = "key-0123456789abcdef"
SECRET = "secret-fedcba9876543210"
PASSWORD = "pw-correct-horse-battery"
LOGGED_USER = "/api/method/frappe.auth.get_logged_user"
VERSIONS = "/api/method/frappe.utils.change_log.get_versions"

_ENV = (
    "FRAPPE_URL",
    "FRAPPE_SITE",
    "FRAPPE_API_KEY",
    "FRAPPE_API_SECRET",
    "FRAPPE_USERNAME",
    "FRAPPE_PASSWORD",
    "FRAPPE_APPS_PATH",
    "FRAPPE_TIMEOUT",
    "FRAPPE_META_TTL",
    "FRAPPE_ALLOW_INSECURE_HTTP",
    "FRAPPE_MCP_TOOLSETS",
    "FRAPPE_MCP_READ_ONLY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    standalone._reset_session()
    yield monkeypatch
    standalone._reset_session()


def _token_env(monkeypatch, url: str = "https://erp.example.com") -> None:
    monkeypatch.setenv("FRAPPE_URL", url)
    monkeypatch.setenv("FRAPPE_API_KEY", KEY)
    monkeypatch.setenv("FRAPPE_API_SECRET", SECRET)


def _site_answers(fake_site, user: str = "Administrator") -> None:
    fake_site.add("GET", LOGGED_USER, user)
    fake_site.add(
        "GET",
        VERSIONS,
        {
            "frappe": {"title": "Frappe Framework", "version": "16.33.1"},
            "erpnext": {"title": "ERPNext", "version": "16.2.0"},
        },
    )


def _run_check(capsys) -> tuple[int, str]:
    with pytest.raises(SystemExit) as raised:
        standalone.main(["check"])
    captured = capsys.readouterr()
    code = raised.value.code
    return (0 if code is None else code), captured.out + captured.err


def _assert_no_secrets(output: str) -> None:
    for secret in (KEY, SECRET, PASSWORD):
        assert secret not in output


# Version


def test_version_flag_prints_the_package_version(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        standalone.main(["--version"])

    assert raised.value.code == 0
    assert capsys.readouterr().out.strip() == f"mcp-frappe {frappe_mcp.__version__}"


def test_version_has_one_source() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    project = pyproject.split("[project]", 1)[1].split("\n[", 1)[0]

    assert 'dynamic = ["version"]' in project
    assert not any(line.startswith("version") for line in project.splitlines())
    assert 'path = "frappe_mcp/__init__.py"' in pyproject
    assert metadata.version("mcp-frappe") == frappe_mcp.__version__


def test_unknown_command_fails_without_starting_the_server(clean_env, capsys) -> None:
    clean_env.setattr(standalone.server, "run", lambda **kwargs: pytest.fail("server ran"))

    with pytest.raises(SystemExit) as raised:
        standalone.main(["serve-http"])

    assert raised.value.code == 2
    assert capsys.readouterr().out == ""


# check


def test_check_reports_a_ready_setup(fake_site, clean_env, capsys) -> None:
    _token_env(clean_env)
    clean_env.setenv("FRAPPE_SITE", "site1.localhost")
    _site_answers(fake_site)

    code, output = _run_check(capsys)

    assert code == 0
    assert f"mcp-frappe {frappe_mcp.__version__}" in output
    assert "https://erp.example.com" in output
    assert "site1.localhost" in output
    assert "API key" in output
    assert "Administrator" in output
    assert "frappe 16.33.1" in output and "erpnext 16.2.0" in output
    assert "developer mode" in output.lower()
    assert "read, data, customize, developer" in output
    assert "FRAPPE_APPS_PATH" in output
    assert output.rstrip().endswith("Ready.")
    _assert_no_secrets(output)


def test_check_shows_the_selected_toolsets(fake_site, clean_env, capsys) -> None:
    _token_env(clean_env)
    clean_env.setenv("FRAPPE_MCP_READ_ONLY", "YES")
    _site_answers(fake_site)

    code, output = _run_check(capsys)

    assert code == 0
    assert "toolsets" in output and "read (read-only mode)" in output


def test_check_fails_when_the_site_rejects_the_credentials(fake_site, clean_env, capsys) -> None:
    _token_env(clean_env)
    fake_site.add(
        "GET",
        LOGGED_USER,
        standalone.FrappeError(
            "Frappe HTTP 401 AuthenticationError: Invalid token", "AuthenticationError",
            status=401,
        ),
    )

    code, output = _run_check(capsys)

    assert code == 1
    assert "Invalid token" in output
    assert "Traceback" not in output
    assert "problem" in output.lower()
    _assert_no_secrets(output)


def test_check_fails_when_the_site_is_unreachable(fake_site, clean_env, capsys) -> None:
    _token_env(clean_env, "http://127.0.0.1:9")
    fake_site.add("GET", LOGGED_USER, httpx.ConnectError("[Errno 111] Connection refused"))

    code, output = _run_check(capsys)

    assert code == 1
    assert "Connection refused" in output
    assert "http://127.0.0.1:9" in output


def test_check_fails_for_a_guest_session(fake_site, clean_env, capsys) -> None:
    _token_env(clean_env)
    _site_answers(fake_site, user="Guest")

    code, output = _run_check(capsys)

    assert code == 1
    assert "Guest" in output


def test_check_fails_without_a_site_url(clean_env, capsys) -> None:
    code, output = _run_check(capsys)

    assert code == 1
    assert "Set FRAPPE_URL" in output
    assert "Traceback" not in output


def test_check_fails_for_bad_settings_without_contacting_the_site(
    fake_site, clean_env, capsys
) -> None:
    _token_env(clean_env)
    clean_env.setenv("FRAPPE_MCP_TOOLSETS", "read,admin")
    clean_env.setenv("FRAPPE_TIMEOUT", "soon")

    code, output = _run_check(capsys)

    assert code == 1
    assert "Unknown toolset in FRAPPE_MCP_TOOLSETS: admin" in output
    assert "FRAPPE_TIMEOUT must be a positive number" in output
    assert fake_site.calls == []


def test_check_never_prints_the_password_or_url_credentials(fake_site, clean_env, capsys) -> None:
    clean_env.setenv("FRAPPE_URL", f"https://admin:{PASSWORD}@erp.example.com:8443/")
    clean_env.setenv("FRAPPE_USERNAME", "dev@example.com")
    clean_env.setenv("FRAPPE_PASSWORD", PASSWORD)
    fake_site.add(
        "GET",
        LOGGED_USER,
        standalone.FrappeError(f"Frappe HTTP 401: Wrong password {PASSWORD}", status=401),
    )

    code, output = _run_check(capsys)

    assert code == 1
    assert "https://erp.example.com:8443" in output
    assert "password login as dev@example.com" in output
    _assert_no_secrets(output)


def test_check_warns_about_insecure_http(fake_site, clean_env, capsys) -> None:
    _token_env(clean_env, "http://frontend:8080")
    clean_env.setenv("FRAPPE_ALLOW_INSECURE_HTTP", "YES")
    _site_answers(fake_site)

    code, output = _run_check(capsys)

    assert code == 0
    assert "FRAPPE_ALLOW_INSECURE_HTTP" in output


def test_check_reports_the_apps_path(fake_site, clean_env, tmp_path, capsys) -> None:
    _token_env(clean_env)
    for app in ("frappe", "erpnext"):
        (tmp_path / app / app).mkdir(parents=True)
    clean_env.setenv("FRAPPE_APPS_PATH", str(tmp_path))
    _site_answers(fake_site)

    code, output = _run_check(capsys)

    assert code == 0
    assert "checkouts found: erpnext, frappe" in output
    assert str(tmp_path) not in output


def test_check_reports_missing_and_read_only_checkouts(
    fake_site, clean_env, tmp_path, capsys
) -> None:
    _token_env(clean_env)
    (tmp_path / "frappe" / "frappe").mkdir(parents=True)
    clean_env.setenv("FRAPPE_APPS_PATH", str(tmp_path))
    clean_env.setattr(standalone, "_writable", lambda path: False)
    _site_answers(fake_site)

    code, output = _run_check(capsys)

    assert code == 1
    assert "no checkout: erpnext" in output
    assert "not writable: frappe" in output


def test_check_fails_when_the_apps_path_does_not_exist(
    fake_site, clean_env, tmp_path, capsys
) -> None:
    _token_env(clean_env)
    clean_env.setenv("FRAPPE_APPS_PATH", str(tmp_path / "missing"))
    _site_answers(fake_site)

    code, output = _run_check(capsys)

    assert code == 1
    assert "FRAPPE_APPS_PATH does not exist" in output


def test_check_leaves_short_secret_values_out_of_redaction(fake_site, clean_env, capsys) -> None:
    clean_env.setenv("FRAPPE_URL", "https://erp.example.com")
    clean_env.setenv("FRAPPE_PASSWORD", "admin")
    _site_answers(fake_site)

    code, output = _run_check(capsys)

    assert code == 0
    assert "logged in as Administrator" in output
    assert "***" not in output


# Server mode


def test_server_mode_prints_nothing_to_stdout(clean_env, capsys) -> None:
    clean_env.setenv("FRAPPE_ALLOW_INSECURE_HTTP", "YES")
    served = []
    clean_env.setattr(standalone.server, "run", lambda **kwargs: served.append(kwargs))

    standalone.main([])

    assert served == [{"transport": "stdio"}]
    assert capsys.readouterr().out == ""


def test_server_process_speaks_only_json_rpc_on_stdout() -> None:
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    }
    env = {key: value for key, value in os.environ.items() if not key.startswith("FRAPPE_")}
    env["FRAPPE_ALLOW_INSECURE_HTTP"] = "YES"  # logs a startup warning, which must go to stderr

    result = subprocess.run(
        [sys.executable, "-m", "frappe_mcp.standalone"],
        input=json.dumps(initialize) + "\n",
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.stderr
    messages = [json.loads(line) for line in lines]
    assert all(message.get("jsonrpc") == "2.0" for message in messages)
    assert messages[0]["id"] == 1 and messages[0]["result"]["serverInfo"]["name"] == "frappe"
    assert "FRAPPE_ALLOW_INSECURE_HTTP" in result.stderr
