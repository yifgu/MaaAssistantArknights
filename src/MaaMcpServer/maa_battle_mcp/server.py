import argparse
import base64
import json
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, unquote

from mcp.server.fastmcp import FastMCP
from mcp.types import (
    CallToolResult,
    ImageContent,
    ResourceLink,
    TextContent,
    ToolAnnotations,
)
from pydantic import Field

from .decision import CLIENT_REGIONS, DecisionContext
from .intelligence import IntelligenceClient
from .media import MIME
from .models import (
    AnalyzeVideoRequest,
    BattleMode,
    BoundedStepRequest,
    DeployRequest,
    Identifier,
    IdentifyRequest,
    ImportVideoRequest,
    IntelligenceConfig,
    OperationRequest,
    PauseRequest,
    Placement,
    PreparationRequest,
    RawIntelligenceConfig,
    RecordClipRequest,
    Settings,
    SpeedRequest,
    TerminalRequest,
    UnitRequest,
)
from .service import BattleError, BattleService
from .unit_data import UnitCatalog

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
    service: BattleService,
    intelligence: IntelligenceClient | None = None,
    *,
    tool_profile: Literal["minimal", "full"] = "minimal",
) -> FastMCP:
    if tool_profile not in ("minimal", "full"):
        raise ValueError("tool_profile must be minimal or full")

    def evidence_references(value: dict) -> list[dict]:
        session_id = value.get("session_id")
        operation_id = value.get("operation_id") or value.get("command_id")
        if operation_id is None and isinstance(value.get("host_fallback"), dict):
            operation_id = value.get("preparation_id")
        if not session_id or not operation_id:
            return []
        frames = [
            frame
            for frame in value.get("evidence_frames", [])
            if isinstance(frame, dict)
        ]
        if value.get("observation_id") and value.get("image_path"):
            frames.append(value)
        for snapshot_key, image_key in (
            ("snapshot", "image_path"),
            ("capture_time_snapshot", "capture_image_path"),
            ("return_time_snapshot", "return_image_path"),
            ("terminal", "terminal_evidence_path"),
        ):
            snapshot = value.get(snapshot_key) or {}
            if isinstance(snapshot, dict):
                path = value.get(image_key) or snapshot.get("image_path")
                if path:
                    frames.append({**snapshot, "image_path": path})
        references = {}
        for frame in frames:
            observation_id = frame.get("observation_id")
            if not observation_id or not frame.get("image_path"):
                continue
            uri = "battle-evidence://" + "/".join(
                quote(str(part), safe="")
                for part in (session_id, operation_id, observation_id)
            )
            references[uri] = {
                "resource_uri": uri,
                "mime_type": "image/png",
                "session_id": session_id,
                "operation_id": operation_id,
                "observation_id": observation_id,
                "captured_at_ms": frame.get("captured_at_ms"),
                "image_path": frame["image_path"],
                "frame_is_live": False,
            }
        return list(references.values())

    def response_value(value: dict, *, compact: bool = True) -> dict:
        references = evidence_references(value)
        value = {**value, **({"evidence": references} if references else {})}
        fallback = value.get("host_fallback")
        if isinstance(fallback, dict):
            indexed = {
                (
                    frame["session_id"],
                    frame["operation_id"],
                    frame["observation_id"],
                ): frame
                for frame in references
            }
            items = []
            for item in fallback.get("items", []):
                retrieval = item.get("retrieval")
                if not isinstance(retrieval, dict):
                    items.append(item)
                    continue
                frame = indexed.get(
                    (
                        retrieval.get("session_id"),
                        retrieval.get("operation_id"),
                        retrieval.get("observation_id"),
                    )
                )
                frame = frame if retrieval.get("available") else None
                retrieval = {
                    **retrieval,
                    "resource_uri": frame["resource_uri"] if frame else None,
                    "mime_type": "image/png" if frame else None,
                }
                if tool_profile == "minimal":
                    retrieval.pop("tool", None)
                    retrieval["available"] = frame is not None
                items.append({**item, "retrieval": retrieval})
            value["host_fallback"] = {**fallback, "items": items}
        if tool_profile == "full" or not compact:
            return value
        canonical = None
        for key in ("return_time_snapshot", "snapshot", "capture_time_snapshot"):
            snapshot = value.get(key)
            if isinstance(snapshot, dict) and isinstance(
                snapshot.get("battle_decision"), dict
            ):
                canonical = snapshot["battle_decision"]
                break
        canonical = canonical or value.get("battle_decision")
        if canonical is None:
            return value
        terrain = canonical.get("terrain") or {}
        geometry = terrain.get("static_geometry")
        if isinstance(geometry, dict):
            canonical = {
                **canonical,
                "terrain": {
                    **terrain,
                    "static_geometry": {
                        key: geometry[key]
                        for key in ("stage_id", "level_id", "width", "height")
                        if key in geometry
                    },
                    "geometry_retrieval": {
                        "tool": "battle_observe",
                        "include_map": True,
                        "source": "caller_bound_static_map_not_live_visual_terrain",
                    },
                },
            }
        projected = {
            "deployment",
            "battlefield",
            "field_units",
            "field_unit_detection",
            "dynamic_overlay",
            "state_events",
            "event_cursor",
            "events_retained_from",
            "cost",
            "kills",
            "total_kills",
            "life",
            "life_evidence",
            "life_delta",
            "life_delta_evidence",
            "speed",
            "battle_decision",
        }
        value = {key: item for key, item in value.items() if key not in projected}
        value["battle_decision"] = canonical
        for key in ("snapshot", "capture_time_snapshot", "return_time_snapshot"):
            snapshot = value.get(key)
            if not isinstance(snapshot, dict):
                continue
            value[key] = {
                name: item for name, item in snapshot.items() if name not in projected
            }
        return value

    def result(value: dict, *, compact: bool = True) -> CallToolResult:
        response = tool_result(response_value(value, compact=compact))
        for evidence in response.structuredContent.get("evidence", []):
            response.content.append(
                ResourceLink(
                    type="resource_link",
                    uri=evidence["resource_uri"],
                    name=f"battle-evidence-{evidence['observation_id']}",
                    mimeType="image/png",
                    description="Retained original PNG; historical read, no new capture or device input",
                )
            )
        return response

    def evidence_result(
        value: dict, *keys: str, include_image: bool | None = None
    ) -> CallToolResult:
        if include_image is None:
            include_image = tool_profile == "full"
        if not include_image:
            return result(value)
        images = []
        seen_paths = set()
        for key in keys:
            relative = value.get(key)
            if not relative:
                continue
            try:
                path = service.evidence_path({"image_path": relative})
                if path in seen_paths:
                    continue
                image = path.read_bytes()
                seen_paths.add(path)
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
        response = result(value)
        response.content.extend(images)
        return response

    @asynccontextmanager
    async def lifespan(_server: FastMCP):
        try:
            yield {}
        finally:
            service.media.stopping = True
            await service.close()
            async with service.lock:
                service.journal.close()
                service.media.store.db.close()

    server = FastMCP(
        "maa-battle",
        instructions=(
            "Controls a real Arknights device. Attach only to a manually started, fixed-map battle. "
            "Verify the supplied stage against screenshot evidence; map binding is not recognized. "
            "Observe before acting. Unknown identities and enemy state are not empty state. "
            "Preparation host_fallback lists unresolved fields for multimodal review; retrieve original "
            "operation frames through their battle-evidence resources. Host interpretation does not become native fact. "
            "suspected_defeat is a deduplicated advisory with unknown cause, not proof of death or tile release. "
            "Use battle_decision domains, provenance, missing prerequisites and deduplicated event IDs "
            "to consume structured native evidence. Full combat state is not available; readiness is not "
            "activation, phase, SP, HP, ammo or healing. Use paired PNGs for unknowns, anomalies and audit. "
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
    catalog = UnitCatalog(service.settings.resource_dir)
    minimal = {
        "battle_status",
        "battle_connect",
        "battle_attach",
        "battle_observe",
        "battle_inspect_preparation",
        "battle_identify",
        "battle_step",
        "battle_get_unit_info",
        "battle_get_operation",
        "battle_close",
    }

    def expose(*, annotations: ToolAnnotations):
        def register(function):
            if function.__name__ == "intel_query":
                enabled = intelligence is not None
            else:
                enabled = tool_profile == "full" or function.__name__ in minimal
            if enabled:
                server.tool(annotations=annotations)(function)
            return function

        return register

    @expose(annotations=READ_ONLY)
    async def battle_get_unit_info(game_id: Identifier) -> CallToolResult:
        """Query bundled unit kinds, effects, base key frames, skills/talents, token ID relations and adapter capabilities. No connection, input or intelligence provider required."""
        return result(catalog.get(game_id))

    @expose(annotations=READ_ONLY)
    async def battle_status() -> CallToolResult:
        """Report connection/session state and first-version capability limits."""
        return result(
            {
                "connected": service.backend is not None,
                "session_id": service.session_id,
                "preparation_session_id": service.preparation_session_id,
                **service.diagnostics(),
                "intelligence_configured": intelligence is not None,
                "tool_profile": tool_profile,
                "tool_count": len(await server.list_tools()),
                "loadout_configured": service.decision.config is not None,
                "mode": service.mode,
                "media": {
                    "requires_matching_core": True,
                    "encoder": "native_OpenCV_MJPEG_AVI_checked_per_artifact",
                    "mp4_encoding": False,
                    "mcp_video_content": False,
                    "binary_resource_limit_bytes": 16 * 1024 * 1024,
                    "frame_response_limit": 4,
                    "recording_limits": {
                        "duration_ms": 8_000,
                        "frames": 24,
                        "raw_bytes": 64 * 1024 * 1024,
                        "published_bytes": 32 * 1024 * 1024,
                    },
                    "retention": {"artifacts": 8, "artifact_bytes": 256 * 1024 * 1024},
                    "offline_job_active": service.media.lock.locked(),
                    "offline_scope": "experimental_video_to_copilot_not_combat_state_or_execution",
                },
                "battle_decision_schema_version": 1,
                "runtime_capabilities": {
                    "preparation_name_fallback": "shared_OperNameAnalyzer_in_formation_name_ROIs",
                    "preparation_host_fallback": "field_scoped_multimodal_review_without_native_fact_promotion",
                    "historical_evidence": "operation_bound_original_PNG_retrieval_without_capture",
                    "defeat_hints": "derived_suspected_defeat_for_host_review_not_presence_authority",
                    "hud": "native_life_cost_kills_speed_scene",
                    "changes": "bounded_native_observation_events_with_stable_ids",
                    "hp": "unassociated_health_bar_rectangles_not_fill_measurement",
                    "owned_skill_ready": "requires_identity_bound_verified_tile_anchor",
                    "cooling_departure": "separate_observed_card_and_conservative_owned_instance_evidence",
                    "full_combat_state": False,
                    "sp_hp_fraction_ammo_phase_live_enemies": "unavailable",
                },
                "limitations": [
                    "Only a single caller-supplied fixed map; no camera movement or stage recognition.",
                    "Battlefield positions are unverified action history; no mid-battle reconstruction.",
                    "Reviewed summon adapters need a fresh concrete card_id, not a startup ID enumeration. Explicit legacy allowlists remain restrictions.",
                    "Instance presence and requested location are separate. Selected-name evidence alone never verifies a tile or authorizes retreat.",
                    "Retreat requires a positive selected identity/button and a verified tile anchor; no production tile-anchor adapter is available yet.",
                    "TL/act55side uses per-tile deployment invariants: supported fixed ground and ranged operators on trenches; other dynamic interactions are refused. Live tile pixel changes are not board/snow/occupancy recognition.",
                    "Mutations are not automatically confirmed. Consume structured evidence/events; paired PNGs remain necessary for unresolved state and anomalies.",
                    "Life differences and cooling/departure alerts are observation-derived, never a native defeat/leak/mechanic cause classification.",
                    "Explicit and implicit ordinary cards share identity/layout/avatar/freshness preflight; duplicates and summons still require concrete card_id.",
                    "battle_step bounds one action or host-time wait; pause and terminal evidence are returned separately.",
                    "Terminal OCR currently recognizes configured CN/English titles only; other screens remain unknown.",
                    "Placement evaluation checks base geometry, not current skill effects or live occupancy.",
                    "No enemy tracking, contract automation, experiment optimizer or arbitrary clicks.",
                    "Stop other MAA/GUI controllers before connecting.",
                ],
            }
        )

    @expose(annotations=MUTATING)
    async def battle_connect() -> CallToolResult:
        """Connect to the server's preconfigured device. Does not launch or enter a stage."""
        return result(await service.connect())

    @expose(annotations=MUTATING)
    async def battle_attach(
        stage_name: Identifier,
        mode: BattleMode | None = None,
        preparation_id: Identifier | None = None,
        confirm_preparation_unchanged: bool = False,
    ) -> CallToolResult:
        """Bind a unique MaaCore stage/level ID to an already-started battle. Verify the returned map."""
        return result(
            await service.attach(
                stage_name, mode, preparation_id, confirm_preparation_unchanged
            )
        )

    @expose(annotations=READ_ONLY)
    async def battle_observe_preparation(
        stage_name: Identifier, mode: BattleMode
    ) -> CallToolResult:
        """Before attach, read the manually opened formation page without input. Only names are recognized; skills/configuration remain unknown."""
        return evidence_result(await service.prepare(stage_name, mode), "image_path")

    @expose(annotations=MUTATING)
    async def battle_inspect_preparation(request: PreparationRequest) -> CallToolResult:
        """Before attach, inspect the current roster in one bounded native transaction. Opens details and scrolls, never selects operators/skills or starts battle. Unknown/ambiguous fields stay null; inspect scan_status, evidence_frames and formation_restored."""
        return evidence_result(await service.inspect_preparation(request), "image_path")

    @expose(annotations=MUTATING)
    async def battle_invalidate_preparation() -> CallToolResult:
        """Invalidate preparation binding after roster, skill, account or stage changes. Sends no device input."""
        return result(await service.invalidate_preparation())

    @expose(annotations=MUTATING)
    async def battle_set_speed(request: SpeedRequest) -> CallToolResult:
        """Set observed 1x/2x idempotently while verified paused. Never resumes. The host chooses the speed."""
        return evidence_result(
            await service.command(
                "set_speed", request, {"multiplier": request.multiplier}
            ),
            "image_path",
            "before_image_path",
        )

    @expose(annotations=READ_ONLY)
    async def battle_collect_terminal(request: TerminalRequest) -> CallToolResult:
        """Collect terminal/pause evidence without input. Two fresh paused frames can clear pause uncertainty, never terminal/safety quarantine."""
        return evidence_result(
            await service.command(
                "finalize", request, {"duration_ms": request.duration_ms}
            ),
            "image_path",
        )

    @expose(annotations=READ_ONLY)
    async def battle_evaluate_placement(
        session_id: Identifier,
        placements: Annotated[list[Placement], Field(min_length=1, max_length=32)],
    ) -> CallToolResult:
        """Check candidate formation base geometry and static tile conflicts. Does not verify live occupancy or optimize a strategy."""
        return result(await service.evaluate_placement(session_id, placements))

    @expose(annotations=READ_ONLY)
    async def battle_observe(
        session_id: Identifier,
        include_image: bool = False,
        include_map: bool = False,
        duration_ms: Annotated[
            int,
            Field(
                ge=0,
                le=8_000,
                json_schema_extra={
                    "anyOf": [{"const": 0}, {"minimum": 100, "maximum": 8_000}]
                },
            ),
        ] = 0,
        operation_id: Identifier | None = None,
        timeout_ms: Annotated[int, Field(ge=1, le=30_000)] = 10_000,
    ) -> CallToolResult:
        """Read a fresh frame, never identify, pause, resume or send input. Images/maps are opt-in. duration_ms=100..8000 collects read-only terminal/pause evidence only in an input-blocked state, within timeout_ms; a running game still advances."""
        request = OperationRequest(
            session_id=session_id,
            operation_id=operation_id or str(uuid.uuid4()),
            timeout_ms=timeout_ms,
        )
        if duration_ms:
            request = TerminalRequest(**request.model_dump(), duration_ms=duration_ms)
            value = await service.command(
                "finalize", request, {"duration_ms": duration_ms}
            )
        elif operation_id is None and timeout_ms == 10_000:
            value = await service.read(session_id, "observe")
        else:
            value = await service.command("observe", request)
        if include_map:
            value = {**value, "map": service.map_info}
        return evidence_result(value, "image_path", include_image=include_image)

    @expose(annotations=READ_ONLY)
    async def battle_get_map(session_id: Identifier) -> CallToolResult:
        """Return current/baseline PNGs, static geometry and per-tile pixel changes from the first session frame. Changes are not terrain classification."""
        result = await service.read(session_id, "map")
        keys = (
            ("image_path", "baseline_image_path")
            if result.get("image_path") != result.get("baseline_image_path")
            else ("image_path",)
        )
        return evidence_result(result, *keys)

    @expose(annotations=READ_ONLY)
    async def battle_capture_evidence(session_id: Identifier) -> CallToolResult:
        """Return a PNG and observation from the SAME frame for multimodal analysis. Keeps 128 captures/session."""
        result = await service.read(session_id, "capture")
        return evidence_result(result, "image_path")

    @expose(annotations=MUTATING)
    async def battle_identify(request: IdentifyRequest) -> CallToolResult:
        """Inspect cards without resuming; explicitly authorize manual-pause adoption. Returns final PNG and pause verification."""
        result = await service.command(
            "identify", request, {"adopt_manual_pause": request.adopt_manual_pause}
        )
        return evidence_result(result, "image_path")

    @expose(annotations=MUTATING)
    async def battle_step(request: BoundedStepRequest) -> CallToolResult:
        """One deploy/retreat/skill, bounded wait, pause-only, or verified-paused 1x/2x speed change; capture and verify pause/terminal evidence. Manual-pause adoption must be explicit. Wait uses host time, not simulation time."""
        result = await service.command(
            "step",
            request,
            request.model_dump(
                exclude={"session_id", "operation_id", "timeout_ms"}, exclude_none=True
            ),
        )
        response = evidence_result(
            result, "return_image_path", "capture_image_path", "image_path"
        )
        response.content.extend(
            item
            for item in media_result(result).content
            if item.type == "resource_link" and item.name == "local-video-artifact"
        )
        return response

    @expose(annotations=MUTATING)
    async def battle_deploy(request: DeployRequest) -> CallToolResult:
        """Legacy deployment entry; same bounded preflight and pause cleanup as battle_step. Resume first; summons require card_id."""
        return evidence_result(
            await service.command(
                "deploy",
                request,
                {
                    "unit_id": request.unit_id,
                    "location": request.location,
                    "direction": request.direction,
                    **(
                        {"card_id": request.card_id}
                        if request.card_id is not None
                        else {}
                    ),
                },
            ),
            "return_image_path",
            "capture_image_path",
        )

    @expose(annotations=MUTATING)
    async def battle_retreat(request: UnitRequest) -> CallToolResult:
        """Legacy entry with the same instance capability, selection and tile-anchor guards as battle_step. Resume first."""
        return evidence_result(
            await service.command("retreat", request, {"unit_id": request.unit_id}),
            "return_image_path",
            "capture_image_path",
        )

    @expose(annotations=MUTATING)
    async def battle_use_skill(request: UnitRequest) -> CallToolResult:
        """Attempt the equipped skill once at a tracked position; never waits indefinitely for readiness."""
        return evidence_result(
            await service.command("skill", request, {"unit_id": request.unit_id}),
            "return_image_path",
            "capture_image_path",
        )

    @expose(annotations=MUTATING)
    async def battle_set_paused(request: PauseRequest) -> CallToolResult:
        """Request pause/resume with state guards. Resume only a server-requested or explicitly adopted pause."""
        return result(
            await service.command("set_paused", request, {"paused": request.paused})
        )

    @expose(annotations=READ_ONLY)
    async def battle_get_operation(
        session_id: Identifier,
        operation_id: Identifier,
        detail: Literal["summary", "full"] = "summary",
    ) -> CallToolResult:
        """Read a durable result, even after disconnection. detail=full returns unchanged recorded boundary/return snapshots and raw evidence without new observation or input; summary uses the profile's usual shape. Full profile stays unabridged."""
        return result(
            service.operation(session_id, operation_id), compact=detail != "full"
        )

    @expose(annotations=READ_ONLY)
    async def battle_get_evidence_frame(
        session_id: Identifier, operation_id: Identifier, observation_id: Identifier
    ) -> CallToolResult:
        """Return one retained original PNG referenced by a recorded operation, including preparation detail frames. Never captures or sends device input."""
        return evidence_result(
            service.evidence_frame(session_id, operation_id, observation_id),
            "image_path",
        )

    def media_result(value: dict) -> CallToolResult:
        response = result(value)
        video = (value.get("media") or value).get("video") or {}
        artifact_id = (value.get("media") or value).get("artifact_id")
        if artifact_id:
            try:
                video = service.media.store.info(artifact_id).get("video") or {}
            except RuntimeError:
                video = {}
        if video.get("resource_retrievable") and video.get("resource_uri"):
            response.content.append(
                ResourceLink(
                    type="resource_link",
                    uri=video["resource_uri"],
                    name="local-video-artifact",
                    mimeType=video.get("mime_type"),
                    size=video.get("bytes"),
                    description="Local video bytes, at most 16 MiB; the manifest distinguishes native acquisition times from imported media PTS",
                )
            )
        return response

    @expose(annotations=READ_ONLY)
    async def battle_record_clip(request: RecordClipRequest) -> CallToolResult:
        """Bounded native read-only sampled clip: never pauses/resumes. Running game keeps advancing. AVI/MJPEG when verified, plus PNG keyframes."""
        value = await service.command(
            "record",
            request,
            {
                "duration_ms": request.duration_ms,
                "interval_ms": request.interval_ms,
                "max_frames": request.max_frames,
            },
        )
        response = evidence_result(value, "image_path")
        response.content.extend(
            item
            for item in media_result(value).content
            if item.type == "resource_link" and item.name == "local-video-artifact"
        )
        return response

    @expose(annotations=READ_ONLY)
    async def battle_get_media(artifact_id: Identifier) -> CallToolResult:
        """Read retained media manifest/current expiry and bounded video resource availability. No new capture, decoder or provider request."""
        return media_result(service.media.store.info(artifact_id))

    @expose(annotations=READ_ONLY)
    async def battle_get_media_frames(
        artifact_id: Identifier,
        frame_ids: Annotated[list[Identifier], Field(min_length=1, max_length=4)],
    ) -> CallToolResult:
        """Read up to four original retained PNG keyframes (16 MiB total). Historical frames never update live state or quarantine."""
        value, images = service.media.store.read_frames(artifact_id, frame_ids)
        response = tool_result(value)
        response.content.extend(
            ImageContent(
                type="image",
                mimeType="image/png",
                data=base64.b64encode(image).decode("ascii"),
            )
            for image in images
        )
        return response

    def register_video_resource(extension: str, mime_type: str) -> None:
        @server.resource(
            f"battle-media://{{artifact_id}}/video{extension}",
            mime_type=mime_type,
            name=f"battle-video-{extension[1:]}",
        )
        async def media_video(artifact_id: str) -> bytes:
            return service.media.store.read_video(artifact_id, mime_type)

    for extension, mime_type in MIME.items():
        register_video_resource(extension, mime_type)

    @expose(annotations=READ_ONLY)
    async def battle_import_video(request: ImportVideoRequest) -> CallToolResult:
        """Import an allowlisted basename from media-import-dir into owned immutable media. Native bounded decoding runs in a separate no-device worker; no controller lock."""
        return media_result(await service.media.import_video(request))

    @expose(annotations=READ_ONLY)
    async def battle_analyze_video(request: AnalyzeVideoRequest) -> CallToolResult:
        """Experimental VIDEO-TO-COPILOT candidates from an imported full-context video, never execution or live combat/defeat truth. Separate bounded no-device Core worker."""
        return result(await service.media.analyze_video(request))

    @expose(annotations=MUTATING)
    async def battle_close() -> CallToolResult:
        """Close the session; waits for an active bounded step to finish its pause cleanup."""
        return result(await service.close())

    @expose(
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
            return result(
                await intelligence.query_complete_stage(
                    identifier, service.mode, service.map_info["height"]
                )
            )
        if session_id is not None:
            raise BattleError("session_id is only valid for a complete stage query")
        return result(
            await intelligence.query(kind, identifier, routes_page, spawns_page)
        )

    @server.resource(
        "battle-evidence://{session_id}/{operation_id}/{observation_id}",
        mime_type="image/png",
        name="battle-evidence",
    )
    async def battle_evidence(
        session_id: str, operation_id: str, observation_id: str
    ) -> bytes:
        frame = service.evidence_frame(
            unquote(session_id), unquote(operation_id), unquote(observation_id)
        )
        return service.evidence_path(frame).read_bytes()

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
    parser.add_argument("--media-import-dir", type=Path)
    parser.add_argument(
        "--tool-profile",
        choices=["minimal", "full"],
        default="minimal",
        help="minimal: ten primary tools; full: legacy, media and geometry extensions",
    )
    parser.add_argument(
        "--allow-summon",
        action="append",
        default=None,
        metavar="GAME_ID",
        help="Deprecated: restrict reviewed summon adapters to these IDs; never authorizes unsupported interactions",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.allow_summon is not None:
        logging.getLogger(__name__).warning(
            "--allow-summon is deprecated and retained as an explicit restrictive filter; "
            "it does not authorize unknown or special interactions"
        )
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
        media_import_dir=args.media_import_dir.resolve()
        if args.media_import_dir
        else None,
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
    create_server(
        BattleService(settings, decision=decision),
        intelligence,
        tool_profile=args.tool_profile,
    ).run(transport="stdio")


if __name__ == "__main__":
    main()
