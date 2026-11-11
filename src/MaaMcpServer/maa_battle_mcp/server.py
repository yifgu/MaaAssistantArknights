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

from .decision import CLIENT_REGIONS, DecisionContext
from .intelligence import IntelligenceClient
from .models import (
    BattleMode,
    BoundedStepRequest,
    DeployRequest,
    Identifier,
    IdentifyRequest,
    IntelligenceConfig,
    PauseRequest,
    Placement,
    RawIntelligenceConfig,
    Settings,
    SpeedRequest,
    TerminalRequest,
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
    def evidence_result(value: dict, *keys: str) -> CallToolResult:
        images = []
        for key in keys:
            relative = value.get(key)
            if not relative:
                continue
            try:
                image = service.evidence_path({"image_path": relative}).read_bytes()
            except (OSError, BattleError) as exc:
                logging.getLogger(__name__).exception("Could not read command evidence")
                value = {
                    **value,
                    "status": "outcome_unknown",
                    "evidence_read_error": str(exc),
                }
                continue
            images.append(
                ImageContent(
                    type="image",
                    mimeType="image/png",
                    data=base64.b64encode(image).decode("ascii"),
                )
            )
        response = tool_result(value)
        response.content.extend(images)
        return response

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
            "Prefer battle_step for one action or bounded wait followed by verified pause or terminal evidence. "
            "Wait success means the transaction completed safely, not necessarily battle victory. "
            "Supplied loadouts and static coverage are not live observations or optimality proofs. "
            "Keep the game paused during intelligence queries and placement evaluation; read-only tools do not pause it. "
            "battle_identify can explicitly adopt a manual pause without resuming. "
            "Manual-pause adoption must be explicit. Close does not pause the game. "
            "No automatic stage entry, spending, contract selection or optimizer. "
            "Unrecognized terminal screens remain unknown; never infer victory from leaving battle."
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
                **service.diagnostics(),
                "intelligence_configured": intelligence is not None,
                "loadout_configured": service.decision.config is not None,
                "mode": service.mode,
                "limitations": [
                    "Only a single caller-supplied fixed map; no camera movement or stage recognition.",
                    "Battlefield positions are unverified action history; no mid-battle reconstruction.",
                    "Summon deployment requires a trusted startup allowlist and fresh card_id; instance skills/retreat remain unsupported.",
                    "Mutations are not automatically confirmed; inspect evidence after each action.",
                    "battle_step bounds one action or host-time wait; pause and terminal evidence are returned separately.",
                    "Terminal OCR currently recognizes configured CN/English titles only; other screens remain unknown.",
                    "Placement evaluation checks base geometry, not current skill effects or live occupancy.",
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
    async def battle_attach(
        stage_name: Identifier,
        mode: BattleMode | None = None,
        preparation_id: Identifier | None = None,
        confirm_preparation_unchanged: bool = False,
    ) -> CallToolResult:
        """Bind a unique MaaCore stage/level ID to an already-started battle. Verify the returned map."""
        return tool_result(
            await service.attach(
                stage_name, mode, preparation_id, confirm_preparation_unchanged
            )
        )

    @server.tool(annotations=READ_ONLY)
    async def battle_observe_preparation(
        stage_name: Identifier, mode: BattleMode
    ) -> CallToolResult:
        """Before attach, read the manually opened formation page without input. Only names are recognized; skills/configuration remain unknown."""
        return evidence_result(await service.prepare(stage_name, mode), "image_path")

    @server.tool(annotations=MUTATING)
    async def battle_invalidate_preparation() -> CallToolResult:
        """Invalidate preparation binding after roster, skill, account or stage changes. Sends no device input."""
        return tool_result(await service.invalidate_preparation())

    @server.tool(annotations=MUTATING)
    async def battle_set_speed(request: SpeedRequest) -> CallToolResult:
        """Set observed 1x/2x idempotently while verified paused. Never resumes. The host chooses the speed."""
        return evidence_result(
            await service.command(
                "set_speed", request, {"multiplier": request.multiplier}
            ),
            "image_path",
            "before_image_path",
        )

    @server.tool(annotations=READ_ONLY)
    async def battle_collect_terminal(request: TerminalRequest) -> CallToolResult:
        """Collect terminal evidence under the input lock within a deadline. Never unlocks or clicks."""
        return evidence_result(
            await service.command(
                "finalize", request, {"duration_ms": request.duration_ms}
            ),
            "image_path",
        )

    @server.tool(annotations=READ_ONLY)
    async def battle_evaluate_placement(
        session_id: Identifier,
        placements: Annotated[list[Placement], Field(min_length=1, max_length=32)],
    ) -> CallToolResult:
        """Check candidate formation base geometry and static tile conflicts. Does not verify live occupancy or optimize a strategy."""
        service._check_session(session_id)
        if service.map_info is None:
            raise BattleError("Attach a map before evaluating placements")
        return tool_result(service.decision.evaluate(service.map_info, placements))

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
        return evidence_result(result, "image_path")

    @server.tool(annotations=MUTATING)
    async def battle_identify(request: IdentifyRequest) -> CallToolResult:
        """Inspect cards without resuming; explicitly authorize manual-pause adoption. Returns final PNG and pause verification."""
        result = await service.command(
            "identify", request, {"adopt_manual_pause": request.adopt_manual_pause}
        )
        return evidence_result(result, "image_path")

    @server.tool(annotations=MUTATING)
    async def battle_step(request: BoundedStepRequest) -> CallToolResult:
        """Run one action or bounded wait; capture, then verify pause or terminal evidence. Wait uses host time, not simulation time."""
        result = await service.command(
            "step",
            request,
            request.model_dump(
                exclude={"session_id", "operation_id", "timeout_ms"}, exclude_none=True
            ),
        )
        return evidence_result(result, "return_image_path", "capture_image_path")

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
        """Request pause/resume with state guards. Resume only a server-requested or explicitly adopted pause."""
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
        """Close the session; waits for an active bounded step to finish its pause cleanup."""
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
        complete_stage: bool = False,
        session_id: Identifier | None = None,
    ) -> CallToolResult:
        """Query configured static intelligence. Intelligence uses game IDs; PRTS uses stage IDs or Chinese names."""
        if intelligence is None:
            raise BattleError(
                "No intelligence provider configured; start with --intel-config"
            )
        if complete_stage:
            if (
                kind != "stage"
                or session_id is None
                or routes_page != 1
                or spawns_page != 1
            ):
                raise BattleError(
                    "Complete stage queries require kind=stage, session_id and initial pages"
                )
            service._check_session(session_id)
            if (
                service.map_info is None
                or service.map_info["stage_id"] != identifier
                or service.mode is None
            ):
                raise BattleError(
                    "Complete intelligence requires the attached exact stage ID and explicit mode"
                )
            if (
                intelligence.config.region
                != CLIENT_REGIONS[service.settings.client_type]
            ):
                raise BattleError(
                    "Provider region does not match the device client; no cross-region fallback"
                )
            return tool_result(
                await intelligence.query_complete_stage(
                    identifier, service.mode, service.map_info["height"]
                )
            )
        if session_id is not None:
            raise BattleError("session_id is only valid for a complete stage query")
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
    parser.add_argument("--loadout-config", type=Path)
    parser.add_argument(
        "--allow-summon", action="append", default=[], metavar="GAME_ID"
    )
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
        allowed_summons=args.allow_summon,
    )
    intelligence = None
    if args.intel_config:
        data = json.loads(args.intel_config.read_text(encoding="utf-8"))
        config = (
            RawIntelligenceConfig
            if data.get("provider") == "raw"
            else IntelligenceConfig
        ).model_validate_json(json.dumps(data))
        intelligence = IntelligenceClient(config)
    decision = DecisionContext(settings, args.loadout_config)
    create_server(BattleService(settings, decision=decision), intelligence).run(
        transport="stdio"
    )


if __name__ == "__main__":
    main()
