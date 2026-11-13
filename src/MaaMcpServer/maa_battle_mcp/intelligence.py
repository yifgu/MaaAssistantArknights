import asyncio
import hashlib
import json
import math
import time
from typing import Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .models import BattleMode, IntelligenceConfig, RawIntelligenceConfig
from .raw_data import RawStageProvider


class IntelligenceError(RuntimeError):
    pass


class IntelligenceClient:
    def __init__(self, config: IntelligenceConfig | RawIntelligenceConfig):
        if config.provider == "prts" and config.region != "cn":
            raise IntelligenceError(
                "PRTS adapter supports CN data only; no cross-region fallback"
            )
        self.config = config
        self.raw = (
            RawStageProvider(config)
            if isinstance(config, RawIntelligenceConfig)
            else None
        )
        self.cache: dict[tuple, tuple[float, dict]] = {}

    def translate(
        self,
        kind: Literal["stage", "operator", "enemy"],
        identifier: str,
        routes_page: int,
        spawns_page: int,
    ) -> tuple[str, dict]:
        if self.config.provider == "intelligence":
            arguments: dict = {"server": self.config.region, "game_id": identifier}
            if kind == "stage":
                arguments.update(
                    include_map=True,
                    include_routes=True,
                    include_spawns=True,
                    routes_page={"page": routes_page, "page_size": 50},
                    spawns_page={"page": spawns_page, "page_size": 50},
                )
            elif kind == "operator":
                arguments.update(
                    include_skills=True, include_talents=True, include_modules=True
                )
            return f"get_{kind}", arguments
        if routes_page != 1 or spawns_page != 1:
            raise IntelligenceError("PRTS does not provide route/spawn pagination")
        if kind == "stage":
            return "get_stage_enemies", {"stage_id": identifier}
        if kind == "operator":
            return "get_operator_basic_info", {"name": identifier, "action": "skills"}
        return "get_enemy_info", {"name": identifier}

    async def query(
        self,
        kind: Literal["stage", "operator", "enemy"],
        identifier: str,
        routes_page: int = 1,
        spawns_page: int = 1,
        *,
        refresh: bool = False,
        mode: BattleMode | None = None,
    ) -> dict:
        key = (kind, identifier, routes_page, spawns_page, mode)
        if not refresh and key in self.cache:
            fetched, value = self.cache[key]
            if time.monotonic() - fetched < 300:
                return {
                    **value,
                    "cached": True,
                    "cache_age_seconds": time.monotonic() - fetched,
                }
        if self.raw is not None:
            if kind != "stage" or mode is None:
                raise IntelligenceError(
                    "Raw snapshots require a complete, explicitly bound stage query"
                )
            try:
                data = await asyncio.to_thread(
                    self.raw.stage, identifier, mode, routes_page, spawns_page
                )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise IntelligenceError(f"Raw snapshot rejected: {exc}") from exc
            return {
                "data": data,
                "provider": "raw",
                "cached": False,
                "fetched_at": time.time(),
            }
        tool, arguments = self.translate(kind, identifier, routes_page, spawns_page)
        env = dict(self.config.env)
        if self.config.provider == "prts":
            env.update(PRTS_TRANSPORT="stdio", PRTS_OUTPUT_CHANNEL="both")
        parameters = StdioServerParameters(
            command=self.config.command, args=self.config.args, env=env
        )
        async with asyncio.timeout(30):
            async with stdio_client(parameters) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    if tool not in {item.name for item in listing.tools}:
                        raise IntelligenceError(
                            f"Provider does not advertise required tool: {tool}"
                        )
                    advertised = next(
                        item for item in listing.tools if item.name == tool
                    )
                    if (
                        kind == "stage"
                        and mode is not None
                        and "mode" in advertised.inputSchema.get("properties", {})
                    ):
                        arguments["mode"] = mode
                    result = await session.call_tool(tool, arguments)
        if result.isError:
            raise IntelligenceError(f"Provider tool failed: {result.model_dump_json()}")
        warnings = [
            "Static intelligence, not live battlefield observations.",
            "Check provider limitations, pagination, region and snapshot provenance before use.",
        ]
        if self.config.provider == "prts" and kind == "stage":
            warnings.append(
                "PRTS stage query returns enemies, not tiles, routes or waves."
            )
        if self.config.provider == "prts" and kind == "operator":
            warnings.append(
                "PRTS operator query uses action=skills, not complete basic information or talents. "
                "Returned candidates are static data, not this account's measured configuration."
            )
        data = result.structuredContent
        if self.config.provider == "intelligence" and data is not None:
            status = data.get("status")
            if status is not None and status not in ("ok", "partial"):
                raise IntelligenceError(f"Provider returned {status}: {data}")
            if status == "partial":
                warnings.append(
                    "Provider returned partial data; do not assume omitted sections are empty."
                )
        if data is None:
            warnings.append(
                "Provider returned unstructured content; no structured data was inferred."
            )
        value = {
            "provider": self.config.provider,
            "region": self.config.region,
            "tool": tool,
            "arguments": arguments,
            "fetched_at": time.time(),
            "cached": False,
            "cache_age_seconds": 0,
            "data": data,
            "content": [
                item.model_dump(mode="json", by_alias=True) for item in result.content
            ],
            "warnings": warnings,
        }
        if len(self.cache) >= 128:
            del self.cache[next(iter(self.cache))]
        self.cache[key] = (time.monotonic(), value)
        return value

    async def query_complete_stage(
        self, identifier: str, mode: BattleMode, height: int
    ) -> dict:
        if self.config.provider not in ("intelligence", "raw"):
            raise IntelligenceError(
                "PRTS cannot supply complete stage maps/routes/spawns"
            )
        # Bound both pagination and total provider time; never fetch indefinitely.
        try:
            async with asyncio.timeout(120):
                return await self._collect_stage(identifier, mode, height)
        except TimeoutError as exc:
            raise IntelligenceError(
                "Provider timed out; stage completeness is unknown"
            ) from exc

    async def _collect_stage(
        self, identifier: str, mode: BattleMode, height: int
    ) -> dict:
        requested = {"routes": 1, "spawns": 1}
        seen = {"routes": set(), "spawns": set()}
        done = {"routes": False, "spawns": False}
        verified_pages = {"routes": True, "spawns": True}
        collections: dict[str, list | None] = {"routes": [], "spawns": []}
        pages = []
        warnings = []
        first = None
        snapshot_key = None
        for _ in range(100):
            page = await self.query(
                "stage",
                identifier,
                requested["routes"],
                requested["spawns"],
                refresh=True,
                mode=mode,
            )
            pages.append(page)
            data = page["data"]
            if not isinstance(data, dict):
                warnings.append("Structured stage data is missing.")
                collections = {"routes": None, "spawns": None}
                break
            for key, expected in (
                ("game_id", identifier),
                ("server", self.config.region),
                ("mode", mode),
            ):
                if key not in data:
                    warnings.append(f"Provider did not verify {key}.")
                elif data[key] != expected:
                    raise IntelligenceError(
                        f"Provider {key} mismatch: expected {expected!r}, received {data[key]!r}"
                    )
            provenance = data.get("provenance")
            current_key = (
                json.dumps(provenance, sort_keys=True)
                if isinstance(provenance, dict) and provenance
                else None
            )
            if first is None:
                first = data
                snapshot_key = current_key
            elif current_key != snapshot_key:
                raise IntelligenceError(
                    "Provider snapshot changed between pages; refusing mixed-version intelligence"
                )
            if current_key is None:
                warnings.append("Provider snapshot provenance is unknown.")
            if not isinstance(provenance, dict) or not (
                provenance.get("game_data_version") or provenance.get("build_id")
            ):
                warnings.append("Provider game-data version is unknown.")
            if not isinstance(provenance, dict) or not provenance.get("source_hash"):
                warnings.append(
                    "Provider source hash is unknown; response hash is not a substitute."
                )
            if data.get("status") != "ok":
                warnings.append("Provider did not declare complete data.")
            for name in ("routes", "spawns"):
                page_number = requested[name]
                if page_number in seen[name]:
                    continue
                seen[name].add(page_number)
                meta = data.get(f"{name}_page")
                if not isinstance(meta, dict):
                    warnings.append(f"Missing {name} pagination metadata.")
                    collections[name] = None
                    done[name] = True
                    verified_pages[name] = False
                    continue
                items = data.get(name, meta.get("items"))
                if not isinstance(items, list):
                    warnings.append(f"Missing structured {name} items.")
                    collections[name] = None
                    done[name] = True
                    verified_pages[name] = False
                    continue
                if meta.get("page") != page_number or type(meta.get("page")) is not int:
                    warnings.append(
                        f"Provider did not verify {name} page {page_number}."
                    )
                    verified_pages[name] = False
                if type(meta.get("has_more")) is not bool:
                    warnings.append(
                        f"Missing {name} has_more; pagination cannot be proven complete."
                    )
                    done[name] = True
                    verified_pages[name] = False
                elif meta["has_more"] and not items:
                    raise IntelligenceError(f"Empty {name} page claims more data")
                else:
                    done[name] = not meta["has_more"]
                collections[name].extend(items)
                if done[name]:
                    total = meta.get("total")
                    if total is not None and (
                        type(total) is not int or total != len(collections[name])
                    ):
                        raise IntelligenceError(
                            f"{name} total does not match fetched items"
                        )
                else:
                    requested[name] += 1
            if all(done.values()):
                break
        else:
            warnings.append("Pagination exceeded the 100-request bound.")
        first = first or {}
        expires_at = first.get("expires_at")
        freshness = "unknown"
        if (
            isinstance(expires_at, (int, float))
            and not isinstance(expires_at, bool)
            and math.isfinite(expires_at)
        ):
            freshness = (
                "expired" if expires_at <= time.time() else "provider_declared_current"
            )
            if freshness == "expired":
                warnings.append("Provider snapshot has expired.")
        coordinate_system = first.get("coordinate_system")
        if coordinate_system not in ("public_row_col", "maa_grid"):
            warnings.append(
                "Unknown coordinate system; coordinates were not converted."
            )
        sections = {
            name: first.get(name)
            for name in (
                "tiles",
                "effects",
                "waves",
                "fragments",
                "conditions",
                "global_modifiers",
            )
        }
        sections.update(collections)
        if sections["fragments"] is None and isinstance(sections["waves"], list):
            waves = sections["waves"]
            if all(
                isinstance(wave, dict) and isinstance(wave.get("fragments"), list)
                for wave in waves
            ):
                sections["fragments"] = [
                    fragment for wave in waves for fragment in wave["fragments"]
                ]
        completeness = {}
        for name, value in sections.items():
            completeness[name] = isinstance(value, list)
            if not completeness[name]:
                warnings.append(f"{name} is unknown, not an empty collection.")
        routes = collections["routes"]
        spawns = collections["spawns"]
        route_ids = None
        if routes is not None:
            ids = [
                route.get("route_id") if isinstance(route, dict) else None
                for route in routes
            ]
            if any(
                not isinstance(item, (str, int)) or isinstance(item, bool)
                for item in ids
            ):
                warnings.append("Routes lack stable route_id references.")
            elif len(set(ids)) != len(ids):
                raise IntelligenceError("Duplicate route IDs across pages")
            else:
                route_ids = set(ids)
        enemy_ids = set()
        enemy_references_complete = spawns is not None
        spawn_ids = set()
        spawn_count = 0
        if spawns is None:
            spawn_count = None
        else:
            for spawn in spawns:
                if not isinstance(spawn, dict):
                    raise IntelligenceError("Invalid spawn record")
                enemy = spawn.get("enemy_game_id")
                if not isinstance(enemy, str) or not enemy:
                    warnings.append("Spawn enemy references are incomplete.")
                    enemy_references_complete = False
                else:
                    enemy_ids.add(enemy)
                if "route_id" not in spawn:
                    warnings.append("Spawn route references are incomplete.")
                elif not isinstance(spawn["route_id"], (str, int)) or isinstance(
                    spawn["route_id"], bool
                ):
                    raise IntelligenceError("Invalid spawn route ID")
                elif route_ids is not None and spawn["route_id"] not in route_ids:
                    raise IntelligenceError("Spawn references an unfetched route")
                spawn_id = spawn.get("spawn_id")
                if isinstance(spawn_id, (str, int)) and not isinstance(spawn_id, bool):
                    if spawn_id in spawn_ids:
                        raise IntelligenceError("Duplicate spawn IDs across pages")
                    spawn_ids.add(spawn_id)
                else:
                    warnings.append(
                        "Spawn IDs are unavailable; cross-page duplicates cannot be excluded."
                    )
                count = spawn.get("count")
                if type(count) is not int or count < 0:
                    spawn_count = None
                    warnings.append("Static spawn count is unknown.")
                elif spawn_count is not None:
                    spawn_count += count
        normalized = None
        if coordinate_system in ("public_row_col", "maa_grid"):
            normalized = self._normalize_coordinates(
                sections, height, coordinate_system
            )
            tiles = normalized["tiles"]
            if (
                isinstance(tiles, list)
                and tiles
                and all(isinstance(row, list) for row in tiles)
                and len(tiles) != height
            ):
                raise IntelligenceError(
                    "Provider tile height differs from the attached map"
                )
            if (
                coordinate_system == "public_row_col"
                and isinstance(tiles, list)
                and tiles
                and all(isinstance(row, list) for row in tiles)
            ):
                if len(tiles) != height:
                    raise IntelligenceError(
                        "Provider tile height differs from the attached map"
                    )
                normalized["tiles"] = list(reversed(tiles))
        warnings = list(dict.fromkeys(warnings))
        return {
            "status": "partial" if warnings else "succeeded",
            "provider": self.config.provider,
            "region": self.config.region,
            "binding": {
                "stage_id": identifier,
                "mode": mode,
                "source": "caller_supplied_not_visually_verified",
            },
            "static_or_live": "static",
            "provenance": first.get("provenance"),
            "freshness": freshness,
            "response_sha256": hashlib.sha256(
                json.dumps([p["data"] for p in pages], sort_keys=True).encode()
            ).hexdigest(),
            "completeness": {
                **completeness,
                "all_pages": all(done.values())
                and all(verified_pages.values())
                and all(collections[name] is not None for name in collections),
            },
            "normalized": normalized,
            "enemy_definitions": first.get("enemy_definitions"),
            "mode_modifiers": first.get("mode_modifiers"),
            "raw_mechanisms": first.get("raw_mechanisms"),
            "non_spawn_actions": first.get("non_spawn_actions"),
            "map_effects": first.get("map_effects"),
            "alignment": "unknown",
            "coordinate_system": "maa_grid" if normalized is not None else "unknown",
            "static_spawn_count": spawn_count,
            "referenced_enemy_ids": sorted(enemy_ids)
            if enemy_references_complete
            else None,
            "pages": pages,
            "warnings": warnings,
            "limitations": [
                "Static spawns do not include every dynamically created entity.",
                "Provider provenance is retained, not independently authenticated or checked against the installed game version.",
                "Collection presence does not prove that every terrain/enemy/skill mechanic has been interpreted.",
            ],
        }

    @classmethod
    def _normalize_coordinates(cls, value, height: int, system: str):
        if isinstance(value, list):
            return [cls._normalize_coordinates(item, height, system) for item in value]
        if not isinstance(value, dict):
            return value
        result = {
            key: cls._normalize_coordinates(item, height, system)
            for key, item in value.items()
        }
        if system == "public_row_col" and ("row" in value or "col" in value):
            row, col = value.get("row"), value.get("col")
            if any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                for v in (row, col)
            ):
                raise IntelligenceError("Invalid public row/col coordinates")
            result["maa_location"] = [col, height - 1 - row]
        return result
