import json
import sys

import pytest

import run_mcp


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(run_mcp, "__file__", str(tmp_path / "run_mcp.py"))
    manifest = {"configuration": "Debug", "control_unit": True}
    (tmp_path / "BUILD-INFO.json").write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_launcher_uses_bundle_paths_and_forwards_options(bundle, monkeypatch):
    from maa_battle_mcp import server

    calls = []
    monkeypatch.setattr(server, "main", lambda: calls.append(sys.argv.copy()))
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_mcp.py", "--address", "localhost:5556", "--user-dir", "custom"],
    )
    assert run_mcp.main() == 0
    assert calls == [
        [
            "maa-battle-mcp",
            "--core-dir",
            str(bundle / "core"),
            "--adb-path",
            str(bundle / "adb" / ("adb.exe" if sys.platform == "win32" else "adb")),
            "--user-dir",
            str(bundle / "state"),
            "--address",
            "localhost:5556",
            "--user-dir",
            "custom",
        ]
    ]


def test_full_suite_uses_bundled_core(bundle, monkeypatch):
    calls = []
    monkeypatch.setattr(pytest, "main", lambda args: calls.append(args) or 3)
    monkeypatch.setattr(sys, "argv", ["run_mcp.py", "--self-test"])
    monkeypatch.setenv("MAA_MCP_NATIVE_CORE", "unrelated")
    monkeypatch.setenv("MAA_MCP_RESOURCE_DIR", "unrelated")
    assert run_mcp.main() == 3
    import os

    assert os.environ["MAA_MCP_NATIVE_CORE"] == str(bundle / "core")
    assert os.environ["MAA_MCP_RESOURCE_DIR"] == str(bundle / "core")
    assert calls[0][0] == str(bundle / "src/MaaMcpServer/tests")


def test_release_bundle_rejects_full_suite(bundle, monkeypatch, capsys):
    (bundle / "BUILD-INFO.json").write_text(
        json.dumps({"configuration": "RelWithDebInfo", "control_unit": True}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["run_mcp.py", "--self-test"])
    assert run_mcp.main() == 2
    assert "Debug bundle" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments", [["--touch-mode", "MaaFwAdb"], ["--touch-mode=MaaFwAdb"]]
)
def test_unavailable_control_unit_is_rejected(bundle, monkeypatch, arguments):
    (bundle / "BUILD-INFO.json").write_text(
        json.dumps({"configuration": "Debug", "control_unit": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["run_mcp.py", *arguments])
    with pytest.raises(SystemExit) as error:
        run_mcp.main()
    assert error.value.code == 2
