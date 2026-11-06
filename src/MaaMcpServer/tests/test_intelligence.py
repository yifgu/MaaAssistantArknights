import sys
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
