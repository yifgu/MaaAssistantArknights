import argparse
import base64
import json
import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import Field

from .intelligence import IntelligenceClient
from .models import (
    DeployRequest,
    Identifier,
    IntelligenceConfig,
    OperationRequest,
    PauseRequest,
    Settings,
    UnitRequest,
)
from .service import BattleError, BattleService

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
MUTATING = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)


def tool_result(value: dict) -> CallToolResult:
    return CallToolResult(
        isError=value.get("status") in ("failed", "outcome_unknown"),
        structuredContent=value,
        content=[TextContent(type="text", text=json.dumps(value, ensure_ascii=False))],
    )


def create_server(
    service: BattleService, intelligence: IntelligenceClient | None = None
) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        try:
            yield {}
        finally:
            await service.close()
            async with service.lock:
                service.journal.close()

    server = FastMCP(
        "maa-battle",
        instructions=(
            "Controls a real Arknights device. Attach only to a manually started, fixed-map battle. "
            "Verify the supplied stage against screenshot evidence; map binding is not recognized. "
            "Observe before acting. Unknown identities and enemy state are not empty state. "
            "Mutations may return outcome_unknown even if input succeeded: capture evidence, "
            "never blindly retry. Reuse an operation_id only for the identical request. "
            "Identify and pause commands may change the UI. Close does not pause the game. "
            "No automatic stage entry, spending, contract selection, victory detection or optimizer."
        ),
        lifespan=lifespan,
    )

    @server.tool(annotations=READ_ONLY)
    async def battle_status() -> CallToolResult:
        """Report connection/session state and first-version capability limits."""
        return tool_result(
            {
                "connected": service.backend is not None,
                "session_id": service.session_id,
                "intelligence_configured": intelligence is not None,
                "limitations": [
                    "Only a single caller-supplied fixed map; no camera movement or stage recognition.",
                    "Battlefield positions are unverified action history; no mid-battle reconstruction.",
                    "Summon/device deployment is rejected until instance-level identity tracking is implemented.",
                    "Mutations are not automatically confirmed; inspect evidence after each action.",
                    "No enemy tracking, contract automation, experiment optimizer or arbitrary clicks.",
                    "Stop other MAA/GUI controllers before connecting.",
                ],
            }
        )

    @server.tool(annotations=MUTATING)
    async def battle_connect() -> CallToolResult:
        """Connect to the server's preconfigured device. Does not launch or enter a stage."""
        return tool_result(await service.connect())

    @server.tool(annotations=MUTATING)
    async def battle_attach(stage_name: Identifier) -> CallToolResult:
        """Bind a unique MaaCore stage/level ID to an already-started battle. Verify the returned map."""
        return tool_result(await service.attach(stage_name))

    @server.tool(annotations=READ_ONLY)
    async def battle_observe(session_id: Identifier) -> CallToolResult:
        """Take a fresh passive observation. Never clicks to identify unknown operators."""
        return tool_result(await service.read(session_id, "observe"))

    @server.tool(annotations=READ_ONLY)
    async def battle_get_map(session_id: Identifier) -> CallToolResult:
        """Return MaaCore's static tile grid and normal/deployment screen projections."""
        return tool_result(await service.read(session_id, "map"))

    @server.tool(annotations=READ_ONLY)
    async def battle_capture_evidence(session_id: Identifier) -> CallToolResult:
        """Return a PNG and observation from the SAME frame for multimodal analysis. Keeps 128 captures/session."""
        result = await service.read(session_id, "capture")
        response = tool_result(result)
        if not response.isError:
            image = service.evidence_path(result).read_bytes()
            response.content.append(
                ImageContent(
                    type="image",
                    mimeType="image/png",
                    data=base64.b64encode(image).decode("ascii"),
                )
            )
        return response

    @server.tool(annotations=MUTATING)
    async def battle_identify(request: OperationRequest) -> CallToolResult:
        """Actively inspect unknown deployment cards using OCR. Leaves a requested pause; inspect evidence then resume."""
        return tool_result(await service.command("identify", request))

    @server.tool(annotations=MUTATING)
    async def battle_deploy(request: DeployRequest) -> CallToolResult:
        """Deploy a uniquely recognized unit to a MaaCore grid tile. No waiting for cost/cooldown; resume first."""
        return tool_result(
            await service.command(
                "deploy",
                request,
                {
                    "unit_id": request.unit_id,
                    "location": request.location,
                    "direction": request.direction,
                },
            )
        )

    @server.tool(annotations=MUTATING)
    async def battle_retreat(request: UnitRequest) -> CallToolResult:
        """Attempt retreat at the unit's tracked position. Confirm the position with evidence first."""
        return tool_result(
            await service.command("retreat", request, {"unit_id": request.unit_id})
        )

    @server.tool(annotations=MUTATING)
    async def battle_use_skill(request: UnitRequest) -> CallToolResult:
        """Attempt the equipped skill once at a tracked position; never waits indefinitely for readiness."""
        return tool_result(
            await service.command("skill", request, {"unit_id": request.unit_id})
        )

    @server.tool(annotations=MUTATING)
    async def battle_set_paused(request: PauseRequest) -> CallToolResult:
        """Request pause/resume with state guards. Resume only a server-requested pause; verify the result."""
        return tool_result(
            await service.command("set_paused", request, {"paused": request.paused})
        )

    @server.tool(annotations=READ_ONLY)
    async def battle_get_operation(
        session_id: Identifier, operation_id: Identifier
    ) -> CallToolResult:
        """Read a durable operation result, including after disconnection. Never replays input."""
        return tool_result(service.operation(session_id, operation_id))

    @server.tool(annotations=MUTATING)
    async def battle_close() -> CallToolResult:
        """Stop/interrupt the worker and invalidate the session. Does NOT pause or abandon the running game."""
        return tool_result(await service.close())

    @server.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        )
    )
    async def intel_query(
        kind: Literal["stage", "operator", "enemy"],
        identifier: Identifier,
        routes_page: Annotated[int, Field(ge=1)] = 1,
        spawns_page: Annotated[int, Field(ge=1)] = 1,
    ) -> CallToolResult:
        """Query configured static intelligence. Intelligence uses game IDs; PRTS uses stage IDs or Chinese names."""
        if intelligence is None:
            raise BattleError(
                "No intelligence provider configured; start with --intel-config"
            )
        return tool_result(
            await intelligence.query(kind, identifier, routes_page, spawns_page)
        )

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Local MaaCore battle MCP (stdio)")
    parser.add_argument("--core-dir", type=Path, required=True)
    parser.add_argument("--resource-dir", type=Path)
    parser.add_argument(
        "--user-dir", type=Path, default=Path.home() / ".maa-battle-mcp"
    )
    parser.add_argument("--adb-path", default="adb")
    parser.add_argument("--address", default="127.0.0.1:5555")
    parser.add_argument("--connection-config", default="General")
    parser.add_argument(
        "--touch-mode",
        choices=["adb", "minitouch", "maatouch", "MaaFwAdb"],
        default="maatouch",
    )
    parser.add_argument(
        "--client-type",
        choices=["Official", "Bilibili", "YoStarEN", "YoStarJP", "YoStarKR", "txwy"],
        default="Official",
    )
    parser.add_argument("--intel-config", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    core_dir = args.core_dir.resolve()
    settings = Settings(
        core_dir=core_dir,
        resource_dir=(args.resource_dir or core_dir).resolve(),
        user_dir=args.user_dir.resolve(),
        adb_path=shutil.which(args.adb_path) or str(Path(args.adb_path).resolve()),
        address=args.address,
        connection_config=args.connection_config,
        touch_mode=args.touch_mode,
        client_type=args.client_type,
    )
    intelligence = None
    if args.intel_config:
        config = IntelligenceConfig.model_validate_json(
            args.intel_config.read_text(encoding="utf-8")
        )
        intelligence = IntelligenceClient(config)
    create_server(BattleService(settings), intelligence).run(transport="stdio")


if __name__ == "__main__":
    main()
