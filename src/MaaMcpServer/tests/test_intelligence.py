import copy
import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

from maa_battle_mcp.intelligence import IntelligenceClient, IntelligenceError
from maa_battle_mcp.models import (
    IntelligenceConfig,
    RawIntelligenceConfig,
    SnapshotFile,
)
from maa_battle_mcp.raw_data import RawDataError, RawStageProvider


def config(provider="intelligence", region="cn"):
    return IntelligenceConfig(provider=provider, region=region, command="unused")


def test_provider_mapping_and_pagination():
    client = IntelligenceClient(config())
    name, args = client.translate("stage", "main_01-07", 2, 3)
    assert name == "get_stage"
    assert args["game_id"] == "main_01-07"
    assert args["server"] == "cn"
    assert args["routes_page"] == {"page": 2, "page_size": 50}
    assert args["spawns_page"] == {"page": 3, "page_size": 50}
    assert client.translate("operator", "char_002_amiya", 1, 1)[1]["include_skills"]


def test_prts_does_not_claim_map_support_or_mix_regions():
    client = IntelligenceClient(config("prts"))
    assert client.translate("stage", "main_01-07", 1, 1) == (
        "get_stage_enemies",
        {"stage_id": "main_01-07"},
    )
    assert client.translate("operator", "阿米娅", 1, 1) == (
        "get_operator_basic_info",
        {"name": "阿米娅", "action": "skills"},
    )
    with pytest.raises(IntelligenceError, match="CN data only"):
        IntelligenceClient(config("prts", "en"))
    with pytest.raises(IntelligenceError, match="pagination"):
        client.translate("stage", "main_01-07", 2, 1)


async def test_real_mcp_provider_roundtrip_and_cache():
    client = IntelligenceClient(
        IntelligenceConfig(
            provider="intelligence",
            command=sys.executable,
            args=[str(Path(__file__).with_name("fake_intelligence.py"))],
        )
    )
    result = await client.query("stage", "main_01-07", 2, 1)
    assert result["data"]["provenance"]["build_id"] == "test-snapshot"
    assert result["data"]["routes_page"]["has_more"] is True
    assert result["cached"] is False
    assert (await client.query("stage", "main_01-07", 2, 1))["cached"] is True
    challenge = await client.query("stage", "main_01-07", 2, 1, mode="challenge")
    assert challenge["data"]["mode"] == "challenge"
    assert challenge["cached"] is False
    assert (await client.query("stage", "main_01-07", 2, 1))["data"]["mode"] is None
    with pytest.raises(IntelligenceError, match="database_unavailable"):
        await client.query("stage", "failure", 2, 1)
    assert ("stage", "failure", 2, 1, None) not in client.cache
    plain = await client.query("stage", "unstructured", 2, 1)
    assert plain["data"] is None
    assert any("unstructured" in warning for warning in plain["warnings"])


def complete_page(route_page, spawn_page):
    return {
        "status": "ok",
        "game_id": "act55side_ex01",
        "server": "cn",
        "mode": "normal",
        "coordinate_system": "public_row_col",
        "provenance": {"build_id": "fixture", "source_hash": "fixture-hash"},
        "tiles": [[{"kind": "row", "number": row}] for row in range(7)],
        "effects": [],
        "conditions": [],
        "global_modifiers": [],
        "waves": [
            {"wave_id": "w", "fragments": [{"fragment_id": f"f{i}"} for i in range(5)]}
        ],
        "routes_page": {"page": route_page, "has_more": False, "total": 40},
        "routes": [
            {"route_id": i, "end": {"row": 5, "col": 6 if i % 2 else 8}}
            for i in range(40)
        ],
        "spawns_page": {"page": spawn_page, "has_more": spawn_page == 1, "total": 95},
        "spawns": [
            {
                "spawn_id": i,
                "enemy_game_id": f"enemy_{i % 3}",
                "route_id": i % 40,
                "count": 1,
            }
            for i in range((spawn_page - 1) * 50, min(spawn_page * 50, 95))
        ],
    }


def paged_client(monkeypatch, mutate=None):
    client = IntelligenceClient(config())
    calls = []

    async def query(kind, identifier, routes_page, spawns_page, *, refresh, mode):
        assert refresh
        assert mode == "normal"
        calls.append((routes_page, spawns_page))
        data = complete_page(routes_page, spawns_page)
        if mutate:
            mutate(data, len(calls))
        return {"data": data}

    monkeypatch.setattr(client, "query", query)
    return client, calls


