#include "BattleControlTask.h"

#include "Config/Miscellaneous/BattleDataConfig.h"
#include "Config/TaskData.h"
#include "Utils/BattlefieldObservation.h"
#include "Vision/Battle/BattleControlState.h"
#include "Vision/Battle/BattlefieldClassifier.h"
#include "Vision/Battle/BattlefieldDetector.h"
#include "Vision/Battle/BattlefieldMatcher.h"
#include "Vision/Matcher.h"
#include "Vision/RegionOCRer.h"

json::value asst::BattleControlTask::life_info(const CapturedFrame& frame, const json::value& snapshot) const
{
    json::value evidence = json::object {
        { "value", nullptr },
        { "source", "unknown" },
        { "verification", "unknown" },
        { "reason", "no_positive_life_hud_anchor" },
        { "confidence", nullptr },
        { "observation_id", snapshot.at("observation_id") },
        { "captured_at_ms", snapshot.at("captured_at_ms") },
    };
    if (frame.image.empty() || (snapshot.get("scene", "") != "running" && snapshot.get("scene", "") != "paused")) {
        evidence["reason"] = "scene_not_actionable";
        return evidence;
    }
    std::optional<MatchRect> best;
    std::string task_name;
    for (const auto& name : { "BattleHpFlag", "BattleHpFlag2" }) {
        Matcher anchor(frame.image);
        anchor.set_task_info(name);
        if (auto match = anchor.analyze(); match && match->score >= 0.9 && (!best || match->score > best->score)) {
            best = *match;
            task_name = name;
        }
    }
    if (!best) {
        return evidence;
    }
    const auto roi = best->rect.move(Task.get(task_name)->rect_move);
    evidence["anchor"] = best->to_json();
    evidence["anchor_task"] = task_name;
    if (!Rect(0, 0, frame.image.cols, frame.image.rows).include(roi)) {
        evidence["reason"] = "life_roi_out_of_frame";
        return evidence;
    }
    RegionOCRer number(frame.image);
    number.set_task_info("BattleControlLife");
    number.set_roi(roi);
    auto numeric = battle::numeric_hud_evidence(number.analyze(), roi, 0, 99);
    numeric["anchor"] = evidence.at("anchor");
    numeric["anchor_task"] = task_name;
    if (!numeric.at("value").is_null()) {
        numeric["confidence"] = std::min(best->score, numeric.at("confidence").as_double());
    }
    numeric["observation_id"] = snapshot.at("observation_id");
    numeric["captured_at_ms"] = snapshot.at("captured_at_ms");
    return numeric;
}

void asst::BattleControlTask::record_state_event(
    const std::string& type,
    const json::value& snapshot,
    json::value details)
{
    m_state_events.emplace_back(
        json::object {
            { "event_id", m_session_id + ":" + std::to_string(++m_state_event_sequence) },
            { "sequence", m_state_event_sequence },
            { "type", type },
            { "source", "derived_native_observation_change_not_game_event" },
            { "observation_id", snapshot.at("observation_id") },
            { "captured_at_ms", snapshot.at("captured_at_ms") },
            { "details", std::move(details) },
        });
    if (m_state_events.size() > 128) {
        m_state_events.pop_front();
    }
}

