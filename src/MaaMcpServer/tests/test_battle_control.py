import asyncio
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from battle_fixture import MARKERS, first_pixel, prepare
from mcp.server.fastmcp.exceptions import ToolError

from maa_battle_mcp import native
from maa_battle_mcp.models import Settings
from maa_battle_mcp.server import create_server
from maa_battle_mcp.service import BattleService

pytestmark = pytest.mark.skipif(
    not os.environ.get("MAA_MCP_NATIVE_CORE"),
    reason="Requires actual Debug Core; all screenshots and device inputs are synthetic",
)


@pytest.fixture
async def battle(tmp_path, monkeypatch, request):
    core = Path(os.environ["MAA_MCP_NATIVE_CORE"]).resolve()
    settings = Settings(
        core_dir=core,
        resource_dir=Path(os.environ.get("MAA_MCP_RESOURCE_DIR", core)).resolve(),
        user_dir=tmp_path,
        adb_path=sys.executable,
        address="fixture",
        connection_config="BattleFixture",
        touch_mode="adb",
    )
    root = prepare(settings, known=getattr(request, "param", False))
    popen = subprocess.Popen

    def start_worker(_command, **kwargs):
        return popen(
            [
                sys.executable,
                "-B",
                "-u",
                str(Path(__file__).with_name("battle_fixture.py")),
                "worker",
            ],
            **kwargs,
        )

    monkeypatch.setattr(native.subprocess, "Popen", start_worker)
    service = BattleService(settings)
    try:
        await service.connect()
        await service.attach("main_01-07")
        yield service, create_server(service), root
    finally:
        await service.close()
        service.journal.close()


