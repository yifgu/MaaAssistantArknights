import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from native_runtime import copy_core, verify_runtime, verify_test_result


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def prepare(args: argparse.Namespace, output: Path) -> None:
    prefix = Path(run(str(args.python), "-I", "-c", "import sys; print(sys.prefix)"))
    shutil.copytree(prefix, output / "python", symlinks=True)
    python = (
        output / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")
    )
    shutil.copytree(args.adb, output / "adb", symlinks=True)
    for name in ("run_mcp.py", "run-mcp.cmd", "run-mcp.sh"):
        shutil.copy2(Path("tools/MaaMcpBundle") / name, output / name)
    (output / "run-mcp.sh").chmod(0o755)
    subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--quiet",
            "--all-groups",
            "--no-emit-project",
            "--project",
            "src/MaaMcpServer",
            "--output-file",
            str(output / "requirements.lock.txt"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--break-system-packages",
            "--require-hashes",
            "-r",
            str(output / "requirements.lock.txt"),
        ],
        check=True,
    )
    wheels = list((output / "dist").glob("maa_battle_mcp-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one MCP wheel")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--break-system-packages",
            "--no-deps",
            str(wheels[0]),
        ],
        check=True,
    )


def finalize(args: argparse.Namespace, output: Path) -> None:
    native = None
    if args.test_result:
        verify_test_result(
            args.test_result, args.platform, args.configuration, args.core_version
        )
    if args.native_runtime:
        native = verify_runtime(
            args.native_runtime,
            args.platform,
            args.configuration,
            args.core_version or run("git", "rev-parse", "HEAD"),
        )
        copy_core(args.native_runtime, output)
    else:
        share = (
            Path("src/MaaUtils/MaaDeps/vcpkg/installed")
            / ("maa-x64-windows" if sys.platform == "win32" else "maa-x64-linux")
            / "share"
        )
        licenses = list(share.glob("*/copyright"))
        if not licenses:
            raise RuntimeError("MaaDeps copyright notices are missing")
        for license_file in licenses:
            destination = output / "licenses/MaaDeps" / license_file.parent.name
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(license_file, destination / "copyright")
    python = (
        output / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")
    )
    manifest = {
        "source_commit": run("git", "rev-parse", "HEAD"),
        "core_version": native["core_version"] if native else None,
        "configuration": args.configuration,
        "platform": args.platform,
        "control_unit": not (
            args.platform == "win-x64" and args.configuration == "Debug"
        ),
        "python": run(str(python), "--version"),
        "adb": run(
            str(output / "adb" / ("adb.exe" if sys.platform == "win32" else "adb")),
            "version",
        ),
        "run_url": (
            f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
            f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            if os.environ.get("GITHUB_RUN_ID")
            else None
        ),
    }
    (output / "BUILD-INFO.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path)
    parser.add_argument("--adb", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--configuration", choices=["Debug", "RelWithDebInfo"], required=True
    )
    parser.add_argument("--platform", choices=["win-x64", "linux-x64"], required=True)
    parser.add_argument(
        "--phase", choices=["all", "prepare", "finalize"], default="all"
    )
    parser.add_argument("--native-runtime", type=Path)
    parser.add_argument("--test-result", type=Path)
    parser.add_argument("--core-version", default="")
    args = parser.parse_args()
    if args.phase != "finalize" and (not args.python or not args.adb):
        parser.error("--python and --adb are required to prepare the environment")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.phase != "finalize":
        prepare(args, output)
    if args.phase != "prepare":
        finalize(args, output)


if __name__ == "__main__":
    main()
