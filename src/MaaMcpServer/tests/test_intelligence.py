import copy
import sys
import time
from pathlib import Path

import pytest

from maa_battle_mcp.intelligence import IntelligenceClient, IntelligenceError
from maa_battle_mcp.models import IntelligenceConfig


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
    with pytest.raises(IntelligenceError, match="database_unavailable"):
        await client.query("stage", "failure", 2, 1)
    assert ("stage", "failure", 2, 1) not in client.cache
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

    async def query(kind, identifier, routes_page, spawns_page, *, refresh):
        assert refresh
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
