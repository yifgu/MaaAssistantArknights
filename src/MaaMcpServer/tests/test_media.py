import asyncio
import base64
import hashlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image

from maa_battle_mcp.media import INLINE_LIMIT, MIME, MediaError, MediaStore
from maa_battle_mcp.models import (
    AnalyzeVideoRequest,
    ImportVideoRequest,
    RecordClipRequest,
)
from maa_battle_mcp.server import create_server
from maa_battle_mcp.service import BattleError, BattleService


def publish(store, number=1, video=b"RIFF0000AVI ", mime_type="video/x-msvideo"):
    artifact_id = f"{number:032x}"
    root = store.allocate(artifact_id)
    (root / "frames").mkdir()
    Image.new("RGB", (8, 8), "red").save(root / "frames/0.png")
    (root / "clip.avi").write_bytes(video)
    return store.publish(
        artifact_id,
        {
            "status": "complete",
            "source_kind": "recorded_mid_battle",
            "timeline": "native_host_acquisition_not_game_simulation_time",
            "frames": [
                {
                    "frame_id": "0",
                    "path": "frames/0.png",
                    "captured_at_ms": 100,
                    "offset_monotonic_ms": 0,
                }
            ],
            "video": {
                "status": "ready",
                "path": "clip.avi",
                "mime_type": mime_type,
            },
        },
    )


def test_registry_hashes_retention_pins_and_explicit_expiry(settings):
    store = MediaStore(settings.user_dir)
    try:
        first = publish(store)
        original = store.read_video(first["artifact_id"])
        assert hashlib.sha256(original).hexdigest() == first["video"]["sha256"]
        second_store = MediaStore(settings.user_dir)
        try:
            with store.pin(first["artifact_id"]):
                with pytest.raises(MediaError, match="pinned"):
                    second_store.expire(first["artifact_id"])
                for number in range(2, 10):
                    publish(store, number)
                assert store.info(first["artifact_id"])["retained"] is True
            store.expire(first["artifact_id"])
            assert second_store.info(first["artifact_id"])["status"] == "expired"
            with pytest.raises(MediaError, match="never recreate"):
                store.allocate(first["artifact_id"])
        finally:
            second_store.db.close()
    finally:
        store.db.close()


def test_bounded_reads_hash_mismatch_and_large_video_no_broken_link(settings):
    store = MediaStore(settings.user_dir)
    try:
        media = publish(store, video=b"x" * (INLINE_LIMIT + 1))
        assert media["video"]["resource_retrievable"] is False
        assert media["video"]["resource_uri"] is None
        with pytest.raises(MediaError, match="bounded"):
            store.read_video(media["artifact_id"])
        value, images = store.read_frames(media["artifact_id"], ["0"])
        assert images and value["frame_is_live"] is False
        with pytest.raises(MediaError):
            store.read_frames(media["artifact_id"], ["0"] * 5)
        store.checked_file(media["artifact_id"], "frames/0.png").write_bytes(
            b"tampered"
        )
        with pytest.raises(MediaError, match="hash mismatch"):
            store.read_frames(media["artifact_id"], ["0"])
    finally:
        store.db.close()


@pytest.mark.parametrize("extension,mime_type", MIME.items())
async def test_resource_callback_and_keyframes_work_after_battle_close(
    settings, monkeypatch, extension, mime_type
):
    service = BattleService(settings)
    try:
        media = publish(
            service.media.store,
            video=b"RIFF-original-owned-video-AVI ",
            mime_type=mime_type,
        )
        server = create_server(service)
        await service.close()
        monkeypatch.setattr(
            service,
            "backend_factory",
            lambda _: pytest.fail("No capture or worker is allowed"),
        )
        resource = list(await server.read_resource(media["video"]["resource_uri"]))
        assert resource[0].content == b"RIFF-original-owned-video-AVI "
        assert media["video"]["resource_uri"].endswith(extension)
        assert resource[0].mime_type == mime_type
        wrong_extension = ".mp4" if extension != ".mp4" else ".avi"
        with pytest.raises(ValueError, match="MIME type"):
            await server.read_resource(
                f"battle-media://{media['artifact_id']}/video{wrong_extension}"
            )
        result = await server.call_tool(
            "battle_get_media_frames",
            {
                "artifact_id": media["artifact_id"],
                "frame_ids": ["0"],
            },
        )
        assert not result.isError
        assert any(item.type == "image" for item in result.content)
        assert (
            result.structuredContent["historical_analysis_changes_live_state"] is False
        )
    finally:
        await service.close()
        service.journal.close()
        service.media.store.db.close()


