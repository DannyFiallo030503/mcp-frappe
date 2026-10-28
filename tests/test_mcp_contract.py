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


def test_update_doctype_takes_an_operation_list_through_an_mcp_session(fake_site) -> None:
    record = {
        "name": "Lab Sample",
        "module": "Lab",
        "custom": 1,
        "modified": "2026-10-20 09:00:00.000001",
        "fields": [{"name": "df-title", "fieldname": "title", "fieldtype": "Data", "idx": 1}],
    }
    fake_site.add("GET", "/api/resource/DocType/Lab%20Sample", record)
    fake_site.add("GET", "/api/resource/Module%20Def", [{"name": "Lab", "app_name": "lab_app"}])
    fake_site.add_doctype("Lab Sample", record["fields"])
    operations = [{"op": "update_field", "fieldname": "title", "set": {"reqd": True}}]
    fake_site.add("GET", "/api/method/frappe.client.get_count", 0)

    async def call_tool():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            return await client.call_tool(
                "update_doctype", {"doctype": "Lab Sample", "operations": operations}
            )

    result = anyio.run(call_tool)

    assert not result.isError
    preview = json.loads(result.content[0].text)
    assert preview["changes"]["changed"] == {"title": {"reqd": {"from": None, "to": 1}}}


def test_list_app_drift_pages_through_an_mcp_session(fake_site, tmp_path, monkeypatch) -> None:
    doctype_dir = tmp_path / "apps" / "lab_app" / "lab_app" / "lab" / "doctype" / "lab_sample"
    doctype_dir.mkdir(parents=True)
    (doctype_dir / "lab_sample.json").write_text('{"name": "Lab Sample"}', encoding="utf-8")
    monkeypatch.setenv("FRAPPE_APPS_PATH", str(tmp_path / "apps"))
    fake_site.add("GET", "/api/resource/Module%20Def", [{"name": "Lab", "app_name": "lab_app"}])
    fake_site.add("GET", "/api/resource/DocType", [])

    async def call_tool():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            return await client.call_tool("list_app_drift", {"app": "lab_app", "limit": 10})

    result = anyio.run(call_tool)

    assert not result.isError
    report = json.loads(result.content[0].text)
    assert report["items"] == [
        {"doctype": "Lab Sample", "module": "Lab", "verdict": "missing_on_site"}
    ]


def test_upsert_notification_through_an_mcp_session_never_sends_a_condition(fake_site) -> None:
    fake_site.add_doctype(
        "Lab Sample",
        [{"fieldname": "title", "fieldtype": "Data"},
         {"fieldname": "reviewer", "fieldtype": "Link", "options": "User"}],
    )
    fake_site.add("GET", "/api/resource/Notification", [])
    fake_site.add("POST", "/api/resource/Notification", {"name": "Sample Saved"})
    arguments = {
        "name": "Sample Saved",
        "document_type": "Lab Sample",
        "event": "Save",
        "channel": "System Notification",
        "recipients": [{"receiver_by_document_field": "reviewer"}],
        "subject": "{{ doc.title | upper }} saved",
        "message": "Review {{ doc.name }}.",
        # Not a parameter: FastMCP drops unknown arguments, so it must never reach Frappe.
        "condition": "doc.title == 'x'",
        "confirm": True,
    }

    async def run():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            tools = (await client.list_tools()).tools
            return tools, await client.call_tool("upsert_notification", arguments)

    tools, result = anyio.run(run)

    (tool,) = [tool for tool in tools if tool.name == "upsert_notification"]
    assert "condition" not in tool.inputSchema["properties"]
    assert tool.inputSchema["properties"]["event"]["enum"] == [
        "New", "Save", "Submit", "Cancel", "Value Change", "Days Before", "Days After"
    ]
    assert tool.inputSchema["properties"]["channel"]["enum"] == ["Email", "System Notification"]
    assert not result.isError, result.content
    (post,) = fake_site.calls_to("POST", "/api/resource/Notification")
    assert post.json["data"]["condition"] is None
    assert post.json["data"]["subject"] == "{{ doc.title | upper }} saved"


def test_upsert_workspace_through_an_mcp_session_never_sends_custom_blocks(fake_site) -> None:
    fake_site.add(
        "GET",
        "/api/resource/Module%20Def",
        [{"name": "Lab", "app_name": "lab_app", "custom": 0}],
    )
    fake_site.add(
        "GET", "/api/resource/DocType", [{"name": "Lab Sample", "istable": 0}]
    )
    fake_site.add("GET", "/api/resource/Workspace", [])
    fake_site.add("POST", "/api/resource/Workspace", {"name": "Lab"})
    arguments = {
        "name": "Lab",
        "module": "Lab",
        "shortcuts": [{"label": "Samples", "type": "DocType", "link_to": "Lab Sample"}],
        # Not a parameter: FastMCP drops unknown arguments, so it must never reach Frappe.
        "custom_blocks": [{"custom_block_name": "Banner"}],
        "confirm": True,
    }

    async def run():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            tools = (await client.list_tools()).tools
            return tools, await client.call_tool("upsert_workspace", arguments)

    tools, result = anyio.run(run)

    (tool,) = [tool for tool in tools if tool.name == "upsert_workspace"]
    assert "custom_blocks" not in tool.inputSchema["properties"]
    assert not result.isError, result.content
    (post,) = fake_site.calls_to("POST", "/api/resource/Workspace")
    assert "custom_blocks" not in post.json["data"]
    assert [block["type"] for block in json.loads(post.json["data"]["content"])] == ["shortcut"]


def test_paged_list_and_submit_preview_round_trip_through_an_mcp_session(fake_site) -> None:
    fake_site.add_doctype("Lab Test", [{"fieldname": "title"}], is_submittable=1)
    fake_site.add("GET", "/api/resource/Lab%20Test", [{"name": "LT-1"}, {"name": "LT-2"}])
    fake_site.add(
        "GET",
        "/api/resource/Lab%20Test/LT-1",
        {"doctype": "Lab Test", "name": "LT-1", "docstatus": 0, "modified": "2026-10-28 09:00"},
    )

    async def call_tools():
        async with create_connected_server_and_client_session(
            standalone.server._mcp_server
        ) as client:
            listed = await client.call_tool(
                "list_documents", {"doctype": "Lab Test", "limit": 1, "order_by": "title desc"}
            )
            preview = await client.call_tool(
                "submit_document", {"doctype": "Lab Test", "name": "LT-1"}
            )
            return listed, preview

    listed, preview = anyio.run(call_tools)

    assert not listed.isError and not preview.isError
    assert json.loads(listed.content[0].text) == {
        "rows": [{"name": "LT-1"}],
        "offset": 0,
        "limit": 1,
        "has_more": True,
    }
    decoded = json.loads(preview.content[0].text)
    assert decoded["preview"] is True
    assert decoded["modified"] == "2026-10-28 09:00"
    assert fake_site.calls_to("POST") == []
