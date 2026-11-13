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
from maa_battle_mcp.service import BattleError, BattleService

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
        variant = getattr(request, "param", False)
        if variant != "prepare" and not (
            isinstance(variant, str) and variant.startswith("preparation:")
        ):
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


async def inspect_preparation(battle, operation_id="inspect", **kwargs):
    _, server, _ = battle
    return await server.call_tool(
        "battle_inspect_preparation",
        {
            "request": {
                "operation_id": operation_id,
                "stage_name": "main_01-07",
                "mode": "normal",
                **kwargs,
            }
        },
    )


@pytest.mark.parametrize("battle", ["preparation:complete"], indirect=True)
async def test_batch_preparation_keeps_ordinary_observation_input_free(battle):
    service, server, root = battle
    result = await server.call_tool(
        "battle_observe_preparation", {"stage_name": "main_01-07", "mode": "normal"}
    )
    value = result.structuredContent
    assert not result.isError, value
    assert [o["name"] for o in value["snapshot"]["operators"]] == ["Fang", "Texas"]
    for oper in value["snapshot"]["operators"]:
        assert oper["equipped_skill_id"] is None
        assert oper["actual_elite"] is None
        assert oper["actual_level"] is None
    assert not any(e["action"] in ("click", "swipe") for e in events(root))
    backend = service.backend
    await service.attach("main_01-07", "normal", value["preparation_id"], True)
    assert service.backend is backend
    assert service.decision.preparation == value
    assert service.preparation_snapshot is None


@pytest.mark.parametrize("battle", ["preparation:complete"], indirect=True)
async def test_batch_preparation_reads_s3_and_restores_without_selection(battle):
    service, _, root = battle
    result = await inspect_preparation(battle)
    value = result.structuredContent
    assert not result.isError, value
    assert value["snapshot"]["scan_status"] == "complete", value
    assert value["formation_restored"] is True
    assert value["roster_unchanged"] is True
    assert value["preparation_valid"] is True
    assert [o["name"] for o in value["snapshot"]["operators"]] == ["Fang", "Texas"]
    for oper in value["snapshot"]["operators"]:
        assert oper["selected_skill"] == 3
        assert oper["equipped_skill_id"] == "fixture_s3"
        assert oper["actual_elite"] == 2
        assert oper["actual_level"] == 50
        selected = oper["fields"]["selected_skill"]
        assert (
            selected["evidence"]["captured_at_ms"]
            >= value["snapshot"]["captured_at_ms"]
        )
        assert len(selected["row_order_frames"]) == 3
        assert service.evidence_path(selected["evidence"]).is_file()
    before = events(root)
    # Only formation portraits, the skill tab, and the visually recognized back button.
    clicks = [list(map(int, e["args"])) for e in before if e["action"] == "click"]
    assert len(clicks) == 6
    assert all(y < 365 for _, y in clicks)
    assert not any(e["action"] in ("pause", "resume", "speed") for e in before)
    assert (root / "mode").read_text() == "formation"
    replay = await inspect_preparation(battle)
    assert replay.structuredContent == value
    assert events(root) == before
    with pytest.raises(ToolError, match="different parameters"):
        await inspect_preparation(battle, mode="challenge")
    await service.invalidate_preparation()
    await inspect_preparation(battle)
    assert service.preparation_snapshot is None


@pytest.mark.parametrize(
    "battle, selected",
    [
        ("preparation:s1", 1),
        ("preparation:s2", 2),
        ("preparation:one_skill", 1),
        ("preparation:two_skills", 2),
    ],
    indirect=["battle"],
)
async def test_batch_preparation_reads_selected_slot_with_variable_skill_count(
    battle, selected
):
    _, _, root = battle
    result = await inspect_preparation(battle)
    value = result.structuredContent
    assert not result.isError, value
    assert value["snapshot"]["scan_status"] == "complete", value
    for oper in value["snapshot"]["operators"]:
        assert oper["selected_skill"] == selected
        assert oper["equipped_skill_id"] == f"fixture_s{selected}"
    assert value["formation_restored"] is True
    assert (root / "mode").read_text() == "formation"
    assert not any(
        e["action"] == "click"
        and e["mode"].startswith("detail-")
        and int(e["args"][1]) >= 365
        for e in events(root)
    )


