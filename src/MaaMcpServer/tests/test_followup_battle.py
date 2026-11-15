"""Synthetic local fixtures exercise actual Debug Core, never a live device."""

import base64
import os

import pytest
from PIL import Image, ImageDraw
from test_battle_control import events, inspect_preparation

pytest_plugins = ("test_battle_control",)
pytestmark = pytest.mark.skipif(
    not os.environ.get("MAA_MCP_NATIVE_CORE"),
    reason="Requires actual Debug Core and synthetic fixture transport",
)


@pytest.mark.parametrize("battle", ["preparation:segmented_name"], indirect=True)
async def test_shared_oper_name_segmentation_recovers_formation_specific_roi(battle):
    _, _, _ = battle
    result = (await inspect_preparation(battle)).structuredContent
    assert result["preparation_valid"] is True, result
    assert result["formation_restored"] is True
    recovered = []
    for oper in result["snapshot"]["operators"]:
        identity = oper["fields"]["identity"]
        if identity["name_source"].startswith("shared_OperNameAnalyzer:"):
            recovered.append(oper["name"])
            candidates = identity["raw_candidates"]
            assert any(item["accepted"] is False for item in candidates)
            assert candidates[-1]["accepted"] is True
            assert candidates[-1]["raw"]["score"] >= 0.9
    assert recovered, result


@pytest.mark.parametrize("battle", ["preparation:no_marker"], indirect=True)
async def test_unresolved_preparation_requests_host_review_of_original_detail_frame(
    battle,
):
    service, server, root = battle
    result = (await inspect_preparation(battle)).structuredContent
    fallback = result["host_fallback"]
    assert fallback["required"] is True
    skill = next(
        item for item in fallback["items"] if item["field"] == "selected_skill"
    )
    assert skill["slot"] == 0
    assert skill["retrieval"]["available"] is True
    assert skill["evidence"]["image_path"] != result["image_path"]
    before = events(root)
    frame = await server.call_tool(
        "battle_get_evidence_frame",
        {
            key: skill["retrieval"][key]
            for key in ("session_id", "operation_id", "observation_id")
        },
    )
    assert not frame.isError
    image = next(content for content in frame.content if content.type == "image")
    assert (
        base64.b64decode(image.data)
        == service.evidence_path(skill["evidence"]).read_bytes()
    )
    assert frame.structuredContent["frame_is_live"] is False
    assert events(root) == before
    assert result["snapshot"]["operators"][0]["selected_skill"] is None


@pytest.mark.parametrize(
    "battle", ["preparation:segmented_ambiguous_name"], indirect=True
)
async def test_segmented_prefix_ambiguity_requests_host_review_instead_of_guessing(
    battle,
):
    result = (await inspect_preparation(battle)).structuredContent
    oper = result["snapshot"]["operators"][0]
    assert oper["name"] is None
    assert oper["operator_game_id"] is None
    identity = next(
        item
        for item in result["host_fallback"]["items"]
        if item["slot"] == 0 and item["field"] == "identity"
    )
    assert any(
        candidate["reason"] == "segmented_name_prefix_ambiguous"
        for candidate in identity["raw_candidates"]
    )


@pytest.mark.parametrize("battle", ["v13_cooling"], indirect=True)
async def test_suspected_defeat_is_deduplicated_host_hint_without_departure_authority(
    battle,
):
    service, server, root = battle
    card = (await service.read(service.session_id, "capture"))["snapshot"][
        "deployment"
    ][0]
    tile = next(
        item for item in service.map_info["tiles"] if item["buildable_type"] == 1
    )
    result = (
        await server.call_tool(
            "battle_step",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "suspected-defeat-unit",
                    "action": "deploy",
                    "unit_id": card["unit_id"],
                    "location": tile["location"],
                    "direction": "Right",
                    "adopt_manual_pause": True,
                    "timeout_ms": 30_000,
                }
            },
        )
    ).structuredContent
    assert result["action_returned"] is True, result
    for speed in (1, 2):
        path = root / f"paused-{speed}x.png"
        with Image.open(path) as source:
            image = source.convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle((120, 590, 129, 599), fill="black")
        draw.rectangle((32, 714, 145, 717), fill=(120, 30, 0))
        image.save(path)
    for _ in range(3):
        snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
        instance = snapshot["battlefield"][0]
        assert instance["presence"] == "unknown"
        assert instance["tile_reserved"] is True
        assert instance["owned_instance_departure_evidence"] is None
    hints = [
        event
        for event in snapshot["state_events"]
        if event["type"] == "suspected_defeat"
    ]
    assert len(hints) == 1
    hint = hints[0]["details"]
    assert hint["departure_verified"] is False
    assert hint["record_coverage"] == "this_session_only"
    assert hint["cause"] == "unknown" and hint["confidence"] is None
    assert hint["requires_host_review"] is True
    assert hint["authorizes_input"] is False
    assert any(
        alert["event_id"] == hints[0]["event_id"]
        for alert in snapshot["battle_decision"]["alerts"]
    )
