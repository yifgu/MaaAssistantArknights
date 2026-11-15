"""Evidence-bearing battle decision view, not a reconstructed game simulation."""

from .configuration import effective_configuration

RUNTIME_REASONS = {
    "hp_fraction": "no_identity_bound_hp_measurement",
    "danger": "hp_and_incoming_damage_unmeasured",
    "equipped_skill_id": "battle_frame_does_not_identify_equipped_skill",
    "skill_ready": "no_identity_bound_positive_readiness_evidence",
    "skill_active": "readiness_does_not_prove_activation",
    "skill_phase": "no_native_phase_measurement",
    "sp_current": "no_identity_bound_sp_measurement",
    "sp_required": "actual_skill_build_not_observed",
    "charges": "no_identity_bound_charge_measurement",
    "ammo": "no_identity_bound_ammo_measurement",
    "remaining_duration_ms": "host_elapsed_time_is_not_game_skill_duration",
    "block_count": "dynamic_blocking_unmeasured",
    "flight_state": "dynamic_flight_unmeasured",
    "summon_owner": "static_summon_candidates_do_not_identify_live_owner",
    "healing_enabled": "skill_activation_and_targets_unmeasured",
}


def knowledge_refs(context: dict) -> dict:
    return {
        "game_id": context.get("operator_game_id"),
        "equipped_skill_id": context["effective_configuration"]["equipped_skill_id"][
            "value"
        ],
        "bundled_tool": "battle_get_unit_info",
        "optional_external_tool": "intel_query",
        "automatically_queried": False,
        "scope": "knowledge_is_external_to_runtime_observation",
    }


def unknown(reason: str, snapshot: dict) -> dict:
    return {
        "value": None,
        "source": "unknown",
        "verification": "unknown",
        "reason": reason,
        "observation_id": snapshot.get("observation_id"),
        "captured_at_ms": snapshot.get("captured_at_ms"),
        "freshness": "unknown_not_measured",
        "availability": "unsupported"
        if "no_native" in reason or "unsupported" in reason
        else "unbound"
        if "identity_bound" in reason or "anchor" in reason
        else "not_measured",
        "usable_for_current_decision": False,
    }


def hud_fact(key: str, snapshot: dict) -> dict:
    value = snapshot.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return unknown("not_measured_native_hud_" + key, snapshot)
    return {
        "value": value,
        "source": "same_frame_native_BattlefieldMatcher",
        "verification": "observed",
        "reason": None,
        "confidence": None,
        "confidence_reason": "native_matcher_does_not_export_numeric_ocr_confidence",
        "observation_id": snapshot.get("observation_id"),
        "captured_at_ms": snapshot.get("captured_at_ms"),
        "freshness": "same_observation",
    }


def runtime_fields(unit: dict, snapshot: dict) -> dict:
    native = unit.get("runtime") or {}
    observation_id = snapshot.get("observation_id")
    captured_at_ms = snapshot.get("captured_at_ms")
    frame_bound = (
        isinstance(observation_id, str)
        and bool(observation_id)
        and isinstance(captured_at_ms, int)
        and not isinstance(captured_at_ms, bool)
    )
    fields = {}
    for key, reason in RUNTIME_REASONS.items():
        value = native.get(key)
        if not isinstance(value, dict):
            value = unknown(reason, snapshot)
        else:
            value = dict(value)
            value["freshness"] = (
                "same_observation"
                if frame_bound
                and value.get("observation_id") == observation_id
                and value.get("captured_at_ms") == captured_at_ms
                else "older_or_unbound_evidence"
            )
            value["usable_for_current_decision"] = (
                value["freshness"] == "same_observation"
                and value.get("verification") == "observed"
                and value.get("value") is not None
            )
            value["availability"] = (
                "observed"
                if value["usable_for_current_decision"]
                else "stale"
                if value["freshness"] == "older_or_unbound_evidence"
                else "unbound"
                if "anchor" in (value.get("reason") or "")
                else "not_measured"
            )
        fields[key] = value
    return fields


