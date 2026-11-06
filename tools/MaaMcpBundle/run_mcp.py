import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "BUILD-INFO.json").read_text(encoding="utf-8"))
    core_dir = root / "core"
    adb = root / "adb" / ("adb.exe" if sys.platform == "win32" else "adb")
    args = sys.argv[1:]
    if args == ["--self-test"]:
        if manifest["configuration"] != "Debug":
            print("Use a Debug bundle for the full test suite.", file=sys.stderr)
            return 2
        import pytest

        os.environ["MAA_MCP_NATIVE_CORE"] = str(core_dir)
        os.environ["MAA_MCP_RESOURCE_DIR"] = str(core_dir)
        return int(
            pytest.main(
                [
                    str(root / "src/MaaMcpServer/tests"),
                    "-q",
                    "-p",
                    "no:cacheprovider",
                ]
            )
        )
    if args == ["--check"]:
        from maa_battle_mcp.models import Settings
        from maa_battle_mcp.native import NativeBackend

        subprocess.run([str(adb), "version"], check=True)
        if manifest["control_unit"]:
            dll_directory = (
                os.add_dll_directory(str(core_dir)) if sys.platform == "win32" else None
            )
            try:
                control = ctypes.CDLL(
                    str(
                        core_dir
                        / (
                            "MaaAdbControlUnit.dll"
                            if sys.platform == "win32"
                            else "libMaaAdbControlUnit.so"
                        )
                    )
                )
                control.MaaAdbControlUnitGetVersion.restype = ctypes.c_char_p
                print(
                    "ADB ControlUnit:", control.MaaAdbControlUnitGetVersion().decode()
                )
            finally:
                if dll_directory is not None:
                    dll_directory.close()
        with tempfile.TemporaryDirectory(prefix="maa-mcp-check-") as directory:
            backend = NativeBackend(
                Settings(
                    core_dir=core_dir,
                    resource_dir=core_dir,
                    user_dir=Path(directory),
                    adb_path=str(adb),
                    address="unused",
                )
            )
            try:
                ready = backend.request(
                    "attach",
                    {"session_id": "bundle-check", "stage_name": "main_01-07"},
                    30,
                )
                if ready.get("protocol_version") != 1:
                    raise RuntimeError("Unsupported BattleControl protocol")
                print("MaaCore BattleControl protocol:", ready["protocol_version"])
            finally:
                backend.close()
        print("Bundle dependencies loaded; no device was connected.")
        return 0

    from maa_battle_mcp.server import main as serve

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--touch-mode")
    options, _ = parser.parse_known_args(args)
    if options.touch_mode == "MaaFwAdb" and not manifest["control_unit"]:
        parser.error("MaaFwAdb requires the RelWithDebInfo bundle on Windows")

    sys.argv = [
        "maa-battle-mcp",
        "--core-dir",
        str(core_dir),
        "--adb-path",
        str(adb),
        "--user-dir",
        str(root / "state"),
        *args,
    ]
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
