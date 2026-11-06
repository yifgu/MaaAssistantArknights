import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from maa_battle_mcp.server import tool_result


async def test_stdio_handshake_tools_and_errors(tmp_path):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "maa_battle_mcp.server",
            "--core-dir",
            str(tmp_path / "missing-core"),
            "--user-dir",
            str(tmp_path / "state"),
        ],
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert len(tools) == 14
            assert tools["battle_observe"].annotations.readOnlyHint is True
            assert tools["battle_identify"].annotations.readOnlyHint is False
            status = await session.call_tool("battle_status")
            assert status.structuredContent["connected"] is False
            observe = await session.call_tool(
                "battle_observe", {"session_id": "missing"}
            )
            assert observe.isError
            invalid = await session.call_tool(
                "battle_deploy",
                {
                    "request": {
                        "session_id": "s",
                        "operation_id": "o",
                        "unit_id": "u",
                        "location": [-1, 2],
                        "direction": "Left",
                    }
                },
            )
            assert invalid.isError
            missing_intel = await session.call_tool(
                "intel_query", {"kind": "stage", "identifier": "main_01-07"}
            )
            assert missing_intel.isError
            missing_core = await session.call_tool("battle_connect")
            assert missing_core.isError
            assert (await session.call_tool("battle_status")).structuredContent[
                "connected"
            ] is False


def test_unknown_outcome_is_not_an_mcp_success():
    result = tool_result({"status": "outcome_unknown", "helper_returned": True})
    assert result.isError is True
    assert result.structuredContent["helper_returned"] is True