@pytest.mark.parametrize(
    "battle",
    [
        "preparation:no_marker",
        "preparation:multiple",
        "preparation:stuck",
        "preparation:unknown_elite",
        "preparation:unknown_level",
        "preparation:ambiguous",
        "preparation:unreadable",
        "preparation:support",
        "preparation:empty",
    ],
    indirect=True,
)
async def test_batch_preparation_preserves_unknowns_and_unsupported_slots(battle):
    _, _, root = battle
    result = await inspect_preparation(battle)
    value = result.structuredContent
    assert not result.isError, value
    assert value["snapshot"]["scan_status"] == "partial", value
    assert value["formation_restored"] is True
    variant = (root / "preparation_variant").read_text()
    opers = value["snapshot"]["operators"]
    if variant in ("no_marker", "multiple", "stuck"):
        assert all(o["selected_skill"] is None for o in opers)
        assert all(o["equipped_skill_id"] is None for o in opers)
    elif variant == "unknown_elite":
        assert all(o["actual_elite"] is None for o in opers)
    elif variant == "unknown_level":
        assert all(o["actual_level"] is None for o in opers)
    elif variant in ("ambiguous", "unreadable"):
        assert opers[0]["operator_game_id"] is None
        assert opers[0]["selected_skill"] is None
        portraits = [
            e
            for e in events(root)
            if e["action"] == "click" and 80 < int(e["args"][1]) < 270
        ]
        assert len(portraits) == 1
    elif variant == "support":
        assert opers[-1]["support"] is True
        assert opers[-1]["selected_skill"] is None
    else:
        assert opers == []


@pytest.mark.parametrize(
    "battle",
    ["preparation:changed", "preparation:lost_page", "preparation:detail_mismatch"],
    indirect=True,
)
async def test_batch_preparation_stops_on_page_or_roster_change(battle):
    service, _, root = battle
    result = await inspect_preparation(battle)
    value = result.structuredContent
    assert result.isError
    assert value["preparation_valid"] is False
    assert service.preparation_snapshot is None
    portraits = [
        e
        for e in events(root)
        if e["action"] == "click" and 80 < int(e["args"][1]) < 270
    ]
    assert len(portraits) == 1
    if (root / "preparation_variant").read_text() == "detail_mismatch":
        assert value["formation_restored"] is False
        assert value["roster_unchanged"] is False
        assert len([e for e in events(root) if e["action"] == "click"]) == 1
    with pytest.raises(BattleError, match="stale"):
        await service.attach("main_01-07", "normal", value["preparation_id"], True)


@pytest.mark.parametrize("battle", ["preparation:complete"], indirect=True)
@pytest.mark.parametrize("close", [False, True])
async def test_batch_preparation_cancel_cleans_up_and_persists_result(battle, close):
    service, _, root = battle
    task = asyncio.create_task(inspect_preparation(battle))
    try:
        async with asyncio.timeout(30):
            while not any(e["action"] == "click" for e in events(root)):
                assert not task.done()
                await asyncio.sleep(0.01)
        session = service.preparation_session_id
        if close:
            await service.close()
            await task
        else:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        result = service.operation(session, "inspect")
        assert result["status"] == "failed"
        assert result["error"]["code"] == "cancelled"
        assert result["formation_restored"] is True
        assert (root / "mode").read_text() == "formation"
        assert (service.backend is None) is close
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("battle", ["preparation:complete"], indirect=True)
async def test_batch_preparation_short_deadline_sends_no_navigation(battle):
    _, _, root = battle
    result = await inspect_preparation(battle, timeout_ms=1000)
    value = result.structuredContent
    assert result.isError
    assert value["error"]["code"] == "deadline_exceeded"
    assert value["formation_restored"] is True
    assert not any(e["action"] in ("click", "swipe") for e in events(root))


