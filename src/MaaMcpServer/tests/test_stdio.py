import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest
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
            assert len(tools) == 20
            assert tools["battle_observe_preparation"].annotations.readOnlyHint is True
            assert tools["battle_collect_terminal"].annotations.readOnlyHint is True
            assert tools["battle_set_speed"].annotations.readOnlyHint is False
            assert tools["battle_evaluate_placement"].annotations.readOnlyHint is True
            step_schema = tools["battle_step"].inputSchema["$defs"][
                "BoundedStepRequest"
            ]
            assert "wait" in step_schema["properties"]["action"]["enum"]
            assert "unit_id" not in step_schema["required"]
            assert tools["battle_observe"].annotations.readOnlyHint is True
            assert tools["battle_identify"].annotations.readOnlyHint is False
            identify_schema = tools["battle_identify"].inputSchema
            identify_request = identify_schema["$defs"]["IdentifyRequest"]
            adoption = identify_request["properties"]["adopt_manual_pause"]
            assert adoption["type"] == "boolean"
            assert adoption["default"] is False
            assert "adopt_manual_pause" not in identify_request["required"]
            assert tools["battle_step"].annotations.readOnlyHint is False
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
            invalid_step = await session.call_tool(
                "battle_step",
                {
                    "request": {
                        "session_id": "s",
                        "operation_id": "o",
                        "action": "deploy",
                        "unit_id": "u",
                    }
                },
            )
            assert invalid_step.isError
            missing_intel = await session.call_tool(
                "intel_query", {"kind": "stage", "identifier": "main_01-07"}
            )
            assert missing_intel.isError
            with anyio.fail_after(15):
                missing_core = await session.call_tool("battle_connect")
            assert missing_core.isError
            assert "core_load" in missing_core.content[0].text
            assert "Worker deadline exceeded" not in missing_core.content[0].text
            assert "outcome unknown" not in missing_core.content[0].text
            assert (await session.call_tool("battle_status")).structuredContent[
                "connected"
            ] is False


def test_unknown_outcome_is_not_an_mcp_success():
    result = tool_result({"status": "outcome_unknown", "helper_returned": True})
    assert result.isError is True
    assert result.structuredContent["helper_returned"] is True


async def test_stdio_worker_lifecycle_with_sdk_client(tmp_path):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            str(Path(__file__).with_name("worker_fixture.py")),
            "server",
            "--core-dir",
            str(tmp_path / "core"),
            "--user-dir",
            str(tmp_path / "state"),
        ],
    )
    with anyio.fail_after(15):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                connected = await session.call_tool("battle_connect")
                assert not connected.isError
                assert connected.structuredContent["core_version"] == "fixture-only"
                status = (await session.call_tool("battle_status")).structuredContent
                assert status["core_version"] == "fixture-only"
                assert status["project_version"] == "0.1.0"
                attached = await session.call_tool(
                    "battle_attach", {"stage_name": "act55side_ex01"}
                )
                assert not attached.isError
                observed = await session.call_tool(
                    "battle_observe",
                    {"session_id": attached.structuredContent["session_id"]},
                )
                assert observed.structuredContent["snapshot"]["fixture"] is True
                assert not (await session.call_tool("battle_close")).isError
                assert not (await session.call_tool("battle_connect")).isError
                assert not (await session.call_tool("battle_close")).isError


async def test_stdio_worker_lifecycle_with_plain_popen(tmp_path):
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("worker_fixture.py")),
            "server",
            "--core-dir",
            str(tmp_path / "core"),
            "--user-dir",
            str(tmp_path / "state"),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    async def exchange(message):
        process.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
        process.stdin.flush()
        if "id" not in message:
            return None
        while True:
            line = await asyncio.wait_for(
                asyncio.to_thread(process.stdout.readline), 15
            )
            assert line, f"stdio server exited with {process.poll()}"
            response = json.loads(line)
            if response.get("id") == message["id"]:
                return response

    try:
        await exchange(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "raw-worker-test", "version": "1"},
                },
            }
        )
        await exchange({"method": "notifications/initialized"})
        connected = await exchange(
            {"id": 2, "method": "tools/call", "params": {"name": "battle_connect"}}
        )
        assert not connected["result"]["isError"]
        attached = await exchange(
            {
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "battle_attach",
                    "arguments": {"stage_name": "act55side_ex01"},
                },
            }
        )
        assert not attached["result"]["isError"]
        session_id = attached["result"]["structuredContent"]["session_id"]
        observed = await exchange(
            {
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "battle_observe",
                    "arguments": {"session_id": session_id},
                },
            }
        )
        assert observed["result"]["structuredContent"]["snapshot"]["fixture"]
        closed = await exchange(
            {"id": 5, "method": "tools/call", "params": {"name": "battle_close"}}
        )
        assert not closed["result"]["isError"]
    finally:
        process.stdin.close()
        try:
            await asyncio.to_thread(process.wait, 5)
        except subprocess.TimeoutExpired:
            process.kill()
            await asyncio.to_thread(process.wait, 5)
        process.stdout.close()


@pytest.mark.skipif(
    not (
        os.environ.get("MAA_MCP_NATIVE_CORE")
        and os.environ.get("MAA_MCP_LIVE_ADDRESS")
        and os.environ.get("MAA_MCP_LIVE_STAGE")
    ),
    reason="Requires explicit native Core, live device address and unique stage ID",
)
async def test_stdio_real_device_connect_attach_capture(tmp_path):
    core = os.environ["MAA_MCP_NATIVE_CORE"]
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "maa_battle_mcp.server",
            "--core-dir",
            core,
            "--resource-dir",
            os.environ.get("MAA_MCP_RESOURCE_DIR", core),
            "--user-dir",
            str(tmp_path / "live-state"),
            "--adb-path",
            os.environ.get("MAA_MCP_ADB_PATH", "adb"),
            "--address",
            os.environ["MAA_MCP_LIVE_ADDRESS"],
            "--connection-config",
            os.environ.get("MAA_MCP_CONNECTION_CONFIG", "General"),
            "--touch-mode",
            "adb",
        ],
    )
    with anyio.fail_after(120):
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                try:
                    connected = await session.call_tool("battle_connect")
                    assert not connected.isError, connected.content
                    attached = await session.call_tool(
                        "battle_attach",
                        {"stage_name": os.environ["MAA_MCP_LIVE_STAGE"]},
                    )
                    assert not attached.isError, attached.content
                    captured = await session.call_tool(
                        "battle_capture_evidence",
                        {"session_id": attached.structuredContent["session_id"]},
                    )
                    assert not captured.isError, captured.content
                    assert any(item.type == "image" for item in captured.content)
                finally:
                    await session.call_tool("battle_close")
