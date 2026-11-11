"""Exercise the production worker transport without a device or native library."""

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from maa_battle_mcp import native


class FixtureCore:
    def __init__(self, settings, progress):
        progress("core_load")
        if settings.address == "startup-hang":
            time.sleep(60)
        if settings.address == "startup-crash":
            os._exit(7)
        progress("resource_load")
        os.write(sys.stdout.fileno(), b"fixture native stdout\n")

    def dispatch(self, operation, payload, settings, cancelled=None):
        if settings.address == "connect-hang" and operation == "connect":
            time.sleep(60)
        if operation == "connect":
            return {"core_version": "fixture-only"}
        if operation == "attach":
            return {
                "protocol_version": 1,
                "session_id": payload["session_id"],
                "map": {"stage_id": payload["stage_name"]},
            }
        if payload.get("action") == "hang":
            time.sleep(60)
        if payload.get("action") == "crash":
            os._exit(7)
        if payload.get("action") == "step":
            assert isinstance(cancelled, threading.Event)
            cancelled.wait(5)
        return {
            "status": "failed"
            if cancelled is not None and cancelled.is_set()
            else "succeeded",
            "session_id": payload["session_id"],
            "command_id": payload["command_id"],
            "snapshot": {"fixture": True},
        }

    def close(self):
        pass


def install_worker_fixture():
    popen = subprocess.Popen

    def start_worker(_command, **kwargs):
        return popen(
            [sys.executable, "-B", "-u", str(Path(__file__).resolve()), "worker"],
            **kwargs,
        )

    native.subprocess.Popen = start_worker


if __name__ == "__main__":
    mode = sys.argv.pop(1)
    if mode == "worker":
        native.NativeCore = FixtureCore
        native._worker()
    elif mode == "server":
        from maa_battle_mcp.server import main

        install_worker_fixture()
        main()
    else:
        raise ValueError(f"Unknown fixture mode: {mode}")