@pytest.mark.parametrize("battle", ["preparation:full_roster"], indirect=True)
async def test_batch_preparation_reads_twelve_members_in_one_operation(battle):
    service, _, root = battle
    result = await inspect_preparation(battle, timeout_ms=180_000)
    value = result.structuredContent
    assert not result.isError, value
    assert value["snapshot"]["scan_status"] == "complete", value
    assert len(value["snapshot"]["operators"]) == 12
    assert all(o["selected_skill"] == 3 for o in value["snapshot"]["operators"])
    assert service.preparation_snapshot == value
    assert (root / "mode").read_text() == "formation"
    assert not any(
        e["action"] == "click"
        and e["mode"].startswith("detail-")
        and int(e["args"][1]) >= 365
        for e in events(root)
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


@pytest.mark.parametrize("battle", ["terminal", "terminal_ocr"], indirect=True)
async def test_split_victory_survives_black_frame_and_keeps_input_quarantined(battle):
    service, server, root = battle
    (root / "transition.json").write_text(
        json.dumps({"remaining": 3, "target": "split_victory"})
    )
    request = {
        "session_id": service.session_id,
        "operation_id": "split-title",
        "action": "wait",
        "duration_ms": 3_000,
        "timeout_ms": 20_000,
        "adopt_manual_pause": True,
    }
    value = (
        await server.call_tool("battle_step", {"request": request})
    ).structuredContent
    assert value["status"] == "succeeded"
    assert value["safe_state"] == "terminal"
    assert value["pause_verified"] is False
    terminal = value["terminal"]
    assert terminal["terminal_state"] == "victory"
    assert terminal["result_source"] == "BattleControlVictorySplit"
    split = terminal["split_victory"]
    assert split["accepted"] is True
    assert split["rejection_reason"] is None
    assert all(fragment["score"] >= 0.9 for fragment in split["fragments"])
    assert terminal["terminal_confidence"] == min(
        fragment["score"] for fragment in split["fragments"]
    )
    assert (
        first_pixel(
            service.evidence_path(
                {"image_path": value["terminal_evidence_path"]}
            ).read_bytes()
        )
        == MARKERS["split_victory"]
    )
    before = events(root)
    replay = await server.call_tool("battle_step", {"request": request})
    assert replay.structuredContent == value
    assert events(root) == before

    for mode in ("unknown", "paused", "paused"):
        (root / "mode").write_text(mode)
        snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
        assert snapshot["session_terminal"] == terminal
        assert snapshot["input_block_reason"] == "terminal_or_safety_quarantine"
    denied = await wait_step(battle, "after-split-title")
    assert denied.structuredContent["error"]["code"] == "battle_ended"
    recorded = events(root)
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe")
        for e in recorded
        if e["mode"] == "split_victory"
    )
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe")
        for e in recorded[len(before) :]
    )


@pytest.mark.parametrize("battle", ["terminal"], indirect=True)
@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("split_missing_mission", "missing_fragment"),
        ("split_missing_accomplished", "missing_fragment"),
        ("split_reversed", "unexpected_layout"),
        ("split_distant", "unexpected_layout"),
        ("split_unaligned", "unexpected_layout"),
        ("split_low_score", "low_confidence"),
    ],
)
async def test_split_victory_rejects_incomplete_weak_or_unrelated_fragments(
    battle, mode, reason
):
    service, _, root = battle
    await service.read(service.session_id, "observe")
    (root / "mode").write_text(mode)
    terminal = (await service.read(service.session_id, "capture"))["snapshot"][
        "terminal"
    ]
    assert terminal["terminal_state"] == "still_in_battle"
    assert terminal["split_victory"]["accepted"] is False
    assert terminal["split_victory"]["rejection_reason"] == reason
    if reason == "low_confidence":
        assert 0.5 <= terminal["split_victory"]["score"] < 0.9
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


@pytest.mark.parametrize("battle", ["terminal"], indirect=True)
async def test_split_victory_needs_battle_context_and_never_joins_frames(battle):
    service, _, root = battle
    (root / "mode").write_text("split_victory")
    cold = (await service.read(service.session_id, "capture"))["snapshot"]
    assert cold["terminal"]["terminal_state"] == "still_in_battle"
    (root / "mode").write_text("paused")
    await service.read(service.session_id, "observe")
    for mode in (
        "split_missing_accomplished",
        "split_missing_mission",
        "split_running",
        "split_menu",
    ):
        (root / "mode").write_text(mode)
        snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
        assert snapshot["terminal"]["terminal_state"] not in ("victory", "defeat")
        assert snapshot["session_terminal"]["terminal_state"] == "unknown"
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


@pytest.mark.parametrize("battle", ["terminal"], indirect=True)
@pytest.mark.parametrize("mode", ["split_conflict", "split_and_full"])
async def test_split_victory_conflicts_by_verdict_not_by_number_of_matches(
    battle, mode
):
    service, _, root = battle
    await service.read(service.session_id, "observe")
    (root / "mode").write_text(mode)
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    terminal = snapshot["terminal"]
    assert terminal["split_victory"]["accepted"] is True
    assert terminal["terminal_state"] == (
        "unknown" if mode == "split_conflict" else "victory"
    )
    if mode == "split_conflict":
        assert terminal["result_source"] == "conflicting_titles"
        assert terminal["terminal_confidence"] is None
        (root / "mode").write_text("split_victory")
        later = (await service.read(service.session_id, "capture"))["snapshot"]
        assert later["session_terminal"]["result_source"] == "conflicting_titles"
    assert snapshot["session_input_blocked"] is True
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


