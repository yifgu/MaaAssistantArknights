"""Actual Debug Core, local synthetic transport and native AVI codec; no live game."""

import asyncio
import base64
import json
import os
import shutil
import subprocess

import pytest
from PIL import Image, ImageDraw, ImageFont
from test_battle_control import events

from maa_battle_mcp import native
from maa_battle_mcp.models import AnalyzeVideoRequest, ImportVideoRequest

pytest_plugins = ("test_battle_control",)
REAL_POPEN = subprocess.Popen
pytestmark = pytest.mark.skipif(
    not os.environ.get("MAA_MCP_NATIVE_CORE"),
    reason="Requires actual Debug Core and local fixture transport",
)


async def record(battle, operation="record", **options):
    service, server, _ = battle
    request = {
        "session_id": service.session_id,
        "operation_id": operation,
        "duration_ms": 600,
        "interval_ms": 200,
        "max_frames": 5,
        "timeout_ms": 19_000,
        **options,
    }
    return await server.call_tool("battle_record_clip", {"request": request}), request


@pytest.mark.parametrize("battle", [True], indirect=True)
@pytest.mark.parametrize("mode", ["paused", "running"])
async def test_native_recording_preserves_scene_and_replay_and_resource_read(
    battle, mode
):
    service, server, root = battle
    (root / "mode").write_text(mode)
    result, request = await record(battle)
    value = result.structuredContent
    assert not result.isError, value
    assert value["input_sent"] is False
    assert value["snapshot"]["scene"] == mode
    media = value["media"]
    assert media["video"]["status"] == "ready", media
    assert media["video"]["mime_type"] == "video/x-msvideo"
    assert media["raw_buffer_bytes"] <= 64 * 1024 * 1024
    assert 2 <= len(media["frames"]) <= 5
    times = [frame["captured_at_ms"] for frame in media["frames"]]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    before = events(root)
    replay = await server.call_tool("battle_record_clip", {"request": request})
    assert replay.structuredContent == value
    assert events(root) == before
    await service.close()
    resource = list(await server.read_resource(media["video"]["resource_uri"]))
    expected = service.media.store.checked_file(
        media["artifact_id"], media["video"]["path"]
    ).read_bytes()
    assert resource[0].content == expected
    frames = await server.call_tool(
        "battle_get_media_frames",
        {
            "artifact_id": media["artifact_id"],
            "frame_ids": [media["frames"][0]["frame_id"]],
        },
    )
    image = next(content for content in frames.content if content.type == "image")
    assert (
        base64.b64decode(image.data)
        == service.media.store.checked_file(
            media["artifact_id"], media["frames"][0]["path"]
        ).read_bytes()
    )
    assert events(root) == before
    assert not any(
        event["action"] in ("pause", "resume", "click", "swipe") for event in before
    )


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_authorized_wait_recording_pauses_and_historical_frames_do_not_recover_lock(
    battle,
):
    service, server, root = battle
    request = {
        "session_id": service.session_id,
        "operation_id": "record-wait",
        "action": "wait",
        "duration_ms": 600,
        "timeout_ms": 19_000,
        "adopt_manual_pause": True,
        "recording": {"interval_ms": 200, "max_frames": 6},
    }
    result = (
        await server.call_tool("battle_step", {"request": request})
    ).structuredContent
    assert result["pause_verified"] is True, result
    assert result["return_time_snapshot"]["scene"] == "paused"
    media = result["media"]
    assert (
        media["frames"][-1]["captured_at_ms"]
        == result["return_time_snapshot"]["captured_at_ms"]
    )
    before = events(root)
    replay = (
        await server.call_tool("battle_step", {"request": request})
    ).structuredContent
    assert replay == result and events(root) == before
    (root / "mode").write_text("intro")
    blocked = (await service.read(service.session_id, "capture"))["snapshot"]
    assert blocked["input_block_reason"] == "pause_verification_pending"
    (root / "mode").write_text("paused")
    before = events(root)
    await server.call_tool(
        "battle_get_media_frames",
        {
            "artifact_id": media["artifact_id"],
            "frame_ids": [media["frames"][-1]["frame_id"]],
        },
    )
    assert events(root) == before
    first = (await service.read(service.session_id, "capture"))["snapshot"]
    assert first["session_input_blocked"] is True
    service.media.store.expire(media["artifact_id"])
    before = events(root)
    assert (
        await server.call_tool("battle_step", {"request": request})
    ).structuredContent == result
    assert events(root) == before
    assert service.media.store.info(media["artifact_id"])["status"] == "expired"