async def test_complete_static_fixture_and_coordinate_binding(monkeypatch):
    client, calls = paged_client(monkeypatch)
    result = await client.query_complete_stage("act55side_ex01", "normal", 7)
    assert result["status"] == "succeeded"
    assert calls == [(1, 1), (1, 2)]
    assert result["completeness"]["all_pages"] is True
    normalized = result["normalized"]
    assert len(normalized["routes"]) == 40
    assert len(normalized["waves"]) == 1
    assert len(normalized["fragments"]) == 5
    assert result["static_spawn_count"] == 95
    assert len(result["referenced_enemy_ids"]) == 3
    assert {tuple(route["end"]["maa_location"]) for route in normalized["routes"]} == {
        (6, 1),
        (8, 1),
    }
    assert normalized["tiles"][0][0]["number"] == 6
    assert "maa_location" not in result["pages"][0]["data"]["routes"][0]["end"]
    assert result["static_or_live"] == "static"


@pytest.mark.parametrize("key", ["server", "game_id", "mode"])
async def test_complete_rejects_wrong_binding(monkeypatch, key):
    client, _ = paged_client(monkeypatch, lambda data, _: data.update({key: "wrong"}))
    with pytest.raises(IntelligenceError, match="mismatch"):
        await client.query_complete_stage("act55side_ex01", "normal", 7)


async def test_complete_rejects_snapshot_drift(monkeypatch):
    client, _ = paged_client(
        monkeypatch,
        lambda data, number: data["provenance"].update(build_id=str(number)),
    )
    with pytest.raises(IntelligenceError, match="snapshot changed"):
        await client.query_complete_stage("act55side_ex01", "normal", 7)


async def test_incomplete_and_expired_data_are_not_empty_success(monkeypatch):
    def mutate(data, _):
        data["status"] = "partial"
        data["expires_at"] = time.time() - 10
        data.pop("effects")
        data["routes_page"].pop("has_more")

    client, _ = paged_client(monkeypatch, mutate)
    result = await client.query_complete_stage("act55side_ex01", "normal", 7)
    assert result["status"] == "partial"
    assert result["freshness"] == "expired"
    assert result["completeness"]["all_pages"] is False
    assert result["normalized"]["effects"] is None
    assert result["warnings"]


async def test_malformed_pagination_cannot_loop_or_hide_missing_pages(monkeypatch):
    def mutate(data, _):
        data["spawns"] = []

    client, _ = paged_client(monkeypatch, mutate)
    with pytest.raises(IntelligenceError, match="Empty spawns"):
        await client.query_complete_stage("act55side_ex01", "normal", 7)


def test_coordinate_conversion_preserves_unknown_and_rejects_invalid():
    raw = {"end": {"row": 5, "col": 6}}
    original = copy.deepcopy(raw)
    assert IntelligenceClient._normalize_coordinates(raw, 7, "unknown") == original
    assert raw == original
    with pytest.raises(IntelligenceError, match="Invalid"):
        IntelligenceClient._normalize_coordinates(
            {"row": True, "col": 1}, 7, "public_row_col"
        )


