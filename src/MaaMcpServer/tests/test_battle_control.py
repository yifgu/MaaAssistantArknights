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
        allowed_summons=["token_10000_silent_healrb"]
        if getattr(request, "param", False) == "summon"
        else [],
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
        if getattr(request, "param", False) != "prepare":
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
    assert int(boundary["observation_id"]) < int(returned["observation_id"])
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


async def wait_step(battle, operation_id="wait", **kwargs):
    service, server, _ = battle
    request = {
        "session_id": service.session_id,
        "operation_id": operation_id,
        "action": "wait",
        "duration_ms": 600,
        "adopt_manual_pause": True,
        **kwargs,
    }
    return await server.call_tool("battle_step", {"request": request})


async def test_wait_pauses_and_replay_never_advances_again(battle):
    service, _, root = battle
    result = await wait_step(battle)
    value = result.structuredContent
    assert value["status"] == "succeeded"
    assert value["pause_verified"] is True
    assert value["terminal"]["terminal_state"] == "still_in_battle"
    assert value["requested_duration_ms"] == 600
    assert value["actual_observed_envelope_ms"] >= 600
    assert (
        first_pixel(
            service.evidence_path(
                {"image_path": value["return_image_path"]}
            ).read_bytes()
        )
        == MARKERS["paused"]
    )
    before = events(root)
    replay = await wait_step(battle)
    assert replay.structuredContent == value
    assert events(root) == before


@pytest.mark.parametrize("target", ["victory", "defeat", "unknown"])
async def test_wait_captures_terminal_or_unknown_without_menu_clicks(battle, target):
    service, _, root = battle
    (root / "transition.json").write_text(
        json.dumps({"remaining": 3, "target": target})
    )
    value = (await wait_step(battle, duration_ms=3_000)).structuredContent
    assert value["terminal"]["terminal_state"] == target
    assert value["pause_verified"] is False
    assert value["pause_not_applicable"] is (target != "unknown")
    assert value["status"] == (
        "succeeded" if target != "unknown" else "outcome_unknown"
    )
    assert not any(
        e["action"] in ("pause", "resume", "click") and e["mode"] == target
        for e in events(root)
    )
    if target != "unknown":
        image = service.evidence_path(
            {"image_path": value["terminal_evidence_path"]}
        ).read_bytes()
        assert first_pixel(image) == MARKERS[target]
    before = events(root)
    denied = await wait_step(battle, "after-end")
    assert denied.structuredContent["error"]["code"] == "battle_ended"
    assert not any(
        e["action"] in ("pause", "resume", "click") for e in events(root)[len(before) :]
    )


async def test_terminal_overlay_wins_over_battle_ui(battle):
    _, _, root = battle
    (root / "transition.json").write_text(
        json.dumps({"remaining": 3, "target": "victory_overlay"})
    )
    result = (
        await wait_step(battle, duration_ms=3_000, timeout_ms=20_000)
    ).structuredContent
    assert result["terminal"]["terminal_state"] == "victory"
    assert result["safe_state"] == "terminal"
    assert result["pause_verified"] is False
    assert result["session_input_blocked"] is True
    assert result["terminal_evidence_path"]
    assert not any(
        e["action"] in ("pause", "resume", "click") and e["mode"] == "victory_overlay"
        for e in events(root)
    )


async def test_intro_is_not_manual_pause(battle):
    service, _, root = battle
    (root / "mode").write_text("intro")
    observation = await service.read(service.session_id, "capture")
    assert observation["snapshot"]["in_battle"] is True
    assert observation["snapshot"]["scene"] == "transition"
    result = await identify(battle, "intro-adoption", adopt_manual_pause=True)
    assert result.isError
    assert not any(e["action"] in ("pause", "resume", "click") for e in events(root))