async def test_real_stdio_resource_read_and_keyframe_callback_after_close(settings):
    settings.user_dir.mkdir(parents=True)
    store = MediaStore(settings.user_dir)
    media = publish(store, video=b"RIFF-mcp-wire-owned-AVI ")
    store.db.close()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "maa_battle_mcp.server",
            "--core-dir",
            str(settings.core_dir),
            "--user-dir",
            str(settings.user_dir),
        ],
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            await session.call_tool("battle_close")
            resource = await session.read_resource(media["video"]["resource_uri"])
            assert resource.contents[0].mimeType == "video/x-msvideo"
            assert (
                base64.b64decode(resource.contents[0].blob)
                == b"RIFF-mcp-wire-owned-AVI "
            )
            frames = await session.call_tool(
                "battle_get_media_frames",
                {
                    "artifact_id": media["artifact_id"],
                    "frame_ids": ["0"],
                },
            )
            assert not frames.isError
            assert any(item.type == "image" for item in frames.content)


@pytest.mark.parametrize(
    "name",
    [
        "../secret.avi",
        "/dev/video0",
        r"\\.\video.avi",
        "http://host/file.avi",
        "list.m3u8",
        "file:video.avi",
    ],
)
async def test_import_rejects_paths_and_network_before_worker(settings, name):
    service = BattleService(settings)
    try:
        service.media.backend_factory = lambda _: pytest.fail(
            "Rejected paths must not start a decoder"
        )
        result = await service.media.import_video(
            ImportVideoRequest(operation_id="bad", import_name=name)
        )
        assert result["status"] == "failed"
    finally:
        await service.close()
        service.journal.close()
        service.media.store.db.close()


async def test_import_symlink_storage_guard_and_no_implicit_provider(settings):
    root = settings.user_dir / "media-import"
    root.mkdir(parents=True)
    outside = settings.user_dir / "outside.avi"
    outside.write_bytes(b"RIFF0000AVI ")
    (root / "linked.avi").symlink_to(outside)
    service = BattleService(settings)
    try:
        result = await service.media.import_video(
            ImportVideoRequest(operation_id="linked", import_name="linked.avi")
        )
        assert result["status"] == "failed"
        service.storage_failed = True
        with pytest.raises(BattleError, match="storage failed"):
            await service.media.import_video(
                ImportVideoRequest(operation_id="guard", import_name="missing.avi")
            )
    finally:
        await service.close()
        service.journal.close()
        service.media.store.db.close()


async def test_import_analysis_separate_no_device_worker_and_replay(
    settings, monkeypatch
):
    root = settings.user_dir / "media-import"
    root.mkdir(parents=True)
    (root / "full.avi").write_bytes(b"RIFF0000AVI " + b"fixture")
    calls = []
    closed = []

    class Backend:
        def __init__(self, configured):
            assert configured.user_dir != settings.user_dir
            self.settings = configured

        def request(self, operation, payload, timeout):
            calls.append(operation)
            assert operation != "connect"
            if operation == "media_inspect":
                frames = self.settings.user_dir / "frames"
                frames.mkdir()
                Image.new("RGB", (8, 8)).save(frames / "0.png")
                return {
                    "status": "succeeded",
                    "decoded_complete": True,
                    "timeline": "imported_media_pts_not_live_capture_time",
                    "frames": [
                        {"frame_id": "0", "path": "frames/0.png", "media_pts_ms": 0}
                    ],
                }
            return {
                "status": "succeeded",
                "candidate": {"stage_name": "fixture", "actions": []},
            }

        def close(self):
            closed.append(True)

        def abort(self):
            pass

    service = BattleService(settings)
    service.media.backend_factory = Backend
    try:
        request = ImportVideoRequest(
            operation_id="import",
            import_name="full.avi",
            declared_context="full_battle",
        )
        first = await service.media.import_video(request)
        assert first["status"] == "succeeded"
        assert not service.lease.is_locked and not service.lock.locked()
        assert await service.media.import_video(request) == first
        artifact_id = first["media"]["artifact_id"]
        assert "captured_at_ms" not in first["media"]["frames"][0]
        analyzed = await service.media.analyze_video(
            AnalyzeVideoRequest(operation_id="analysis", artifact_id=artifact_id)
        )
        assert analyzed["scope"] == "experimental_video_to_copilot"
        assert analyzed["executed"] is False
        assert len(closed) == 2 and calls == ["media_inspect", "video_analyze"]
        service.media.store.expire(artifact_id)
        with monkeypatch.context() as context:
            context.setattr(
                "maa_battle_mcp.media.uuid.uuid4",
                lambda: pytest.fail("Replay must not allocate another artifact intent"),
            )
            assert await service.media.import_video(request) == first
        assert service.media.store.info(artifact_id)["status"] == "expired"
        assert calls == ["media_inspect", "video_analyze"]
    finally:
        await service.close()
        service.journal.close()
        service.media.store.db.close()