def events(root):
    return [
        json.loads(line)
        for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


async def identify(battle, operation_id, **kwargs):
    service, server, _ = battle
    return await server.call_tool(
        "battle_identify",
        {
            "request": {
                "session_id": service.session_id,
                "operation_id": operation_id,
                "timeout_ms": 30_000,
                **kwargs,
            }
        },
    )


async def test_cold_manual_pause_requires_authorization_and_never_resumes(battle):
    service, _, root = battle
    observed = await service.read(service.session_id, "capture")
    assert observed["snapshot"]["unresolved_count"] == 1
    denied = await identify(battle, "default")
    assert denied.isError
    assert denied.structuredContent["error"]["code"] == "pause_state_unknown"
    explicit_false = await identify(battle, "false", adopt_manual_pause=False)
    assert explicit_false.structuredContent["error"]["code"] == "pause_state_unknown"
    assert not any(e["action"] in ("click", "pause", "resume") for e in events(root))

    result = await identify(battle, "adopt", adopt_manual_pause=True)
    value = result.structuredContent
    assert not result.isError
    assert value["identification_status"] == "partial"
    assert value["unresolved_count"] == 1
    assert value["pause_verified"] is True
    assert value["safe_state"] == "paused"
    assert value["snapshot"]["pause_requested"] is True
    assert value["deployment"][0]["identity_reason"] == "name_ocr_failed"
    image = next(c for c in result.content if c.type == "image")
    assert base64.b64decode(image.data) == service.evidence_path(value).read_bytes()
    assert first_pixel(base64.b64decode(image.data)) == MARKERS["paused"]
    before = events(root)
    replay = await identify(battle, "adopt", adopt_manual_pause=True)
    assert replay.structuredContent == value
    assert events(root) == before
    with pytest.raises(ToolError, match="different parameters"):
        await identify(battle, "adopt", adopt_manual_pause=False)
    assert events(root) == before
    again = await identify(battle, "owned")
    assert again.structuredContent["pause_verified"] is True
    assert any(e["action"] == "click" and int(e["args"][1]) > 580 for e in events(root))
    assert not any(e["action"] in ("pause", "resume") for e in events(root))


async def test_unrecognized_screen_cannot_be_adopted(battle):
    _, _, root = battle
    (root / "mode").write_text("unknown", encoding="utf-8")
    result = await identify(battle, "unknown", adopt_manual_pause=True)
    assert result.isError
    assert result.structuredContent["error"]["code"] == "not_in_battle"
    assert not any(e["action"] in ("click", "pause", "resume") for e in events(root))
    (root / "mode").write_text("paused", encoding="utf-8")
    denied = await identify(battle, "not-owned")
    assert denied.structuredContent["error"]["code"] == "pause_state_unknown"


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_running_identification_still_pauses_without_resuming(battle):
    _, _, root = battle
    (root / "mode").write_text("running", encoding="utf-8")
    result = await identify(battle, "running")
    assert not result.isError
    assert result.structuredContent["identification_status"] == "complete"
    assert result.structuredContent["identified_count"] == 1
    assert result.structuredContent["pause_verified"] is True
    image = next(c for c in result.content if c.type == "image")
    assert first_pixel(base64.b64decode(image.data)) == MARKERS["paused"]
    assert len([e for e in events(root) if e["action"] == "pause"]) == 1
    assert not any(e["action"] == "resume" for e in events(root))


async def test_cancelled_adoption_does_not_resume(battle):
    service, _, root = battle
    session = service.session_id
    task = asyncio.create_task(identify(battle, "cancel", adopt_manual_pause=True))
    try:
        async with asyncio.timeout(30):
            while not any(e["action"] == "click" for e in events(root)):
                assert not task.done()
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.operation(session, "cancel")["status"] == "outcome_unknown"
        assert (root / "mode").read_text(encoding="utf-8") == "paused"
        assert not any(e["action"] in ("pause", "resume") for e in events(root))
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_step_preserves_acquisition_time_and_original_png_on_replay(battle):
    service, server, root = battle
    initial = await service.read(service.session_id, "capture")
    assert initial["snapshot"]["identified_count"] == 1
    unit = initial["snapshot"]["deployment"][0]["unit_id"]
    map_result = await service.read(service.session_id, "map")
    tile = next(t for t in map_result["map"]["tiles"] if t["buildable_type"] == 1)
    request = {
        "session_id": service.session_id,
        "operation_id": "step",
        "action": "deploy",
        "unit_id": unit,
        "location": tile["location"],
        "direction": "None",
        "adopt_manual_pause": True,
    }
    result = await server.call_tool("battle_step", {"request": request})
    value = result.structuredContent
    assert value["status"] == "outcome_unknown"
    assert value["action_returned"] is True
    assert value["pause_verified"] is True
    boundary = value["capture_time_snapshot"]
    returned = value["return_time_snapshot"]
    assert boundary["running_button_visible"] is True
    assert returned["running_button_visible"] is False
    assert boundary["captured_at_ms"] < returned["captured_at_ms"]
    # Analysis deliberately visits the return frame first, but acquisition order is unchanged.
    assert int(boundary["observation_id"]) > int(returned["observation_id"])
    pause_event = next(e for e in events(root) if e["action"] == "pause")
    assert (
        boundary["captured_at_ms"] <= pause_event["at_ms"] < returned["captured_at_ms"]
    )
    images = [base64.b64decode(c.data) for c in result.content if c.type == "image"]
    for key, snapshot, mode, image in (
        ("return_image_path", returned, "paused", images[0]),
        ("capture_image_path", boundary, "running", images[1]),
    ):
        assert Path(value[key]).stem == snapshot["observation_id"]
        assert service.evidence_path({"image_path": value[key]}).read_bytes() == image
        assert first_pixel(image) == MARKERS[mode]
    before = events(root)
    replay = await server.call_tool("battle_step", {"request": request})
    assert replay.structuredContent == value
    assert events(root) == before
    fresh = await service.read(service.session_id, "capture")
    assert fresh["snapshot"]["captured_at_ms"] > returned["captured_at_ms"]
    assert fresh["image_path"] not in (
        value["capture_image_path"],
        value["return_image_path"],
    )
