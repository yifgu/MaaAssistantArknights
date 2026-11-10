import json
import os
from pathlib import Path

import pytest

from maa_battle_mcp.models import Settings
from maa_battle_mcp.native import NativeBackend, NativeCore


@pytest.mark.skipif(
    not os.environ.get("MAA_MCP_NATIVE_CORE"),
    reason="Set MAA_MCP_NATIVE_CORE to a Debug Core installation for the native ABI smoke test",
)
def test_debug_core_task_protocol_without_device(tmp_path):
    core_dir = Path(os.environ["MAA_MCP_NATIVE_CORE"]).resolve()
    settings = Settings(
        core_dir=core_dir,
        resource_dir=Path(os.environ.get("MAA_MCP_RESOURCE_DIR", core_dir)).resolve(),
        user_dir=tmp_path,
        adb_path="adb",
        address="unused",
    )
    core = NativeCore(settings)
    try:
        ready = core.dispatch(
            "attach",
            {"session_id": "native-smoke", "stage_name": "main_01-07"},
            settings,
        )
        assert ready["protocol_version"] == 1
        assert ready["map"]["stage_id"] == "main_01-07"
        assert (
            len(ready["map"]["tiles"]) == ready["map"]["width"] * ready["map"]["height"]
        )
        command = {
            "session_id": "native-smoke",
            "command_id": "map-1",
            "action": "map",
            "args": {},
            "timeout_ms": 1000,
        }
        for invalid in (
            {**command, "timeout_ms": -1},
            {**command, "timeout_ms": 1.5},
            {**command, "timeout_ms": True},
            {**command, "timeout_ms": 30_001},
            {**command, "action": "step", "timeout_ms": 10_001},
            {
                **command,
                "action": "step",
                "args": {
                    "action": "deploy",
                    "unit_id": "unit",
                    "adopt_manual_pause": False,
                },
            },
            {**command, "session_id": "other"},
            {**command, "action": "click"},
            {**command, "action": "deploy", "args": {"unit_id": "x"}},
            {
                **command,
                "action": "deploy",
                "args": {"unit_id": "x", "location": [1.5, 2], "direction": "Left"},
            },
            {
                **command,
                "action": "deploy",
                "args": {"unit_id": "x", "location": [1, 2], "direction": "Diagonal"},
            },
        ):
            assert not core.lib.AsstSetTaskParams(
                core.handle, core.task_id, json.dumps(invalid).encode("utf-8")
            )
        result = core.dispatch("command", command, settings)
        assert result["status"] == "succeeded"
        assert result["command_id"] == "map-1"
        assert result["map"] == ready["map"]
    finally:
        core.close()


@pytest.mark.skipif(
    not os.environ.get("MAA_MCP_NATIVE_CORE"),
    reason="Set MAA_MCP_NATIVE_CORE to exercise the actual native worker",
)
def test_native_worker_process_protocol(tmp_path):
    core_dir = Path(os.environ["MAA_MCP_NATIVE_CORE"]).resolve()
    backend = NativeBackend(
        Settings(
            core_dir=core_dir,
            resource_dir=Path(
                os.environ.get("MAA_MCP_RESOURCE_DIR", core_dir)
            ).resolve(),
            user_dir=tmp_path,
            adb_path="adb",
            address="unused",
        )
    )
    try:
        ready = backend.request(
            "attach", {"session_id": "worker-smoke", "stage_name": "main_01-07"}, 30
        )
        assert ready["session_id"] == "worker-smoke"
        result = backend.request(
            "command",
            {
                "session_id": "worker-smoke",
                "command_id": "map-1",
                "action": "map",
                "args": {},
                "timeout_ms": 1000,
            },
            2,
        )
        assert result["map"] == ready["map"]
    finally:
        backend.close()
    assert backend.process.poll() is not None
