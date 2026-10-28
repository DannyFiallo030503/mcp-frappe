"""Agent ergonomics: readable errors, instructions, annotations, toolsets, and report tools."""

from __future__ import annotations

import json

import anyio
import httpx
import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

from frappe_mcp import standalone

RUN = "/api/method/frappe.desk.query_report.run"
SCRIPT = "/api/method/frappe.desk.query_report.get_script"
REPORTS = "/api/resource/Report"


def _server_messages(*messages: str) -> str:
    return json.dumps([json.dumps({"message": text, "indicator": "red"}) for text in messages])


# Errors


def test_nested_server_messages_become_plain_text_without_the_traceback() -> None:
    response = httpx.Response(
        417,
        json={
            "exc_type": "ValidationError",
            "exc": json.dumps(["Traceback (most recent call last):\n  secret frame"]),
            "exception": "frappe.exceptions.ValidationError: raw",
            "_server_messages": _server_messages(
                "Status must be one of <b>Open</b>, <b>Closed</b>&nbsp;only<br>Row 2"
            ),
        },
    )

    with pytest.raises(standalone.FrappeError) as raised:
        standalone._check(response)

    error = raised.value
    assert error.status == 417
    assert error.exc_type == "ValidationError"
    text = str(error)
    assert text.startswith(
        "Frappe HTTP 417 ValidationError: Status must be one of Open, Closed only\nRow 2"
    )
    assert "Hint: Frappe rejected the values" in text
    assert "Traceback" not in text and "secret" not in text and "<b>" not in text
    assert error.message == text


def test_application_error_in_a_200_response_is_decoded_too() -> None:
    response = httpx.Response(
        200,
        json={"exc": "[\"Traceback ...\"]", "exc_type": "DoesNotExistError",
              "_server_messages": _server_messages("ToDo T-9 not found")},
    )

    with pytest.raises(standalone.FrappeError) as raised:
        standalone._check(response)

    assert raised.value.status is None
    assert str(raised.value).startswith("Frappe DoesNotExistError: ToDo T-9 not found\nHint:")
    assert "Traceback" not in str(raised.value)


def test_mandatory_error_lists_the_missing_fields() -> None:
    response = httpx.Response(
        417,
        json={
            "exc_type": "MandatoryError",
            "_server_messages": _server_messages(
                "Error: Value missing for <strong>Lab Sample</strong>: Title",
                "Error: Value missing for Lab Sample: Due Date",
            ),
        },
    )

    with pytest.raises(standalone.FrappeError, match=r"Hint: Missing: Title, Due Date\. "):
        standalone._check(response)


@pytest.mark.parametrize(
    ("exc_type", "phrase"),
    [
        ("PermissionError", "do not retry the change another way"),
        ("DoesNotExistError", "does not exist"),
        ("LinkValidationError", "Link field"),
        ("DuplicateEntryError", "already exists"),
        ("TimestampMismatchError", "fresh preview"),
    ],
)
def test_common_exception_types_get_a_hint(exc_type, phrase) -> None:
    response = httpx.Response(417, json={"exc_type": exc_type, "_server_messages": "[]"})

    with pytest.raises(standalone.FrappeError) as raised:
        standalone._check(response)

    assert "\nHint: " in str(raised.value)
    assert phrase in str(raised.value)
    assert raised.value.exc_type == exc_type


def test_exception_line_is_used_without_its_class_path_when_no_messages() -> None:
    response = httpx.Response(
        500,
        json={"exc_type": "CustomError", "exception": "my_app.errors.CustomError: broke here"},
    )

    with pytest.raises(standalone.FrappeError) as raised:
        standalone._check(response)

    assert str(raised.value) == "Frappe HTTP 500 CustomError: broke here"


def test_non_json_error_bodies_still_raise_a_readable_error() -> None:
    with pytest.raises(standalone.FrappeError, match=r"^Frappe HTTP 502: request failed$"):
        standalone._check(httpx.Response(502, text="<html>Bad Gateway</html>"))


def test_error_text_is_bounded_and_keeps_its_hint() -> None:
    response = httpx.Response(
        403,
        json={"exc_type": "PermissionError", "_server_messages": _server_messages("x" * 9000)},
    )

    with pytest.raises(standalone.FrappeError) as raised:
        standalone._check(response)

    text = str(raised.value)
    assert len(text) <= standalone._ERROR_MAX_CHARS
    assert "...[truncated]" in text
    assert text.endswith(standalone._ERROR_HINTS["PermissionError"])


# Instructions, annotations, and argument descriptions


def _list_tools():
    async def run():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            return (await client.list_tools()).tools

    return anyio.run(run)


def test_initialize_result_carries_the_server_instructions(monkeypatch) -> None:
    seen = {}
    original = ClientSession.initialize

    async def capture(self):
        seen["result"] = await original(self)
        return seen["result"]

    monkeypatch.setattr(ClientSession, "initialize", capture)

    async def run():
        async with create_connected_server_and_client_session(standalone.server._mcp_server):
            pass

    anyio.run(run)

    instructions = seen["result"].instructions
    assert instructions == standalone._INSTRUCTIONS
    assert len(instructions) < 1500
    for phrase in ("get_site_info", "list_doctypes(search=", "diff_doctype", "confirm=true",
                   "PermissionError"):
        assert phrase in instructions


