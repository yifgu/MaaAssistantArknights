import asyncio
import time
from typing import Literal

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .models import IntelligenceConfig


class IntelligenceError(RuntimeError):
    pass


class IntelligenceClient:
    def __init__(self, config: IntelligenceConfig):
        if config.provider == "prts" and config.region != "cn":
            raise IntelligenceError(
                "PRTS adapter supports CN data only; no cross-region fallback"
            )
        self.config = config
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
    ) -> dict:
        key = (kind, identifier, routes_page, spawns_page)
        if key in self.cache:
            fetched, value = self.cache[key]
            if time.monotonic() - fetched < 300:
                return {**value, "cached": True}
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
            "data": data,
            "content": [item.model_dump(mode="json") for item in result.content],
            "warnings": warnings,
        }
        if len(self.cache) >= 128:
            del self.cache[next(iter(self.cache))]
        self.cache[key] = (time.monotonic(), value)
        return value
