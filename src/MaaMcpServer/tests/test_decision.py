import json

import pytest

from maa_battle_mcp.decision import DecisionContext, DecisionError
from maa_battle_mcp.models import Placement


@pytest.fixture
def context(settings):
    root = settings.resource_dir / "resource"
    root.mkdir(parents=True)
    ranges = {
        "3-3": [[x, y] for x in range(4) for y in (-1, 0, 1)],
        "y-6": [[x, y] for x in range(-1, 3) for y in (-1, 0, 1)]
        + [[x, y] for x in (0, 1) for y in (-2, 2)],
    }
    chars = {
        "char_4228_closur": {
            "profession": "PIONEER",
            "position": "RANGED",
            "rangeId": ["3-3"] * 3,
        },
        "char_1052_kalts2": {
            "profession": "MEDIC",
            "position": "RANGED",
            "rangeId": ["y-6"] * 3,
        },
        "char_2027_wang": {
            "profession": "SPECIAL",
            "position": "RANGED",
            "rangeId": ["3-3"] * 3,
        },
        "char_2025_shu": {
            "profession": "TANK",
            "position": "MELEE",
            "rangeId": ["unknown"] * 3,
        },
        "token": {"profession": "TOKEN", "position": "NONE", "rangeId": ["3-3"] * 3},
    }
    (root / "battle_data.json").write_text(
        json.dumps({"chars": chars, "ranges": ranges})
    )
    return DecisionContext(settings)


@pytest.fixture
def map_info():
    return {
        "stage_id": "act55side_ex01",
        "width": 11,
        "height": 7,
        "tiles": [
            {"location": [x, y], "buildable_type": 1 if (x, y) == (8, 3) else 2}
            for y in range(7)
            for x in range(11)
        ],
    }


def placement(game_id, x, y, direction, elite=2):
    return Placement(
        operator_game_id=game_id,
        location=[x, y],
        direction=direction,
        assumed_elite=elite,
    )


def test_report_base_geometry_and_comparison(context, map_info):
    formation = [
        placement("char_4228_closur", 6, 2, "Down"),
        placement("char_1052_kalts2", 7, 2, "Left"),
        placement("char_2027_wang", 4, 2, "Up"),
        placement("char_2025_shu", 8, 3, "Left"),
    ]
    current = context.evaluate(map_info, formation)["placements"]
    assert "char_2025_shu" not in current[0]["geometric_allied_coverage"]
    assert "char_2027_wang" not in current[1]["geometric_allied_coverage"]
    assert all(item["static_buildability_compatible"] for item in current)
    comparison = [
        placement("char_4228_closur", 7, 2, "Down"),
        placement("char_1052_kalts2", 6, 2, "Right"),
        placement("char_2027_wang", 7, 1, "Left"),
        formation[3],
    ]
    candidate = context.evaluate(map_info, comparison)["placements"]
    assert "char_2025_shu" in candidate[0]["geometric_allied_coverage"]
    assert set(candidate[1]["geometric_allied_coverage"]) == {
        item.operator_game_id for item in comparison
    }
    assert all(not item["conflicting_operators"] for item in candidate)


@pytest.mark.parametrize(
    ("direction", "expected"),
    [("Right", [6, 2]), ("Up", [3, 0]), ("Left", [0, 2]), ("Down", [3, 5])],
)
def test_all_four_rotations(context, map_info, direction, expected):
    item = placement("char_4228_closur", 3, 2, direction)
    assert (
        expected
        in context.evaluate(map_info, [item])["placements"][0]["base_range_cells"]
    )


def test_unknown_range_and_elite_never_become_empty_or_fallback(context, map_info):
    result = context.evaluate(
        map_info,
        [
            placement("char_4228_closur", 3, 2, "Right", None),
            placement("char_2025_shu", 8, 3, "Left"),
        ],
    )
    assert all(item["base_range_cells"] is None for item in result["placements"])


