from copy import deepcopy

from maa_battle_mcp.decision import DecisionContext


def test_whole_decision_contract_preserves_unknown_domains_and_native_evidence(
    settings,
):
    snapshot = {
        "observation_id": "8",
        "captured_at_ms": 200,
        "scene": "paused",
        "session_input_blocked": False,
        "life": 2,
        "life_delta": -1,
        "life_evidence": {
            "value": 2,
            "source": "same_frame_numeric_ocr",
            "verification": "observed",
            "observation_id": "8",
            "captured_at_ms": 200,
            "raw": {"text": "2", "score": 0.99, "rect": [700, 20, 20, 30]},
        },
        "deployment": [],
        "battlefield": [
            {
                "unit_id": "instance:deploy-1",
                "operation_id": "deploy-1",
                "game_ids": ["char_fixture"],
                "presence": "unknown",
                "presence_source": "input_is_not_placement_evidence",
                "location": [1, 2],
                "location_verified": False,
                "operation_state": "deployment_input_attempted",
                "runtime": {
                    "skill_ready": {
                        "value": None,
                        "classifier_ready": True,
                        "score": 0.99,
                        "source": "skill_ready_cls",
                        "verification": "unknown",
                        "reason": "requested_anchor_not_identity_bound",
                        "observation_id": "8",
                        "captured_at_ms": 200,
                    }
                },
            }
        ],
        "field_units": [{"score": 0.99, "instance_id": None}],
    }
    DecisionContext(settings).enrich_snapshot(snapshot)
    view = snapshot["battle_decision"]
    assert view["progress"]["life_evidence"] == snapshot["life_evidence"]
    assert view["progress"]["life_loss_cause"] == "unknown"
    assert view["coverage"]["full_battle_state"] is False
    for field in ("live_units", "waves", "routes"):
        assert view["enemies"][field]["value"] is None
        assert view["enemies"][field]["reason"]
    ally = view["allies"]["units"][0]
    assert ally["runtime"]["skill_ready"]["value"] is None
    assert ally["runtime"]["skill_ready"]["classifier_ready"] is True
    assert ally["runtime"]["skill_ready"]["freshness"] == "same_observation"
    assert ally["runtime"]["skill_active"]["value"] is None
    assert ally["runtime"]["healing_enabled"]["value"] is None
    assert ally["configuration"]["actual_level"]["value"] is None
    assert ally["action_availability"]["skill"]["status"] == "blocked"
    assert view["allies"]["field_units"][0]["instance_id"] is None
    assert len(view["allies"]["action_history"]) == 1


def test_runtime_changes_retain_stable_native_event_ids_without_host_duplicates(
    settings,
):
    event = {
        "event_id": "session:1",
        "sequence": 1,
        "type": "life_decreased",
        "source": "derived_native_observation_change_not_game_event",
        "observation_id": "2",
        "details": {"value": -1, "cause": "unknown"},
    }
    snapshot = {
        "observation_id": "3",
        "captured_at_ms": 300,
        "deployment": [],
        "battlefield": [],
        "event_cursor": 1,
        "events_retained_from": 1,
        "state_events": [event],
    }
    decision = DecisionContext(settings)
    decision.enrich_snapshot(snapshot)
    saved = deepcopy(snapshot)
    decision.enrich_snapshot(snapshot)
    assert snapshot == saved
    assert snapshot["battle_decision"]["events"]["items"] == [event]
    assert snapshot["battle_decision"]["alerts"][0]["event_id"] == "session:1"


def test_departure_does_not_remove_history_or_invent_defeat(settings):
    snapshot = {
        "observation_id": "4",
        "captured_at_ms": 400,
        "scene": "paused",
        "deployment": [],
        "battlefield": [
            {
                "unit_id": "instance:deploy",
                "game_ids": ["char_fixture"],
                "operation_id": "deploy",
                "presence": "gone",
                "presence_source": "owned_instance_departure_evidence",
                "departed_reason": "unknown",
                "operation_state": "retreat_input_attempted",
                "owned_instance_departure_evidence": {
                    "cause": "unknown",
                    "manual_retreat_input_attempted": True,
                },
            }
        ],
    }
    DecisionContext(settings).enrich_snapshot(snapshot)
    view = snapshot["battle_decision"]["allies"]
    assert view["units"][0]["departed_reason"] == "unknown"
    assert view["units"][0]["presence"] == "gone"
    assert view["action_history"][0]["operation_state"] == "retreat_input_attempted"


def test_older_runtime_evidence_and_unavailable_cards_never_authorize_action(settings):
    snapshot = {
        "observation_id": "10",
        "captured_at_ms": 500,
        "scene": "paused",
        "session_input_blocked": True,
        "input_block_reason": "pause_verification_pending",
        "deployment": [
            {
                "unit_id": "card-unit",
                "game_ids": ["char_fixture"],
                "available": False,
                "cooling": True,
                "runtime": {
                    "skill_ready": {
                        "value": True,
                        "observation_id": "9",
                        "captured_at_ms": 400,
                    }
                },
            }
        ],
        "battlefield": [],
    }
    DecisionContext(settings).enrich_snapshot(snapshot)
    context = snapshot["deployment"][0]["decision_context"]
    assert context["runtime"]["skill_ready"]["freshness"] == "older_or_unbound_evidence"
    assert context["action_availability"]["deploy"]["status"] == "blocked"
    assert (
        "pause_verification_pending"
        in context["action_availability"]["deploy"]["missing_prerequisites"]
    )


def test_missing_capture_identity_does_not_make_runtime_evidence_fresh(settings):
    for missing in ("observation_id", "captured_at_ms"):
        frame = {"observation_id": "11", "captured_at_ms": 600}
        frame.pop(missing)
        snapshot = {
            **frame,
            "scene": "paused",
            "deployment": [],
            "battlefield": [
                {
                    "unit_id": "instance:deploy",
                    "game_ids": ["char_fixture"],
                    "presence": "present",
                    "location_verified": True,
                    "entity": {"entity_kind": "operator"},
                    "runtime": {
                        "skill_ready": {
                            **frame,
                            "value": True,
                            "verification": "observed",
                        }
                    },
                }
            ],
        }
        DecisionContext(settings).enrich_snapshot(snapshot)
        context = snapshot["battlefield"][0]["decision_context"]
        assert context["runtime"]["skill_ready"]["usable_for_current_decision"] is False
        assert context["action_availability"]["skill"]["status"] == "blocked"