@pytest.mark.parametrize(
    "final_scene, verdict", [("victory", "victory"), ("conflict", "unknown")]
)
async def test_read_only_terminal_updates_never_unlock(battle, final_scene, verdict):
    service, server, root = battle
    await service.read(service.session_id, "observe")
    (root / "mode").write_text("unknown")
    first = await service.read(service.session_id, "capture")
    assert first["snapshot"]["session_input_blocked"] is True
    (root / "mode").write_text(final_scene)
    result = await server.call_tool(
        "battle_collect_terminal",
        {
            "request": {
                "session_id": service.session_id,
                "operation_id": "finalize",
                "duration_ms": 100,
            }
        },
    )
    assert result.structuredContent["terminal"]["terminal_state"] == verdict
    assert result.structuredContent["session_input_blocked"] is True
    if final_scene == "conflict":
        (root / "mode").write_text("victory")
        observed = await service.read(service.session_id, "observe")
        assert observed["snapshot"]["session_terminal"]["terminal_state"] == "unknown"
    denied = await identify(battle, "after-end", adopt_manual_pause=True)
    assert denied.structuredContent["error"]["code"] == "battle_ended"
    assert not any(e["action"] in ("pause", "resume", "click") for e in events(root))


async def test_speed_is_idempotent_and_preserves_manual_pause(battle):
    service, server, root = battle
    for operation, speed in enumerate((2, 2, 1)):
        request = {
            "session_id": service.session_id,
            "operation_id": f"speed-{operation}",
            "multiplier": speed,
        }
        result = await server.call_tool("battle_set_speed", {"request": request})
        value = result.structuredContent
        assert value["status"] == "succeeded"
        assert value["snapshot"]["speed"]["value"] == speed
        assert value["snapshot"]["pause_requested"] is False
        before = events(root)
        replay = await server.call_tool("battle_set_speed", {"request": request})
        assert replay.structuredContent == value
        assert events(root) == before
    assert len([e for e in events(root) if e["action"] == "speed"]) == 2
    assert not any(e["action"] in ("pause", "resume") for e in events(root))
    denied = await identify(battle, "still-manual")
    assert denied.structuredContent["error"]["code"] == "pause_state_unknown"


@pytest.mark.parametrize(
    "scene", ["intro", "unknown", "victory", "running", "paused_unknown_speed"]
)
async def test_speed_rejects_nonpaused_scenes(battle, scene):
    service, server, root = battle
    (root / "mode").write_text(scene)
    result = await server.call_tool(
        "battle_set_speed",
        {
            "request": {
                "session_id": service.session_id,
                "operation_id": "speed",
                "multiplier": 2,
            }
        },
    )
    assert result.isError
    assert not any(
        e["action"] in ("speed", "pause", "resume", "click") for e in events(root)
    )


@pytest.mark.parametrize("fault", ["ignore-speed", "speed-resumes"])
async def test_speed_readback_failure_blocks_input_and_cleans_up(battle, fault):
    service, server, root = battle
    (root / fault).touch()
    value = (
        await server.call_tool(
            "battle_set_speed",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "fault",
                    "multiplier": 2,
                }
            },
        )
    ).structuredContent
    assert value["status"] == "outcome_unknown"
    assert value["session_input_blocked"] is True
    assert value["snapshot"]["session_input_blocked"] is True
    assert value["pause_verified"] is True
    assert value["unexpected_resume"] is (fault == "speed-resumes")
    assert (root / "mode").read_text() == "paused"
    assert not any(e["action"] == "resume" for e in events(root))


@pytest.mark.parametrize("battle", ["prepare"], indirect=True)
async def test_preparation_rejects_wrong_page_and_reuses_one_controller(battle):
    service, server, root = battle
    result = await server.call_tool(
        "battle_observe_preparation", {"stage_name": "main_01-07", "mode": "normal"}
    )
    assert result.isError
    assert result.structuredContent["error"]["code"] == "preparation_not_recognized"
    assert service.preparation_snapshot is None
    backend = service.backend
    await service.attach("main_01-07")
    assert service.backend is backend
    assert (await service.read(service.session_id, "prepare"))["error"][
        "code"
    ] == "already_attached"
    assert (await service.read(service.session_id, "observe"))["snapshot"][
        "scene"
    ] == "paused"
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


