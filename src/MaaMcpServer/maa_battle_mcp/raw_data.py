"""Pinned local game data; never downloads or interprets unknown mechanics as empty."""

import copy
import hashlib
import json
import math
from pathlib import Path

from .models import BattleMode, RawIntelligenceConfig, SnapshotFile


class RawDataError(ValueError):
    pass


def defined_overlay(base, update):
    """Apply sparse enemy definitions without replacing inherited values by defaults."""
    if isinstance(update, dict):
        if "m_defined" in update:
            if type(update["m_defined"]) is not bool or "m_value" not in update:
                raise RawDataError("Malformed enemy definition flag")
            return copy.deepcopy(update["m_value"] if update["m_defined"] else base)
        result = copy.deepcopy(base) if isinstance(base, dict) else {}
        for key, value in update.items():
            result[key] = defined_overlay(result.get(key), value)
        return result
    return copy.deepcopy(base if update is None else update)


class RawStageProvider:
    MAX_BYTES = 64 * 1024 * 1024

    def __init__(self, config: RawIntelligenceConfig):
        self.config = config
        self.files: dict[str, dict] = {}

    def load(self, spec: SnapshotFile):
        root = self.config.root.resolve()
        path = (root / spec.path).resolve()
        if Path(spec.path).is_absolute() or not path.is_relative_to(root):
            raise RawDataError("Snapshot path escapes the configured root")
        if spec.path not in self.files:
            with path.open("rb") as stream:
                data = stream.read(self.MAX_BYTES + 1)
            if len(data) > self.MAX_BYTES:
                raise RawDataError("Snapshot exceeds the 64 MiB limit")
            if hashlib.sha256(data).hexdigest() != spec.sha256:
                raise RawDataError(f"Snapshot hash mismatch: {spec.path}")
            decoded = json.loads(data)
            if not isinstance(decoded, dict):
                raise RawDataError("Snapshot root must be an object")
            self.files[spec.path] = decoded
        return self.files[spec.path]

    @staticmethod
    def point(value, height, width):
        if not isinstance(value, dict):
            raise RawDataError("Missing route point")
        row, col = value.get("row"), value.get("col")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (row, col)):
            raise RawDataError("Invalid route coordinates")
        if not (0 <= row < height and 0 <= col < width):
            raise RawDataError("Route coordinates exceed map bounds")
        return {**value, "maa_location": [col, height - 1 - row]}

    def enemies(self, references):
        database = self.load(self.config.enemy_database)
        records = database.get("enemies")
        if not isinstance(records, list):
            raise RawDataError("Enemy database has no records")
        index = {}
        for item in records:
            key = item.get("Key", item.get("key"))
            if not isinstance(key, str) or key in index:
                raise RawDataError("Missing or duplicate enemy database key")
            index[key] = item.get("Value", item.get("value"))
        result = []
        for ref in references:
            enemy_id, level = ref.get("id"), ref.get("level")
            if type(level) is not int or level < 0:
                raise RawDataError("Invalid enemy level")
            resolved = {}
            if ref.get("useDb") is True:
                levels = index.get(enemy_id)
                if not isinstance(levels, list):
                    raise RawDataError(f"Missing enemy definition: {enemy_id}")
                if any(type(item.get("level")) is not int for item in levels):
                    raise RawDataError("Invalid enemy database level")
                if len({item["level"] for item in levels}) != len(levels):
                    raise RawDataError("Duplicate enemy database level")
                if not any(item["level"] == level for item in levels):
                    raise RawDataError(f"Missing enemy level: {enemy_id}/{level}")
                for item in sorted(levels, key=lambda item: item["level"]):
                    if item["level"] <= level:
                        resolved = defined_overlay(resolved, item["enemyData"])
            elif ref.get("useDb") is not False or not isinstance(
                ref.get("overwrittenData"), dict
            ):
                raise RawDataError("Unknown enemy reference source")
            resolved = defined_overlay(resolved, ref.get("overwrittenData"))
            result.append(
                {
                    "enemy_game_id": enemy_id,
                    "level": level,
                    "definition": resolved,
                    "reference": copy.deepcopy(ref),
                    "source": "static_database_with_level_overrides",
                    "runtime_state": None,
                }
            )
        return result

    @staticmethod
    def modifiers(runes, mode):
        if runes is None:
            return None
        if not isinstance(runes, list) or not all(
            isinstance(rune, dict) for rune in runes
        ):
            raise RawDataError("Malformed rune collection")
        masks = {
            "NONE": 0,
            "NORMAL": 1,
            "FOUR_STAR": 2,
            "EASY": 4,
            "SIX_STAR": 8,
            "ALL": 15,
        }
        selected = 1 if mode == "normal" else 2
        result = []
        for rune in runes:
            mask = rune.get("difficultyMask")
            numeric = masks.get(mask) if isinstance(mask, str) else mask
            known = type(numeric) is int and 0 <= numeric <= 15
            result.append(
                {
                    "raw": copy.deepcopy(rune),
                    "applies_to_mode": bool(numeric & selected) if known else None,
                    "mechanic_interpreted": False,
                }
            )
        return result

    def stage(
        self, identifier: str, mode: BattleMode, routes_page: int, spawns_page: int
    ):
        spec = self.config.stages.get(identifier)
        if spec is None:
            raise RawDataError(
                "Stage is absent from the pinned regional snapshot; no fallback"
            )
        raw = self.load(spec)
        grid, tiles = raw["mapData"]["map"], raw["mapData"]["tiles"]
        if not grid or not grid[0] or any(len(row) != len(grid[0]) for row in grid):
            raise RawDataError("Invalid map dimensions")
        height, width = len(grid), len(grid[0])
        normalized_tiles = []
        for y, row in enumerate(grid):
            normalized_row = []
            for x, tile_index in enumerate(row):
                if type(tile_index) is not int or not 0 <= tile_index < len(tiles):
                    raise RawDataError("Invalid tile reference")
                normalized_row.append({**tiles[tile_index], "location": [x, y]})
            normalized_tiles.append(normalized_row)
        routes = []
        for i, route in enumerate(raw["routes"]):
            checkpoints = []
            for checkpoint in route["checkpoints"]:
                point = copy.deepcopy(checkpoint)
                # WAIT checkpoints also carry a position; it is not a movement destination.
                kind = checkpoint.get("type")
                movement = None
                if type(kind) is not bool and kind in ("MOVE", 0):
                    movement = True
                    point["position"] = self.point(
                        checkpoint["position"], height, width
                    )
                elif type(kind) is not bool and kind in (
                    "WAIT_FOR_SECONDS",
                    "WAIT_FOR_PLAY_TIME",
                    "WAIT_CURRENT_FRAGMENT_TIME",
                    "WAIT_CURRENT_WAVE_TIME",
                    1,
                    2,
                    3,
                    4,
                ):
                    movement = False
                point["is_movement"] = movement
                checkpoints.append(point)
            routes.append(
                {
                    **route,
                    "route_id": i,
                    "startPosition": self.point(route["startPosition"], height, width),
                    "endPosition": self.point(route["endPosition"], height, width),
                    "checkpoints": checkpoints,
                }
            )
        enemies = self.enemies(raw["enemyDbRefs"])
        known_enemies = {enemy["enemy_game_id"] for enemy in enemies}
        spawns, other_actions, fragments = [], [], []
        for wi, wave in enumerate(raw["waves"]):
            for fi, fragment in enumerate(wave["fragments"]):
                fragment_id = f"{self.config.commit}:{identifier}:w{wi}:f{fi}"
                fragments.append(
                    {**fragment, "fragment_id": fragment_id, "wave_index": wi}
                )
                for ai, action in enumerate(fragment["actions"]):
                    entry = {
                        **action,
                        "spawn_id": f"{fragment_id}:a{ai}",
                        "fragment_id": fragment_id,
                    }
                    if type(action.get("actionType")) is bool or action.get(
                        "actionType"
                    ) not in ("SPAWN", 0):
                        other_actions.append(entry)
                        continue
                    route_index, count = action.get("routeIndex"), action.get("count")
                    if type(route_index) is not int or not 0 <= route_index < len(
                        routes
                    ):
                        raise RawDataError("Spawn routeIndex is out of bounds")
                    if (
                        type(count) is not int
                        or count < 0
                        or action.get("key") not in known_enemies
                    ):
                        raise RawDataError("Spawn count or enemy definition is invalid")
                    spawns.append(
                        {
                            **entry,
                            "enemy_game_id": action["key"],
                            "route_id": route_index,
                        }
                    )

        def page(items, number):
            start = (number - 1) * 50
            return items[start : start + 50], {
                "page": number,
                "has_more": start + 50 < len(items),
                "total": len(items),
            }

        route_items, route_meta = page(routes, routes_page)
        spawn_items, spawn_meta = page(spawns, spawns_page)
        return {
            "status": "partial",
            "game_id": identifier,
            "server": self.config.region,
            "mode": mode,
            "coordinate_system": "maa_grid",
            "tiles": normalized_tiles,
            "routes": route_items,
            "routes_page": route_meta,
            "spawns": spawn_items,
            "spawns_page": spawn_meta,
            "waves": raw["waves"],
            "fragments": fragments,
            "effects": None,
            "conditions": None,
            "global_modifiers": None,
            "enemy_definitions": enemies,
            "non_spawn_actions": other_actions,
            "mode_modifiers": self.modifiers(raw.get("runes"), mode),
            "raw_mechanisms": {
                key: raw.get(key)
                for key in (
                    "optionalRunes",
                    "globalBuffs",
                    "branches",
                    "extraRoutes",
                    "predefines",
                    "hardPredefines",
                    "options",
                )
            },
            "map_effects": raw["mapData"].get("effects"),
            "alignment": "unknown",
            "installed_game_version_verified": False,
            "provenance": {
                "repository": self.config.repository,
                "commit": self.config.commit,
                "data_version": self.config.data_version,
                "schema": "arknights-raw-level/v1",
                "build_id": self.config.commit,
                "source_hash": spec.sha256,
                "files": [spec.model_dump(), self.config.enemy_database.model_dump()],
                "verification": "local_sha256_verified_repository_attribution_supplied",
            },
        }