void asst::BattleControlTask::observe_runtime(const CapturedFrame& frame, json::value& snapshot)
{
    auto life = life_info(frame, snapshot);
    life["capture_order_ns"] =
        std::chrono::duration_cast<std::chrono::nanoseconds>(frame.started.time_since_epoch()).count();
    const bool hard_scene = m_terminal_seen || !snapshot.get("in_battle", false);
    if (hard_scene) {
        m_previous_life = json::object();
        m_life_gap_observations = 0;
    }
    auto delta = battle::life_difference(m_previous_life, life);
    delta["gap_observations"] = m_life_gap_observations;
    delta["gap_scope"] = "runtime_evidence_observations_not_every_internal_safety_capture";
    delta["comparison_scope"] = "observed_endpoints_not_instant_leak_or_game_mechanic";
    snapshot["life_evidence"] = life;
    snapshot["life"] = life.at("value");
    snapshot["life_delta_evidence"] = delta;
    snapshot["life_delta"] = delta.at("value");
    if (delta.find<int>("value") && delta.at("value").as_integer() != 0) {
        record_state_event(delta.at("value").as_integer() < 0 ? "life_decreased" : "life_changed", snapshot, delta);
    }
    if (life.get("verification", "") == "observed") {
        m_previous_life = life;
        m_life_gap_observations = 0;
    }
    else if (!hard_scene && !m_previous_life.empty()) {
        ++m_life_gap_observations;
    }
    const bool actionable =
        !frame.image.empty() && (snapshot.get("scene", "") == "running" || snapshot.get("scene", "") == "paused");
    auto unknown = [&](const std::string& reason) {
        return json::object {
            { "value", nullptr },
            { "source", "unknown" },
            { "verification", "unknown" },
            { "reason", reason },
            { "observation_id", snapshot.at("observation_id") },
            { "captured_at_ms", snapshot.at("captured_at_ms") },
        };
    };
    snapshot["field_units"] =
        actionable && m_allow_optional_visuals ? json::value(json::array()) : json::value(nullptr);
    snapshot["field_unit_detection"] = unknown(
        actionable ? "not_measured_optional_observer_skipped_budget_or_safety_capture" : "scene_not_actionable");
    if (actionable && m_allow_optional_visuals) {
        BattlefieldDetector detector(frame.image);
        detector.set_object_of_interest({ .operators = true });
        if (const auto result = detector.analyze()) {
            std::vector<battle::FieldUnitDetection> detections;
            for (const auto& detected : result->operators) {
                detections.emplace_back(battle::FieldUnitDetection { detected.rect, detected.score });
            }
            std::vector<std::pair<Point, Point>> projected_tiles;
            Matcher detail(frame.image);
            detail.set_task_info("BattleOperRetreat");
            detail.set_roi(Rect(m_retreat_button_pos.x - 50, m_retreat_button_pos.y - 50, 100, 100));
            const auto selected_button = detail.analyze();
            const bool selected_detail = selected_button && selected_button->score >= 0.95 &&
                                         selected_button->rect.include(m_retreat_button_pos);
            if (!selected_detail && frame.image.cols == 1280 && frame.image.rows == 720 && !m_has_multi_stages) {
                for (const auto& [location, tile] : m_normal_tile_info) {
                    projected_tiles.emplace_back(location, tile.pos);
                }
            }
            const auto candidates = battle::project_field_unit_candidates(
                detections,
                projected_tiles,
                Task.get("BattleOperBoxRectMove")->rect_move,
                Rect(0, 0, frame.image.cols, frame.image.rows));
            size_t index = 0;
            for (const auto& candidate : candidates) {
                json::array locations;
                json::array requested_instances;
                for (const auto& location : candidate.locations) {
                    locations.emplace_back(json::array { location.x, location.y });
                    for (const auto& [id, instance] : m_instances) {
                        if (instance.location == location && instance.presence != Presence::Gone) {
                            requested_instances.emplace_back(id);
                        }
                    }
                }
                json::value direction = unknown(
                    !candidate.unique() ? "no_unique_uncontested_tile_projection" : "classifier_budget_exhausted");
                if (candidate.unique() && !m_cancel_requested && !need_exit() &&
                    std::chrono::steady_clock::now() < m_optional_runtime_deadline) {
                    const auto& pos = m_normal_tile_info.at(candidate.locations.front()).pos;
                    const auto roi =
                        Rect(pos.x, pos.y, 0, 0).move(Task.get("BattleDeployDirectionRectMove")->rect_move);
                    if (Rect(0, 0, frame.image.cols, frame.image.rows).include(roi)) {
                        BattlefieldClassifier classifier(frame.image);
                        classifier.set_object_of_interest({ .deploy_direction = true });
                        classifier.set_base_point(pos);
                        if (const auto classified = classifier.analyze()) {
                            const auto& facing = classified->deploy_direction;
                            const bool confident =
                                std::isfinite(facing.score) && facing.score >= 0.9 && facing.score <= 1;
                            const auto name = facing.direction == battle::DeployDirection::Right  ? "Right"
                                              : facing.direction == battle::DeployDirection::Down ? "Down"
                                              : facing.direction == battle::DeployDirection::Left ? "Left"
                                              : facing.direction == battle::DeployDirection::Up   ? "Up"
                                                                                                  : "";
                            direction = json::object {
                                { "value", confident && *name ? json::value(name) : json::value(nullptr) },
                                { "source", "deploy_direction_cls_at_candidate_tile" },
                                { "verification", "candidate_not_identity_bound" },
                                { "score", facing.score },
                                { "probabilities",
                                  json::array { facing.prob[0], facing.prob[1], facing.prob[2], facing.prob[3] } },
                                { "roi", json::array { roi.x, roi.y, roi.width, roi.height } },
                                { "base_point", json::array { pos.x, pos.y } },
                                { "presence_proof", false },
                                { "reason", "four_direction_classes_have_no_empty_or_unknown_unit_class" },
                                { "observation_id", snapshot.at("observation_id") },
                                { "captured_at_ms", snapshot.at("captured_at_ms") },
                            };
                        }
                    }
                }
                snapshot["field_units"].as_array().emplace_back(
                    json::object {
                        { "observation_local_id",
                          snapshot.at("observation_id").as_string() + ":bar:" + std::to_string(index++) },
                        { "source", "operators_det_health_bar" },
                        { "score", candidate.score },
                        { "rect",
                          json::array { candidate.detection.x,
                                        candidate.detection.y,
                                        candidate.detection.width,
                                        candidate.detection.height } },
                        { "tile_projection_rect",
                          json::array { candidate.projection.x,
                                        candidate.projection.y,
                                        candidate.projection.width,
                                        candidate.projection.height } },
                        { "candidate_locations", std::move(locations) },
                        { "candidate_location",
                          candidate.unique()
                              ? json::value(
                                    json::array { candidate.locations.front().x, candidate.locations.front().y })
                              : json::value(nullptr) },
                        { "projection_status",
                          projected_tiles.empty() ? "projection_unavailable_selected_view_or_unsupported_frame"
                          : candidate.contested   ? "competing_detections"
                          : candidate.unique()    ? "unique_candidate_not_identity_proof"
                                                  : "ambiguous_or_unmapped" },
                        { "projection_binding", "caller_map_and_assumed_normal_camera_not_visually_verified" },
                        { "requested_instance_candidates", std::move(requested_instances) },
                        { "direction_evidence", std::move(direction) },
                        { "observation_id", snapshot.at("observation_id") },
                        { "captured_at_ms", snapshot.at("captured_at_ms") },
                        { "instance_id", nullptr },
                        { "game_id", nullptr },
                        { "location", nullptr },
                        { "hp_fraction", unknown("detector_localizes_bar_not_hp_fill") },
                        { "danger", unknown("hp_and_incoming_damage_unmeasured") },
                        { "association", "projection_candidates_not_selected_identity_or_owned_instance_proof" },
                        { "authorizes_input", false },
                    });
            }
            snapshot["field_unit_detection"] = json::object {
                { "source", "operators_det" },
                { "status", "observed_candidates_not_complete_occupancy" },
                { "required_score", 0.9 },
                { "observation_id", snapshot.at("observation_id") },
                { "captured_at_ms", snapshot.at("captured_at_ms") },
                { "absence_proves_departure", false },
                { "tile_mapping", "all_projected_candidates_no_first_match_or_nearest_neighbour" },
                { "selected_identity_tile_anchor_available", false },
            };
        }
    }
    if (actionable) {
        for (const auto& card : snapshot.at("deployment").as_array()) {
            const auto game_id = card.get("game_id", "");
            if (game_id.empty() || std::ranges::count_if(snapshot.at("deployment").as_array(), [&](const auto& other) {
                                       return other.get("game_id", "") == game_id;
                                   }) != 1) {
                continue;
            }
            const bool cooling = card.get("cooling", false);
            if (const auto previous = m_observed_cooling.find(game_id);
                previous == m_observed_cooling.end() ? cooling : previous->second != cooling) {
                record_state_event(
                    cooling ? "observed_card_cooling" : "observed_card_cooling_cleared",
                    snapshot,
                    json::object {
                        { "game_id", game_id },
                        { "card_id", card.at("card_id") },
                        { "cooling", cooling },
                        { "cause", "unknown" },
                        { "owned_instance_departure_proved", false },
                    });
            }
            m_observed_cooling.insert_or_assign(game_id, cooling);
        }
    }
    for (auto& [id, instance] : m_instances) {
        const auto previous_runtime = instance.runtime;
        json::array candidate_units;
        if (snapshot.at("field_units").is_array()) {
            for (const auto& unit : snapshot.at("field_units").as_array()) {
                const auto& candidates = unit.at("requested_instance_candidates").as_array();
                if (std::ranges::any_of(candidates, [&](const auto& candidate) {
                        return candidate.as_string() == id;
                    })) {
                    candidate_units.emplace_back(
                        json::object {
                            { "field_unit_id", unit.at("observation_local_id") },
                            { "candidate_locations", unit.at("candidate_locations") },
                            { "projection_status", unit.at("projection_status") },
                            { "requested_instance_candidates", unit.at("requested_instance_candidates") },
                            { "direction_evidence", unit.at("direction_evidence") },
                        });
                }
            }
        }
        instance.spatial_evidence = json::object {
            { "source", "same_frame_health_bar_projection" },
            { "verification", "unverified" },
            { "measurement", snapshot.at("field_unit_detection") },
            { "requested_location", json::array { instance.location.x, instance.location.y } },
            { "candidate_units", std::move(candidate_units) },
            { "observation_id", snapshot.at("observation_id") },
            { "captured_at_ms", snapshot.at("captured_at_ms") },
            { "missing_evidence", "selected_identity_bound_tile_and_owned_placement" },
            { "detection_absence_proves_departure", false },
            { "authorizes_input", false },
        };
        const bool inspect_ready = m_allow_optional_visuals && !m_cancel_requested && !need_exit() &&
                                   std::chrono::steady_clock::now() < m_optional_runtime_deadline;
        instance.runtime = json::object();
        for (const auto& [key, reason] : {
                 std::pair { "hp_fraction", "no_identity_bound_hp_measurement" },
                 { "danger", "hp_and_incoming_damage_unmeasured" },
                 { "equipped_skill_id", "battle_frame_does_not_identify_equipped_skill" },
                 { "skill_ready", "owned_instance_tile_anchor_unverified" },
                 { "skill_active", "readiness_does_not_prove_activation" },
                 { "skill_phase", "phase_not_observable_by_readiness_classifier" },
                 { "sp_current", "no_identity_bound_sp_measurement" },
                 { "sp_required", "actual_skill_build_not_observed" },
                 { "charges", "no_identity_bound_charge_measurement" },
                 { "ammo", "no_identity_bound_ammo_measurement" },
                 { "remaining_duration_ms", "host_elapsed_time_is_not_game_skill_duration" },
                 { "block_count", "dynamic_blocking_unmeasured" },
                 { "flight_state", "dynamic_flight_unmeasured" },
                 { "summon_owner", "static_summon_candidates_do_not_identify_live_owner" },
                 { "healing_enabled", "skill_activation_and_targets_unmeasured" },
             }) {
            instance.runtime[key] = unknown(
                !actionable ? "scene_not_actionable"
                : std::string_view(key) == "skill_ready" && !inspect_ready
                    ? "not_measured_optional_observer_skipped_budget_or_safety_capture"
                    : reason);
        }
        instance.observed_card_cooling = unknown(actionable ? "no_unique_matching_card" : "scene_not_actionable");
        std::vector<json::value> cards;
        if (actionable) {
            for (const auto& card : snapshot.at("deployment").as_array()) {
                if (card.get("game_id", "") == instance.game_id) {
                    cards.emplace_back(card);
                }
            }
        }
        if (cards.size() == 1) {
            const auto& card = cards.front();
            const bool cooling = card.get("cooling", false);
            instance.observed_card_cooling = json::object {
                { "value", cooling },
                { "source", "same_frame_deployment_card" },
                { "verification", "observed" },
                { "confidence", nullptr },
                { "confidence_reason", "native_hsv_threshold_has_no_calibrated_probability" },
                { "card_id", card.at("card_id") },
                { "rect", card.at("rect") },
                { "observation_id", snapshot.at("observation_id") },
                { "captured_at_ms", snapshot.at("captured_at_ms") },
            };
            if (cooling && !card.get("available", true) && frame.started > instance.last_cooling_capture &&
                frame.started > instance.last_cooling_break_capture) {
                ++instance.cooling_confirmations;
                instance.last_cooling_capture = frame.started;
                instance.cooling_evidence.emplace_back(instance.observed_card_cooling);
                if (instance.cooling_evidence.size() > 2) {
                    instance.cooling_evidence.erase(instance.cooling_evidence.begin());
                }
            }
            else if (!cooling || card.get("available", true) || frame.started <= instance.last_cooling_break_capture) {
                instance.cooling_confirmations = 0;
                instance.cooling_evidence.clear();
            }
            const auto props = BattleData.find_oper_by_id(instance.game_id);
            const bool ordinary_without_substitute = props && props->role != battle::Role::Drone &&
                                                     props->sub_role != battle::SubRole::Unknown &&
                                                     props->sub_role != battle::SubRole::Special_DollKeeper;
            const auto count = std::ranges::count_if(m_instances, [&](const auto& other) {
                return other.second.game_id == instance.game_id && other.second.presence != Presence::Gone;
            });
            const bool selected =
                instance.selection_evidence.size() == 2 &&
                std::ranges::all_of(instance.selection_evidence, [](const auto& e) { return e.accepted; });
            const bool departed = battle::owned_instance_departure(
                {
                    instance.tag.role != battle::Role::Drone,
                    true,
                    count == 1,
                    ordinary_without_substitute,
                    selected && instance.owned_presence_verified &&
                        instance.location_evidence == LocationEvidence::VerifiedTileAnchor,
                    cooling,
                    card.get("available", true),
                    instance.cooling_confirmations,
                });
            const bool hint = battle::suspected_defeat_hint(
                {
                    instance.tag.role != battle::Role::Drone,
                    true,
                    count == 1,
                    ordinary_without_substitute,
                    instance.owned_presence_verified,
                    cooling,
                    card.get("available", true),
                    instance.cooling_confirmations,
                },
                instance.operation_state == OperationState::RetreatInputAttempted,
                m_terminal_seen || snapshot.get("scene", "") == "terminal");
            if (hint && !instance.suspected_defeat_reported && instance.presence != Presence::Gone) {
                record_state_event(
                    "suspected_defeat",
                    snapshot,
                    json::object {
                        { "claim", "possible_uncommanded_departure_for_host_review" },
                        { "source", "derived_cooling_advisory_for_multimodal_host" },
                        { "instance_id", id },
                        { "game_id", instance.game_id },
                        { "card_evidence", instance.observed_card_cooling },
                        { "departure_verified", departed },
                        { "cause", "unknown" },
                        { "confidence", nullptr },
                        { "confidence_reason", "advisory_not_calibrated_defeat_probability" },
                        { "retreat_input_recorded", false },
                        { "record_coverage", "this_session_only" },
                        { "alternative_causes",
                          json::array {
                              "external_or_pre_session_input",
                              "mechanic_or_forced_retreat",
                              "identity_or_ownership_uncertainty",
                          } },
                        { "requires_host_review", true },
                        { "authorizes_input", false },
                        { "changes_presence", false },
                        { "releases_tile", false },
                    });
                instance.suspected_defeat_reported = true;
            }
            if (!cooling) {
                instance.suspected_defeat_reported = false;
            }
            if (departed && instance.presence != Presence::Gone) {
                instance.departure_evidence = json::object {
                    { "source", "two_fresh_unique_ordinary_cooling_cards_and_selected_kind_history" },
                    { "card", instance.observed_card_cooling },
                    { "cooling_endpoints", instance.cooling_evidence },
                    { "consecutive_captures", instance.cooling_confirmations },
                    { "instance_id", id },
                    { "cause", "unknown" },
                    { "placement_ever_succeeded", nullptr },
                    { "manual_retreat_input_attempted",
                      instance.operation_state == OperationState::RetreatInputAttempted },
                    { "location_verified", instance.location_evidence == LocationEvidence::VerifiedTileAnchor },
                };
                instance.presence = Presence::Gone;
                instance.presence_source = "owned_instance_departure_evidence";
                // Only an identity-bound tile anchor can release helper occupancy.
                if (instance.location_evidence == LocationEvidence::VerifiedTileAnchor) {
                    m_used_tiles.erase(instance.location);
                }
                record_state_event("owned_instance_departed_reason_unknown", snapshot, instance.departure_evidence);
            }
        }
        else {
            instance.cooling_confirmations = 0;
            instance.cooling_evidence.clear();
        }
        const auto tile = m_normal_tile_info.find(instance.location);
        if (actionable && inspect_ready && instance.presence != Presence::Gone && tile != m_normal_tile_info.end()) {
            const auto& pos = tile->second.pos;
            const auto roi = Rect(pos.x, pos.y, 0, 0).move(Task.get("BattleSkillReady")->rect_move);
            if (Rect(0, 0, frame.image.cols, frame.image.rows).include(roi)) {
                BattlefieldClassifier classifier(frame.image);
                classifier.set_object_of_interest({ .skill_ready = true });
                classifier.set_base_point(pos);
                if (const auto classified = classifier.analyze()) {
                    const auto& ready = classified->skill_ready;
                    const bool anchored = instance.presence == Presence::Present &&
                                          instance.location_evidence == LocationEvidence::VerifiedTileAnchor;
                    auto candidate = json::object {
                        { "value",
                          anchored && ready.ready && ready.score >= 0.9f ? json::value(true) : json::value(nullptr) },
                        { "source", "skill_ready_cls" },
                        { "verification", anchored && ready.ready && ready.score >= 0.9f ? "observed" : "unknown" },
                        { "reason",
                          !anchored            ? json::value("requested_anchor_not_identity_bound")
                          : !ready.ready       ? json::value("negative_class_does_not_measure_active_or_cooldown")
                          : ready.score < 0.9f ? json::value("low_confidence")
                                               : json::value(nullptr) },
                        { "classifier_ready", ready.ready },
                        { "score", ready.score },
                        { "probabilities", json::array { ready.prob[0], ready.prob[1], ready.prob[2] } },
                        { "roi", json::array { ready.rect.x, ready.rect.y, ready.rect.width, ready.rect.height } },
                        { "base_point", json::array { pos.x, pos.y } },
                        { "location_verified", anchored },
                        { "observation_id", snapshot.at("observation_id") },
                        { "captured_at_ms", snapshot.at("captured_at_ms") },
                    };
                    instance.runtime["skill_ready"] = candidate;
                    if (candidate.get("value", false) && !previous_runtime.get("skill_ready", "value", false)) {
                        record_state_event(
                            "skill_ready_observed",
                            snapshot,
                            json::object {
                                { "instance_id", id },
                                { "evidence", candidate },
                            });
                    }
                }
            }
        }
    }
}