@pytest.mark.parametrize(
    "battle, expected",
    [("summon", "stale_card"), ("summon_unapproved", "unsupported_unit")],
    indirect=["battle"],
)
async def test_summon_preflight_never_adopts_an_unauthorized_manual_pause(
    battle, expected
):
    service, server, root = battle
    card = (await service.read(service.session_id, "capture"))["snapshot"][
        "deployment"
    ][0]
    value = (
        await server.call_tool(
            "battle_step",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "reject",
                    "action": "deploy",
                    "unit_id": card["unit_id"],
                    "card_id": "stale",
                    "location": [1, 1],
                    "direction": "None",
                }
            },
        )
    ).structuredContent
    assert value["error"]["code"] == expected
    assert value["pause_verified"] is True
    assert value["snapshot"]["pause_requested"] is False
    denied = await identify(battle, "still-unowned")
    assert denied.structuredContent["error"]["code"] == "pause_state_unknown"
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


@pytest.mark.parametrize("battle", ["summon"], indirect=True)
async def test_summon_instances_and_replay_are_separate(battle):
    service, server, root = battle
    tiles = [
        t
        for t in (await service.read(service.session_id, "map"))["map"]["tiles"]
        if t["buildable_type"] == 1
    ]
    instances = []
    for number in range(2):
        snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
        card = snapshot["deployment"][0]
        assert card["identity_reason"] == "instance_tracking_required"
        request = {
            "session_id": service.session_id,
            "operation_id": f"summon-{number}",
            "action": "deploy",
            "unit_id": card["unit_id"],
            "card_id": card["card_id"],
            "location": tiles[number]["location"],
            "direction": "None",
            "adopt_manual_pause": True,
            "timeout_ms": 30_000,
        }
        result = (
            await server.call_tool("battle_step", {"request": request})
        ).structuredContent
        assert result["action_returned"] is True
        assert result["pause_verified"] is True
        instances = result["return_time_snapshot"]["battlefield"]
        assert len(instances) == number + 1
        before = events(root)
        replay = await server.call_tool("battle_step", {"request": request})
        assert replay.structuredContent == result
        assert events(root) == before
    assert len({instance["unit_id"] for instance in instances}) == 2
    denied = await server.call_tool(
        "battle_step",
        {
            "request": {
                "session_id": service.session_id,
                "operation_id": "unsupported",
                "action": "skill",
                "unit_id": instances[0]["unit_id"],
            }
        },
    )
    assert denied.structuredContent["error"]["code"] == "unsupported_instance_action"


async def test_wait_cancel_reaches_core_and_preserves_pause(battle):
    service, _, root = battle
    task = asyncio.create_task(wait_step(battle, duration_ms=8_000))
    try:
        async with asyncio.timeout(30):
            while not any(e["action"] == "resume" for e in events(root)):
                assert not task.done()
                await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        result = service.operation(service.session_id, "wait")
        assert result["status"] == "failed"
        assert result["error"]["code"] == "cancelled"
        assert result["pause_verified"] is True
        assert result["actual_observed_envelope_ms"] < 8_000
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_wait_requires_manual_pause_authorization(battle):
    _, _, root = battle
    value = (await wait_step(battle, adopt_manual_pause=False)).structuredContent
    assert value["error"]["code"] == "pause_state_unknown"
    assert value["pause_verified"] is True
    assert not any(e["action"] in ("pause", "resume", "click") for e in events(root))


async def test_unrecognized_transition_is_not_success_even_if_pause_returns(battle):
    _, _, root = battle
    (root / "transition.json").write_text(
        json.dumps({"remaining": 3, "target": "unknown"})
    )
    (root / "return-after-unknown").touch()
    value = (await wait_step(battle, duration_ms=3_000)).structuredContent
    assert value["status"] == "outcome_unknown"
    assert value["pause_verified"] is True
    assert value["session_input_blocked"] is True
    assert value["warnings"]


async def test_external_pause_interrupts_wait_without_resuming_again(battle):
    _, _, root = battle
    (root / "transition.json").write_text(
        json.dumps({"remaining": 3, "target": "paused"})
    )
    value = (await wait_step(battle, duration_ms=3_000)).structuredContent
    assert value["status"] == "failed"
    assert value["error"]["code"] == "wait_interrupted"
    assert value["pause_verified"] is True
    assert sum(e["action"] == "resume" for e in events(root)) == 1