@pytest.mark.parametrize("battle", ["terminal_ocr"], indirect=True)
@pytest.mark.parametrize("mode", ["split_tutorial", "split_results"])
async def test_split_victory_uses_exact_ocr_words_not_tutorial_or_result_text(
    battle, mode
):
    service, _, root = battle
    await service.read(service.session_id, "observe")
    (root / "mode").write_text(mode)
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["terminal"]["terminal_state"] == "still_in_battle"
    assert snapshot["terminal"]["split_victory"]["accepted"] is False
    assert snapshot["session_terminal"]["terminal_state"] == "unknown"
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
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


@pytest.mark.parametrize("gap", ["intro", "running"])
async def test_pause_uncertainty_requires_consecutive_positive_captures(battle, gap):
    service, _, root = battle
    for mode in ("paused", "running", "intro"):
        (root / "mode").write_text(mode)
        snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["scene"] == "transition"
    assert snapshot["in_battle"] is True
    assert snapshot["pause_evidence"] is None
    assert snapshot["session_input_blocked"] is True
    assert snapshot["input_block_reason"] == "pause_verification_pending"
    diagnostic = snapshot["pause_diagnostics"]
    assert diagnostic["observation_id"] == snapshot["observation_id"]
    assert diagnostic["captured_at_ms"] == snapshot["captured_at_ms"]
    assert diagnostic["match"] is None
    assert diagnostic["candidates"] is None  # The fixture uses templates, not OCR.
    assert diagnostic["roi"] == [200, 180, 880, 360]

    for mode in ("paused", gap, "paused"):
        (root / "mode").write_text(mode)
        snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
        assert snapshot["session_input_blocked"] is True
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["scene"] == "paused"
    assert snapshot["session_input_blocked"] is False
    assert snapshot["input_block_reason"] is None
    assert snapshot["pause_requested"] is False
    denied = await identify(battle, "still-manual")
    assert denied.structuredContent["error"]["code"] == "pause_state_unknown"
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


async def test_mutation_that_observes_pause_recovery_is_not_executed(battle):
    service, _, root = battle
    for mode in ("paused", "intro", "paused"):
        (root / "mode").write_text(mode)
        await service.read(service.session_id, "observe")
    denied = await identify(battle, "recovery-is-not-adoption", adopt_manual_pause=True)
    value = denied.structuredContent
    assert value["error"]["code"] == "pause_unverified"
    assert value["input_sent"] is False
    assert value["snapshot"]["session_input_blocked"] is False
    assert value["snapshot"]["pause_requested"] is False
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )
    recovered = await identify(battle, "explicit-new-adoption", adopt_manual_pause=True)
    assert recovered.structuredContent["pause_verified"] is True


@pytest.mark.parametrize("battle", ["ocr_pause"], indirect=True)
async def test_failed_ocr_pause_keeps_diagnostics_without_positive_evidence(battle):
    service, _, root = battle
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["scene"] == "transition"
    assert snapshot["pause_evidence"] is None
    diagnostic = snapshot["pause_diagnostics"]
    assert diagnostic["match"] is None
    assert diagnostic["full_match"] is True
    assert (
        diagnostic["candidate_source"] == "same_frame_ocr_without_required_text_filter"
    )
    assert isinstance(diagnostic["candidates"], list)
    assert all(
        {"text", "score", "rect"} <= candidate.keys()
        for candidate in diagnostic["candidates"]
    )
    denied = await identify(battle, "ocr-is-not-pause", adopt_manual_pause=True)
    assert denied.isError
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


@pytest.mark.parametrize("battle", ["localized_ocr"], indirect=True)
@pytest.mark.parametrize(
    ("mode", "source", "text"),
    [
        ("paused_english", "BattleControlPaused", "PAUSE"),
        ("paused_chinese", "BattleControlPausedLocalized", "暂停中"),
        ("paused_bilingual", "BattleControlPaused", "PAUSE"),
    ],
)
async def test_exact_pause_markers_are_alternatives_not_joint_requirements(
    battle, mode, source, text
):
    service, _, root = battle
    (root / "mode").write_text(mode)
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["scene"] == "paused"
    assert snapshot["pause_evidence"]["source"] == source
    assert snapshot["pause_evidence"]["text"] == text
    assert snapshot["pause_evidence"]["score"] >= 0.9
    assert snapshot["pause_requested"] is False
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