def action_prerequisites(unit: dict, snapshot: dict) -> dict:
    scene = snapshot.get("scene")
    common = []
    if snapshot.get("session_input_blocked") is True:
        common.append(snapshot.get("input_block_reason") or "session_input_blocked")
    if scene not in ("running", "paused"):
        common.append("scene_not_actionable")
    if unit.get("presence") == "gone":
        common.append("owned_instance_departed")
    if unit.get("location_verified") is not True:
        common.append("owned_instance_tile_anchor_unverified")
    if not str(unit.get("unit_id", "")).startswith("instance:"):
        common.append("no_owned_instance")
    entity = unit.get("entity") or {}
    capabilities = entity.get("capabilities") or {}
    skill = list(common)
    kind = entity.get("entity_kind")
    if kind in ("TOKEN", "TRAP", "summon", "device", "token", "trap"):
        skill.append("summon_skill_prohibited")
    if kind not in ("operator", "token", "trap"):
        skill.append("native_unit_kind_or_skill_capability_unavailable")
    ready = unit["decision_context"]["runtime"]["skill_ready"]
    if ready.get("value") is not True or not ready.get("usable_for_current_decision"):
        skill.append("native_manual_skill_selection_and_ready_anchor_required")
    retreat = list(common)
    if (capabilities.get("manual_retreat") or {}).get("value") is not True:
        retreat.append("reviewed_retreat_capability_missing_or_prohibited")
    retreat.append("fresh_selected_identity_and_enabled_retreat_button_required")
    return {
        "skill": {
            "status": "blocked"
            if common
            or kind in ("TOKEN", "TRAP", "summon", "device", "token", "trap")
            or kind not in ("operator", "token", "trap")
            or ready.get("availability") == "stale"
            else "native_preflight_required",
            "missing_prerequisites": skill,
            "tool": "battle_step",
            "native_manual_skill_safety_loop_preserved": True,
        },
        "retreat": {
            "status": "blocked"
            if common
            or (capabilities.get("manual_retreat") or {}).get("value") is not True
            else "native_preflight_required",
            "missing_prerequisites": retreat,
            "tool": "battle_step",
        },
    }