@pytest.mark.parametrize("battle", [True], indirect=True)
@pytest.mark.parametrize("close", [False, True])
async def test_recording_cancellation_and_close_leave_no_input_or_worker(battle, close):
    service, server, root = battle
    task = asyncio.create_task(
        record(
            battle, "cancel-record", duration_ms=8_000, interval_ms=500, max_frames=20
        )
    )
    for _ in range(100):
        if (
            service.step_active
            and len([e for e in events(root) if e["action"] == "capture"]) >= 2
        ):
            break
        await asyncio.sleep(0.05)
    if close:
        await service.close()
        await task
        assert service.backend is None
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    saved = service.operation(
        service.preparation_session_id
        or next(
            row[0]
            for row in service.journal.execute(
                "SELECT session FROM operations WHERE id='cancel-record'"
            )
        ),
        "cancel-record",
    )
    assert saved["input_sent"] is False
    assert saved["media"]["artifact_id"]
    assert not any(
        e["action"] in ("pause", "resume", "click", "swipe") for e in events(root)
    )


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_real_native_import_and_legacy_missing_context_are_isolated(battle):
    service, _, root = battle
    result, _ = await record(battle, "import-source")
    media = result.structuredContent["media"]
    imports = service.settings.user_dir / "media-import"
    imports.mkdir(exist_ok=True)
    shutil.copyfile(
        service.media.store.checked_file(media["artifact_id"], media["video"]["path"]),
        imports / "clip.avi",
    )
    seen = []

    class Offline(native.NativeBackend):
        def __init__(self, settings):
            (settings.user_dir / "fixture").mkdir(parents=True)
            shutil.copytree(root / "resource", settings.user_dir / "fixture/resource")
            super().__init__(settings)

        def request(self, operation, payload, timeout):
            seen.append(operation)
            assert operation != "connect"
            return super().request(operation, payload, timeout)

    service.media.backend_factory = Offline
    before = events(root)
    imported = await service.media.import_video(
        ImportVideoRequest(
            operation_id="native-import",
            import_name="clip.avi",
            declared_context="full_battle",
        )
    )
    assert imported["status"] == "succeeded", imported
    frame = imported["media"]["frames"][0]
    assert "media_pts_ms" in frame and "captured_at_ms" not in frame
    analyzed = await service.media.analyze_video(
        AnalyzeVideoRequest(
            operation_id="native-legacy",
            artifact_id=imported["media"]["artifact_id"],
            timeout_ms=30_000,
        )
    )
    assert analyzed["status"] == "failed"
    assert analyzed["scope"] == "experimental_video_to_copilot"
    assert analyzed["executed"] is False
    assert [operation for operation in seen if operation != "close"] == [
        "media_inspect",
        "video_analyze",
    ]
    assert events(root) == before
    assert service.backend is not None


@pytest.mark.parametrize("battle", [True], indirect=True)
async def test_media_publication_failure_does_not_skip_wait_pause_cleanup(
    battle, monkeypatch
):
    service, server, root = battle

    def fail(*args, **kwargs):
        raise RuntimeError("fixture media publication quota")

    monkeypatch.setattr(service.media.store, "publish", fail)
    result = (
        await server.call_tool(
            "battle_step",
            {
                "request": {
                    "session_id": service.session_id,
                    "operation_id": "failed-media-wait",
                    "action": "wait",
                    "duration_ms": 400,
                    "timeout_ms": 19_000,
                    "adopt_manual_pause": True,
                    "recording": {"interval_ms": 200, "max_frames": 5},
                }
            },
        )
    ).structuredContent
    assert result["pause_verified"] is True, result
    assert result["return_time_snapshot"]["scene"] == "paused"
    assert result["media"]["status"] == "unavailable"
    assert result["status"] == "failed"
    assert result["error"]["code"] == "media_unavailable"
    assert len([event for event in events(root) if event["action"] == "pause"]) == 1