@pytest.mark.parametrize("battle", ["localized_ocr"], indirect=True)
async def test_localized_pause_uses_production_ocr_and_preserves_recovery_contract(
    battle,
):
    service, server, root = battle
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["scene"] == "paused"
    evidence = snapshot["pause_evidence"]
    assert evidence["source"] == "BattleControlPausedLocalized"
    assert evidence["text"] == "暂停中"
    assert evidence["score"] >= 0.9
    x, y, width, height = evidence["rect"]
    assert 560 <= x < x + width <= 720
    assert 350 <= y < y + height <= 420
    assert snapshot["pause_requested"] is False
    assert (await identify(battle, "not-adopted")).structuredContent["error"][
        "code"
    ] == "pause_state_unknown"

    (root / "mode").write_text("pausex_only")
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["input_block_reason"] == "pause_verification_pending"
    assert snapshot["pause_evidence"] is None
    assert "PAUSEX" in {
        item["text"] for item in snapshot["pause_diagnostics"]["candidates"]
    }
    assert snapshot["pause_diagnostics"]["localized"]["match"] is None

    (root / "mode").write_text("paused_localized")
    snapshot = (await service.read(service.session_id, "observe"))["snapshot"]
    assert snapshot["session_input_blocked"] is True
    value = (
        await server.call_tool(
            "battle_collect_terminal",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "localized-readonly-recovery",
                    "duration_ms": 1000,
                }
            },
        )
    ).structuredContent
    assert value["pause_verified"] is True
    assert value["session_input_blocked"] is False
    assert value["input_sent"] is False
    assert value["snapshot"]["pause_requested"] is False
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )
    adopted = (
        await identify(battle, "adopt-localized", adopt_manual_pause=True)
    ).structuredContent
    assert adopted["pause_verified"] is True
    assert adopted["snapshot"]["pause_requested"] is True
    assert not any(e["action"] in ("pause", "resume") for e in events(root))


@pytest.mark.parametrize("battle", ["localized_ocr"], indirect=True)
@pytest.mark.parametrize(
    ("mode", "scene"),
    [
        ("misplaced_localized", "transition"),
        ("suffixed_localized", "transition"),
        ("running_localized", "running"),
        ("victory_localized", "terminal"),
    ],
)
async def test_localized_pause_rejects_wrong_position_text_and_scene(
    battle, mode, scene
):
    service, server, root = battle
    (root / "mode").write_text(mode)
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["scene"] == scene
    assert snapshot["pause_evidence"] is None
    if scene == "transition":
        diagnostic = snapshot["pause_diagnostics"]["localized"]
        assert diagnostic["source"] == "BattleControlPausedLocalized"
        assert diagnostic["roi"] == [560, 350, 160, 70]
        assert diagnostic["full_match"] is True
        assert diagnostic["required_score"] == 0.9
        assert diagnostic["observation_id"] == snapshot["observation_id"]
        assert diagnostic["captured_at_ms"] == snapshot["captured_at_ms"]
    rejected = await server.call_tool(
        "battle_set_speed",
        {
            "request": {
                "session_id": service.session_id,
                "operation_id": "not-a-paused-scene",
                "multiplier": 2,
            }
        },
    )
    assert rejected.isError
    assert not any(
        e["action"] in ("speed", "pause", "resume", "click", "swipe")
        for e in events(root)
    )
    if scene == "terminal":
        (root / "mode").write_text("paused_localized")
        for _ in range(2):
            recovered = (await service.read(service.session_id, "capture"))["snapshot"]
            assert (
                recovered["pause_evidence"]["source"] == "BattleControlPausedLocalized"
            )
            assert recovered["input_block_reason"] == "terminal_or_safety_quarantine"


