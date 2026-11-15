import base64
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from jsonschema import Draft202012Validator
from mcp.server.fastmcp.exceptions import ToolError
from PIL import Image

from maa_battle_mcp.intelligence import IntelligenceClient
from maa_battle_mcp.models import IntelligenceConfig
from maa_battle_mcp.server import create_server
from maa_battle_mcp.service import BattleService


@pytest.fixture
async def service(settings):
    value = BattleService(settings)
    try:
        yield value
    finally:
        await value.close()
        value.journal.close()
        value.media.store.db.close()


@pytest.mark.parametrize("profile,base_count", [("minimal", 10), ("full", 27)])
@pytest.mark.parametrize("configured", [False, True])
async def test_real_tool_lists_profiles_and_optional_intelligence(
    service, profile, base_count, configured
):
    intelligence = (
        IntelligenceClient(
            IntelligenceConfig(provider="intelligence", command="unused")
        )
        if configured
        else None
    )
    server = create_server(service, intelligence, tool_profile=profile)
    tools = {tool.name: tool for tool in await server.list_tools()}
    assert len(tools) == base_count + int(configured)
    assert ("intel_query" in tools) is configured
    assert tools["battle_observe"].annotations.readOnlyHint is True
    assert tools["battle_inspect_preparation"].annotations.readOnlyHint is False
    assert tools["battle_step"].annotations.readOnlyHint is False
    if profile == "minimal":
        assert set(tools) - {"intel_query"} == {
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
    else:
        assert tools["battle_get_evidence_frame"].annotations.readOnlyHint is True
        assert tools["battle_set_paused"].annotations.readOnlyHint is False


async def step_contract(service):
    server = create_server(service)
    tool = next(
        tool for tool in await server.list_tools() if tool.name == "battle_step"
    )
    Draft202012Validator.check_schema(tool.inputSchema)
    return server, Draft202012Validator(tool.inputSchema)


@pytest.mark.parametrize(
    "action,args",
    [
        ("deploy", {"unit_id": "u", "location": [1, 2], "direction": "Right"}),
        ("retreat", {"unit_id": "instance:o"}),
        ("skill", {"unit_id": "instance:o"}),
        ("wait", {"duration_ms": 100}),
        ("pause", {}),
        ("speed", {"multiplier": 2}),
    ],
)
async def test_discriminated_request_schema_and_mcp_dispatch(service, action, args):
    server, schema = await step_contract(service)
    request = {
        "session_id": "s",
        "operation_id": "o",
        "action": action,
        **args,
    }
    schema.validate({"request": request})
    service.command = AsyncMock(return_value={"status": "succeeded"})
    await server.call_tool("battle_step", {"request": request})
    command, parsed, payload = service.command.call_args.args
    assert command == "step"
    assert parsed.session_id == "s"
    assert payload == {"action": action, "adopt_manual_pause": False, **args}
    for required in args:
        if required == "unit_id" or action in ("deploy", "wait", "speed"):
            incomplete = {
                key: value for key, value in request.items() if key != required
            }
            assert list(schema.iter_errors({"request": incomplete}))


@pytest.mark.parametrize(
    "args",
    [
        {"action": "deploy"},
        {"action": "deploy", "unit_id": "u", "location": [1, 2]},
        {"action": "deploy", "unit_id": "u", "direction": "Right"},
        {"action": "wait", "duration_ms": 100, "unit_id": None},
        {"action": "wait", "duration_ms": 99},
        {"action": "pause", "duration_ms": 100},
        {"action": "pause", "unit_id": "u"},
        {"action": "speed", "multiplier": True},
        {"action": "speed", "multiplier": 3},
        {"action": "speed", "multiplier": 1, "location": [1, 2]},
        {"action": "skill", "unit_id": "u", "recording": {}},
        {"action": "retreat", "unit_id": "u", "card_id": "c"},
        {"action": "skill", "unit_id": "u", "adopt_manual_pause": "true"},
    ],
)
async def test_exported_schema_rejects_missing_or_forbidden_action_fields(
    service, args
):
    server, schema = await step_contract(service)
    request = {"session_id": "s", "operation_id": "o", **args}
    assert list(schema.iter_errors({"request": request}))
    service.command = AsyncMock()
    with pytest.raises(ToolError, match="validation error"):
        await server.call_tool("battle_step", {"request": request})
    service.command.assert_not_called()


@pytest.mark.parametrize(
    "args",
    [
        {"timeout_ms": 1_000, "duration_ms": 100},
        {
            "duration_ms": 2_000,
            "recording": {"interval_ms": 200, "max_frames": 2},
        },
    ],
)
async def test_wait_cross_field_limits_remain_enforced_before_input(service, args):
    server, _ = await step_contract(service)
    service.command = AsyncMock()
    with pytest.raises(ToolError, match="validation error"):
        await server.call_tool(
            "battle_step",
            {
                "request": {
                    "session_id": "s",
                    "operation_id": "o",
                    "action": "wait",
                    **args,
                }
            },
        )
    service.command.assert_not_called()


def stored_frame(service):
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
        "/x8AAwMCAO+aL1sAAAAASUVORK5CYII="
    )
    path = service.settings.user_dir / "battle-control/s/frame.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(image)
    geometry = {
        "stage_id": "fixture",
        "width": 2,
        "height": 2,
        "tiles": [{"location": [1, 1]}],
    }
    snapshot = {
        "observation_id": "8",
        "captured_at_ms": 200,
        "scene": "paused",
        "deployment": [],
        "battlefield": [
            {
                "unit_id": "instance:deploy",
                "game_ids": [],
                "spatial_evidence": {"verification": "candidate_only"},
                "action_verification": {"verified": False},
            }
        ],
        "instance_association": {
            "status": "candidate_only",
            "selected_tile_anchor": "unavailable",
        },
        "field_units": [
            {
                "instance_id": None,
                "candidate_locations": [[1, 1], [1, 2]],
                "projection_status": "ambiguous",
                "direction_evidence": {"verification": "candidate_only"},
                "authorizes_input": False,
            }
        ],
        "unrecognized_diagnostic": {"value": None, "reason": "unknown"},
    }
    service.decision.enrich_snapshot(snapshot, geometry)
    value = {
        "status": "succeeded",
        "session_id": "s",
        "command_id": "o",
        "image_path": str(path.relative_to(service.settings.user_dir)),
        "snapshot": snapshot,
    }
    service.journal.execute(
        "INSERT INTO operations(session,id,request,result) VALUES(?,?,?,?)",
        ("s", "o", "{}", json.dumps(value)),
    )
    service.journal.commit()
    service.map_info = geometry
    return value, image


