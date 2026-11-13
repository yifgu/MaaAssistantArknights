import hashlib
import json
import os
from types import SimpleNamespace

import pytest

import assemble
import native_runtime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    root = tmp_path / "native"
    (root / "install/resource").mkdir(parents=True)
    (root / "install/libMaaCore.so").write_bytes(b"core")
    (root / "licenses/opencv").mkdir(parents=True)
    (root / "licenses/opencv/copyright").write_text("notice")
    monkeypatch.setattr(native_runtime, "git", lambda *args: "commit")
    monkeypatch.setattr(native_runtime, "dependency_fingerprint", lambda: "deps")
    metadata = {
        "source_commit": "commit",
        "platform": "linux-x64",
        "configuration": "RelWithDebInfo",
        "core_version": "v1.2.3",
        "dependencies": "deps",
        "build_options": native_runtime.core_options("linux-x64"),
        "core_sha256": hashlib.sha256(b"core").hexdigest(),
    }
    (root / "native.json").write_text(json.dumps(metadata))
    return root


@pytest.mark.parametrize(
    "field",
    [
        "source_commit",
        "platform",
        "configuration",
        "core_version",
        "dependencies",
        "build_options",
        "core_sha256",
    ],
)
def test_reuse_rejects_mismatched_runtime(runtime, field):
    manifest = runtime / "native.json"
    metadata = json.loads(manifest.read_text())
    metadata[field] = "different"
    manifest.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match=field):
        native_runtime.verify_runtime(runtime, "linux-x64", "RelWithDebInfo", "v1.2.3")


def test_reuse_preserves_native_files_and_licenses_without_foreign_components(
    runtime, tmp_path
):
    native_runtime.verify_runtime(runtime, "linux-x64", "RelWithDebInfo", "v1.2.3")
    install = runtime / "install"
    for name in (
        "MaaCore.h",
        "MaaCore.pdb",
        "MaaAdbControlUnit.so",
        "MaaWin32ControlUnit.dll",
        "MAA.Updater.exe",
        "msvcp140d.dll",
        "vcruntime140_1d.dll",
        "ucrtbased.dll",
    ):
        (install / name).write_bytes(b"excluded")
    for name in ("Python", "msvc-debug"):
        (install / name).mkdir()
        (install / name / "excluded").write_text("excluded")
    (install / "libdependency.so.1").write_bytes(b"runtime")
    (install / "tool").write_text("executable")
    (install / "tool").chmod(0o755)
    if os.name != "nt":
        (install / "libdependency.so").symlink_to("libdependency.so.1")
    output = tmp_path / "bundle"
    native_runtime.copy_core(runtime, output)
    assert (output / "core/libMaaCore.so").read_bytes() == b"core"
    assert (output / "core/libdependency.so.1").read_bytes() == b"runtime"
    assert (output / "core/resource").is_dir()
    assert (output / "licenses/MaaDeps/opencv/copyright").read_text() == "notice"
    assert not (output / "core/Python").exists()
    assert not (output / "core/MaaAdbControlUnit.so").exists()
    assert not list((output / "core").glob("*d.dll"))
    assert not (output / "core/MaaCore.pdb").exists()
    assert not (output / "core/MAA.Updater.exe").exists()
    if os.name != "nt":
        assert (output / "core/libdependency.so").is_symlink()
        assert (output / "core/tool").stat().st_mode & 0o111


def test_record_runtime_reads_actual_cmake_version_and_dependency_notices(
    runtime, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    build = tmp_path / "build"
    build.mkdir()
    options = native_runtime.core_options("linux-x64")
    (build / "CMakeCache.txt").write_text(
        "// Generated cache\n"
        + "\n".join(f"{key}:BOOL={value}" for key, value in options.items())
        + "\nMAA_HASH_VERSION:STRING=v2.0.0-ci\n"
    )
    notices = tmp_path / (
        "src/MaaUtils/MaaDeps/vcpkg/installed/maa-x64-linux/share/dependency"
    )
    notices.mkdir(parents=True)
    (notices / "copyright").write_text("dependency notice")
    native_runtime.record_runtime(build, runtime, "linux-x64", "RelWithDebInfo")
    metadata = native_runtime.verify_runtime(
        runtime, "linux-x64", "RelWithDebInfo", "v2.0.0-ci"
    )
    assert metadata["core_version"] == "v2.0.0-ci"
    assert (
        runtime / "licenses/dependency/copyright"
    ).read_text() == "dependency notice"


def test_finalize_uses_verified_core_version_not_a_synthetic_source_version(
    runtime, tmp_path, monkeypatch
):
    output = tmp_path / "bundle"
    output.mkdir()
    monkeypatch.setattr(
        assemble, "run", lambda *args: "commit" if args[0] == "git" else "runtime"
    )
    assemble.finalize(
        SimpleNamespace(
            native_runtime=runtime,
            platform="linux-x64",
            configuration="RelWithDebInfo",
            core_version="v1.2.3",
            test_result=None,
        ),
        output,
    )
    manifest = json.loads((output / "BUILD-INFO.json").read_text())
    assert manifest["source_commit"] == "commit"
    assert manifest["core_version"] == "v1.2.3"


def test_bundle_fan_in_requires_matching_successful_test_receipt(
    runtime, tmp_path, monkeypatch
):
    monkeypatch.setattr(native_runtime, "git", lambda *args: "commit")
    receipt = tmp_path / "tested.json"
    receipt.write_text(
        json.dumps(native_runtime.test_result("linux-x64", "RelWithDebInfo", "v1"))
    )
    native_runtime.verify_test_result(receipt, "linux-x64", "RelWithDebInfo", "v1")
    with pytest.raises(ValueError, match="test result"):
        native_runtime.verify_test_result(receipt, "linux-x64", "Debug", "v1")
    output = tmp_path / "bundle"
    with pytest.raises(ValueError, match="test result"):
        assemble.finalize(
            SimpleNamespace(
                test_result=receipt,
                native_runtime=runtime,
                platform="linux-x64",
                configuration="Debug",
                core_version="v1",
            ),
            output,
        )
    assert not (output / "core").exists()
    monkeypatch.setattr(native_runtime, "git", lambda *args: "other-commit")
    with pytest.raises(ValueError, match="test result"):
        native_runtime.verify_test_result(receipt, "linux-x64", "RelWithDebInfo", "v1")
