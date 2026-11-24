"""The frappe-dev plugin in plugins/frappe-dev (Claude Code and Codex) and its marketplaces."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import frappe_mcp
from frappe_mcp import standalone

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "frappe-dev"
SKILL = PLUGIN / "skills" / "frappe-development"

# The sdist ships tests/ but not plugins/.
pytestmark = pytest.mark.skipif(not PLUGIN.is_dir(), reason="plugins/ not present")

# Registered only with FRAPPE_BENCH_PATH or FRAPPE_MCP_METHODS.
OPT_IN_TOOLS = {"run_bench", "list_allowed_methods", "make_mapped_document", "call_allowed_method"}
TOOL_LIKE = re.compile(
    r"`((?:list|get|create|update|delete|upsert|export|rollback|diff|promote|customize|set|run|"
    r"submit|cancel|undo|search|count|make|call)_[a-z_]+|rename)(?:\([^`]*\))?`"
)


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_plugin_and_marketplace_versions_follow_the_package() -> None:
    manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    marketplace = _json(ROOT / ".claude-plugin" / "marketplace.json")
    (entry,) = marketplace["plugins"]

    assert manifest["name"] == entry["name"] == "frappe-dev"
    assert manifest["version"] == entry["version"] == frappe_mcp.__version__
    assert entry["source"] == "./plugins/frappe-dev"
    assert marketplace["name"] == "mcp-frappe"


def test_codex_plugin_and_marketplace_versions_follow_the_package() -> None:
    manifest = _json(PLUGIN / ".codex-plugin" / "plugin.json")
    marketplace = _json(ROOT / ".agents" / "plugins" / "marketplace.json")
    (entry,) = marketplace["plugins"]

    assert manifest["name"] == entry["name"] == "frappe-dev"
    assert manifest["version"] == entry["version"] == frappe_mcp.__version__
    assert entry["source"]["path"] == "./plugins/frappe-dev"
    assert manifest["skills"] == "./skills/"
    assert marketplace["name"] == "mcp-frappe"


def test_the_server_entry_takes_every_value_from_the_plugin_options() -> None:
    manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    options = manifest["userConfig"]
    (name, server), = _json(PLUGIN / ".mcp.json")["mcpServers"].items()
    source = (ROOT / "frappe_mcp" / "standalone.py").read_text(encoding="utf-8")

    assert name == "frappe-dev"
    assert server["command"] == "uvx" and server["args"] == ["mcp-frappe"]
    used = set()
    for variable, value in server["env"].items():
        assert variable.startswith("FRAPPE_") and f'"{variable}"' in source, variable
        match = re.fullmatch(r"\$\{user_config\.(\w+)\}", value)
        assert match and match[1] in options, f"{variable} must come from a plugin option"
        used.add(match[1])
    assert used == set(options)
    sensitive = {key for key, option in options.items() if option.get("sensitive")}
    assert sensitive == {"api_key", "api_secret"}
    # Optional options default to empty, which the server reads as unset.
    for key, option in options.items():
        assert option.get("required") or option.get("default") == "", key


def test_the_skill_names_only_tools_the_server_has() -> None:
    known = set(standalone.server._tool_manager._tools) | OPT_IN_TOOLS
    assert all(callable(getattr(standalone, tool, None)) for tool in OPT_IN_TOOLS)
    files = [SKILL / "SKILL.md", *sorted((SKILL / "references").glob("*.md"))]
    named = set()
    for path in files:
        named |= set(TOOL_LIKE.findall(path.read_text(encoding="utf-8")))
    assert named, "the skill should name the tools it routes to"
    # update_doctype / customize_doctype operation names, and the native server's tools.
    not_tools = {"set_property", "set_properties", "update_field", "call_method",
                 "search_doctypes"}
    assert named - not_tools <= known, sorted(named - not_tools - known)


def test_the_skill_has_frontmatter_and_stays_short() -> None:
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    front = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert front
    fields = dict(line.split(": ", 1) for line in front[1].splitlines())
    assert fields["name"] == "frappe-development"
    assert 50 < len(fields["description"]) <= 1024
    assert len(text.splitlines()) < 120