@pytest.mark.parametrize("battle", ["localized_split_ocr"], indirect=True)
async def test_split_pause_production_ocr_recovers_without_replaying_or_adopting(
    battle,
):
    service, server, root = battle
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["scene"] == "paused"
    evidence = snapshot["pause_evidence"]
    assert evidence["source"] == "BattleControlPausedLocalizedSplit"
    assert evidence["accepted"] is True
    assert evidence["rejection_reason"] is None
    assert evidence["text"] == "暂停中"
    assert [fragment["text"] for fragment in evidence["fragments"]] == ["暂停", "中"]
    assert evidence["score"] == min(
        fragment["score"] for fragment in evidence["fragments"]
    )
    assert evidence["score"] >= 0.9
    assert evidence["observation_id"] == snapshot["observation_id"]
    assert evidence["captured_at_ms"] == snapshot["captured_at_ms"]
    assert (await identify(battle, "split-not-adopted")).structuredContent["error"][
        "code"
    ] == "pause_state_unknown"

    for mode in (
        "split_pause_prefix",
        "split_pause_suffix",
        "paused_split",
        "split_pause_prefix",
        "paused_split",
    ):
        (root / "mode").write_text(mode)
        snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
        assert snapshot["session_input_blocked"] is True
        if mode != "paused_split":
            assert snapshot["pause_evidence"] is None
            diagnostic = snapshot["pause_diagnostics"]["localized"]
            rule = diagnostic["fragment_rule"]
            assert (
                diagnostic["candidate_source"] == "same_frame_localized_pause_rule_ocr"
            )
            assert rule["accepted"] is False
            assert rule["fragments"] == diagnostic["candidates"]
            assert rule["observation_id"] == snapshot["observation_id"]
            assert rule["captured_at_ms"] == snapshot["captured_at_ms"]

    denied = (
        await identify(battle, "split-recovery-not-adoption", adopt_manual_pause=True)
    ).structuredContent
    assert denied["error"]["code"] == "pause_unverified"
    assert denied["input_sent"] is False
    assert denied["snapshot"]["session_input_blocked"] is False
    assert denied["snapshot"]["pause_requested"] is False
    assert (
        denied["snapshot"]["pause_evidence"]["source"]
        == "BattleControlPausedLocalizedSplit"
    )
    before = events(root)
    replay = (
        await identify(battle, "split-recovery-not-adoption", adopt_manual_pause=True)
    ).structuredContent
    assert replay == denied
    assert events(root) == before
    assert not any(
        event["action"] in ("pause", "resume", "click", "swipe", "speed")
        for event in before
    )
    adopted = (
        await identify(battle, "split-new-adoption", adopt_manual_pause=True)
    ).structuredContent
    assert adopted["pause_verified"] is True
    assert adopted["snapshot"]["pause_requested"] is True
    assert not any(event["action"] in ("pause", "resume") for event in events(root))


@pytest.mark.parametrize("battle", ["localized_split_ocr"], indirect=True)
@pytest.mark.parametrize(
    ("mode", "scene"),
    [
        ("split_pause_reversed", "transition"),
        ("split_pause_distant", "transition"),
        ("split_pause_other_row", "transition"),
        ("split_pause_extra_suffix", "transition"),
        ("running_split", "running"),
        ("victory_split", "terminal"),
    ],
)
async def test_split_pause_ocr_rejects_unrelated_text_and_defers_to_scene(
    battle, mode, scene
):
    service, server, root = battle
    (root / "mode").write_text(mode)
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["scene"] == scene
    assert snapshot["pause_evidence"] is None
    if scene == "transition":
        rule = snapshot["pause_diagnostics"]["localized"]["fragment_rule"]
        assert rule["accepted"] is False
        assert rule["rejection_reason"] is not None
    result = await server.call_tool(
        "battle_set_speed",
        {
            "request": {
                "session_id": service.session_id,
                "operation_id": "not-a-verified-split-pause",
                "multiplier": 2,
            }
        },
    )
    assert result.isError
    assert not any(
        event["action"] in ("pause", "resume", "click", "swipe", "speed")
        for event in events(root)
    )


@pytest.mark.parametrize("recover", [True, False])
async def test_read_only_collection_can_verify_pause_but_never_send_input(
    battle, recover
):
    service, server, root = battle
    await service.read(service.session_id, "observe")
    (root / "mode").write_text("intro")
    await service.read(service.session_id, "observe")
    if recover:
        (root / "mode").write_text("paused")
    request = {
        "session_id": service.session_id,
        "operation_id": "verify-pause",
        "duration_ms": 6_000 if recover else 1_000,
        "timeout_ms": 15_000,
    }
    value = (
        await server.call_tool("battle_collect_terminal", {"request": request})
    ).structuredContent
    assert value["status"] == "succeeded"  # Collection, not a battle outcome.
    assert value["session_input_blocked"] is not recover
    assert value["pause_verified"] is recover
    assert value["snapshot"]["session_input_blocked"] is not recover
    assert value["terminal"]["terminal_state"] == "unknown"
    before = events(root)
    replay = await server.call_tool("battle_collect_terminal", {"request": request})
    assert replay.structuredContent == value
    assert events(root) == before
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


