import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--configuration", choices=["Debug", "RelWithDebInfo"], required=True
    )
    parser.add_argument("--platform", choices=["win-x64", "linux-x64"], required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    prefix = Path(run(str(args.python), "-I", "-c", "import sys; print(sys.prefix)"))
    shutil.copytree(prefix, output / "python", symlinks=True)
    python = (
        output / "python" / ("python.exe" if sys.platform == "win32" else "bin/python3")
    )
    shutil.copytree(args.adb, output / "adb", symlinks=True)
    for name in ("run_mcp.py", "run-mcp.cmd", "run-mcp.sh"):
        shutil.copy2(Path(__file__).parent / name, output / name)
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
    manifest = {
        "source_commit": run("git", "rev-parse", "HEAD"),
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


if __name__ == "__main__":
    main()