def test_every_tool_has_annotations_and_a_toolset() -> None:
    tools = _list_tools()
    grouped = {name: toolset for toolset, names in standalone._TOOLSETS.items()
               for name in names}

    assert set(grouped) == {tool.name for tool in tools}
    assert sum(len(names) for names in standalone._TOOLSETS.values()) == len(grouped)
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None, tool.name
        assert annotations.title and tool.title == annotations.title, tool.name
        assert annotations.readOnlyHint is (grouped[tool.name] == "read"), tool.name
        assert annotations.openWorldHint is False, tool.name
        if not annotations.readOnlyHint:
            assert annotations.destructiveHint is not None, tool.name
            assert annotations.idempotentHint is not None, tool.name


def test_destructive_tools_are_marked() -> None:
    destructive = {
        tool.name for tool in _list_tools() if tool.annotations.destructiveHint
    }

    assert {
        "delete_document",
        "delete_doctype",
        "cancel_document",
        "rollback_export",
        "set_role_permissions",
    } <= destructive
    assert "create_document" not in destructive


@pytest.mark.parametrize(
    ("tool", "argument"),
    [
        ("list_documents", "filters"),
        ("list_documents", "or_filters"),
        ("list_documents", "fields"),
        ("list_documents", "order_by"),
        ("count_documents", "filters"),
        ("get_document", "fields"),
        ("update_document", "expected_modified"),
        ("update_document", "confirm"),
        ("export_customizations", "expected_preview"),
        ("rollback_export", "expected_preview"),
        ("get_doctype_schema", "detail"),
        ("run_report", "filters"),
        ("create_doctype", "fields"),
    ],
)
def test_key_arguments_carry_descriptions(tool, argument) -> None:
    (found,) = [item for item in _list_tools() if item.name == tool]

    assert found.inputSchema["properties"][argument].get("description"), (tool, argument)


def test_every_confirm_and_token_argument_is_described() -> None:
    for tool in _list_tools():
        for argument in ("confirm", "expected_modified", "expected_preview", "filters", "fields"):
            schema = tool.inputSchema["properties"].get(argument)
            if schema is not None:
                assert schema.get("description"), (tool.name, argument)


def test_filters_description_shows_every_operator_and_an_example() -> None:
    (tool,) = [item for item in _list_tools() if item.name == "list_documents"]
    description = tool.inputSchema["properties"]["filters"]["description"]

    for operator in standalone._FILTER_OPERATORS:
        assert operator in description
    assert '[["status", "=", "Open"]' in description


# Toolsets


@pytest.fixture
def tool_registry(monkeypatch):
    """Let a test remove tools from the shared server without affecting other tests."""
    tools = standalone.server._tool_manager._tools
    monkeypatch.setattr(standalone.server._tool_manager, "_tools", dict(tools))
    monkeypatch.delenv("FRAPPE_MCP_TOOLSETS", raising=False)
    monkeypatch.delenv("FRAPPE_MCP_READ_ONLY", raising=False)
    return monkeypatch


def test_every_toolset_is_on_by_default(tool_registry) -> None:
    assert standalone._selected_toolsets() == {"read", "data", "customize", "developer"}


def test_toolsets_narrow_the_tool_list(tool_registry) -> None:
    tool_registry.setenv("FRAPPE_MCP_TOOLSETS", " Read , developer")

    standalone._apply_toolsets(standalone._selected_toolsets())

    names = {tool.name for tool in _list_tools()}
    assert names == standalone._TOOLSETS["read"] | standalone._TOOLSETS["developer"]
    assert "create_document" not in names and "create_custom_field" not in names


def test_read_only_mode_exposes_no_mutation_tool(tool_registry) -> None:
    tool_registry.setenv("FRAPPE_MCP_READ_ONLY", "YES")

    standalone._apply_toolsets(standalone._selected_toolsets())

    tools = _list_tools()
    assert {tool.name for tool in tools} == standalone._TOOLSETS["read"]
    assert all(tool.annotations.readOnlyHint for tool in tools)
    assert "list_reports" in {tool.name for tool in tools}


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"FRAPPE_MCP_TOOLSETS": "read,admin"}, "Unknown toolset in FRAPPE_MCP_TOOLSETS: admin"),
        ({"FRAPPE_MCP_READ_ONLY": "true"}, "FRAPPE_MCP_READ_ONLY must be YES or NO"),
        (
            {"FRAPPE_MCP_READ_ONLY": "YES", "FRAPPE_MCP_TOOLSETS": "read,data"},
            "remove data from FRAPPE_MCP_TOOLSETS",
        ),
    ],
)
def test_bad_toolset_settings_fail_at_startup(tool_registry, env, message) -> None:
    for key, value in env.items():
        tool_registry.setenv(key, value)
    ran = []
    tool_registry.setattr(standalone.server, "run", lambda **kwargs: ran.append(kwargs))

    with pytest.raises(SystemExit, match=message):
        standalone.main()

    assert ran == []
    assert len(standalone.server._tool_manager._tools) == sum(
        len(names) for names in standalone._TOOLSETS.values()
    )


