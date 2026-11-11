"""Isolate native code and its blocking calls from the MCP event loop."""

import ctypes
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

from .diagnostics import provenance, stage_data
from .models import Settings

logger = logging.getLogger(__name__)


class NativeError(RuntimeError):
    pass


def _log_phase(phase: str) -> None:
    logger.info("MaaCore worker phase: %s", phase)


class NativeCore:
    def __init__(
        self, settings: Settings, progress: Callable[[str], None] = _log_phase
    ):
        progress("core_load")
        names = {
            "win32": "MaaCore.dll",
            "linux": "libMaaCore.so",
            "darwin": "libMaaCore.dylib",
        }
        name = names.get(sys.platform)
        if name is None:
            raise NativeError(f"Unsupported platform: {sys.platform}")
        self._dll_directory = (
            os.add_dll_directory(str(settings.core_dir))
            if sys.platform == "win32"
            else None
        )
        loader = ctypes.WinDLL if sys.platform == "win32" else ctypes.CDLL
        self.lib = loader(str(settings.core_dir / name))
        self.handle: int | None = None
        self.events: queue.Queue[tuple[int, dict[str, Any]]] = queue.Queue()
        self._callback_type = ctypes.CFUNCTYPE(
            None, ctypes.c_int32, ctypes.c_char_p, ctypes.c_void_p
        )
        self._callback = self._callback_type(self._on_callback)
        self._bind()
        progress("resource_load")
        settings.user_dir.mkdir(parents=True, exist_ok=True)
        self._require(
            self.lib.AsstSetUserDir(str(settings.user_dir).encode("utf-8")),
            "AsstSetUserDir",
        )
        self._require(
            self.lib.AsstLoadResource(str(settings.resource_dir).encode("utf-8")),
            "AsstLoadResource",
        )
        if settings.client_type not in ("Official", "Bilibili"):
            overlay = settings.resource_dir / "resource/global" / settings.client_type
            self._require(
                self.lib.AsstLoadResource(str(overlay).encode("utf-8")),
                "AsstLoadResource(client overlay)",
            )
        progress("core_create")
        self.handle = self.lib.AsstCreateEx(self._callback, None)
        self._require(self.handle, "AsstCreateEx")
        for key, value in (
            (2, settings.touch_mode),
            (3, "0"),
            (5, "0"),
            (6, settings.client_type),
        ):
            self._require(
                self.lib.AsstSetInstanceOption(self.handle, key, value.encode("utf-8")),
                f"AsstSetInstanceOption({key})",
            )
        self.task_id: int | None = None
        self.preparation_session: str | None = None

    def _bind(self) -> None:
        pointer = ctypes.c_void_p
        text = ctypes.c_char_p
        integer = ctypes.c_int32
        boolean = ctypes.c_uint8
        declarations = {
            "AsstSetUserDir": (boolean, [text]),
            "AsstLoadResource": (boolean, [text]),
            "AsstCreateEx": (pointer, [self._callback_type, pointer]),
            "AsstDestroy": (None, [pointer]),
            "AsstSetInstanceOption": (boolean, [pointer, integer, text]),
            "AsstAsyncConnect": (
                integer,
                [pointer, text, text, text, boolean],
            ),
            "AsstConnected": (boolean, [pointer]),
            "AsstAppendTask": (integer, [pointer, text, text]),
            "AsstSetTaskParams": (boolean, [pointer, integer, text]),
            "AsstStart": (boolean, [pointer]),
            "AsstStop": (boolean, [pointer]),
            "AsstGetVersion": (text, []),
        }
        for name, (result, arguments) in declarations.items():
            function = getattr(self.lib, name)
            function.restype = result
            function.argtypes = arguments

    @staticmethod
    def _require(value: object, operation: str) -> None:
        if not value:
            raise NativeError(f"{operation} failed; inspect MaaCore asst.log")

    def _on_callback(self, message: int, data: bytes, _context: int) -> None:
        import json

        try:
            event = json.loads(data)
            if message in (0, 1, 10_000, 10_004) or event.get("what") in (
                "BattleControlReady",
                "BattleControlResult",
            ):
                self.events.put((message, event))
        except (ValueError, TypeError):
            logger.exception("Invalid MaaCore callback")

    def _wait(
        self,
        what: str,
        timeout: float,
        command_id: str | None = None,
        cancelled: threading.Event | None = None,
        session_id: str | None = None,
    ) -> dict:
        deadline = time.monotonic() + timeout
        cancel_sent = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeError("MaaCore callback deadline exceeded")
            try:
                if cancelled is not None and cancelled.is_set() and not cancel_sent:
                    self._require(
                        self.lib.AsstSetTaskParams(
                            self.handle,
                            self.task_id,
                            json.dumps(
                                {
                                    "session_id": session_id,
                                    "cancel_command_id": command_id,
                                }
                            ).encode("utf-8"),
                        ),
                        "BattleControl cancellation",
                    )
                    cancel_sent = True
                message, event = self.events.get(timeout=min(remaining, 0.05))
            except queue.Empty as exc:
                if time.monotonic() >= deadline:
                    raise NativeError("MaaCore callback deadline exceeded") from exc
                continue
            if message in (0, 1, 10_000, 10_004):
                raise NativeError(f"MaaCore task stopped or failed: {event}")
            details = event.get("details", {})
            if event.get("what") == what and (
                command_id is None or details.get("command_id") == command_id
            ):
                return details

    def dispatch(
        self,
        operation: str,
        payload: dict,
        settings: Settings,
        cancelled: threading.Event | None = None,
    ) -> dict:
        import json

        if operation == "connect":
            call_id = self.lib.AsstAsyncConnect(
                self.handle,
                settings.adb_path.encode("utf-8"),
                settings.address.encode("utf-8"),
                settings.connection_config.encode("utf-8"),
                True,
            )
            self._require(call_id, "AsstAsyncConnect")
            self._require(self.lib.AsstConnected(self.handle), "Device connection")
            return {
                "core_version": self.lib.AsstGetVersion().decode("utf-8"),
                **provenance(settings),
            }
        if operation == "preparation":
            if self.task_id is None:
                self.preparation_session = payload["session_id"]
                self.task_id = self.lib.AsstAppendTask(
                    self.handle,
                    b"BattleControl",
                    json.dumps(
                        {
                            "session_id": self.preparation_session,
                            "preparation_only": True,
                            "allowed_summons": settings.allowed_summons,
                        }
                    ).encode("utf-8"),
                )
                self._require(self.task_id, "BattleControl preparation initialization")
                self._require(self.lib.AsstStart(self.handle), "AsstStart")
                self._wait("BattleControlReady", 30)
            if self.preparation_session != payload["session_id"]:
                raise NativeError(
                    "Preparation is only available before battle attachment"
                )
            return self.dispatch("command", payload, settings, cancelled)
        if operation == "attach":
            if (
                self.task_id is not None
                and self.preparation_session != payload["session_id"]
            ):
                raise NativeError("A battle task is already attached")
            try:
                map_source = stage_data(settings, payload["stage_name"])
            except (OSError, ValueError) as exc:
                raise NativeError(str(exc)) from exc
            if self.task_id is not None:
                ready = self.dispatch(
                    "command",
                    {
                        "session_id": payload["session_id"],
                        "command_id": "bind",
                        "action": "bind",
                        "args": {"stage_name": payload["stage_name"]},
                        "timeout_ms": 30_000,
                    },
                    settings,
                    cancelled,
                )
                if ready.get("status") != "succeeded":
                    raise NativeError(f"Battle binding failed: {ready}")
                self.preparation_session = None
            else:
                self.task_id = self.lib.AsstAppendTask(
                    self.handle,
                    b"BattleControl",
                    json.dumps(
                        {**payload, "allowed_summons": settings.allowed_summons}
                    ).encode("utf-8"),
                )
                self._require(
                    self.task_id,
                    "BattleControl task rejected preflighted stage; build this checkout's Core",
                )
                self._require(self.lib.AsstStart(self.handle), "AsstStart")
                ready = self._wait("BattleControlReady", 30)
            if ready["map"]["stage_id"] != map_source["stage_id"]:
                raise NativeError(
                    f"Core map binding mismatch for stage {payload['stage_name']!r} "
                    f"under resource root {settings.resource_dir}"
                )
            return {**ready, "map_source": map_source}
        if operation == "command":
            if self.task_id is None:
                raise NativeError("No battle task is attached")
            self._require(
                self.lib.AsstSetTaskParams(
                    self.handle, self.task_id, json.dumps(payload).encode("utf-8")
                ),
                "BattleControl command submission",
            )
            callback_timeout = payload["timeout_ms"] / 1000
            if payload["action"] == "step":
                callback_timeout += 10
            return self._wait(
                "BattleControlResult",
                callback_timeout,
                payload["command_id"],
                cancelled,
                payload["session_id"],
            )
        raise NativeError(f"Unsupported worker operation: {operation}")

    def close(self) -> None:
        if self.handle is not None:
            self.lib.AsstStop(self.handle)
            self.lib.AsstDestroy(self.handle)
            self.handle = None
        if self._dll_directory is not None:
            self._dll_directory.close()