async def test_default_observe_is_compact_without_images_or_implicit_input(service):
    original, image = stored_frame(service)
    service.read = AsyncMock(return_value=deepcopy(original))
    service.command = AsyncMock(side_effect=AssertionError("No device input"))
    server = create_server(service)
    template = next(
        template
        for template in await server.list_resource_templates()
        if template.name == "battle-evidence"
    )
    assert template.mimeType == "image/png"
    response = await server.call_tool("battle_observe", {"session_id": "s"})
    service.read.assert_awaited_once_with("s", "observe")
    service.command.assert_not_called()
    assert not any(item.type == "image" for item in response.content)
    data = response.structuredContent
    assert data["battle_decision"]["observation_id"] == "8"
    assert "battle_decision" not in data["snapshot"]
    assert "deployment" not in data["snapshot"]
    assert "battlefield" not in data["snapshot"]
    assert data["snapshot"]["unrecognized_diagnostic"]["value"] is None
    assert "map" not in data
    assert "tiles" not in data["battle_decision"]["terrain"]["static_geometry"]
    assert data["battle_decision"]["enemies"]["live_units"]["value"] is None
    allies = data["battle_decision"]["allies"]
    assert allies["instance_association"]["selected_tile_anchor"] == "unavailable"
    assert allies["units"][0]["spatial_evidence"]["verification"] == "candidate_only"
    assert allies["units"][0]["action_verification"]["verified"] is False
    assert allies["field_units"][0]["candidate_locations"] == [[1, 1], [1, 2]]
    assert allies["field_units"][0]["authorizes_input"] is False
    uri = data["evidence"][0]["resource_uri"]
    assert uri == "battle-evidence://s/o/8"
    await service.close()
    resources = list(await server.read_resource(uri))
    assert resources[0].mime_type == "image/png"
    assert resources[0].content == image
    service.read.assert_awaited_once()
    assert service.operation("s", "o") == original
    with pytest.raises(ValueError, match="Unknown"):
        await server.read_resource("battle-evidence://s/other-operation/8")


async def test_step_returns_one_canonical_final_decision_without_changing_audit(
    service,
):
    original, _ = stored_frame(service)
    boundary = deepcopy(original["snapshot"])
    boundary["observation_id"] = "9"
    boundary["captured_at_ms"] = 100
    service.decision.enrich_snapshot(boundary, service.map_info)
    value = {
        **original,
        "capture_time_snapshot": boundary,
        "capture_image_path": original["image_path"],
        "return_time_snapshot": original["snapshot"],
        "return_image_path": original["image_path"],
    }
    saved = deepcopy(value)
    service.command = AsyncMock(return_value=value)
    response = await create_server(service).call_tool(
        "battle_step",
        {"request": {"session_id": "s", "operation_id": "o", "action": "pause"}},
    )
    data = response.structuredContent
    assert data["battle_decision"]["observation_id"] == "8"
    for key in ("snapshot", "capture_time_snapshot", "return_time_snapshot"):
        assert "battle_decision" not in data[key]
        assert "deployment" not in data[key]
        assert "battlefield" not in data[key]
    assert not any(item.type == "image" for item in response.content)
    assert value == saved


