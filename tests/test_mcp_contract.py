"""Exercise the server through a real MCP client session, as an agent would see it."""

import json

import anyio
from mcp.shared.memory import create_connected_server_and_client_session

from frappe_mcp import standalone


def test_every_tool_has_a_description_and_an_object_schema() -> None:
    async def list_tools():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            return (await client.list_tools()).tools

    tools = anyio.run(list_tools)

    assert {tool.name for tool in tools} == set(standalone.server._tool_manager._tools)
    for tool in tools:
        assert tool.description and tool.description.strip(), tool.name
        assert tool.inputSchema.get("type") == "object", tool.name


def test_read_tool_round_trips_through_an_mcp_session(fake_site) -> None:
    fake_site.add(
        "GET",
        "/api/resource/Module%20Def",
        [
            {"name": "Example Module", "app_name": "example_app"},
            {"name": "Core", "app_name": "frappe"},
        ],
    )

    async def call_tool():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            return await client.call_tool("list_installed_apps", {})

    result = anyio.run(call_tool)

    assert not result.isError
    decoded = [json.loads(block.text) for block in result.content]
    assert decoded == [
        {"app": "example_app", "modules": ["Example Module"]},
        {"app": "frappe", "modules": ["Core"]},
    ]
    assert len(fake_site.calls_to("GET", "/api/resource/Module%20Def")) == 1


def test_schema_tool_exposes_the_detail_choice() -> None:
    async def list_tools():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            return (await client.list_tools()).tools

    (tool,) = [tool for tool in anyio.run(list_tools) if tool.name == "get_doctype_schema"]
    detail = tool.inputSchema["properties"]["detail"]

    assert detail["enum"] == ["compact", "full"]
    assert detail["default"] == "compact"
