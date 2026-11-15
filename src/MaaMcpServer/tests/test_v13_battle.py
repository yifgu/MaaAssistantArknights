"""Real Debug Core with synthetic, local-only transport; not original-frame replay."""

import os
import random
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from test_battle_control import events, inspect_preparation

pytest_plugins = ("test_battle_control",)

pytestmark = pytest.mark.skipif(
    not os.environ.get("MAA_MCP_NATIVE_CORE"),
    reason="Requires Debug Core; no original game frames or real device inputs",
)


def change_avatar(root, modes, *, swap):
    for mode in modes:
        for speed in (1, 2):
            path = root / f"{mode}-{speed}x.png"
            with Image.open(path) as source:
                image = source.convert("RGB")
            avatar = image.crop((62, 628, 122, 688))
            pixels = (
                random.Random(999).randbytes(60 * 60 * 3)
                if swap
                else bytes(int(channel * 0.75 + 10) for channel in avatar.tobytes())
            )
            image.paste(Image.frombytes("RGB", (60, 60), pixels), (62, 628))
            image.save(path)


@pytest.mark.parametrize("battle", [True], indirect=True)
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("swap", [False, True])
async def test_v13_card_binding_has_same_post_resume_protection(battle, explicit, swap):
    service, server, root = battle
    snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    card = snapshot["deployment"][0]
    tile = next(t for t in service.map_info["tiles"] if t["buildable_type"] == 1)
    change_avatar(root, ("running",), swap=swap)
    request = {
        "session_id": service.session_id,
        "operation_id": "card-change",
        "action": "deploy",
        "unit_id": card["unit_id"],
        "location": tile["location"],
        "direction": "Right",
        "adopt_manual_pause": True,
        "timeout_ms": 30_000,
        **({"card_id": card["card_id"]} if explicit else {}),
    }
    value = (
        await server.call_tool("battle_step", {"request": request})
    ).structuredContent
    if swap:
        assert value["error"]["code"] in ("stale_card", "unit_unavailable"), value
        assert not any(event["action"] == "swipe" for event in events(root))
    else:
        assert value["action_returned"] is True, value
        binding = value["card_binding_evidence"]
        assert binding["pre_resume"]["accepted"] is True
        assert binding["post_resume"]["accepted"] is True
        assert binding["post_resume"]["appearance"]["score"] >= 0.95
    saved_events = events(root)
    replay = (
        await server.call_tool("battle_step", {"request": request})
    ).structuredContent
    assert replay == value
    assert events(root) == saved_events


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_v13_explicit_stale_card_is_rejected_before_resume(battle):
    service, server, root = battle
    card = (await service.read(service.session_id, "capture"))["snapshot"][
        "deployment"
    ][0]
    change_avatar(root, ("paused",), swap=True)
    tile = next(t for t in service.map_info["tiles"] if t["buildable_type"] == 1)
    value = (
        await server.call_tool(
            "battle_step",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "pre-resume-stale",
                    "action": "deploy",
                    "unit_id": card["unit_id"],
                    "card_id": card["card_id"],
                    "location": tile["location"],
                    "direction": "Right",
                    "adopt_manual_pause": True,
                    "timeout_ms": 30_000,
                }
            },
        )
    ).structuredContent
    assert value["error"]["code"] == "stale_card", value
    assert not any(event["action"] in ("resume", "swipe") for event in events(root))


@pytest.mark.parametrize("battle", [True], indirect=True)
@pytest.mark.parametrize("explicit", [False, True])
async def test_v13_card_reordering_after_resume_is_rejected_for_both_entries(
    battle, explicit
):
    service, server, root = battle
    card = (await service.read(service.session_id, "capture"))["snapshot"][
        "deployment"
    ][0]
    for speed in (1, 2):
        path = root / f"running-{speed}x.png"
        with Image.open(path) as source:
            image = source.convert("RGB")
        moved = image.crop((30, 588, 150, 720))
        ImageDraw.Draw(image).rectangle((30, 588, 149, 719), fill="black")
        image.paste(moved, (140, 588))
        image.save(path)
    tile = next(t for t in service.map_info["tiles"] if t["buildable_type"] == 1)
    value = (
        await server.call_tool(
            "battle_step",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "reordered-card",
                    "action": "deploy",
                    "unit_id": card["unit_id"],
                    "location": tile["location"],
                    "direction": "Right",
                    "adopt_manual_pause": True,
                    "timeout_ms": 30_000,
                    **({"card_id": card["card_id"]} if explicit else {}),
                }
            },
        )
    ).structuredContent
    assert value["error"]["code"] in ("stale_card", "unit_unavailable"), value
    assert value["pause_verified"] is True
    assert not any(event["action"] == "swipe" for event in events(root))


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_v13_life_endpoints_survive_unknown_without_duplicate_events(battle):
    service, _, root = battle
    font = ImageFont.load_default(size=28)

    async def observe_life(text):
        for speed in (1, 2):
            path = root / f"paused-{speed}x.png"
            with Image.open(path) as source:
                image = source.convert("RGB")
            draw = ImageDraw.Draw(image)
            draw.rectangle((540, 20, 640, 55), fill="black")
            if text is not None:
                draw.text((543, 20), text, font=font, fill="white")
            image.save(path)
        return (await service.read(service.session_id, "capture"))["snapshot"]

    previous = await observe_life("3")
    assert previous["life"] == 3, previous["life_evidence"]
    missing = await observe_life(None)
    assert missing["life"] is None
    assert missing["life_delta"] is None
    current = await observe_life("2")
    assert current["life"] == 2, current["life_evidence"]
    assert current["life_delta"] == -1
    delta = current["life_delta_evidence"]
    assert delta["previous"]["value"] == 3
    assert delta["current"]["value"] == 2
    assert delta["gap_observations"] >= 1
    assert delta["comparison_interval_ms"] > 0
    assert delta["cause"] == "unknown"
    repeated = await observe_life("2")
    assert repeated["life_delta"] == 0
    changes = [
        event for event in repeated["state_events"] if event["type"] == "life_decreased"
    ]
    assert len(changes) == 1
    assert repeated["battle_decision"]["events"]["items"] == repeated["state_events"]
    assert (
        repeated["battle_decision"]["progress"]["life_evidence"]
        == repeated["life_evidence"]
    )
    assert not any(
        event["action"] in ("resume", "swipe", "click") for event in events(root)
    )