async def test_read_only_recovery_does_not_overrun_sampling_budget(battle):
    service, server, root = battle
    await service.read(service.session_id, "observe")
    (root / "mode").write_text("intro")
    await service.read(service.session_id, "observe")
    (root / "mode").write_text("paused")
    (root / "capture-delay").write_text("0.5")
    before = len(events(root))
    value = (
        await server.call_tool(
            "battle_collect_terminal",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "insufficient-verification-budget",
                    "duration_ms": 100,
                }
            },
        )
    ).structuredContent
    assert value["snapshot"]["scene"] == "paused"
    assert value["snapshot"]["pause_evidence"] is not None
    assert value["pause_verified"] is False
    assert value["session_input_blocked"] is True
    assert [e["action"] for e in events(root)[before:]] == ["capture"]


@pytest.mark.parametrize("battle", [True], indirect=True)
@pytest.mark.parametrize("transition_delay", [0, 3.1])
async def test_deploy_pause_dropout_recovers_without_second_toggle_or_replay(
    battle, transition_delay
):
    service, server, root = battle
    initial = (await service.read(service.session_id, "capture"))["snapshot"]
    tile = next(t for t in service.map_info["tiles"] if t["buildable_type"] == 1)
    (root / "pause-sequence.json").write_text(json.dumps(["intro", "paused", "paused"]))
    (root / "capture-delay").write_text(json.dumps({"intro": transition_delay}))
    request = {
        "session_id": service.session_id,
        "operation_id": "deploy-with-pause-dropout",
        "action": "deploy",
        "unit_id": initial["deployment"][0]["unit_id"],
        "location": tile["location"],
        "direction": "None",
        "adopt_manual_pause": True,
        "timeout_ms": 30_000,
    }
    value = (
        await server.call_tool("battle_step", {"request": request})
    ).structuredContent
    assert value["action_returned"] is True
    assert value["status"] == "outcome_unknown"
    assert value["capture_time_snapshot"]["scene"] == "running"
    assert value["return_time_snapshot"]["scene"] == "paused"
    assert value["pause_verified"] is True
    assert value["safe_state"] == "paused"
    assert value["session_input_blocked"] is False
    before = events(root)
    assert sum(e["action"] == "pause" for e in before) == 1
    assert sum(e["action"] == "resume" for e in before) == 1
    pause_index = next(i for i, e in enumerate(before) if e["action"] == "pause")
    assert all(e["action"] == "capture" for e in before[pause_index + 1 :])
    assert [e["mode"] for e in before[pause_index + 1 :]] == [
        "intro",
        "paused",
        "paused",
    ]
    replay = await server.call_tool("battle_step", {"request": request})
    assert replay.structuredContent == value
    assert events(root) == before


@pytest.mark.parametrize("quarantine", ["unknown", "victory", "conflict"])
async def test_positive_pause_cannot_clear_hard_quarantine(battle, quarantine):
    service, _, root = battle
    for mode in ("paused", "intro", quarantine, "paused", "paused", "paused"):
        (root / "mode").write_text(mode)
        snapshot = (await service.read(service.session_id, "observe"))["snapshot"]
    assert snapshot["session_input_blocked"] is True
    assert snapshot["input_block_reason"] == "terminal_or_safety_quarantine"
    denied = await identify(battle, "must-stay-blocked", adopt_manual_pause=True)
    assert denied.structuredContent["error"]["code"] == "battle_ended"
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


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


@pytest.mark.parametrize("battle", [False, "speed_templates"], indirect=True)
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


@pytest.mark.parametrize("battle", ["speed_templates"], indirect=True)
async def test_native_speed_templates_are_alternatives_to_unmatched_ocr_not_each_other(
    battle,
):
    service, _, root = battle
    for speed in (1, 2):
        (root / "speed").write_text(str(speed))
        observed = (await service.read(service.session_id, "capture"))["snapshot"][
            "speed"
        ]
        assert observed["value"] == speed
        assert observed["one_x_ocr_evidence"] is None
        assert observed["conflict"] is False
        if speed == 1:
            assert (
                observed["one_x_evidence"]["source"] == "BattleControlSpeedOneTemplate"
            )
            assert observed["one_x_template_evidence"]["score"] >= 0.9
            assert (
                observed["two_x_evidence"] is None
                or observed["two_x_evidence"]["score"] < 0.9
            )
        else:
            assert observed["two_x_evidence"]["score"] >= 0.9
            assert observed["one_x_template_evidence"] is None
    assert not any(
        event["action"] in ("pause", "resume", "click", "swipe", "speed")
        for event in events(root)
    )


