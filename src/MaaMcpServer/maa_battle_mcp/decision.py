"""Static decision support; never promotes supplied configuration to live observation."""

import hashlib
import json
import time
from pathlib import Path

from .models import BattleMode, LoadoutConfig, Placement, Settings

CLIENT_REGIONS = {
    "Official": "cn",
    "Bilibili": "cn",
    "YoStarEN": "en",
    "YoStarJP": "jp",
    "YoStarKR": "kr",
    "txwy": "tw",
}


class DecisionError(ValueError):
    pass


class DecisionContext:
    def __init__(self, settings: Settings, loadout_path: Path | None = None):
        self.settings = settings
        self.config = None
        self.provenance = None
        self.preparation: dict | None = None
        if loadout_path is not None:
            raw = loadout_path.read_bytes()
            self.config = LoadoutConfig.model_validate_json(raw)
            self.provenance = {
                "source": "user_supplied",
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "loaded_at": time.time(),
                "verification": "user_confirmed"
                if self.config.confirmed_by_user
                else "unverified",
                "visually_verified": False,
            }

    def validate_binding(self, stage_id: str, mode: BattleMode | None) -> None:
        if self.config is None:
            return
        if (
            self.config.region != CLIENT_REGIONS[self.settings.client_type]
            or self.config.stage_id != stage_id
            or self.config.mode != mode
        ):
            raise DecisionError(
                "Loadout region/stage/mode does not match the explicit battle binding"
            )

    @staticmethod
    def _preparation_identity(oper: dict) -> str | None:
        candidates = oper.get("game_ids", [])
        if "operator_game_id" not in oper and not oper.get("fields"):
            return candidates[0] if len(candidates) == 1 else None
        resolved = oper.get("operator_game_id")
        identity = oper.get("fields", {}).get("identity", {})
        evidence = identity.get("evidence") or {}
        if (
            not isinstance(resolved, str)
            or resolved not in candidates
            or identity.get("value") != resolved
            or not evidence.get("observation_id")
            or not evidence.get("image_path")
        ):
            return None
        if (
            identity.get("source") == "preparation_vision_with_static_identity_mapping"
            and identity.get("verification")
            == "derived_from_observed_name_and_ordinary_operator_class"
            and identity.get("mapping_source") == "bundled_battle_data_roles"
            and identity.get("ordinary_operator_game_ids") == [resolved]
        ):
            return resolved
        if (
            candidates == [resolved]
            and identity.get("source") == "preparation_vision"
            and identity.get("verification") == "observed"
        ):
            return resolved
        return None

    def enrich_snapshot(self, snapshot: dict) -> None:
        configured = (
            {}
            if self.config is None
            else {oper.operator_game_id: oper for oper in self.config.operators}
        )
        for collection in ("deployment", "battlefield"):
            for unit in snapshot.get(collection, []):
                candidates = unit.get("game_ids", [])
                matches = [game_id for game_id in candidates if game_id in configured]
                resolved = candidates[0] if len(candidates) == 1 else None
                loadout = configured.get(resolved)
                fields = {}
                for field in (
                    "actual_elite",
                    "actual_level",
                    "potential",
                    "module_id",
                    "module_level",
                    "equipped_skill_id",
                    "skill_level",
                    "mastery",
                ):
                    value = getattr(loadout, field) if loadout is not None else None
                    fields[field] = {
                        "value": value,
                        "source": "user_supplied" if value is not None else "unknown",
                        "verification": self.provenance["verification"]
                        if value is not None
                        else "unknown",
                    }
                for field in (
                    "sp_current",
                    "sp_required",
                    "sp_type",
                    "trigger_mode",
                    "charges",
                    "active_phase",
                    "remaining_duration",
                    "talents",
                ):
                    fields[field] = {
                        "value": None,
                        "source": "unknown",
                        "verification": "unknown",
                    }
                unit["decision_context"] = {
                    "operator_game_id": resolved,
                    "identity_source": "native_candidates",
                    "configured_candidate_game_ids": matches,
                    "identity_verified": False,
                    "loadout": fields,
                    "loadout_provenance": self.provenance
                    if loadout is not None
                    else None,
                    "warnings": [
                        "Supplied loadout is not a measurement of this battle. Skill readiness does not identify the equipped skill or SP."
                    ],
                }
                if self.preparation is not None:
                    roster = self.preparation["snapshot"]["operators"]
                    matches = (
                        [oper for oper in roster if resolved in oper["game_ids"]]
                        if resolved
                        else []
                    )
                    prepared_identity = (
                        self._preparation_identity(matches[0])
                        if len(matches) == 1
                        else None
                    )
                    identity_match = (
                        None
                        if not resolved
                        or len(matches) == 1
                        and prepared_identity is None
                        else len(matches) == 1 and prepared_identity == resolved
                    )
                    preparation = {
                        "preparation_id": self.preparation["preparation_id"],
                        "image_path": self.preparation.get("image_path"),
                        "identity_match": identity_match,
                        "status": "matched"
                        if identity_match is True
                        else "conflict"
                        if identity_match is False
                        else "unknown",
                        "configuration_verified": False,
                    }
                    unit["decision_context"]["preparation"] = preparation
                    if identity_match is True:
                        observed = matches[0].get("fields", {})
                        preparation["observed_fields"] = observed
                        preparation["configuration_verified"] = all(
                            observed.get(field, {}).get("value") is not None
                            for field in (
                                "actual_elite",
                                "actual_level",
                                "selected_skill",
                            )
                        )
                        preparation["configuration_scope"] = (
                            "preparation_observation_caller_reconfirmed_not_live_measurement"
                        )
                        preparation["conflicts"] = {
                            field: {
                                "user_supplied": fields[field]["value"],
                                "preparation_observed": observed[field]["value"],
                                "evidence": observed[field].get("evidence"),
                            }
                            for field in (
                                "actual_elite",
                                "actual_level",
                                "equipped_skill_id",
                            )
                            if fields[field]["value"] is not None
                            and observed.get(field, {}).get("value") is not None
                            and fields[field]["value"] != observed[field]["value"]
                        }

    def evaluate(self, map_info: dict, placements: list[Placement]) -> dict:
        path = self.settings.resource_dir / "resource/battle_data.json"
        try:
            raw = path.read_bytes()
            data = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise DecisionError(f"Cannot read bundled battle ranges: {exc}") from exc
        ids = [item.operator_game_id for item in placements]
        if len(ids) != len(set(ids)):
            raise DecisionError(
                "Duplicate operators require instance tracking, which is unsupported"
            )
        tiles = {tuple(tile["location"]): tile for tile in map_info["tiles"]}
        loadouts = (
            {}
            if self.config is None
            else {oper.operator_game_id: oper for oper in self.config.operators}
        )
        results = []
        for placement in placements:
            props = data.get("chars", {}).get(placement.operator_game_id)
            if props is None:
                raise DecisionError(
                    f"Unknown operator game ID: {placement.operator_game_id}"
                )
            if props.get("profession") not in {
                "PIONEER",
                "WARRIOR",
                "TANK",
                "SNIPER",
                "CASTER",
                "MEDIC",
                "SUPPORT",
                "SPECIAL",
            }:
                raise DecisionError(
                    "Summon/device coverage requires instance semantics and is unsupported"
                )
            elite = placement.assumed_elite
            source = "caller_assumption"
            if elite is None:
                configured = loadouts.get(placement.operator_game_id)
                elite = configured.actual_elite if configured else None
                source = "user_supplied_loadout" if elite is not None else "unknown"
            ranges = props.get("rangeId", [])
            range_id = (
                ranges[elite] if elite is not None and elite < len(ranges) else None
            )
            relative = data.get("ranges", {}).get(range_id)
            cells = None
            if relative is not None:
                cells = set()
                for dx, dy in relative:
                    for _ in range(
                        ("Right", "Up", "Left", "Down").index(placement.direction)
                    ):
                        dx, dy = dy, -dx
                    absolute = (placement.location[0] + dx, placement.location[1] + dy)
                    if absolute in tiles:
                        cells.add(absolute)
            tile = tiles.get(tuple(placement.location))
            position = {"MELEE": 1, "RANGED": 2, "ALL": 3}.get(props.get("position"))
            compatible = (
                tile is not None
                and position is not None
                and (
                    tile["buildable_type"] == 3
                    or tile["buildable_type"] in (1, 2)
                    and position in (tile["buildable_type"], 3)
                )
            )
            conflicts = [
                other.operator_game_id
                for other in placements
                if other is not placement and other.location == placement.location
            ]
            results.append(
                {
                    "operator_game_id": placement.operator_game_id,
                    "location": placement.location,
                    "direction": placement.direction,
                    "elite": elite,
                    "elite_source": source,
                    "base_range_id": range_id,
                    "base_range_cells": None
                    if cells is None
                    else [list(cell) for cell in sorted(cells)],
                    "geometric_allied_coverage": None
                    if cells is None
                    else [
                        other.operator_game_id
                        for other in placements
                        if tuple(other.location) in cells
                    ],
                    "static_buildability_compatible": compatible,
                    "conflicting_operators": conflicts,
                    "uncertainties": [
                        "Base geometry only; no equipped-skill, talent, module, healing eligibility or terrain-effect inference.",
                        *(
                            []
                            if cells is not None
                            else [
                                "Elite or range data is unknown; no fallback range was substituted."
                            ]
                        ),
                    ],
                }
            )
        return {
            "status": "succeeded",
            "scope": "candidate_formation_not_live_occupancy",
            "stage_id": map_info["stage_id"],
            "coordinate_system": "maa_grid",
            "range_source": {
                "kind": "bundled_static",
                "source_sha256": hashlib.sha256(raw).hexdigest(),
            },
            "placements": results,
            "warnings": [
                "Not a survival, healing, skill-effect or optimality proof. Existing battlefield occupancy must be checked separately."
            ],
        }