def test_conflicts_and_incompatible_tiles(context, map_info):
    result = context.evaluate(
        map_info,
        [
            placement("char_4228_closur", 8, 3, "Right"),
            placement("char_1052_kalts2", 8, 3, "Left"),
        ],
    )["placements"]
    assert not result[0]["static_buildability_compatible"]
    assert result[0]["conflicting_operators"] == ["char_1052_kalts2"]
    with pytest.raises(DecisionError, match="instance"):
        context.evaluate(map_info, [placement("token", 0, 0, "Up")])
    with pytest.raises(DecisionError, match="Duplicate"):
        context.evaluate(map_info, [placement("char_4228_closur", 0, 0, "Up")] * 2)


def test_loadout_binding_sources_and_ambiguous_identity(settings, tmp_path):
    path = tmp_path / "loadout.json"
    path.write_text(
        json.dumps(
            {
                "region": "cn",
                "stage_id": "act55side_ex01",
                "mode": "normal",
                "confirmed_by_user": True,
                "operators": [
                    {
                        "operator_game_id": "char_a",
                        "actual_elite": 2,
                        "equipped_skill_id": "skill_a",
                    }
                ],
            }
        )
    )
    context = DecisionContext(settings, path)
    context.validate_binding("act55side_ex01", "normal")
    for stage, mode in [
        ("other", "normal"),
        ("act55side_ex01", None),
        ("act55side_ex01", "challenge"),
    ]:
        with pytest.raises(DecisionError, match="binding"):
            context.validate_binding(stage, mode)
    snapshot = {
        "deployment": [
            {"game_ids": ["char_b", "char_a"]},
            {"game_ids": ["char_b", "char_c"]},
        ],
        "enemies": None,
    }
    context.enrich_snapshot(snapshot)
    known, unknown = [unit["decision_context"] for unit in snapshot["deployment"]]
    assert known["operator_game_id"] == "char_a"
    assert known["loadout"]["equipped_skill_id"]["value"] == "skill_a"
    assert known["loadout"]["actual_elite"]["verification"] == "user_confirmed"
    assert known["loadout_provenance"]["visually_verified"] is False
    assert known["loadout"]["sp_current"]["value"] is None
    assert unknown["operator_game_id"] is None
    assert unknown["loadout"]["actual_elite"]["value"] is None
    assert snapshot["enemies"] is None
    settings.client_type = "YoStarEN"
    with pytest.raises(DecisionError, match="binding"):
        context.validate_binding("act55side_ex01", "normal")


def test_native_preparation_fields_preserve_supplied_values_and_conflicts(
    settings, tmp_path
):
    path = tmp_path / "loadout.json"
    path.write_text(
        json.dumps(
            {
                "region": "cn",
                "stage_id": "stage",
                "mode": "normal",
                "operators": [
                    {
                        "operator_game_id": "char_a",
                        "actual_elite": 1,
                        "equipped_skill_id": "supplied",
                    }
                ],
            }
        )
    )
    context = DecisionContext(settings, path)
    observed = {
        key: {
            "value": value,
            "source": "preparation_vision",
            "verification": "observed",
            "evidence": {"observation_id": "2", "image_path": "battle-control/s/2.png"},
        }
        for key, value in (
            ("actual_elite", 2),
            ("actual_level", 50),
            ("selected_skill", 3),
            ("equipped_skill_id", "native"),
        )
    }
    context.preparation = {
        "preparation_id": "p",
        "snapshot": {"operators": [{"game_ids": ["char_a"], "fields": observed}]},
    }
    snapshot = {
        "deployment": [{"game_ids": ["char_a"]}, {"game_ids": ["char_a", "char_b"]}]
    }
    context.enrich_snapshot(snapshot)
    known, ambiguous = [u["decision_context"] for u in snapshot["deployment"]]
    assert known["loadout"]["actual_elite"]["value"] == 1
    assert known["loadout"]["equipped_skill_id"]["value"] == "supplied"
    prep = known["preparation"]
    assert prep["observed_fields"] == observed
    assert prep["configuration_verified"] is True
    assert prep["conflicts"]["actual_elite"]["preparation_observed"] == 2
    assert prep["conflicts"]["equipped_skill_id"]["user_supplied"] == "supplied"
    assert ambiguous["preparation"]["configuration_verified"] is False
    assert "observed_fields" not in ambiguous["preparation"]