@pytest.mark.parametrize("battle", ["preparation:complete"], indirect=True)
async def test_real_legacy_wrapper_can_produce_candidates_from_controlled_full_context(
    battle,
):
    service, _, root = battle
    encoder = service.settings.core_dir / (
        "MaaMcpMediaTest.exe" if os.name == "nt" else "MaaMcpMediaTest"
    )
    if not encoder.is_file():
        pytest.skip(
            "Controlled full-context fixture requires the Debug native media test encoder"
        )
    tasks_path = root / "resource/tasks/fixture.json"
    tasks = json.loads(tasks_path.read_text())
    for task in tasks.values():
        if task.get("algorithm") == "MatchTemplate":
            task["templThreshold"] = 0.8
    tasks["BattleOperRole"]["templThreshold"] = 0.75
    templates = root / "resource/template"
    kills_marker = Image.new("RGB", (24, 24))
    marker_draw = ImageDraw.Draw(kills_marker)
    marker_draw.rectangle((5, 5, 18, 18), outline="white", width=3)
    marker_draw.line((6, 6, 17, 17), fill="white", width=2)
    kills_marker.save(templates / "BattleKillsFlag.png")
    tasks["BattleKillsFlag"] = {
        "baseTask": "#none",
        "algorithm": "MatchTemplate",
        "template": "BattleKillsFlag.png",
        "roi": [498, 18, 28, 28],
        "templThreshold": 0.9,
    }
    tasks["BattleStageName"] = {
        "baseTask": "#none",
        "algorithm": "OcrDetect",
        "isAscii": False,
        "text": [],
        "roi": [40, 80, 220, 65],
        "useRaw": True,
        "withoutDet": True,
        "binThreshold": [100, 255],
    }
    tasks["BattleSpeedButton"] = {
        "baseTask": "#none",
        "algorithm": "MatchTemplate",
        "template": "empty.png",
        "roi": [1050, 20, 20, 20],
        "specialParams": [100, 100],
    }
    tasks["BattleAvatarDataForFormation"] = {
        "baseTask": "#none",
        "algorithm": "MatchTemplate",
        "template": "empty.png",
        "roi": [0, 0, 150, 165],
        "rectMove": [10, 10, 40, 40],
        "templThreshold": 0.8,
    }
    tasks_path.write_text(json.dumps(tasks))
    # Codec-safe synthetic UI markers: MJPEG preserves luminance, not random chroma.
    for template in templates.glob("*.png"):
        with Image.open(template) as original:
            marker = original.convert("L").convert("RGB")
        marker.save(template)
    with Image.open(root / "running-1x.png") as original:
        running = original.convert("RGB")
    running.paste(kills_marker, (500, 20))
    draw = ImageDraw.Draw(running)
    draw.rectangle((1050, 20, 1069, 39), fill="white")
    draw.text((550, 20), "0/41", font=ImageFont.load_default(size=28), fill="white")
    with Image.open(root / "formation-1x.png") as original:
        formation = original.convert("RGB")
    formation.paste(running.crop((72, 638, 112, 678)), (100, 100))
    stage = Image.new("RGB", (1280, 720))
    ImageDraw.Draw(stage).text(
        (50, 85), "1-7", font=ImageFont.load_default(size=42), fill="white"
    )
    files = []
    for name, image in (
        ("formation-context", formation),
        ("stage-context", stage),
        ("battle-context", running),
    ):
        path = root / f"{name}.png"
        image.convert("L").convert("RGB").save(path)
        files.append(path)
    imports = service.settings.user_dir / "media-import"
    imports.mkdir(exist_ok=True)
    output = imports / "full.avi"
    sequence = [files[0]] * 6 + [files[1]] * 6 + [files[2]] * 36
    with REAL_POPEN(
        [str(encoder), "--encode", str(output), "2", *map(str, sequence)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        _, error = process.communicate(timeout=30)
        assert process.returncode == 0, error.decode()

    class Offline(native.NativeBackend):
        def __init__(self, settings):
            (settings.user_dir / "fixture").mkdir(parents=True)
            shutil.copytree(root / "resource", settings.user_dir / "fixture/resource")
            super().__init__(settings)

        def request(self, operation, payload, timeout):
            assert operation != "connect"
            return super().request(operation, payload, timeout)

    service.media.backend_factory = Offline
    imported = await service.media.import_video(
        ImportVideoRequest(
            operation_id="full-native-import",
            import_name="full.avi",
            declared_context="full_battle",
        )
    )
    assert imported["status"] == "succeeded", imported
    result = await service.media.analyze_video(
        AnalyzeVideoRequest(
            operation_id="full-native-analysis",
            artifact_id=imported["media"]["artifact_id"],
            timeout_ms=120_000,
        )
    )
    assert result["status"] == "succeeded", result
    assert result["candidate"]["stage_name"] == "1-7"
    assert result["scope"] == "experimental_video_to_copilot"
    assert result["executed"] is False
