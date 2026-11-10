import hashlib
import json
from types import SimpleNamespace

import pytest

from maa_battle_mcp.diagnostics import provenance, stage_data
from maa_battle_mcp.native import NativeCore, NativeError


@pytest.fixture
def catalog(settings):
    root = settings.resource_dir / "resource/Arknights-Tile-Pos"
    root.mkdir(parents=True)
    entries = {}
    for stage in ("act55side_ex01", "act55side_ex01#f#"):
        filename = stage.replace("#", "-") + ".json"
        (root / filename).write_text("{}", encoding="utf-8")
        entries[stage] = {
            "stageId": stage,
            "levelId": "activities/act55side/level_act55side_ex01",
            "code": "TL-EX-1",
            "filename": filename,
        }
    (root / "overview.json").write_text(json.dumps(entries), encoding="utf-8")
    return root, entries


def test_stage_preflight_never_binds_shared_display_code(settings, catalog):
    root, entries = catalog
    for stage in entries:
        result = stage_data(settings, stage)
        assert result["stage_id"] == stage
        assert result["map_sha256"] == hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(ValueError, match="0 exact stage/level ID matches"):
        stage_data(settings, "TL-EX-1")
    with pytest.raises(ValueError, match="2 exact stage/level ID matches"):
        stage_data(settings, "activities/act55side/level_act55side_ex01")


def test_unique_level_alias_is_accepted(settings, catalog):
    root, entries = catalog
    entries.pop("act55side_ex01#f#")
    (root / "overview.json").write_text(json.dumps(entries), encoding="utf-8")
    assert stage_data(
        settings, "activities/act55side/level_act55side_ex01"
    ) == stage_data(settings, "act55side_ex01")


def test_missing_stage_reports_id_and_configured_root(settings, catalog):
    with pytest.raises(ValueError) as error:
        stage_data(settings, "act55side_ex02")
    assert "act55side_ex02" in str(error.value)
    assert str(settings.resource_dir) in str(error.value)


def test_catalog_entry_requires_existing_map(settings, catalog):
    root, entries = catalog
    (root / entries["act55side_ex01"]["filename"]).unlink()
    with pytest.raises(ValueError, match="Missing map data"):
        stage_data(settings, "act55side_ex01")


def test_preflight_happens_before_native_task_submission(settings, catalog):
    core = NativeCore.__new__(NativeCore)
    core.task_id = None
    with pytest.raises(NativeError, match="act55side_ex02"):
        core.dispatch("attach", {"stage_name": "act55side_ex02"}, settings)


def test_core_map_binding_must_match_preflight(settings, catalog, monkeypatch):
    core = NativeCore.__new__(NativeCore)
    core.task_id = None
    core.handle = 1
    core.lib = SimpleNamespace(
        AsstAppendTask=lambda *_: 1,
        AsstStart=lambda *_: True,
    )
    monkeypatch.setattr(
        core, "_wait", lambda *_: {"map": {"stage_id": "act55side_ex01#f#"}}
    )
    with pytest.raises(NativeError, match="Core map binding mismatch"):
        core.dispatch(
            "attach", {"stage_name": "act55side_ex01", "session_id": "s"}, settings
        )


def test_missing_catalog_is_not_reported_as_missing_capability(settings):
    with pytest.raises(ValueError, match="Cannot read map catalog"):
        stage_data(settings, "act55side_ex01")


def test_provenance_separates_package_build_and_resource_identity(settings, catalog):
    settings.core_dir.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "source_commit": "tested-commit",
        "configuration": "RelWithDebInfo",
        "platform": "win-x64",
    }
    (settings.core_dir.parent / "BUILD-INFO.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    info = provenance(settings)
    assert info["project_version"] == "0.1.0"
    assert info["build"] == manifest
    assert info["resources"]["root"] == str(settings.resource_dir)
    before = info["resources"]["map_catalog_sha256"]
    root, _ = catalog
    with (root / "overview.json").open("a", encoding="utf-8") as file:
        file.write("\n")
    assert provenance(settings)["resources"]["map_catalog_sha256"] != before


def test_absent_provenance_is_explicit(settings):
    info = provenance(settings)
    assert info["build"] is None
    assert info["resources"]["map_catalog_sha256"] is None