@pytest.mark.parametrize(
    "scene",
    [
        "intro",
        "unknown",
        "victory",
        "running",
        "paused_unknown_speed",
        "paused_speed_conflict",
    ],
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
    if scene in ("paused_unknown_speed", "paused_speed_conflict"):
        value = result.structuredContent
        assert value["error"]["code"] == "speed_unknown"
        assert value["snapshot"]["speed"]["value"] is None
        assert value["snapshot"]["speed"]["conflict"] is (
            scene == "paused_speed_conflict"
        )
    assert not any(
        e["action"] in ("speed", "pause", "resume", "click") for e in events(root)
    )


@pytest.mark.parametrize("battle", [False, "speed_templates"], indirect=True)
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
    for _ in range(3):
        snapshot = (await service.read(service.session_id, "observe"))["snapshot"]
        assert snapshot["session_input_blocked"] is True
    denied = await identify(battle, "still-quarantined", adopt_manual_pause=True)
    assert denied.structuredContent["error"]["code"] == "battle_ended"


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


@pytest.mark.parametrize(
    "battle, paused_capture_delay",
    [(False, 4.1), ("localized_split_ocr", 0)],
    indirect=["battle"],
)
async def test_wait_cancel_reaches_core_and_preserves_pause(
    battle, paused_capture_delay
):
    service, _, root = battle
    if paused_capture_delay:
        (root / "capture-delay").write_text(
            json.dumps({"paused": paused_capture_delay})
        )
    task = asyncio.create_task(wait_step(battle, duration_ms=8_000, timeout_ms=30_000))
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
        assert result["capture_time_snapshot"]["scene"] == "running"
        assert result["return_time_snapshot"]["scene"] == "paused"
        # Cancellation must stop wait sampling, not bound OCR and evidence cleanup.
        assert result["samples"] == []
        if paused_capture_delay:
            assert result["actual_observed_envelope_ms"] >= 8_000
        assert [
            e["action"]
            for e in events(root)
            if e["action"] in ("pause", "resume", "click", "swipe")
        ] == ["resume", "pause"]
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


@pytest.mark.parametrize("running_observed", [True, False])
async def test_pause_uncertainty_interrupts_wait_even_if_read_only_recovery_succeeds(
    battle, running_observed
):
    _, _, root = battle
    sequence = [
        "paused",
        *(["running"] if running_observed else []),
        "intro",
        "paused",
        "paused",
    ]
    (root / "capture-sequence.json").write_text(json.dumps(sequence))
    value = (
        await wait_step(battle, duration_ms=8_000, timeout_ms=30_000)
    ).structuredContent
    assert value["status"] == "failed"
    assert value["error"]["code"] == "pause_unverified"
    assert value["pause_verified"] is True
    assert value["session_input_blocked"] is False
    inputs = [e for e in events(root) if e["action"] != "capture"]
    assert [
        e["action"]
        for e in inputs
        if e["action"] in ("pause", "resume", "click", "swipe")
    ] == ["resume"]


@pytest.mark.parametrize("battle", [False, "localized_split_ocr"], indirect=True)
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
@pytest.mark.parametrize("transition_delay", [0, 3.1])
async def test_pause_roundtrip_reports_current_ownership(battle, transition_delay):
    service, server, root = battle
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
    if transition_delay:
        (root / "pause-sequence.json").write_text(
            json.dumps(["intro", "paused", "paused"])
        )
        (root / "capture-delay").write_text(json.dumps({"intro": transition_delay}))
    paused = (
        await server.call_tool(
            "battle_set_paused",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "pause",
                    "paused": True,
                    "timeout_ms": 30_000,
                }
            },
        )
    ).structuredContent
    assert paused["status"] == "succeeded"
    assert paused["snapshot"]["pause_requested"] is True
    assert paused["pause_verified"] is True
    assert sum(e["action"] == "pause" for e in events(root)) == 1
    assert sum(e["action"] == "resume" for e in events(root)) == 1


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