def test_main_applies_the_toolsets_before_serving(tool_registry) -> None:
    tool_registry.setenv("FRAPPE_MCP_TOOLSETS", "read")
    served = []
    tool_registry.setattr(
        standalone.server,
        "run",
        lambda **kwargs: served.append(set(standalone.server._tool_manager._tools)),
    )

    standalone.main()

    assert served == [standalone._TOOLSETS["read"]]


# Reports


def test_list_reports_pages_enabled_reports(fake_site) -> None:
    rows = [
        {"name": f"Report {index}", "ref_doctype": "ToDo", "report_type": "Report Builder",
         "module": "Desk", "is_standard": "No"}
        for index in range(3)
    ]
    fake_site.add("GET", REPORTS, rows)

    result = standalone.list_reports(search="Rep", ref_doctype="ToDo", limit=2, offset=4)

    assert result == {"rows": rows[:2], "offset": 4, "limit": 2, "has_more": True}
    (call,) = fake_site.calls_to("GET", REPORTS)
    assert json.loads(call.params["filters"]) == [
        ["disabled", "=", 0],
        ["name", "like", "%Rep%"],
        ["ref_doctype", "=", "ToDo"],
    ]
    assert call.params["limit_start"] == 4
    assert call.params["limit_page_length"] == 3


@pytest.mark.parametrize(
    "kwargs",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"search": ""}, {"ref_doctype": "x;y"}],
)
def test_list_reports_rejects_bad_arguments(fake_site, kwargs) -> None:
    with pytest.raises(ValueError):
        standalone.list_reports(**kwargs)

    assert fake_site.calls == []


def test_get_report_filters_truncates_the_script_and_drops_the_print_format(fake_site) -> None:
    script = "frappe.query_reports['Stock Ledger'] = {filters: []};\n" + "x" * 9000
    fake_site.add(
        "GET",
        SCRIPT,
        {
            "script": script,
            "html_format": "<div>{{ secret }}</div>",
            "execution_time": 1.2,
            "filters": [
                {"name": "f1", "parent": "Stock Ledger", "idx": 1, "fieldname": "company",
                 "label": "Company", "fieldtype": "Link", "options": "Company", "mandatory": 1,
                 "default": None, "wildcard_filter": 0},
            ],
        },
        params={"report_name": "Stock Ledger"},
    )

    result = standalone.get_report_filters(" Stock Ledger ")

    assert result["report_name"] == "Stock Ledger"
    assert result["filters"] == [
        {"fieldname": "company", "label": "Company", "fieldtype": "Link",
         "options": "Company", "mandatory": 1, "wildcard_filter": 0}
    ]
    assert len(result["script"]) == standalone._REPORT_SCRIPT_CHARS
    assert result["script"] == script[: standalone._REPORT_SCRIPT_CHARS]
    assert result["script_truncated"] is True
    assert "html_format" not in result and "secret" not in json.dumps(result)


def test_get_report_filters_keeps_a_short_script_whole(fake_site) -> None:
    fake_site.add("GET", SCRIPT, {"script": "frappe.query_reports['R'] = {}", "filters": None})

    result = standalone.get_report_filters("R")

    assert result["filters"] == []
    assert result["script"] == "frappe.query_reports['R'] = {}"
    assert result["script_truncated"] is False


def test_run_report_pages_rows_and_reports_the_total(fake_site) -> None:
    fake_site.add(
        "GET",
        RUN,
        {"result": [[index] for index in range(10)], "columns": [{"label": "N"}],
         "chart": {"data": {"labels": ["big"]}}, "report_summary": []},
    )

    result = standalone.run_report("Numbers", filters={"company": "Example"}, limit=3, offset=6)

    assert result["result"] == [[6], [7], [8]]
    assert result["total_rows"] == 10
    assert result["returned_rows"] == 3
    assert result["has_more"] is True and result["truncated"] is True
    assert "chart" not in result
    assert result["columns"] == [{"label": "N"}]
    (call,) = fake_site.calls_to("GET", RUN)
    assert json.loads(call.params["filters"]) == {"company": "Example"}


def test_run_report_keeps_the_chart_when_asked(fake_site) -> None:
    fake_site.add("GET", RUN, {"result": [[1]], "chart": {"type": "bar"}})

    result = standalone.run_report("Numbers", include_chart=True, offset=1)

    assert result["chart"] == {"type": "bar"}
    assert result["result"] == []
    assert result["total_rows"] == 1 and result["has_more"] is False


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": 501}, {"offset": -1}])
def test_run_report_rejects_bad_paging(fake_site, kwargs) -> None:
    with pytest.raises(ValueError):
        standalone.run_report("Numbers", **kwargs)

    assert fake_site.calls == []
