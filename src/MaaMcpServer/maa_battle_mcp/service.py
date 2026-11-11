import asyncio
import json
import logging
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from filelock import FileLock, Timeout

from .decision import CLIENT_REGIONS, DecisionContext
from .diagnostics import provenance
from .models import BattleMode, OperationRequest, Settings
from .native import NativeBackend, NativeError

logger = logging.getLogger(__name__)


class Backend(Protocol):
    def request(self, operation: str, payload: dict, timeout: float) -> dict: ...
    def cancel(self, session_id: str, command_id: str) -> None: ...
    def abort(self) -> None: ...
    def close(self) -> None: ...


class BattleError(RuntimeError):
    pass


class BattleService:
    def __init__(
        self,
        settings: Settings,
        backend_factory: Callable[[Settings], Backend] = NativeBackend,
        decision: DecisionContext | None = None,
    ):
        self.settings = settings
        self.backend_factory = backend_factory
        self.decision = decision or DecisionContext(settings)
        self.map_info: dict | None = None
        self.mode: BattleMode | None = None
        self.backend: Backend | None = None
        self.connection_info: dict = {}
        self.session_id: str | None = None
        self.preparation_session_id: str | None = None
        self.preparation_snapshot: dict | None = None
        self.lock = asyncio.Lock()
        self.step_active = False
        self.storage_failed = False
        self.active_request: OperationRequest | None = None
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
            self.preparation_session_id = None
            self.preparation_snapshot = None
            self.decision.preparation = None
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

    async def prepare(self, stage_name: str, mode: BattleMode) -> dict:
        if self.lock.locked():
            raise BattleError("Controller is busy")
        async with self.lock:
            if self.backend is None or self.session_id is not None:
                raise BattleError(
                    "Preparation requires a connection without an attached battle"
                )
            self.preparation_snapshot = None
            self.decision.preparation = None
            self.preparation_session_id = self.preparation_session_id or str(
                uuid.uuid4()
            )
            request_id = str(uuid.uuid4())
            try:
                result = await asyncio.to_thread(
                    self.backend.request,
                    "preparation",
                    {
                        "session_id": self.preparation_session_id,
                        "command_id": request_id,
                        "action": "prepare",
                        "args": {},
                        "timeout_ms": 30_000,
                    },
                    35,
                )
            except (NativeError, asyncio.CancelledError):
                await asyncio.to_thread(self.backend.abort)
                self.backend = None
                self.preparation_session_id = None
                self.lease.release()
                raise
            if result.get("status") == "succeeded":
                result["region"] = CLIENT_REGIONS[self.settings.client_type]
                result["preparation_id"] = request_id
                result["binding"] = {
                    "stage_id": stage_name,
                    "mode": mode,
                    "source": "caller_supplied",
                }
                self.preparation_snapshot = result
            return result

    async def invalidate_preparation(self) -> dict:
        async with self.lock:
            self.preparation_snapshot = None
            self.decision.preparation = None
            return {"status": "succeeded", "preparation_valid": False}

    async def attach(
        self,
        stage_name: str,
        mode: BattleMode | None = None,
        preparation_id: str | None = None,
        confirm_preparation_unchanged: bool = False,
    ) -> dict:
        async with self.lock:
            if self.backend is None:
                raise BattleError("Connect before attaching")
            if self.session_id is not None:
                raise BattleError(
                    "A battle is already attached; close before starting another"
                )
            self.decision.validate_binding(stage_name, mode)
            prepared = self.preparation_snapshot
            if preparation_id is not None:
                if (
                    not confirm_preparation_unchanged
                    or prepared is None
                    or prepared["preparation_id"] != preparation_id
                    or prepared["binding"]["stage_id"] != stage_name
                    or prepared["binding"]["mode"] != mode
                ):
                    raise BattleError(
                        "Preparation binding is stale, mismatched or not explicitly reconfirmed"
                    )
            elif confirm_preparation_unchanged:
                raise BattleError("Reconfirmation requires preparation_id")
            session = self.preparation_session_id or str(uuid.uuid4())
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
                self.map_info = result.get("map")
                self.mode = mode
                self.decision.preparation = (
                    prepared if preparation_id is not None else None
                )
                self.preparation_snapshot = None
                self.preparation_session_id = None
                result["preparation_id"] = preparation_id
                result["preparation_binding"] = (
                    "caller_reconfirmed_not_continuously_observed"
                    if preparation_id
                    else None
                )
                result["mode"] = mode
                result["mode_source"] = "caller_supplied_not_visually_verified"
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
        if self.storage_failed:
            raise BattleError(
                "Operation storage failed; do not retry input in this service"
            )
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
            self.active_request = request
            try:
                timeout = request.timeout_ms / 1000 + (10 if action == "step" else 0)
                request_task = asyncio.create_task(
                    asyncio.to_thread(backend.request, "command", payload, timeout)
                )
                if action == "step":
                    while True:
                        try:
                            result = await asyncio.shield(request_task)
                            break
                        except asyncio.CancelledError:
                            cancelled = True
                            backend.cancel(request.session_id, request.operation_id)
                else:
                    result = await request_task
            except (NativeError, asyncio.CancelledError) as exc:
                logger.warning("Battle operation interrupted: %s", exc)
                if not request_task.done():
                    request_task.add_done_callback(
                        lambda task: None if task.cancelled() else task.exception()
                    )
                await asyncio.to_thread(backend.abort)
                self.backend = None
                self.session_id = None
                self.map_info = None
                self.mode = None
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
                if cancelled:
                    raise asyncio.CancelledError from exc
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return result
            finally:
                self.step_active = False
                self.active_request = None
            for key in ("snapshot", "capture_time_snapshot", "return_time_snapshot"):
                if isinstance(result.get(key), dict):
                    self.decision.enrich_snapshot(result[key])
            if isinstance(result.get("deployment"), list):
                self.decision.enrich_snapshot(result)
            self._save_result(request, result)
            if cancelled:
                raise asyncio.CancelledError
            return result

    def _save_result(self, request: OperationRequest, result: dict) -> None:
        try:
            self.journal.execute(
                "UPDATE operations SET result=? WHERE session=? AND id=?",
                (
                    json.dumps(result, ensure_ascii=False),
                    request.session_id,
                    request.operation_id,
                ),
            )
            self.journal.commit()
        except sqlite3.Error as exc:
            self.storage_failed = True
            try:
                self.journal.rollback()
            except sqlite3.Error:
                logger.exception("Could not roll back failed operation persistence")
            logger.exception(
                "Could not persist operation %s; input may have occurred",
                request.operation_id,
            )
            raise BattleError(
                "Operation result could not be persisted; do not retry input"
            ) from exc

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
        if backend is not None and self.step_active and self.active_request is not None:
            try:
                backend.cancel(
                    self.active_request.session_id, self.active_request.operation_id
                )
            except NativeError:
                logger.exception(
                    "Could not signal step cancellation; terminating worker with unknown pause state"
                )
                await asyncio.to_thread(backend.abort)
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
            self.map_info = None
            self.mode = None
            self.preparation_session_id = None
            self.preparation_snapshot = None
            self.decision.preparation = None
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
