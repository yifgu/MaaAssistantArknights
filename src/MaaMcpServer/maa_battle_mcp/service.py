import asyncio
import json
import logging
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from filelock import FileLock, Timeout

from .diagnostics import provenance
from .models import OperationRequest, Settings
from .native import NativeBackend, NativeError

logger = logging.getLogger(__name__)


class Backend(Protocol):
    def request(self, operation: str, payload: dict, timeout: float) -> dict: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...


class BattleError(RuntimeError):
    pass


class BattleService:
    def __init__(
        self,
        settings: Settings,
        backend_factory: Callable[[Settings], Backend] = NativeBackend,
    ):
        self.settings = settings
        self.backend_factory = backend_factory
        self.backend: Backend | None = None
        self.connection_info: dict = {}
        self.session_id: str | None = None
        self.lock = asyncio.Lock()
        self.step_active = False
        settings.user_dir.mkdir(parents=True, exist_ok=True)
        self.lease = FileLock(settings.user_dir / "controller.lock", timeout=0)
        self.journal = sqlite3.connect(settings.user_dir / "operations.sqlite3")
        self.journal.execute(
            "CREATE TABLE IF NOT EXISTS operations ("
            "session TEXT, id TEXT, request TEXT NOT NULL, result TEXT, "
            "PRIMARY KEY(session, id))"
        )
        self.journal.commit()

    def _check_session(self, session_id: str) -> Backend:
        if self.backend is None or self.session_id != session_id:
            raise BattleError("Unknown or expired battle session; reconnect and attach")
        return self.backend

    async def connect(self) -> dict:
        async with self.lock:
            if self.backend is not None:
                raise BattleError(
                    "Already connected; close the existing connection first"
                )
            try:
                self.lease.acquire()
            except Timeout as exc:
                raise BattleError(
                    "Another controller owns this user directory"
                ) from exc
            try:
                backend = self.backend_factory(self.settings)
            except (OSError, RuntimeError):
                self.lease.release()
                raise
            self.backend = backend
            try:
                result = await asyncio.to_thread(backend.request, "connect", {}, 60)
                if self.backend is not backend:
                    raise NativeError("Connection was closed during initialization")
                self.connection_info = result
                return {"status": "connected", **result}
            except (NativeError, asyncio.CancelledError):
                await asyncio.to_thread(backend.abort)
                self.backend = None
                self.connection_info = {}
                self.lease.release()
                raise

    def diagnostics(self) -> dict:
        return {**self.connection_info, **provenance(self.settings)}

    async def attach(self, stage_name: str) -> dict:
        async with self.lock:
            if self.backend is None:
                raise BattleError("Connect before attaching")
            if self.session_id is not None:
                raise BattleError(
                    "A battle is already attached; close before starting another"
                )
            session = str(uuid.uuid4())
            backend = self.backend
            try:
                result = await asyncio.to_thread(
                    backend.request,
                    "attach",
                    {"stage_name": stage_name, "session_id": session},
                    30,
                )
                if result.get("protocol_version") != 1:
                    raise NativeError("Unsupported BattleControl protocol version")
                if self.backend is not backend:
                    raise NativeError("Connection was closed during attachment")
                self.session_id = session
                return result
            except (NativeError, asyncio.CancelledError):
                await asyncio.to_thread(backend.abort)
                self.backend = None
                self.connection_info = {}
                self.lease.release()
                raise

    async def command(
        self, action: str, request: OperationRequest, args: dict | None = None
    ) -> dict:
        # Do not silently queue tactical decisions behind a slow vision request.
        if self.lock.locked():
            raise BattleError(
                "Battle controller is busy; query or cancel the current operation"
            )
        async with self.lock:
            backend = self._check_session(request.session_id)
            payload = {
                "session_id": request.session_id,
                "command_id": request.operation_id,
                "action": action,
                "args": args or {},
                "timeout_ms": request.timeout_ms,
            }
            encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            previous = self.journal.execute(
                "SELECT request, result FROM operations WHERE session=? AND id=?",
                (request.session_id, request.operation_id),
            ).fetchone()
            if previous is not None:
                if previous[0] != encoded:
                    raise BattleError(
                        "operation_id was already used with different parameters"
                    )
                if previous[1] is None:
                    raise BattleError(
                        "Operation has no final result; do not retry its input"
                    )
                return json.loads(previous[1])
            self.journal.execute(
                "INSERT INTO operations(session,id,request) VALUES(?,?,?)",
                (request.session_id, request.operation_id, encoded),
            )
            self.journal.commit()
            cancelled = False
            self.step_active = action == "step"
            try:
                timeout = request.timeout_ms / 1000 + (10 if action == "step" else 0)
                request_task = asyncio.create_task(
                    asyncio.to_thread(backend.request, "command", payload, timeout)
                )
                if action == "step":
                    try:
                        result = await asyncio.shield(request_task)
                    except asyncio.CancelledError:
                        # Let Core finish its prepared capture-and-pause cleanup before releasing the lock.
                        cancelled = True
                        result = await asyncio.shield(request_task)
                else:
                    result = await request_task
            except (NativeError, asyncio.CancelledError) as exc:
                logger.warning("Battle operation interrupted: %s", exc)
                await asyncio.to_thread(backend.abort)
                self.backend = None
                self.session_id = None
                self.connection_info = {}
                self.lease.release()
                result = {
                    "status": "outcome_unknown",
                    "session_id": request.session_id,
                    "command_id": request.operation_id,
                    "error": {
                        "code": "session_invalidated",
                        "message": "Worker stopped. Input may have occurred; reconnect and inspect before retrying.",
                    },
                }
                if action == "step":
                    result["pause_verified"] = False
                    result["safe_state"] = "unknown"
                self._save_result(request, result)
                if isinstance(exc, asyncio.CancelledError) or cancelled:
                    raise
                return result
            finally:
                self.step_active = False
            self._save_result(request, result)
            if cancelled:
                raise asyncio.CancelledError
            return result

    def _save_result(self, request: OperationRequest, result: dict) -> None:
        self.journal.execute(
            "UPDATE operations SET result=? WHERE session=? AND id=?",
            (
                json.dumps(result, ensure_ascii=False),
                request.session_id,
                request.operation_id,
            ),
        )
        self.journal.commit()

    async def read(self, session_id: str, action: str) -> dict:
        return await self.command(
            action,
            OperationRequest(session_id=session_id, operation_id=str(uuid.uuid4())),
        )

    def operation(self, session_id: str, operation_id: str) -> dict:
        row = self.journal.execute(
            "SELECT result FROM operations WHERE session=? AND id=?",
            (session_id, operation_id),
        ).fetchone()
        if row is None:
            raise BattleError("Unknown operation")
        if row[0] is None:
            return {
                "status": "outcome_unknown",
                "message": "Pending or interrupted operation; never automatically replay it",
            }
        return json.loads(row[0])

    def evidence_path(self, result: dict) -> Path:
        relative = result.get("image_path")
        if not isinstance(relative, str):
            raise BattleError("Core did not return screenshot evidence")
        base = (self.settings.user_dir / "battle-control").resolve()
        path = (self.settings.user_dir / relative).resolve()
        if not path.is_relative_to(base) or path.suffix != ".png" or not path.is_file():
            raise BattleError("Invalid screenshot path from Core")
        if path.stat().st_size > 16 * 1024 * 1024:
            raise BattleError("Screenshot exceeds the 16 MiB evidence limit")
        return path

    async def close(self) -> dict:
        backend = self.backend
        if backend is not None and self.lock.locked() and not self.step_active:
            await asyncio.to_thread(backend.abort)
        async with self.lock:
            if self.backend is not None and self.backend is not backend:
                raise BattleError(
                    "Connection changed during closure; close it explicitly"
                )
            current = self.backend
            self.backend = None
            self.session_id = None
            self.connection_info = {}
            if current is not None:
                try:
                    await asyncio.to_thread(current.close)
                except NativeError:
                    logger.exception("Graceful shutdown failed; worker was terminated")
            self.lease.release()
        return {
            "status": "closed",
            "message": "Control released. This does not pause, abandon or stop the game.",
        }