void asst::BattleControlTask::complete_runtime_evidence(
    const Command& command,
    const CapturedFrame& frame,
    json::value& snapshot)
{
    // Optional inference is never part of the resumed/cleanup window.
    const auto started = std::chrono::steady_clock::now();
    m_optional_runtime_deadline = command.deadline - std::chrono::milliseconds(500);
    if (!frame.image.empty() && (snapshot.get("scene", "") == "running" || snapshot.get("scene", "") == "paused")) {
        BattlefieldMatcher matcher(frame.image);
        if (snapshot.at("cost").is_null()) {
            matcher.set_object_of_interest({ .costs = true });
            if (const auto result = matcher.analyze(); result && result->costs.status != BattlefieldMatcher::Invalid) {
                snapshot["cost"] = result->costs.value;
            }
        }
        if (snapshot.at("kills").is_null() || snapshot.at("total_kills").is_null()) {
            matcher.set_object_of_interest({ .kills = true });
            if (const auto result = matcher.analyze(); result && result->kills.status != BattlefieldMatcher::Invalid) {
                snapshot["kills"] = result->kills.value.first;
                snapshot["total_kills"] = result->kills.value.second;
            }
        }
    }
    m_allow_optional_visuals = !m_cancel_requested && !need_exit() &&
                               started + m_optional_runtime_cost + std::chrono::milliseconds(500) < command.deadline;
    observe_runtime(frame, snapshot);
    if (m_allow_optional_visuals) {
        m_optional_runtime_cost = std::max(
            m_optional_runtime_cost,
            std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - started));
    }
    m_allow_optional_visuals = false;
    json::array battlefield;
    for (const auto& unit : snapshot.at("battlefield").as_array()) {
        if (!unit.get("unit_id", "").starts_with("instance:")) {
            battlefield.emplace_back(unit);
        }
    }
    for (auto& [id, instance] : m_instances) {
        if (snapshot.get("scene", "") == "terminal" &&
            (snapshot.at("terminal").get("terminal_state", "") == "victory" ||
             snapshot.at("terminal").get("terminal_state", "") == "defeat")) {
            instance.presence = Presence::Gone;
            instance.presence_source = "verified_terminal_screen";
        }
        auto info = instance_info(instance);
        info["skill_ready"] = instance.runtime.at("skill_ready").get("value", json::value(nullptr));
        info["skill_evidence"] = instance.runtime.at("skill_ready");
        battlefield.emplace_back(std::move(info));
    }
    snapshot["battlefield"] = std::move(battlefield);
    snapshot["state_events"] = json::array(m_state_events);
    snapshot["event_cursor"] = m_state_event_sequence;
    snapshot["events_retained_from"] =
        m_state_events.empty() ? json::value(m_state_event_sequence + 1) : m_state_events.front().at("sequence");
}