async def test_close_terminates_only_the_active_offline_worker(settings):
    root = settings.user_dir / "media-import"
    root.mkdir(parents=True)
    (root / "full.avi").write_bytes(b"RIFF0000AVI ")
    entered, release = threading.Event(), threading.Event()
    backends = []

    class Backend:
        def __init__(self, configured):
            backends.append(self)
            self.aborted = False

        def request(self, *args):
            entered.set()
            release.wait(5)
            raise RuntimeError("fixture worker aborted")

        def abort(self):
            self.aborted = True
            release.set()

        def close(self):
            release.set()

    service = BattleService(settings)
    service.media.backend_factory = Backend
    try:
        task = asyncio.create_task(
            service.media.import_video(
                ImportVideoRequest(operation_id="close", import_name="full.avi")
            )
        )
        assert await asyncio.to_thread(entered.wait, 5)
        await service.close()
        result = await task
        assert result["status"] == "partial"
        assert result["error"]["code"] == "cancelled"
        assert backends[0].aborted is True
        assert service.media.active_backend is None
        assert not service.media.offline_lease.is_locked
    finally:
        await service.close()
        service.journal.close()
        service.media.store.db.close()


@pytest.mark.parametrize(
    "values",
    [
        {"duration_ms": 8_000, "interval_ms": 200, "max_frames": 24},
        {"duration_ms": 2_000, "max_frames": 2},
    ],
)
def test_impossible_recording_configurations_are_rejected(values):
    with pytest.raises(ValueError):
        RecordClipRequest(session_id="s", operation_id="o", **values)


async def test_import_oversize_and_replaced_inode_never_reach_decoder(settings):
    root = settings.user_dir / "media-import"
    root.mkdir(parents=True)
    with (root / "oversize.avi").open("wb") as stream:
        stream.truncate(128 * 1024 * 1024 + 1)
    service = BattleService(settings)
    try:
        service.media.backend_factory = lambda _: pytest.fail("No decoder may start")
        result = await service.media.import_video(
            ImportVideoRequest(
                operation_id="oversize",
                import_name="oversize.avi",
            )
        )
        assert result["status"] == "failed"
        source = root / "replace.avi"
        source.write_bytes(b"RIFF0000AVI ")
        checked, identity = service.media._import_source("replace.avi")
        source.rename(root / "old.avi")
        source.write_bytes(b"RIFF1111AVI ")
        with pytest.raises(MediaError, match="identity changed"):
            service.media._open_import(checked, identity)
    finally:
        await service.close()
        service.journal.close()
        service.media.store.db.close()


def test_interrupted_job_preserves_committed_artifact_intent_without_recreation(
    settings,
):
    store = MediaStore(settings.user_dir)
    try:
        body = {"kind": "import", "import_name": "full.avi"}
        assert store.admit_job("interrupted", body) is None
        artifact_id = "a" * 32
        store.job_artifact("interrupted", artifact_id)
        store.allocate(artifact_id)
        store.discard(artifact_id, "interrupted")
        replay = store.admit_job("interrupted", body)
        assert replay["status"] == "outcome_unknown"
        assert replay["artifact_id"] == artifact_id
        assert store.info(artifact_id)["status"] == "interrupted"
        assert not store.directory(artifact_id).exists()
    finally:
        store.db.close()


def test_concurrent_stores_cannot_overbook_media_reservations(settings):
    store = MediaStore(settings.user_dir)
    checked = threading.Event()
    resume = threading.Event()

    def allocate(number, pause):
        other = MediaStore(settings.user_dir)
        artifact_id = f"{number:032x}"
        if pause:
            prune = other.prune

            def paused_prune(*args, **kwargs):
                prune(*args, **kwargs)
                checked.set()
                if not resume.wait(10):
                    raise AssertionError("Concurrent allocation did not finish")

            other.prune = paused_prune
        try:
            other.allocate(artifact_id)
            return True
        except MediaError:
            return False
        finally:
            other.artifact_lock(artifact_id).release()
            other.db.close()

    try:
        for number in range(1, 8):
            publish(store, number)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(allocate, 100, True)
            try:
                assert checked.wait(5)
                second = executor.submit(allocate, 101, False)
                second.result(timeout=5)
            finally:
                resume.set()
            assert first.result(timeout=5)
        count = store.db.execute(
            "SELECT COUNT(*) FROM artifacts WHERE status IN ('ready','partial','pending')"
        ).fetchone()[0]
        assert count <= 8
    finally:
        resume.set()
        store.db.close()