@pytest.fixture
def raw_snapshot(tmp_path):
    # Self-authored structure, not redistributed game data.
    level = {
        "mapData": {
            "map": [[0, 1], [1, 0]],
            "tiles": [{"tileKey": "tile_end"}, {"tileKey": "tile_road"}],
        },
        "routes": [
            {
                "startPosition": {"row": 0, "col": 1},
                "endPosition": {"row": 1, "col": 0},
                "checkpoints": [
                    {
                        "type": "WAIT_FOR_SECONDS",
                        "position": {"row": 0, "col": 0},
                        "time": 9,
                    }
                ],
            }
        ],
        "enemyDbRefs": [
            {
                "id": "enemy",
                "level": 1,
                "useDb": True,
                "overwrittenData": {
                    "attributes": {"atk": {"m_defined": True, "m_value": 7}},
                },
            }
        ],
        "waves": [
            {
                "fragments": [
                    {
                        "preDelay": 5,
                        "actions": [
                            {
                                "actionType": "SPAWN",
                                "key": "enemy",
                                "routeIndex": 0,
                                "count": 2,
                                "interval": 3,
                                "hiddenGroup": "conditional",
                                "blockFragment": False,
                            }
                            for _ in range(51)
                        ]
                        + [{"actionType": "UNKNOWN_ACTION", "key": "script"}],
                    }
                ]
            }
        ],
        "runes": [
            {"difficultyMask": "FOUR_STAR", "key": "enemy_attribute_mul"},
            {"difficultyMask": 99},
        ],
    }
    enemies = {
        "enemies": [
            {
                "key": "enemy",
                "value": [
                    {
                        "level": 0,
                        "enemyData": {
                            "attributes": {
                                "maxHp": {"m_defined": True, "m_value": 10},
                                "atk": {"m_defined": True, "m_value": 3},
                            },
                            "skills": [{"prefabKey": "Summon"}],
                        },
                    },
                    {
                        "level": 1,
                        "enemyData": {
                            "attributes": {"maxHp": {"m_defined": False, "m_value": 0}}
                        },
                    },
                ],
            }
        ]
    }

    def write(name, data):
        content = json.dumps(data).encode()
        (tmp_path / name).write_bytes(content)
        return SnapshotFile(path=name, sha256=hashlib.sha256(content).hexdigest())

    config = RawIntelligenceConfig(
        provider="raw",
        root=tmp_path,
        repository="fixture/self-authored",
        commit="a" * 40,
        data_version="fixture",
        region="cn",
        stages={"stage": write("level.json", level)},
        enemy_database=write("enemies.json", enemies),
    )
    return config, level, write


async def test_raw_snapshot_pages_coordinates_and_sparse_enemy_levels(raw_snapshot):
    config, _, _ = raw_snapshot
    client = IntelligenceClient(config)
    result = await client.query_complete_stage("stage", "normal", 2)
    assert result["status"] == "partial"
    assert result["completeness"]["all_pages"] is True
    assert result["static_spawn_count"] == 102
    assert len(result["normalized"]["spawns"]) == 51
    assert len(result["pages"]) == 2
    assert result["normalized"]["tiles"][0][0]["tileKey"] == "tile_end"
    route = result["normalized"]["routes"][0]
    assert route["endPosition"]["maa_location"] == [0, 0]
    assert route["checkpoints"][0]["is_movement"] is False
    assert "maa_location" not in route["checkpoints"][0]["position"]
    definition = result["enemy_definitions"][0]["definition"]
    assert definition["attributes"] == {"maxHp": 10, "atk": 7}
    assert definition["skills"][0]["prefabKey"] == "Summon"
    assert result["mode_modifiers"][0]["applies_to_mode"] is False
    assert result["mode_modifiers"][1]["applies_to_mode"] is None
    assert result["non_spawn_actions"][0]["actionType"] == "UNKNOWN_ACTION"
    assert result["alignment"] == "unknown"
    hard = await client.query_complete_stage("stage", "challenge", 2)
    assert hard["mode_modifiers"][0]["applies_to_mode"] is True
    with pytest.raises(IntelligenceError, match="height"):
        await client.query_complete_stage("stage", "normal", 7)
    with pytest.raises(IntelligenceError, match="no fallback"):
        await client.query_complete_stage("unavailable-region-stage", "normal", 2)


@pytest.mark.parametrize("failure", ["route", "enemy", "hash", "escape"])
async def test_raw_snapshot_rejects_invalid_sources(raw_snapshot, failure):
    config, level, write = raw_snapshot
    if failure == "route":
        level["waves"][0]["fragments"][0]["actions"][0]["routeIndex"] = 9
        config.stages["stage"] = write("level.json", level)
    elif failure == "enemy":
        level["enemyDbRefs"][0]["id"] = "missing"
        config.stages["stage"] = write("level.json", level)
    elif failure == "hash":
        config.stages["stage"].sha256 = "0" * 64
    else:
        config.stages["stage"].path = "../outside.json"
    with pytest.raises(IntelligenceError, match="Raw snapshot rejected"):
        await IntelligenceClient(config).query_complete_stage("stage", "normal", 2)


def test_raw_snapshot_coordinates_do_not_coerce_boolean():
    with pytest.raises(RawDataError, match="coordinates"):
        RawStageProvider.point({"row": True, "col": 1}, 2, 2)
