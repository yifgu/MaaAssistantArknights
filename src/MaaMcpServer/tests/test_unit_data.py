import importlib.util
import json
import os
from pathlib import Path

import pytest

from maa_battle_mcp.server import create_server
from maa_battle_mcp.service import BattleService
from maa_battle_mcp.unit_data import UnitCatalog


@pytest.fixture
def resource_root():
    configured = os.getenv("MAA_MCP_RESOURCE_DIR") or os.getenv("MAA_MCP_NATIVE_CORE")
    return Path(configured) if configured else Path(__file__).resolve().parents[3]


@pytest.fixture
def bundled(resource_root):
    return UnitCatalog(resource_root)


def test_grenade_effects_are_local_and_respawn_is_not_explosion_timer(bundled):
    result = bundled.get("trap_341_dsjtzd")
    assert result["status"] == "succeeded"
    unit = result["unit"]
    assert unit["entity_kind"] == "trap"
    assert unit["name"] == "自爆榴弹"
    assert "翻板" in unit["description"]
    assert unit["skills"] == []
    assert unit["base_range_definitions"]["x-4"]["grids"]
    stats = unit["base_attributes"][0]["key_frames"][0]["data"]
    assert stats["cost"] == 20
    assert stats["respawnTime"] == 20
    assert unit["talents"][0]["candidates"][0]["blackboard"][0]["value"] == 5
    assert unit["capabilities"]["manual_retreat"]["value"] is None
    assert unit["capabilities"]["standard_drag"]["value"] is None
    assert unit["mechanism_support"]["status"] == "unsupported"
    assert unit["mechanism_support"]["respawn_time_is_explosion_timer"] is False
    assert len(result["source"]["commit"]) == 40


def test_drone_adapter_and_owner_relations_do_not_claim_live_stats(bundled):
    drone = bundled.get("token_10000_silent_healrb")["unit"]
    assert drone["entity_kind"] == "token"
    assert drone["capabilities"]["standard_drag"]["value"] is True
    assert drone["capabilities"]["direction"]["value"] is False
    assert drone["capabilities"]["standard_drag"]["game_data_value"] is None
    assert drone["capabilities"]["manual_retreat"]["live_verified"] is False
    assert "skcom_withdraw" in drone["skill_definitions"]
    silence = bundled.get("char_108_silent")["unit"]
    assert any(
        relation["game_id"] == "token_10000_silent_healrb"
        and relation["source_field"] == "skills.overrideTokenKey"
        and relation["condition"]["skillId"]
        for relation in silence["token_relations"]
    )
    assert "cost" not in drone
    assert "available" not in drone
    assert bundled.get("not_a_game_id")["error"]["code"] == "unknown_game_id"
    assert bundled.get("char_1001_amiya2")["unit"]["entity_kind"] == "operator"
    assert bundled.get("char_1037_amiya3")["unit"]["entity_kind"] == "operator"


async def test_query_requires_neither_connection_nor_provider(settings, resource_root):
    settings.resource_dir = resource_root
    service = BattleService(settings)
    try:
        server = create_server(service)
        response = await server.call_tool(
            "battle_get_unit_info", {"game_id": "trap_341_dsjtzd"}
        )
        assert not response.isError, response.structuredContent
        assert service.backend is None
        assert service.session_id is None
    finally:
        service.journal.close()


def test_missing_and_invalid_metadata_do_not_become_unknown_permission(tmp_path):
    catalog = UnitCatalog(tmp_path)
    assert catalog.get("token")["error"]["code"] == "unit_metadata_unavailable"
    path = tmp_path / "resource/unit_info.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema_version": 99}))
    assert catalog.get("token")["error"]["code"] == "unit_metadata_unavailable"


@pytest.mark.parametrize(
    "field, value",
    [("skills", [{"skillId": []}]), ("base_attributes", [{"range_id": {}}])],
)
def test_malformed_references_fail_closed_instead_of_raising(tmp_path, field, value):
    unit = {"game_id": "token", "skills": [], "base_attributes": [], field: value}
    path = tmp_path / "resource/unit_info.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chars": {"token": unit},
                "skills": {},
                "ranges": {},
                "source": {},
            }
        )
    )
    assert UnitCatalog(tmp_path).get("token")["error"]["code"] == (
        "unit_metadata_unavailable"
    )


def test_regeneration_verifies_source_bytes_before_invoking_updater(
    tmp_path, monkeypatch
):
    import hashlib

    root = Path(__file__).resolve().parents[3]
    generator = root / "tools/ResourceUpdater/generate_unit_info.py"
    if not generator.is_file():
        generator = Path.cwd() / "tools/ResourceUpdater/generate_unit_info.py"
    if not generator.is_file():
        pytest.skip("Regeneration tests require a ResourceUpdater source checkout")
    spec = importlib.util.spec_from_file_location("unit_generator", generator)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    input_dir = tmp_path / "excel"
    input_dir.mkdir()
    table = input_dir / "character_table.json"
    table.write_bytes(b'{"synthetic":true}')
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "files": {
                    "character_table.json": {
                        "sha256": hashlib.sha256(table.read_bytes()).hexdigest()
                    }
                }
            }
        )
    )
    calls = []
    monkeypatch.setattr(
        module.subprocess, "run", lambda args, **kwargs: calls.append(args)
    )
    module.generate(tmp_path / "updater", input_dir, tmp_path / "output.json", source)
    assert len(calls) == 1
    table.write_bytes(b'{"synthetic":false}')
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        module.generate(
            tmp_path / "updater", input_dir, tmp_path / "output.json", source
        )
    assert len(calls) == 1
