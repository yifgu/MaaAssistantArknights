"""Isolate native code and its blocking calls from the MCP event loop."""

import ctypes
import logging
import multiprocessing
import os
import queue
import sys
import threading
import time
from multiprocessing.connection import Connection
from typing import Any

from .models import Settings

logger = logging.getLogger(__name__)


class NativeError(RuntimeError):
    pass


class NativeCore:
    def __init__(self, settings: Settings):
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

    def _wait(self, what: str, timeout: float, command_id: str | None = None) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeError("MaaCore callback deadline exceeded")
            try:
                message, event = self.events.get(timeout=remaining)
            except queue.Empty as exc:
                raise NativeError("MaaCore callback deadline exceeded") from exc
            if message in (0, 1, 10_000, 10_004):
                raise NativeError(f"MaaCore task stopped or failed: {event}")
            details = event.get("details", {})
            if event.get("what") == what and (
                command_id is None or details.get("command_id") == command_id
            ):
                return details

    def dispatch(self, operation: str, payload: dict, settings: Settings) -> dict:
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
            return {"core_version": self.lib.AsstGetVersion().decode("utf-8")}
        if operation == "attach":
            if self.task_id is not None:
                raise NativeError("A battle task is already attached")
            self.task_id = self.lib.AsstAppendTask(
                self.handle, b"BattleControl", json.dumps(payload).encode("utf-8")
            )
            self._require(
                self.task_id,
                "BattleControl unavailable or invalid stage; build this checkout's Core",
            )
            self._require(self.lib.AsstStart(self.handle), "AsstStart")
            return self._wait("BattleControlReady", 30)
        if operation == "command":
            if self.task_id is None:
                raise NativeError("No battle task is attached")
            self._require(
                self.lib.AsstSetTaskParams(
                    self.handle, self.task_id, json.dumps(payload).encode("utf-8")
                ),
                "BattleControl command submission",
            )
            return self._wait(
                "BattleControlResult",
                payload["timeout_ms"] / 1000,
                payload["command_id"],
            )
        raise NativeError(f"Unsupported worker operation: {operation}")

    def close(self) -> None:
        if self.handle is not None:
            self.lib.AsstStop(self.handle)
            self.lib.AsstDestroy(self.handle)
            self.handle = None
        if self._dll_directory is not None:
            self._dll_directory.close()


def _worker(connection: Connection, settings: Settings) -> None:
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    core = None
    try:
        core = NativeCore(settings)
        while True:
            operation, payload = connection.recv()
            if operation == "close":
                core.close()
                core = None
                connection.send({"ok": True, "result": {}})
                return
            result = core.dispatch(operation, payload, settings)
            connection.send({"ok": True, "result": result})
    except EOFError:
        logger.info("MaaCore worker disconnected")
    except Exception as exc:
        # This is the process boundary: never let a native/FFI error look successful.
        logger.exception("MaaCore worker failed")
        try:
            connection.send({"ok": False, "error": str(exc)})
        except (OSError, EOFError):
            logger.error("Unable to deliver worker failure")
    finally:
        if core is not None:
            core.close()
        connection.close()


class NativeBackend:
    def __init__(self, settings: Settings):
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=_worker,
            args=(child, settings),
            name="maa-battle-worker",
            daemon=True,
        )
        self.lock = threading.Lock()
        self.abort_lock = threading.Lock()
        self.aborted = False
        self.process.start()
        child.close()

    def request(self, operation: str, payload: dict, timeout: float) -> dict:
        with self.lock:
            try:
                self.connection.send((operation, payload))
                if not self.connection.poll(timeout):
                    raise NativeError(
                        "Worker deadline exceeded; battle outcome unknown"
                    )
                response = self.connection.recv()
                if not response["ok"]:
                    raise NativeError(response["error"])
                return response["result"]
            except (OSError, EOFError) as exc:
                raise NativeError(
                    "MaaCore worker exited; battle outcome unknown"
                ) from exc
            finally:
                if not self.process.is_alive():
                    self.process.join(timeout=1)

    def abort(self) -> None:
        with self.abort_lock:
            if self.aborted:
                return
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(timeout=2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=2)
            self.connection.close()
            self.aborted = True

    def close(self) -> None:
        try:
            if self.process.is_alive():
                self.request("close", {}, 5)
        finally:
            self.abort()
