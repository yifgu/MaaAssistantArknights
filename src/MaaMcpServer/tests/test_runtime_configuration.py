from maa_battle_mcp.configuration import effective_configuration
from maa_battle_mcp.decision import DecisionContext
from maa_battle_mcp.intelligence import IntelligenceClient
from maa_battle_mcp.unit_data import UnitCatalog


def test_effective_preparation_configuration_is_not_a_silent_build_override():
    observed = {
        "value": "skill_s2",
        "source": "preparation_vision_with_static_skill_mapping",
        "verification": "derived_from_observed_slot_and_unique_identity",
        "evidence": {
            "observation_id": "prep",
            "captured_at_ms": 100,
            "image_path": "prep.png",
        },
    }
    context = {
        "loadout": {"equipped_skill_id": {"value": None}},
        "preparation": {
            "identity_match": True,
            "observed_fields": {"equipped_skill_id": observed},
        },
    }
    fields = effective_configuration(context, {"observation_id": "battle"})
    assert fields["equipped_skill_id"]["value"] == "skill_s2"
    assert fields["equipped_skill_id"]["preparation_evidence"] == observed
    context["loadout"]["equipped_skill_id"] = {
        "value": "skill_s3",
        "source": "user_supplied",
        "verification": "user_confirmed",
    }
    conflict = effective_configuration(context, {})
    assert conflict["equipped_skill_id"]["value"] is None
    assert (
        conflict["equipped_skill_id"]["reason"] == "unresolved_configuration_conflict"
    )


def test_runtime_enrichment_never_queries_operator_knowledge(settings, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Runtime observation must not query a knowledge catalog")

    monkeypatch.setattr(UnitCatalog, "get", forbidden)
    monkeypatch.setattr(IntelligenceClient, "query", forbidden)
    decision = DecisionContext(settings)
    observed = {
        "value": "skill_s2",
        "source": "preparation_vision_with_static_skill_mapping",
        "verification": "derived_from_observed_slot_and_unique_identity",
        "evidence": {"observation_id": "prep", "image_path": "prep.png"},
    }
    decision.preparation = {
        "preparation_id": "prep",
        "snapshot": {
            "operators": [
                {
                    "game_ids": ["char_fixture"],
                    "operator_game_id": "char_fixture",
                    "fields": {
                        "equipped_skill_id": observed,
                        "identity": {
                            "value": "char_fixture",
                            "source": "preparation_vision",
                            "verification": "observed",
                            "evidence": {
                                "observation_id": "prep",
                                "image_path": "prep.png",
                            },
                        },
                    },
                }
            ],
        },
    }
    for collection in ("deployment", "battlefield"):
        snapshot = {
            "observation_id": "1",
            "captured_at_ms": 100,
            "deployment": [],
            "battlefield": [],
        }
        snapshot[collection].append(
            {"unit_id": "instance:1", "game_ids": ["char_fixture"]}
        )
        decision.enrich_snapshot(snapshot)
        context = snapshot[collection][0]["decision_context"]
        assert context["knowledge_refs"]["equipped_skill_id"] == "skill_s2"
        assert context["knowledge_refs"]["automatically_queried"] is False
        assert "static_expected" not in str(snapshot)
        assert context["runtime"]["sp_current"]["value"] is None
