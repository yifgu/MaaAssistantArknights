"""Record and validate same-run native runtimes before reusing them in MCP bundles."""

import argparse
import fnmatch
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True).strip()


def dependency_fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(git("-C", "src/MaaUtils", "rev-parse", "HEAD").encode())
    for name in (
        "tools/maadeps-download.py",
        "src/MaaUtils/tools/maadeps_download.py",
        "src/MaaUtils/tools/maadeps_extract.py",
    ):
        path = Path(name)
        digest.update(name.encode())
        digest.update(path.read_bytes() if path.is_file() else b"<absent>")
    return digest.hexdigest()


def core_options(platform: str) -> dict[str, str]:
    return {
        "BUILD_MAA_UTILS": "ON",
        "INSTALL_RESOURCE": "ON",
        "INSTALL_FLATTEN": "ON",
        "WITH_EMULATOR_EXTRAS": "ON" if platform.startswith("win-") else "OFF",
        "WITH_EXTRA_CALLERS": "OFF",
        "WITH_MAC_SCK": "OFF",
        "WITH_RPATH_LIBRARY": "ON" if platform.startswith("linux-") else "OFF",
        "MAADEPS_TRIPLET": (
            "maa-" + platform.removeprefix("win-") + "-windows"
            if platform.startswith("win-")
            else "maa-" + platform.removeprefix("linux-") + "-linux"
        ),
    }


def core_library(runtime: Path, platform: str) -> Path:
    return (
        runtime
        / "install"
        / ("MaaCore.dll" if platform.startswith("win-") else "libMaaCore.so")
    )


def record_runtime(
    build: Path, runtime: Path, platform: str, configuration: str
) -> None:
    cache = {}
    for line in (build / "CMakeCache.txt").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith(("//", "#")) and "=" in line:
            name, value = line.split("=", 1)
            cache[name.split(":", 1)[0]] = value
    options = {name: cache.get(name) for name in core_options(platform)}
    if options != core_options(platform):
        raise ValueError(f"Unexpected Core build options: {options}")
    version = cache["MAA_HASH_VERSION"]
    if not version or version == "DEBUG_VERSION":
        raise ValueError("An explicit Core version is required")
    notices = (
        Path("src/MaaUtils/MaaDeps/vcpkg/installed")
        / core_options(platform)["MAADEPS_TRIPLET"]
        / "share"
    )
    licenses = list(notices.glob("*/copyright"))
    if not licenses:
        raise ValueError("MaaDeps copyright notices are missing")
    for license_file in licenses:
        destination = runtime / "licenses" / license_file.parent.name
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copy2(license_file, destination / "copyright")
    metadata = {
        "source_commit": git("rev-parse", "HEAD"),
        "platform": platform,
        "configuration": configuration,
        "core_version": version,
        "dependencies": dependency_fingerprint(),
        "build_options": options,
        "core_sha256": hashlib.sha256(
            core_library(runtime, platform).read_bytes()
        ).hexdigest(),
    }
    (runtime / "native.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )


def verify_runtime(
    runtime: Path, platform: str, configuration: str, version: str
) -> dict:
    metadata = json.loads((runtime / "native.json").read_text(encoding="utf-8"))
    expected = {
        "source_commit": git("rev-parse", "HEAD"),
        "platform": platform,
        "configuration": configuration,
        "core_version": version,
        "dependencies": dependency_fingerprint(),
        "build_options": core_options(platform),
        "core_sha256": hashlib.sha256(
            core_library(runtime, platform).read_bytes()
        ).hexdigest(),
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(f"Native runtime mismatch: {key}")
    if not (runtime / "install/resource").is_dir():
        raise ValueError("Native runtime resources are missing")
    if not list((runtime / "licenses").glob("*/copyright")):
        raise ValueError("Native runtime licenses are missing")
    return metadata


def test_result(platform: str, configuration: str, version: str) -> dict[str, str]:
    return {
        "source_commit": git("rev-parse", "HEAD"),
        "platform": platform,
        "configuration": configuration,
        "core_version": version or git("rev-parse", "HEAD"),
    }


def verify_test_result(
    path: Path, platform: str, configuration: str, version: str
) -> None:
    if json.loads(path.read_text(encoding="utf-8")) != test_result(
        platform, configuration, version
    ):
        raise ValueError(
            "MCP test result does not match the selected source and bundle"
        )


def copy_core(runtime: Path, output: Path) -> None:
    excluded = (
        "python",
        "msvc-debug",
        "*.h",
        "*.pdb",
        "*.bak",
        "maa.updater*",
        "*controlunit*",
        "ucrtbased.dll",
        "msvcp*d.dll",
        "vcruntime*d.dll",
        "concrt*d.dll",
        "vccorlib*d.dll",
    )

    def ignore(directory: str, names: list[str]) -> list[str]:
        if Path(directory) != runtime / "install":
            return []
        return [
            name
            for name in names
            if any(fnmatch.fnmatchcase(name.lower(), pattern) for pattern in excluded)
        ]

    shutil.copytree(runtime / "install", output / "core", symlinks=True, ignore=ignore)
    shutil.copytree(runtime / "licenses", output / "licenses/MaaDeps", symlinks=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--core-version", default="")
    parser.add_argument("--tested-bundle", type=Path)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument(
        "--platform",
        choices=["win-x64", "win-arm64", "linux-x64", "linux-arm64"],
        required=True,
    )
    parser.add_argument(
        "--configuration", choices=["Debug", "RelWithDebInfo"], required=True
    )
    args = parser.parse_args()
    if args.tested_bundle:
        args.tested_bundle.write_text(
            json.dumps(
                test_result(args.platform, args.configuration, args.core_version),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    elif not args.runtime:
        parser.error("--runtime is required when recording or verifying a runtime")
    elif args.verify:
        verify_runtime(
            args.runtime,
            args.platform,
            args.configuration,
            args.core_version or git("rev-parse", "HEAD"),
        )
    elif args.build:
        record_runtime(args.build, args.runtime, args.platform, args.configuration)
    else:
        parser.error("--build is required when not verifying a runtime")


if __name__ == "__main__":
    main()