@pytest.mark.parametrize(
    "battle",
    ["preparation:level60", "preparation:malformed_level", "preparation:jitter"],
    indirect=True,
)
async def test_v13_preparation_parses_levels_and_matches_spatial_roster(battle):
    _, _, root = battle
    value = (await inspect_preparation(battle)).structuredContent
    assert value["formation_restored"] is True, value
    assert value["roster_unchanged"] is True, value
    assert value["preparation_valid"] is True, value
    variant = (root / "preparation_variant").read_text()
    for unit in value["snapshot"]["operators"]:
        assert unit["actual_level"] == (
            None if variant == "malformed_level" else 60 if variant == "level60" else 50
        ), unit["fields"]["actual_level"]
        assert unit["selected_skill"] == 3
        assert unit["fields"]["selected_skill"]["fingerprint"]["skill_slot"] == 3
    assert value["selected_skill_preservation_verified"] is None


@pytest.mark.parametrize("battle", ["localized_ocr"], indirect=True)
async def test_v13_occluded_label_and_skewed_continue_readonly_recovery(battle):
    service, _, root = battle
    with Image.open(Path(__file__).parent / "fixtures/paused-zh-cn.png") as label:
        prefix = label.crop((0, 0, 68, 40)).convert("RGB")
    for speed in (1, 2):
        path = root / f"paused_localized-{speed}x.png"
        with Image.open(path) as source:
            image = source.convert("RGB")
        draw = ImageDraw.Draw(image)
        draw.rectangle((560, 350, 720, 420), fill="black")
        image.paste(prefix, (591, 363))
        draw.polygon(((1196, 39), (1190, 68), (1224, 54)), fill="white")
        image.save(path)
    for mode in ("paused_english", "intro"):
        (root / "mode").write_text(mode)
        snapshot = (await service.read(service.session_id, "capture"))["snapshot"]
    assert snapshot["input_block_reason"] == "pause_verification_pending"
    (root / "mode").write_text("paused_localized")
    first = (await service.read(service.session_id, "capture"))["snapshot"]
    assert first["scene"] == "paused", first["pause_diagnostics"]
    assert first["session_input_blocked"] is True
    second = (await service.read(service.session_id, "capture"))["snapshot"]
    assert second["session_input_blocked"] is False
    evidence = second["pause_evidence"]
    assert evidence["text"] == "暂停"
    assert evidence["continue_button_evidence"]["accepted"] is True
    assert evidence["corroboration"]["battle_hud"] is True
    assert (
        evidence["fragments"] == second["pause_diagnostics"]["localized"]["candidates"]
    )
    assert second["pause_requested"] is False
    (root / "mode").write_text("unknown")
    await service.read(service.session_id, "capture")
    (root / "mode").write_text("paused_localized")
    for _ in range(2):
        hard = (await service.read(service.session_id, "capture"))["snapshot"]
        assert hard["input_block_reason"] == "terminal_or_safety_quarantine"
    assert not any(
        event["action"] in ("resume", "pause", "swipe", "click")
        for event in events(root)
    )


@pytest.mark.parametrize("battle", ["v13_cooling"], indirect=True)
async def test_v13_cooling_is_not_proof_of_unknown_owned_instance_departure(battle):
    service, server, root = battle
    card = (await service.read(service.session_id, "capture"))["snapshot"][
        "deployment"
    ][0]
    tile = next(t for t in service.map_info["tiles"] if t["buildable_type"] == 1)
    value = (
        await server.call_tool(
            "battle_step",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "cooling-unknown-placement",
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
    assert value["action_returned"] is True, value
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
        assert snapshot["deployment"][0]["cooling"] is True
        instance = snapshot["battlefield"][0]
        assert instance["observed_card_cooling"]["value"] is True
        assert instance["presence"] == "unknown"
        assert instance["tile_reserved"] is True
        assert instance["owned_instance_departure_evidence"] is None
        assert instance["operation_state"] == "deployment_input_attempted"
    cooling = [
        event
        for event in snapshot["state_events"]
        if event["type"] == "observed_card_cooling"
    ]
    assert len(cooling) == 1
    assert cooling[0]["details"]["cause"] == "unknown"


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_v13_legacy_skill_failure_preserves_same_frame_png(battle):
    service, server, root = battle
    (root / "mode").write_text("running")
    result = await server.call_tool(
        "battle_use_skill",
        {
            "request": {
                "session_id": service.session_id,
                "operation_id": "untracked-skill",
                "unit_id": "instance:untracked",
                "timeout_ms": 30_000,
            }
        },
    )
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "unit_not_tracked"
    assert result.structuredContent["return_image_path"]
    assert any(content.type == "image" for content in result.content)
    assert not any(event["action"] == "swipe" for event in events(root))
