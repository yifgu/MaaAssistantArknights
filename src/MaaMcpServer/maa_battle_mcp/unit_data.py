"""Bundled offline metadata, separate from live cards and caller-supplied loadouts."""

import hashlib
import json
from pathlib import Path


class UnitCatalog:
    def __init__(self, resource_dir: Path):
        self.path = resource_dir / "resource/unit_info.json"
        self._stamp = None
        self._data = None
        self._sha256 = None

    def get(self, game_id: str) -> dict:
        try:
            stat = self.path.stat()
            if stat.st_size > 64 * 1024 * 1024:
                raise ValueError("Bundled unit metadata exceeds 64 MiB")
            stamp = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
            if stamp != self._stamp:
                raw = self.path.read_bytes()
                data = json.loads(raw)
                if (
                    not isinstance(data, dict)
                    or data.get("schema_version") != 1
                    or not isinstance(data.get("chars"), dict)
                    or not isinstance(data.get("skills"), dict)
                    or not isinstance(data.get("source"), dict)
                    or not isinstance(data.get("ranges"), dict)
                ):
                    raise ValueError("Invalid bundled unit metadata schema")
                self._data = data
                self._sha256 = hashlib.sha256(raw).hexdigest()
                self._stamp = stamp
        except (OSError, ValueError) as exc:
            return {
                "status": "failed",
                "error": {"code": "unit_metadata_unavailable", "message": str(exc)},
            }
        unit = self._data["chars"].get(game_id)
        if not isinstance(unit, dict) or unit.get("game_id") != game_id:
            return {
                "status": "failed",
                "error": {"code": "unknown_game_id", "message": game_id},
            }
        if (
            not isinstance(unit.get("skills"), list)
            or any(not isinstance(skill, dict) for skill in unit["skills"])
            or any(
                skill.get("skillId") is not None
                and not isinstance(skill["skillId"], str)
                for skill in unit["skills"]
            )
            or not isinstance(unit.get("base_attributes"), list)
            or any(not isinstance(phase, dict) for phase in unit["base_attributes"])
            or any(
                not isinstance(phase.get("range_id"), str)
                for phase in unit["base_attributes"]
            )
        ):
            return {
                "status": "failed",
                "error": {
                    "code": "unit_metadata_unavailable",
                    "message": "Invalid unit skill references or base attributes",
                },
            }
        return {
            "status": "succeeded",
            "unit": {
                **unit,
                "skill_definitions": {
                    skill["skillId"]: self._data["skills"][skill["skillId"]]
                    for skill in unit.get("skills", [])
                    if skill.get("skillId") in self._data["skills"]
                },
                "base_range_definitions": {
                    phase["range_id"]: self._data["ranges"][phase["range_id"]]
                    for phase in unit.get("base_attributes", [])
                    if phase.get("range_id") in self._data["ranges"]
                },
            },
            "source": self._data["source"],
            "resource_sha256": self._sha256,
            "scope": "static_game_data_not_live_loadout_or_visual_measurement",
            "limitations": [
                "Base key frames are not actual deployed stats or current card cost.",
                "Game tables do not specify every engine/UI policy. Null capabilities are unknown, not permission.",
                "Adapter support is not live validation; selected-kind evidence proves neither the selected tile nor this owned instance. An icon match does not prove button availability.",
            ],
        }
