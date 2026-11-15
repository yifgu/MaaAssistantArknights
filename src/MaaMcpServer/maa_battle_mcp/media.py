"""Owned, bounded local media. No game-state inference and no operator knowledge."""

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
import weakref
from pathlib import Path

from filelock import FileLock, Timeout

from .native import NativeBackend

MIB = 1024 * 1024
INLINE_LIMIT = 16 * MIB
RECORDING_LIMIT = 32 * MIB
IMPORT_LIMIT = 128 * MIB
RETENTION_BYTES = 256 * MIB
RETENTION_COUNT = 8
ARTIFACT = re.compile(r"^[0-9a-f]{32}$")
MIME = {
    ".avi": "video/x-msvideo",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}


class MediaError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            value.update(chunk)
    return value.hexdigest()


class MediaStore:
    def __init__(self, user_dir: Path):
        self.root = user_dir / "battle-media"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.db = sqlite3.connect(user_dir / "media.sqlite3")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,status TEXT,created REAL,bytes INTEGER,manifest TEXT,hash TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,request TEXT,result TEXT)"
        )
        if "artifact_id" not in {
            row[1] for row in self.db.execute("PRAGMA table_info(jobs)")
        }:
            self.db.execute("ALTER TABLE jobs ADD COLUMN artifact_id TEXT")
        self.db.commit()
        self.pins: dict[str, int] = {}
        self.locks: dict[str, FileLock] = {}
        self.lock_root = user_dir / "media-pins"
        self.lock_root.mkdir(exist_ok=True)
        self.retention_lock = FileLock(user_dir / "media-retention.lock", timeout=0)
        # A previous process may have died before publication. Never resume its work.
        for (artifact_id,) in self.db.execute(
            "SELECT id FROM artifacts WHERE status='pending'"
        ).fetchall():
            try:
                self.expire(artifact_id, "interrupted")
            except MediaError:
                pass

    def artifact_lock(self, artifact_id: str) -> FileLock:
        self.directory(artifact_id)
        if artifact_id not in self.locks:
            self.locks[artifact_id] = FileLock(
                self.lock_root / f"{artifact_id}.lock", timeout=0
            )
        return self.locks[artifact_id]

    def directory(self, artifact_id: str) -> Path:
        if not ARTIFACT.fullmatch(artifact_id):
            raise MediaError("Invalid media artifact ID")
        path = self.root / artifact_id
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise MediaError("Unsafe media artifact directory")
        return path

    def allocate(self, artifact_id: str, reservation: int = RECORDING_LIMIT) -> Path:
        if (
            not isinstance(reservation, int)
            or isinstance(reservation, bool)
            or not 0 < reservation <= RETENTION_BYTES
        ):
            raise MediaError("Invalid media byte reservation")
        try:
            with self.retention_lock:
                return self._allocate(artifact_id, reservation)
        except Timeout as exc:
            raise MediaError("Media retention reservation is busy") from exc

    def _allocate(self, artifact_id: str, reservation: int) -> Path:
        if self.db.execute(
            "SELECT id FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone():
            raise MediaError(
                "Media intent already exists; never recreate expired or interrupted media"
            )
        self.prune(extra_count=1, extra_bytes=reservation)
        path = self.directory(artifact_id)
        lock = self.artifact_lock(artifact_id)
        lock.acquire()
        try:
            path.mkdir(mode=0o700, exist_ok=False)
            self.db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?,?)",
                (artifact_id, "pending", time.time(), reservation, None, None),
            )
            self.db.commit()
        except BaseException:
            lock.release()
            raise
        self.pins[artifact_id] = 1
        return path

    def discard(self, artifact_id: str, reason: str = "failed") -> None:
        self.pins[artifact_id] = max(0, self.pins.get(artifact_id, 0) - 1)
        self.artifact_lock(artifact_id).release()
        self.expire(artifact_id, reason)

    def expire(self, artifact_id: str, reason: str = "expired") -> None:
        if self.pins.get(artifact_id, 0):
            raise MediaError("Media artifact is pinned")
        try:
            with self.artifact_lock(artifact_id):
                path = self.directory(artifact_id)
                if path.exists():
                    shutil.rmtree(path)
                self.db.execute(
                    "UPDATE artifacts SET status=?,bytes=0 WHERE id=?",
                    (reason, artifact_id),
                )
                self.db.commit()
        except Timeout as exc:
            raise MediaError("Media artifact is pinned by another process") from exc

    def prune(
        self, extra_count: int = 0, extra_bytes: int = 0, exclude: str | None = None
    ) -> None:
        rows = self.db.execute(
            "SELECT id,bytes,status FROM artifacts WHERE status IN ('ready','partial','pending') ORDER BY created"
        ).fetchall()
        blocked = set()
        while (
            len(rows) + extra_count > RETENTION_COUNT
            or sum(row[1] for row in rows) + extra_bytes > RETENTION_BYTES
        ):
            victim = next(
                (
                    row
                    for row in rows
                    if row[0] != exclude
                    and row[2] != "pending"
                    and row[0] not in blocked
                    and not self.pins.get(row[0])
                ),
                None,
            )
            if victim is None:
                raise MediaError("Media retention quota is pinned or exhausted")
            try:
                self.expire(victim[0])
            except MediaError:
                blocked.add(victim[0])
                continue
            rows.remove(victim)

    @contextlib.contextmanager
    def pin(self, artifact_id: str, *, pending: bool = False):
        row = self.db.execute(
            "SELECT status FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise MediaError("Unknown media artifact")
        if row[0] not in (
            ("ready", "partial", "pending") if pending else ("ready", "partial")
        ):
            raise MediaError(f"Media artifact is {row[0]}; replay cannot recreate it")
        lock = self.artifact_lock(artifact_id)
        self.pins[artifact_id] = self.pins.get(artifact_id, 0) + 1
        try:
            try:
                lock.acquire()
            except Timeout as exc:
                raise MediaError("Media artifact is pinned by another process") from exc
            current = self.db.execute(
                "SELECT status FROM artifacts WHERE id=?", (artifact_id,)
            ).fetchone()
            if current is None or current[0] not in (
                ("ready", "partial", "pending") if pending else ("ready", "partial")
            ):
                raise MediaError(
                    "Media artifact expired before its read/job pin was acquired"
                )
            yield self.directory(artifact_id)
        finally:
            self.pins[artifact_id] -= 1
            lock.release()

    def checked_file(self, artifact_id: str, relative: str) -> Path:
        root = self.directory(artifact_id)
        path = root / relative
        if (
            not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or path.is_symlink()
        ):
            raise MediaError("Unsafe media file reference")
        resolved = path.resolve()
        if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
            raise MediaError("Missing or escaped media file")
        return resolved

    def publish(self, artifact_id: str, manifest: dict) -> dict:
        root = self.directory(artifact_id)
        frames = manifest.get("frames") or []
        if len(frames) > 24:
            raise MediaError("Too many published media frames")
        for frame in frames:
            path = self.checked_file(artifact_id, frame["path"])
            if path.stat().st_size > INLINE_LIMIT:
                raise MediaError("Media frame exceeds the individual image limit")
            frame["bytes"] = path.stat().st_size
            frame["sha256"] = digest(path)
        video = manifest.get("video") or {}
        if video.get("status") in ("ready", "unverified"):
            path = self.checked_file(artifact_id, video["path"])
            video["bytes"] = path.stat().st_size
            video["sha256"] = digest(path)
        manifest["artifact_id"] = artifact_id
        manifest["availability_as_of_ms"] = int(time.time() * 1000)
        manifest["availability_tool"] = "battle_get_media"
        manifest["video"] = video
        payload = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
        if len(payload) > MIB:
            raise MediaError("Media manifest exceeds limit")
        (root / "manifest.json").write_bytes(payload)
        # Only published paths are retained; worker logs/caches cannot grow behind the quota.
        allowed = {"manifest.json"} | {frame["path"] for frame in frames}
        if video.get("status") in ("ready", "unverified"):
            allowed.add(video["path"])
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_symlink():
                raise MediaError("Symlink in media output")
            if path.is_file() and path.relative_to(root).as_posix() not in allowed:
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        total = sum(path.stat().st_size for path in root.rglob("*") if path.is_file())
        limit = (
            RECORDING_LIMIT
            if manifest.get("source_kind") == "recorded_mid_battle"
            else IMPORT_LIMIT + RECORDING_LIMIT + MIB
        )
        if total > limit:
            raise MediaError("Published artifact byte limit exceeded")
        reserved = self.db.execute(
            "SELECT bytes FROM artifacts WHERE id=? AND status='pending'",
            (artifact_id,),
        ).fetchone()
        if reserved is None:
            raise MediaError("Media artifact was already published or expired")
        if total > reserved[0]:
            raise MediaError("Published artifact exceeds its reserved byte budget")
        status = (
            "ready"
            if manifest.get("status") in ("complete", "succeeded")
            else "partial"
        )
        self.db.execute(
            "UPDATE artifacts SET status=?,bytes=?,manifest=?,hash=? WHERE id=?",
            (
                status,
                total,
                json.dumps(manifest, ensure_ascii=False),
                hashlib.sha256(payload).hexdigest(),
                artifact_id,
            ),
        )
        self.db.commit()
        self.pins[artifact_id] = max(0, self.pins.get(artifact_id, 0) - 1)
        self.artifact_lock(artifact_id).release()
        return self.info(artifact_id)

    def info(self, artifact_id: str) -> dict:
        row = self.db.execute(
            "SELECT status,manifest,hash FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        if row is None:
            raise MediaError("Unknown media artifact")
        if row[0] not in ("ready", "partial"):
            return {
                "artifact_id": artifact_id,
                "status": row[0],
                "retained": False,
                "replay_recreates_media": False,
            }
        result = json.loads(row[1])
        result["manifest_sha256"] = row[2]
        result["retained"] = True
        video = result.get("video") or {}
        extension = next(
            (
                extension
                for extension, mime_type in MIME.items()
                if mime_type == video.get("mime_type")
            ),
            None,
        )
        retrievable = (
            video.get("status") == "ready"
            and video.get("bytes", INLINE_LIMIT + 1) <= INLINE_LIMIT
            and extension is not None
        )
        if retrievable:
            try:
                retrievable = (
                    self.checked_file(artifact_id, video["path"]).stat().st_size
                    == video["bytes"]
                )
            except (MediaError, OSError):
                retrievable = False
        video["resource_retrievable"] = retrievable
        video["resource_read_limit_bytes"] = INLINE_LIMIT
        video["resource_uri"] = (
            f"battle-media://{artifact_id}/video{extension}" if retrievable else None
        )
        if video.get("status") == "ready" and not retrievable:
            video["inline_unavailable_reason"] = (
                "video_exceeds_16_MiB_binary_resource_limit"
                if video.get("bytes", INLINE_LIMIT + 1) > INLINE_LIMIT
                else "unsupported_video_mime_type"
                if extension is None
                else "video_missing_or_changed"
            )
        result["video"] = video
        return result

    def read_video(self, artifact_id: str, mime_type: str | None = None) -> bytes:
        with self.pin(artifact_id):
            video = self.info(artifact_id)["video"]
            if mime_type is not None and video.get("mime_type") != mime_type:
                raise MediaError("Video resource MIME type does not match the artifact")
            if not video.get("resource_retrievable"):
                raise MediaError(
                    "Video is not available for bounded binary resource retrieval"
                )
            path = self.checked_file(artifact_id, video["path"])
            with path.open("rb") as stream:
                data = stream.read(INLINE_LIMIT + 1)
            if (
                len(data) > INLINE_LIMIT
                or hashlib.sha256(data).hexdigest() != video["sha256"]
            ):
                raise MediaError("Video byte limit or content hash mismatch")
            return data

    def read_frames(
        self, artifact_id: str, frame_ids: list[str]
    ) -> tuple[dict, list[bytes]]:
        if not 1 <= len(frame_ids) <= 4 or len(set(frame_ids)) != len(frame_ids):
            raise MediaError("Select one to four distinct media frame IDs")
        with self.pin(artifact_id):
            manifest = self.info(artifact_id)
            indexed = {frame["frame_id"]: frame for frame in manifest["frames"]}
            if any(frame_id not in indexed for frame_id in frame_ids):
                raise MediaError("Unknown frame ID in media artifact")
            selected = [indexed[frame_id] for frame_id in frame_ids]
            if sum(frame["bytes"] for frame in selected) > INLINE_LIMIT:
                raise MediaError("Selected frames exceed the 16 MiB response limit")
            images = []
            for frame in selected:
                with self.checked_file(artifact_id, frame["path"]).open("rb") as stream:
                    data = stream.read(INLINE_LIMIT - sum(map(len, images)) + 1)
                if (
                    sum(map(len, images)) + len(data) > INLINE_LIMIT
                    or hashlib.sha256(data).hexdigest() != frame["sha256"]
                ):
                    raise MediaError(
                        "Frame response byte limit or content hash mismatch"
                    )
                images.append(data)
            return {
                "status": "succeeded",
                "artifact_id": artifact_id,
                "frames": selected,
                "timeline": manifest["timeline"],
                "frame_is_live": False,
                "historical_analysis_changes_live_state": False,
            }, images

    def admit_job(self, operation_id: str, request: dict) -> dict | None:
        encoded = json.dumps(request, sort_keys=True)
        row = self.db.execute(
            "SELECT request,result,artifact_id FROM jobs WHERE id=?", (operation_id,)
        ).fetchone()
        if row:
            if row[0] != encoded:
                raise MediaError(
                    "Media operation_id already used with different parameters"
                )
            return (
                json.loads(row[1])
                if row[1]
                else {
                    "status": "outcome_unknown",
                    "operation_id": operation_id,
                    "artifact_id": row[2],
                    "error": {
                        "code": "interrupted_media_job",
                        "message": "Never automatically replay this job",
                    },
                }
            )
        self.db.execute(
            "INSERT INTO jobs(id,request) VALUES(?,?)", (operation_id, encoded)
        )
        self.db.commit()
        return None

    def job_artifact(self, operation_id: str, artifact_id: str) -> None:
        self.db.execute(
            "UPDATE jobs SET artifact_id=? WHERE id=?", (artifact_id, operation_id)
        )
        self.db.commit()

    def finish_job(self, operation_id: str, result: dict) -> dict:
        result = {**result, "operation_id": operation_id, "session_id": "media"}
        self.db.execute(
            "UPDATE jobs SET result=? WHERE id=?",
            (json.dumps(result, ensure_ascii=False), operation_id),
        )
        self.db.commit()
        return result

    def operation(self, operation_id: str) -> dict:
        row = self.db.execute(
            "SELECT result,artifact_id FROM jobs WHERE id=?", (operation_id,)
        ).fetchone()
        if not row:
            raise MediaError("Unknown media operation")
        return (
            json.loads(row[0])
            if row[0]
            else {
                "status": "outcome_unknown",
                "operation_id": operation_id,
                "artifact_id": row[1],
            }
        )


class MediaService:
    def __init__(self, settings, guard, backend_factory=NativeBackend):
        self.settings = settings
        self._guard = weakref.WeakMethod(guard)
        self.backend_factory = backend_factory
        self.store = MediaStore(settings.user_dir)
        self.lock = asyncio.Lock()
        self.active_backend = None
        self.active_task = None
        self.cancelled = threading.Event()
        self.active_io = None
        self.closing = False
        self.stopping = False
        self.idle = asyncio.Event()
        self.idle.set()
        self.offline_lease = FileLock(
            settings.user_dir / "media-worker.lock", timeout=0
        )

    def guard(self):
        if self.closing or self.stopping:
            raise MediaError("Media worker admission is closed during shutdown")
        guard = self._guard()
        if guard is None:
            raise MediaError("Owning service is no longer available")
        guard()

    def admit(self, operation_id: str, request: dict):
        try:
            self.offline_lease.acquire()
        except Timeout as exc:
            raise MediaError(
                "Another process owns the expensive offline media worker"
            ) from exc
        try:
            previous = self.store.admit_job(operation_id, request)
            if previous is not None:
                self.offline_lease.release()
            return previous
        except BaseException:
            self.offline_lease.release()
            raise

    async def cancel_jobs(self):
        self.cancelled.set()
        backend = self.active_backend
        if backend is not None:
            await self._join(asyncio.create_task(asyncio.to_thread(backend.abort)))
        if self.active_io is not None:
            await self._join(self.active_io)
        task = self.active_task
        if task is not None and task is not asyncio.current_task():
            await self._join(asyncio.create_task(self.idle.wait()))

    @staticmethod
    async def _join(task):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        with contextlib.suppress(Exception, asyncio.CancelledError):
            task.result()

    async def _native(
        self, directory: Path, operation: str, payload: dict, timeout: float
    ):
        if self.cancelled.is_set():
            raise MediaError("Media job cancelled")
        started = time.monotonic()
        backend = self.backend_factory(
            self.settings.model_copy(
                update={"user_dir": directory, "touch_mode": "adb"}
            )
        )
        self.active_backend = backend
        try:
            timeout -= time.monotonic() - started
            if timeout <= 0 or self.cancelled.is_set():
                raise MediaError(
                    "Offline media deadline or cancellation before dispatch"
                )
            payload = {
                **payload,
                "timeout_ms": max(1, min(payload["timeout_ms"], int(timeout * 1000))),
            }
            return await asyncio.to_thread(backend.request, operation, payload, timeout)
        except BaseException:
            await asyncio.to_thread(backend.abort)
            raise
        finally:
            try:
                await asyncio.to_thread(backend.close)
            finally:
                try:
                    await asyncio.to_thread(backend.abort)
                finally:
                    if self.active_backend is backend:
                        self.active_backend = None

    def _import_source(self, name: str):
        root = (
            self.settings.media_import_dir or self.settings.user_dir / "media-import"
        ).resolve()
        if (
            not name
            or Path(name).name != name
            or name.startswith(".")
            or any(c in name for c in "/\\:%?#")
        ):
            raise MediaError("Import requires a safe basename, never a path or URL")
        source = root / name
        if (
            source.suffix.lower() not in MIME
            or source.is_symlink()
            or not source.resolve().is_relative_to(root)
        ):
            raise MediaError("Unsupported or escaped imported media")
        metadata = source.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 1 <= metadata.st_size <= IMPORT_LIMIT
        ):
            raise MediaError("Imported media must be a regular file of at most 128 MiB")
        identity = (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        with self._open_import(source, identity) as stream:
            magic = stream.read(16)
        valid = (
            source.suffix.lower() == ".avi"
            and magic[:4] == b"RIFF"
            and magic[8:12] == b"AVI "
            or source.suffix.lower() in (".mp4", ".mov")
            and magic[4:8] == b"ftyp"
            or source.suffix.lower() == ".mkv"
            and magic[:4] == b"\x1aE\xdf\xa3"
        )
        if not valid:
            raise MediaError("Imported container signature is not allowlisted")
        return source, identity

    @staticmethod
    def _open_import(source: Path, identity: tuple[int, ...]):
        descriptor = os.open(
            source,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0),
        )
        stream = os.fdopen(descriptor, "rb")
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
            != identity
        ):
            stream.close()
            raise MediaError(
                "Import source identity changed or became a symlink/device"
            )
        return stream

    def _copy_import(self, source: Path, target: Path, deadline: float, identity):
        value = hashlib.sha256()
        total = 0
        with self._open_import(source, identity) as stream, target.open("xb") as output:
            for chunk in iter(lambda: stream.read(MIB), b""):
                if self.cancelled.is_set() or time.monotonic() >= deadline:
                    raise MediaError("Import cancelled or copy deadline exceeded")
                total += len(chunk)
                if total > min(IMPORT_LIMIT, identity[2]):
                    raise MediaError("Import grew beyond its validated size limit")
                value.update(chunk)
                output.write(chunk)
            metadata = os.fstat(stream.fileno())
            if total != identity[2] or metadata.st_mtime_ns != identity[3]:
                raise MediaError(
                    "Import source changed while its immutable copy was being made"
                )
        return value.hexdigest(), total

    async def import_video(self, request) -> dict:
        self.guard()
        if self.lock.locked():
            raise MediaError("Another expensive offline media job is active")
        async with self.lock:
            previous = self.admit(
                request.operation_id, {"kind": "import", **request.model_dump()}
            )
            if previous is not None:
                return previous
            self.cancelled.clear()
            artifact_id = uuid.uuid4().hex
            self.active_task = asyncio.current_task()
            self.idle.clear()
            directory = None
            try:
                self.store.job_artifact(request.operation_id, artifact_id)
                deadline = time.monotonic() + request.timeout_ms / 1000
                source, identity = self._import_source(request.import_name)
                directory = self.store.allocate(
                    artifact_id, identity[2] + RECORDING_LIMIT + MIB
                )
                filename = "input" + source.suffix.lower()
                with self.store.pin(artifact_id, pending=True):
                    self.active_io = asyncio.create_task(
                        asyncio.to_thread(
                            self._copy_import,
                            source,
                            directory / filename,
                            deadline,
                            identity,
                        )
                    )
                    sha256, size = await asyncio.shield(self.active_io)
                    self.active_io = None
                    remaining = int((deadline - time.monotonic()) * 1000)
                    if remaining <= 0 or self.cancelled.is_set():
                        raise MediaError("Import cancelled or deadline exceeded")
                    inspected = await self._native(
                        directory,
                        "media_inspect",
                        {"filename": filename, "timeout_ms": remaining},
                        remaining / 1000,
                    )
                manifest = {
                    **inspected,
                    "artifact_id": artifact_id,
                    "source_kind": "imported_video",
                    "declared_context": request.declared_context,
                    "context_source": "caller_claim_not_recognized_fact",
                    "context_verified": False,
                    "input_sha256": sha256,
                    "video": {
                        "status": "ready"
                        if inspected.get("decoded_complete")
                        else "unverified",
                        "path": filename,
                        "bytes": size,
                        "mime_type": MIME[source.suffix.lower()],
                        "original_import": True,
                        "codec": inspected.get("reported_codec"),
                        "codec_source": "native_decoder_reported_tag_or_unknown",
                    },
                }
                result = {
                    "status": inspected["status"],
                    "media": self.store.publish(artifact_id, manifest),
                }
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.cancel_jobs()
                if directory is not None:
                    self.store.discard(artifact_id, "cancelled")
                self.store.finish_job(
                    request.operation_id,
                    {"status": "partial", "error": {"code": "cancelled"}},
                )
                raise
            except (OSError, RuntimeError, ValueError) as error:
                if directory is not None:
                    self.store.discard(
                        artifact_id,
                        "cancelled" if self.cancelled.is_set() else "failed",
                    )
                result = {
                    "status": "partial" if self.cancelled.is_set() else "failed",
                    "artifact_id": artifact_id,
                    "error": {
                        "code": "cancelled"
                        if self.cancelled.is_set()
                        else "media_import_failed",
                        "message": str(error),
                    },
                }
            finally:
                self.active_task = None
                self.active_io = None
                self.offline_lease.release()
                self.idle.set()
            return self.store.finish_job(request.operation_id, result)

    async def analyze_video(self, request) -> dict:
        self.guard()
        if self.lock.locked():
            raise MediaError("Another expensive offline media job is active")
        async with self.lock:
            previous = self.admit(
                request.operation_id, {"kind": "analyze", **request.model_dump()}
            )
            if previous is not None:
                return previous
            self.cancelled.clear()
            job = self.settings.user_dir / "media-jobs" / uuid.uuid4().hex
            self.active_task = asyncio.current_task()
            self.idle.clear()
            input_sha256 = None
            try:
                self.store.job_artifact(request.operation_id, request.artifact_id)
                job.mkdir(mode=0o700, parents=True)
                deadline = time.monotonic() + request.timeout_ms / 1000
                with self.store.pin(request.artifact_id):
                    manifest = self.store.info(request.artifact_id)
                    input_sha256 = manifest.get("input_sha256") or manifest.get(
                        "video", {}
                    ).get("sha256")
                    if (
                        manifest.get("source_kind") != "imported_video"
                        or manifest.get("declared_context") != "full_battle"
                        or not manifest.get("decoded_complete")
                    ):
                        raise MediaError(
                            "Legacy VideoRecognition requires a completely decoded imported video with caller-declared full_battle context; recorded short clips remain available as keyframes"
                        )
                    source = self.store.checked_file(
                        request.artifact_id, manifest["video"]["path"]
                    )
                    if (
                        await asyncio.to_thread(digest, source)
                        != manifest["input_sha256"]
                    ):
                        raise MediaError("Owned input hash changed")
                    native = await self._native(
                        job,
                        "video_analyze",
                        {"filename": str(source), "timeout_ms": request.timeout_ms},
                        max(0.001, deadline - time.monotonic()),
                    )
                    candidate = native.get("candidate")
                    result = {
                        **native,
                        "scope": "experimental_video_to_copilot",
                        "artifact_id": request.artifact_id,
                        "input_sha256": manifest["input_sha256"],
                        "declared_context_source": "caller_claim_native_still_validates_formation_and_stage",
                        "candidate": candidate,
                        "executed": False,
                        "live_state_changed": False,
                        "device_connected": False,
                        "working_set": "bounded_128_MiB_retained_normalized_images_excludes_models_and_codec_internals",
                        "warnings": [
                            "Approximate VIDEO-TO-COPILOT candidates, not generic combat-state or defeat truth",
                            "Legacy recognition can guess deployment identities and treats disappearance as Retreat",
                            "Formation/stage context and native decoder support are required",
                        ],
                    }
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.cancel_jobs()
                self.store.finish_job(
                    request.operation_id,
                    {"status": "partial", "error": {"code": "cancelled"}},
                )
                raise
            except (OSError, RuntimeError, ValueError) as error:
                result = {
                    "status": "partial" if self.cancelled.is_set() else "failed",
                    "scope": "experimental_video_to_copilot",
                    "executed": False,
                    "live_state_changed": False,
                    "error": {
                        "code": "cancelled"
                        if self.cancelled.is_set()
                        else "video_analysis_unavailable",
                        "message": str(error),
                    },
                }
            finally:
                try:
                    await asyncio.to_thread(shutil.rmtree, job, True)
                finally:
                    self.offline_lease.release()
                    self.active_task = None
                    self.idle.set()
            result.setdefault("artifact_id", request.artifact_id)
            result.setdefault("input_sha256", input_sha256)
            result.setdefault("device_connected", False)
            result.setdefault(
                "warnings",
                [
                    "Experimental video-to-copilot only; disappearance may become Retreat and identities may be guessed",
                    "No execution or live combat-state update; formation/stage and decoder support are required",
                ],
            )
            return self.store.finish_job(request.operation_id, result)