def _worker() -> None:
    # Keep native stdout away from both the private protocol and MCP stdout.
    output_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdin.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO)
    with os.fdopen(output_fd, "w", encoding="utf-8", buffering=1) as output:
        output_lock = threading.Lock()

        def send(message: dict) -> None:
            with output_lock:
                output.write(json.dumps(message, ensure_ascii=False) + "\n")
                output.flush()

        def progress(phase: str) -> None:
            _log_phase(phase)
            send({"phase": phase})

        core = None
        active: threading.Thread | None = None
        cancellation = threading.Event()
        active_payload: dict = {}

        def dispatch(operation: str, payload: dict) -> None:
            try:
                result = core.dispatch(operation, payload, settings, cancellation)
                send({"ok": True, "result": result})
            except Exception as exc:
                logger.exception("MaaCore worker request failed")
                send({"ok": False, "error": str(exc)})

        try:
            progress("worker_started")
            settings = Settings.model_validate_json(sys.stdin.readline())
            core = NativeCore(settings, progress)
            progress("ready")
            for line in sys.stdin:
                operation, payload = json.loads(line)
                if operation == "cancel":
                    if (
                        payload.get("session_id") == active_payload.get("session_id")
                        and payload.get("command_id")
                        == active_payload.get("command_id")
                        and active_payload.get("action") == "step"
                    ):
                        cancellation.set()
                    else:
                        logger.warning("Ignoring cancellation for an inactive step")
                    continue
                if active is not None:
                    # The normal request channel stays serial; cancel is admitted above.
                    active.join()
                progress(operation)
                if operation == "close":
                    core.close()
                    core = None
                    send({"ok": True, "result": {}})
                    return
                active_payload = payload
                cancellation.clear()
                active = threading.Thread(
                    target=dispatch, args=(operation, payload), name="maa-core-request"
                )
                active.start()
        except Exception as exc:
            # This is the process boundary: never let a native/FFI error look successful.
            logger.exception("MaaCore worker failed")
            try:
                send({"ok": False, "error": str(exc)})
            except (OSError, ValueError):
                logger.exception("Unable to deliver worker failure")
        finally:
            cancellation.set()
            if active is not None:
                active.join()
            if core is not None:
                core.close()