@pytest.mark.parametrize("profile", ["minimal", "full"])
async def test_operation_detail_reads_original_boundary_and_return_without_input(
    service, profile
):
    original, _ = stored_frame(service)
    boundary = deepcopy(original["snapshot"])
    boundary.update(observation_id="9", captured_at_ms=100, cost=1)
    final = deepcopy(original["snapshot"])
    final["cost"] = 2
    for snapshot in (boundary, final):
        service.decision.enrich_snapshot(snapshot, service.map_info)
    value = {
        **original,
        "capture_time_snapshot": boundary,
        "capture_image_path": original["image_path"],
        "return_time_snapshot": final,
        "return_image_path": original["image_path"],
    }
    service.journal.execute(
        "UPDATE operations SET result=? WHERE session=? AND id=?",
        (json.dumps(value), "s", "o"),
    )
    service.journal.commit()
    before = service.journal.execute(
        "SELECT request,result FROM operations WHERE session=? AND id=?", ("s", "o")
    ).fetchone()
    service.command = AsyncMock(side_effect=AssertionError("No new command"))
    service.read = AsyncMock(side_effect=AssertionError("No new observation"))
    server = create_server(service, tool_profile=profile)
    operation = next(
        tool
        for tool in await server.list_tools()
        if tool.name == "battle_get_operation"
    )
    schema = Draft202012Validator(operation.inputSchema)
    assert operation.inputSchema["properties"]["detail"]["default"] == "summary"
    assert list(
        schema.iter_errors(
            {"session_id": "s", "operation_id": "o", "detail": "unknown"}
        )
    )
    summary = await server.call_tool(
        "battle_get_operation", {"session_id": "s", "operation_id": "o"}
    )
    if profile == "minimal":
        assert "deployment" not in summary.structuredContent["capture_time_snapshot"]
    else:
        assert summary.structuredContent["capture_time_snapshot"] == boundary
    complete = await server.call_tool(
        "battle_get_operation",
        {"session_id": "s", "operation_id": "o", "detail": "full"},
    )
    assert {
        key: item
        for key, item in complete.structuredContent.items()
        if key != "evidence"
    } == value
    evidence = complete.structuredContent["evidence"]
    assert {frame["observation_id"] for frame in evidence} == {"8", "9"}
    assert not any(item.type == "image" for item in complete.content)
    assert (
        service.journal.execute(
            "SELECT request,result FROM operations WHERE session=? AND id=?", ("s", "o")
        ).fetchone()
        == before
    )
    service.command.assert_not_called()
    service.read.assert_not_called()


@pytest.mark.parametrize("action,args", [("pause", {}), ("speed", {"multiplier": 2})])
@pytest.mark.parametrize("aliased_paths", [False, True])
async def test_full_pause_speed_steps_inline_native_image_once(
    service, action, args, aliased_paths
):
    value, image = stored_frame(service)
    if aliased_paths:
        value["return_image_path"] = value["image_path"]
        value["capture_image_path"] = value["image_path"]
    service.command = AsyncMock(return_value=value)
    response = await create_server(service, tool_profile="full").call_tool(
        "battle_step",
        {
            "request": {
                "session_id": "s",
                "operation_id": "o",
                "action": action,
                **args,
            }
        },
    )
    images = [item for item in response.content if item.type == "image"]
    assert len(images) == 1
    assert base64.b64decode(images[0].data) == image