async def test_wait_deadline_failure_still_returns_paused_evidence(battle):
    _, _, root = battle
    (root / "capture-delay").write_text("0.7")
    value = (
        await wait_step(battle, duration_ms=100, timeout_ms=1_100)
    ).structuredContent
    assert value["status"] == "failed"
    assert value["error"]["code"] == "deadline_exceeded"
    assert value["pause_verified"] is True
    assert value["return_image_path"]


async def test_evidence_write_failure_is_not_wait_success(battle):
    service, _, _ = battle
    directory = service.settings.user_dir / "battle-control" / service.session_id
    directory.parent.mkdir(parents=True, exist_ok=True)
    if directory.exists():
        directory.rename(directory.with_name("evidence-before-failure"))
    directory.write_text("simulate unavailable screenshot directory")
    result = (await wait_step(battle)).structuredContent
    assert result["status"] != "succeeded"
    assert result["evidence_errors"]
    assert result["pause_verified"] is True


async def test_full_eight_second_wait_uses_explicit_cleanup_budget(battle):
    result = (
        await wait_step(battle, duration_ms=8_000, timeout_ms=30_000)
    ).structuredContent
    assert result["status"] == "succeeded"
    assert result["pause_verified"] is True
    assert result["resumed_interval_upper_bound_ms"] >= 8_000
    assert result["deadline_margin_ms"] > 0


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_pause_roundtrip_reports_current_ownership(battle):
    service, server, _ = battle
    await identify(battle, "adopt", adopt_manual_pause=True)
    resumed = (
        await server.call_tool(
            "battle_set_paused",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "resume",
                    "paused": False,
                }
            },
        )
    ).structuredContent
    assert resumed["status"] == "succeeded"
    assert resumed["snapshot"]["scene"] == "running"
    assert resumed["snapshot"]["pause_requested"] is False
    paused = (
        await server.call_tool(
            "battle_set_paused",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "pause",
                    "paused": True,
                }
            },
        )
    ).structuredContent
    assert paused["status"] == "succeeded"
    assert paused["snapshot"]["pause_requested"] is True
    assert paused["pause_verified"] is True


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_identification_evidence_failure_is_not_success(battle):
    service, _, root = battle
    directory = service.settings.user_dir / "battle-control" / service.session_id
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text("simulate unavailable screenshot directory")
    value = (
        await identify(battle, "evidence-failure", adopt_manual_pause=True)
    ).structuredContent
    assert value["status"] == "outcome_unknown"
    assert value["evidence_error"]["code"] == "evidence_write_failed"
    assert value["pause_verified"] is True
    assert not any(e["action"] in ("pause", "resume") for e in events(root))


async def test_close_cancels_native_wait_before_releasing_control(battle):
    service, _, root = battle
    session = service.session_id
    waiting = asyncio.create_task(wait_step(battle, duration_ms=8_000))
    try:
        async with asyncio.timeout(30):
            while not any(e["action"] == "resume" for e in events(root)):
                assert not waiting.done()
                await asyncio.sleep(0.01)
        assert (await service.close())["status"] == "closed"
        result = (await waiting).structuredContent
        assert result["error"]["code"] == "cancelled"
        assert result["pause_verified"] is True
        assert service.operation(session, "wait")["status"] == "failed"
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_placement_tool_is_read_only_and_loadout_remains_unknown(battle):
    service, server, root = battle
    snapshot = (await service.read(service.session_id, "observe"))["snapshot"]
    unit = snapshot["deployment"][0]
    context = unit["decision_context"]
    assert context["operator_game_id"] == "char_123_fang"
    assert context["loadout"]["actual_elite"]["value"] is None
    tile = next(t for t in service.map_info["tiles"] if t["buildable_type"] == 1)
    before = events(root)
    result = await server.call_tool(
        "battle_evaluate_placement",
        {
            "session_id": service.session_id,
            "placements": [
                {
                    "operator_game_id": "char_123_fang",
                    "location": tile["location"],
                    "direction": "Right",
                    "assumed_elite": 1,
                }
            ],
        },
    )
    assert not result.isError
    assert (
        result.structuredContent["placements"][0]["static_buildability_compatible"]
        is True
    )
    assert events(root) == before