class NativeBackend:
    def __init__(self, settings: Settings):
        self.lock = threading.Lock()
        self.send_lock = threading.RLock()
        self.active_command: tuple[str, str] | None = None
        self.last_command: tuple[str, str] | None = None
        self.pending_cancel: tuple[str, str] | None = None
        self.abort_lock = threading.Lock()
        self.aborted = False
        self.ready = False
        self.phase = "process_start"
        self.responses: queue.Queue[dict | NativeError] = queue.Queue()
        command = [sys.executable]
        if sys.flags.isolated:
            command.append("-I")
        command.extend(["-B", "-u", "-m", "maa_battle_mcp.native"])
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.reader = threading.Thread(
            target=self._read_responses, name="maa-battle-worker-reader", daemon=True
        )
        self.reader.start()
        try:
            self._send(settings.model_dump_json())
        except NativeError:
            self.abort()
            raise

    def _send(self, message: str) -> None:
        try:
            with self.send_lock:
                self.process.stdin.write(message + "\n")
                self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise NativeError(f"Worker pipe closed during {self.phase}") from exc

    def _read_responses(self) -> None:
        try:
            for line in self.process.stdout:
                response = json.loads(line)
                if not isinstance(response, dict):
                    raise ValueError("Worker response must be an object")
                if "phase" in response:
                    if not isinstance(response["phase"], str):
                        raise ValueError("Worker phase must be a string")
                elif response.get("ok") is True:
                    if not isinstance(response.get("result"), dict):
                        raise ValueError("Worker result must be an object")
                elif response.get("ok") is not False or not isinstance(
                    response.get("error"), str
                ):
                    raise ValueError("Invalid worker response envelope")
                self.responses.put(response)
        except (OSError, ValueError) as exc:
            self.responses.put(NativeError(f"Invalid worker response: {exc}"))
        finally:
            self.responses.put(NativeError("Worker response pipe closed"))

    def _receive(self, deadline: float, operation: str) -> dict:
        try:
            response = self.responses.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty as exc:
            suffix = (
                "; battle outcome unknown"
                if operation == "command" and self.ready
                else "; no battle command submitted"
            )
            raise NativeError(
                f"Worker deadline exceeded during {self.phase}{suffix}"
            ) from exc
        if isinstance(response, NativeError):
            raise NativeError(
                f"{response} during {self.phase} (exit code {self.process.poll()})"
            )
        if "phase" in response:
            self.phase = response["phase"]
            logger.info("MaaCore worker pid=%s phase=%s", self.process.pid, self.phase)
        elif response.get("ok") is False:
            raise NativeError(f"Worker failed during {self.phase}: {response['error']}")
        return response

    def request(self, operation: str, payload: dict, timeout: float) -> dict:
        with self.lock:
            try:
                deadline = time.monotonic() + timeout
                while not self.ready:
                    response = self._receive(deadline, operation)
                    if "phase" not in response:
                        raise NativeError("Worker returned a result before readiness")
                    self.ready = response.get("phase") == "ready"
                self.phase = operation
                with self.send_lock:
                    session_id, command_id = (
                        payload.get("session_id"),
                        payload.get("command_id"),
                    )
                    if (
                        operation == "command"
                        and isinstance(session_id, str)
                        and isinstance(command_id, str)
                    ):
                        self.active_command = (session_id, command_id)
                        self.last_command = self.active_command
                    self._send(json.dumps([operation, payload], ensure_ascii=False))
                    if (
                        self.pending_cancel is not None
                        and self.pending_cancel == self.active_command
                    ):
                        self.cancel(*self.pending_cancel)
                    self.pending_cancel = None
                while True:
                    response = self._receive(deadline, operation)
                    if "phase" in response:
                        continue
                    if response.get("ok") is not True:
                        raise NativeError(f"Invalid worker result during {self.phase}")
                    return response["result"]
            except NativeError:
                self.abort()
                raise
            finally:
                with self.send_lock:
                    self.active_command = None

    def cancel(self, session_id: str, command_id: str) -> None:
        target = (session_id, command_id)
        with self.send_lock:
            if self.active_command == target:
                self._send(
                    json.dumps(
                        ["cancel", {"session_id": session_id, "command_id": command_id}]
                    )
                )
            elif self.active_command is None and self.last_command != target:
                # Cancellation may arrive before the executor thread submits its command.
                self.pending_cancel = target

    def abort(self) -> None:
        with self.abort_lock:
            if self.aborted:
                return
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=2)
            self.process.stdin.close()
            self.reader.join(timeout=2)
            if not self.reader.is_alive():
                self.process.stdout.close()
            self.aborted = True

    def close(self) -> None:
        try:
            if self.process.poll() is None and not self.aborted:
                self.request("close", {}, 5)
        finally:
            self.abort()


if __name__ == "__main__":
    _worker()