@pytest.mark.parametrize("profile", ["minimal", "full"])
async def test_preparation_fallback_fields_link_recorded_resources_without_journal_edits(
    service, profile
):
    original, _ = stored_frame(service)
    detail_path = service.settings.user_dir / "battle-control/s/detail.png"
    Image.new("RGB", (2, 2), "blue").save(detail_path)
    proof = {
        "observation_id": "9",
        "captured_at_ms": 150,
        "image_path": str(detail_path.relative_to(service.settings.user_dir)),
    }

    class PreparationBackend:
        def __init__(self):
            self.calls = []

        def request(self, operation, payload, timeout):
            self.calls.append((operation, payload))
            assert operation == "preparation"
            assert payload["action"] == "inspect_preparation"
            return {
                "status": "succeeded",
                "session_id": payload["session_id"],
                "image_path": original["image_path"],
                "evidence_frames": [proof],
                "snapshot": {
                    "page": "formation",
                    "observation_id": "8",
                    "captured_at_ms": 200,
                    "operators": [
                        {
                            "slot": 0,
                            "fields": {
                                "identity": {"value": "char_fixture"},
                                "actual_level": {
                                    "value": None,
                                    "reason": "low_confidence",
                                    "evidence": proof,
                                },
                                "actual_elite": {"value": 0},
                                "selected_skill": {
                                    "value": None,
                                    "evidence": {"observation_id": None},
                                },
                                "equipped_skill_id": {"value": "sk_fixture"},
                            },
                        }
                    ],
                },
            }

        def close(self):
            pass

    backend = PreparationBackend()
    service.backend = backend
    service.preparation_session_id = "s"
    server = create_server(service, tool_profile=profile)
    tools = {tool.name for tool in await server.list_tools()}
    assert ("battle_get_evidence_frame" in tools) is (profile == "full")
    inspected = await server.call_tool(
        "battle_inspect_preparation",
        {
            "request": {
                "operation_id": "inspect",
                "stage_name": "stage",
                "mode": "normal",
            }
        },
    )
    assert not inspected.isError
    before = service.journal.execute(
        "SELECT request,result FROM operations WHERE session=? AND id=?",
        ("s", "inspect"),
    ).fetchone()
    raw = json.loads(before[1])
    assert (
        raw["host_fallback"]["items"][0]["retrieval"]["tool"]
        == "battle_get_evidence_frame"
    )
    assert "resource_uri" not in raw["host_fallback"]["items"][0]["retrieval"]
    await service.close()
    results = [inspected]
    for detail in ("summary", "full"):
        results.append(
            await server.call_tool(
                "battle_get_operation",
                {"session_id": "s", "operation_id": "inspect", "detail": detail},
            )
        )
    for result in results:
        fields = {
            item["field"]: item["retrieval"]
            for item in result.structuredContent["host_fallback"]["items"]
        }
        level = fields["actual_level"]
        assert level["available"] is True
        assert level["resource_uri"] == "battle-evidence://s/inspect/9"
        assert level["mime_type"] == "image/png"
        assert fields["selected_skill"]["available"] is False
        assert fields["selected_skill"]["resource_uri"] is None
        if profile == "minimal":
            assert all("tool" not in retrieval for retrieval in fields.values())
        else:
            assert level["tool"] == "battle_get_evidence_frame"
        resource = list(await server.read_resource(level["resource_uri"]))
        assert resource[0].content == detail_path.read_bytes()
    assert len(backend.calls) == 1
    assert (
        service.journal.execute(
            "SELECT request,result FROM operations WHERE session=? AND id=?",
            ("s", "inspect"),
        ).fetchone()
        == before
    )


@pytest.mark.parametrize("profile", ["minimal", "full"])
async def test_opt_in_image_is_exact_same_frame_and_map_is_structured(service, profile):
    original, image = stored_frame(service)
    service.read = AsyncMock(return_value=deepcopy(original))
    server = create_server(service, tool_profile=profile)
    response = await server.call_tool(
        "battle_observe",
        {"session_id": "s", "include_image": True, "include_map": True},
    )
    images = [item for item in response.content if item.type == "image"]
    assert len(images) == 1
    assert base64.b64decode(images[0].data) == image
    assert response.structuredContent["map"]["stage_id"] == "fixture"
    uri = response.structuredContent["evidence"][0]["resource_uri"]
    assert list(await server.read_resource(uri))[0].content == image
    if profile == "full":
        assert response.structuredContent["snapshot"] == original["snapshot"]


async def test_observe_collection_uses_only_read_only_finalize(service):
    service.command = AsyncMock(return_value={"status": "succeeded"})
    service.read = AsyncMock()
    server = create_server(service)
    observe = next(
        tool for tool in await server.list_tools() if tool.name == "battle_observe"
    )
    schema = Draft202012Validator(observe.inputSchema)
    assert list(schema.iter_errors({"session_id": "s", "duration_ms": 99}))
    await server.call_tool(
        "battle_observe",
        {
            "session_id": "s",
            "operation_id": "collect",
            "duration_ms": 100,
            "timeout_ms": 1_000,
        },
    )
    command, request, args = service.command.call_args.args
    assert command == "finalize"
    assert request.operation_id == "collect"
    assert args == {"duration_ms": 100}
    service.read.assert_not_called()
    service.command.reset_mock()
    with pytest.raises(ToolError, match="deadline"):
        await server.call_tool(
            "battle_observe",
            {"session_id": "s", "duration_ms": 2_000, "timeout_ms": 1_000},
        )
    service.command.assert_not_called()


async def test_full_geometry_uses_native_service_entry(service):
    service.evaluate_placement = AsyncMock(return_value={"status": "succeeded"})
    server = create_server(service, tool_profile="full")
    await server.call_tool(
        "battle_evaluate_placement",
        {
            "session_id": "s",
            "placements": [
                {
                    "operator_game_id": "char_fixture",
                    "location": [1, 2],
                    "direction": "Right",
                }
            ],
        },
    )
    service.evaluate_placement.assert_awaited_once()