def enrich_battle_decision(snapshot: dict, map_info: dict | None = None) -> None:
    """Expose all decision domains, differentiating unavailable from empty."""
    allies = []
    history = []
    for unit in snapshot.get("battlefield", []):
        context = unit["decision_context"]
        context["effective_configuration"] = effective_configuration(context, snapshot)
        context["knowledge_refs"] = knowledge_refs(context)
        context["runtime"] = runtime_fields(unit, snapshot)
        context["action_availability"] = action_prerequisites(unit, snapshot)
        allies.append(
            {
                "unit_id": unit.get("unit_id"),
                "game_ids": unit.get("game_ids"),
                "presence": unit.get("presence", "unknown"),
                "presence_source": unit.get(
                    "presence_source", "unowned_action_history"
                ),
                "location": unit.get("location"),
                "location_verified": unit.get("location_verified") is True,
                "spatial_evidence": unit.get("spatial_evidence"),
                "action_verification": unit.get("action_verification"),
                "observed_card_cooling": unit.get("observed_card_cooling")
                or unknown("no_unique_matching_card", snapshot),
                "owned_instance_departure_evidence": unit.get(
                    "owned_instance_departure_evidence"
                ),
                "departed_reason": unit.get("departed_reason"),
                "runtime": context["runtime"],
                "configuration": context["effective_configuration"],
                "knowledge_refs": context["knowledge_refs"],
                "preparation": context.get("preparation"),
                "action_availability": context["action_availability"],
            }
        )
        if unit.get("operation_id"):
            history.append(
                {
                    key: unit.get(key)
                    for key in (
                        "unit_id",
                        "operation_id",
                        "operation_state",
                        "helper_returned",
                        "location",
                        "direction",
                    )
                }
            )
    cards = []
    for unit in snapshot.get("deployment", []):
        context = unit["decision_context"]
        context["effective_configuration"] = effective_configuration(context, snapshot)
        context["knowledge_refs"] = knowledge_refs(context)
        context["runtime"] = runtime_fields(unit, snapshot)
        blockers = []
        if snapshot.get("session_input_blocked") is True:
            blockers.append(snapshot.get("input_block_reason"))
        if snapshot.get("scene") not in ("running", "paused"):
            blockers.append("scene_not_actionable")
        if unit.get("cooling") is True or unit.get("available") is False:
            blockers.append("card_cooling_or_unavailable")
        if len(unit.get("game_ids") or []) != 1:
            blockers.append("card_identity_not_unique")
        drag = (
            ((unit.get("entity") or {}).get("capabilities") or {}).get("standard_drag")
            or {}
        ).get("value")
        if drag is not True:
            blockers.append(
                "reviewed_standard_deployment_capability_missing_or_prohibited"
            )
        context["action_availability"] = {
            "deploy": {
                "status": "blocked" if blockers else "native_preflight_required",
                "missing_prerequisites": blockers,
                "required_checks": [
                    "same_bound_identity_layout_avatar_and_30s_freshness",
                    "unique_ordinary_card_or_explicit_duplicate_or_summon_card_id",
                    "reviewed_adapter_cost_terrain_and_tile_reservation",
                    "verified_resume_and_cleanup_budget",
                ],
                "tool": "battle_step",
            }
        }
        cards.append(
            {
                key: unit.get(key)
                for key in (
                    "card_id",
                    "unit_id",
                    "game_ids",
                    "index",
                    "rect",
                    "cost",
                    "available",
                    "cooling",
                    "identity_reason",
                )
            }
            | {
                "source": "same_frame_native_deployment",
                "observation_id": snapshot.get("observation_id"),
                "captured_at_ms": snapshot.get("captured_at_ms"),
                "action_availability": context["action_availability"],
                "effective_configuration": context["effective_configuration"],
                "knowledge_refs": context["knowledge_refs"],
            }
        )
    events = snapshot.get("state_events") or []
    alerts = [
        {
            "event_id": event.get("event_id"),
            "sequence": event.get("sequence"),
            "type": event.get("type"),
            "severity": "warning",
            "evidence": event,
        }
        for event in events
        if event.get("type")
        in (
            "life_decreased",
            "observed_card_cooling",
            "owned_instance_departed_reason_unknown",
            "suspected_defeat",
        )
    ]
    if snapshot.get("session_input_blocked") is True:
        alerts.append(
            {
                "type": "input_quarantined",
                "severity": "stop_input",
                "reason": snapshot.get("input_block_reason"),
                "observation_id": snapshot.get("observation_id"),
            }
        )
    snapshot["battle_decision"] = {
        "schema_version": 1,
        "source": "native_evidence_and_host_contract_not_game_simulation",
        "observation_id": snapshot.get("observation_id"),
        "captured_at_ms": snapshot.get("captured_at_ms"),
        "freshness_basis": "host_acquisition_time_not_device_render_or_game_time",
        "safety": {
            key: snapshot.get(key)
            for key in (
                "scene",
                "session_input_blocked",
                "input_block_reason",
                "pause_evidence",
                "terminal",
            )
        },
        "progress": {
            "cost": snapshot.get("cost"),
            "kills": snapshot.get("kills"),
            "total_kills": snapshot.get("total_kills"),
            "cost_evidence": hud_fact("cost", snapshot),
            "kills_evidence": hud_fact("kills", snapshot),
            "total_kills_evidence": hud_fact("total_kills", snapshot),
            "life": snapshot.get("life"),
            "life_evidence": snapshot.get("life_evidence")
            or unknown("native_life_evidence_unavailable", snapshot),
            "life_delta": snapshot.get("life_delta"),
            "life_delta_evidence": snapshot.get("life_delta_evidence")
            or unknown("no_comparable_native_life_observations", snapshot),
            "life_loss_cause": "unknown",
            "speed": snapshot.get("speed"),
            "game_elapsed_time": unknown("no_game_simulation_clock", snapshot),
        },
        "allies": {
            "coverage": "owned_action_instances_and_unassociated_visual_candidates",
            "units": allies,
            "action_history": history,
            "field_units": snapshot.get("field_units"),
            "field_unit_detection": snapshot.get("field_unit_detection"),
            "instance_association": snapshot.get("instance_association"),
            "complete_occupancy": False,
        },
        "deployment": {"cards": cards, "coverage": "same_frame_detected_cards"},
        "terrain": {
            "static_geometry": {
                key: value
                for key, value in map_info.items()
                if key != "dynamic_overlay"
            }
            if map_info is not None
            else None,
            "static_source": "caller_bound_map_not_current_visual_terrain",
            "dynamic_overlay": snapshot.get("dynamic_overlay"),
            "semantic_state": unknown(
                "pixel_changes_not_terrain_classification", snapshot
            ),
            "reservations": [
                {
                    "unit_id": unit.get("unit_id"),
                    "location": unit.get("location"),
                    "reserved": unit.get("tile_reserved"),
                    "location_verified": unit.get("location_verified"),
                }
                for unit in snapshot.get("battlefield", [])
            ],
        },
        "enemies": {
            "live_units": unknown(
                "no_native_enemy_detector_or_identity_tracking", snapshot
            ),
            "waves": unknown("no_live_wave_measurement", snapshot),
            "routes": unknown("no_live_enemy_route_tracking", snapshot),
            "static_candidates": {
                "tool": "intel_query",
                "scope": "optional_static_stage_schedules_not_live_spawn_or_positions",
                "automatically_loaded": False,
            },
        },
        "mechanics": {
            "static_units_tool": "battle_get_unit_info",
            "static_unit_data_is_account_or_runtime_state": False,
            "blocking_flight_targets_damage_and_skill_phases": unknown(
                "no_native_combat_simulation_or_full_mechanic_measurement", snapshot
            ),
        },
        "coverage": {
            "battle_hud": "partial_native_life_cost_kills_speed_scene",
            "owned_presence": "conservative_selected_kind_and_unique_cooling_evidence",
            "hp": "unassociated_health_bar_rectangles_not_hp_fraction",
            "skills": "positive_readiness_only_with_identity_bound_tile_anchor",
            "sp_charges_ammo_phase_duration": "unavailable",
            "account_configuration": "per_field_preparation_or_user_supplied_not_static_maxima",
            "enemy_positions_waves_routes": "unavailable_live_optional_static_intelligence",
            "terrain_semantics": "unavailable_pixel_change_evidence_only",
            "full_battle_state": False,
        },
        "events": {
            "source": "derived_native_observation_changes_not_engine_events",
            "cursor": snapshot.get("event_cursor"),
            "retained_from": snapshot.get("events_retained_from"),
            "items": events,
            "consumer_rule": "deduplicate_by_event_id_and_request_new_snapshot_on_cursor_gap",
        },
        "alerts": alerts,
        "inspection": {
            "read_only_tools": [
                "battle_observe",
                "battle_get_operation",
            ],
            "original_frame_resource": "battle-evidence://{session_id}/{operation_id}/{observation_id}",
            "image_and_map_options": "battle_observe(include_image=true, include_map=true)",
            "full_profile_read_only_tools": [
                "battle_capture_evidence",
                "battle_get_map",
                "battle_get_evidence_frame",
            ],
            "authorized_attached_input_tools": ["battle_identify"],
            "pre_attach_configuration_inspector": "battle_inspect_preparation",
            "missing_runtime_skill_detail_hp_or_enemy_inspector": True,
            "image_policy": "same_frame_PNG_kept_for_unknowns_anomalies_and_audit_not_replaced_by_guesses",
        },
    }
