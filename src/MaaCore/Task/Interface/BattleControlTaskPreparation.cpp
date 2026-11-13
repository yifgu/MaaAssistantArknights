#include "BattleControlTask.h"

#include <algorithm>
#include <ranges>
#include <thread>

#include "Config/Miscellaneous/BattleDataConfig.h"
#include "Config/TaskData.h"
#include "Controller/Controller.h"
#include "MaaUtils/NoWarningCV.hpp"
#include "Utils/Logger.hpp"
#include "Vision/Battle/BattleFormationAnalyzer.h"
#include "Vision/Matcher.h"
#include "Vision/Miscellaneous/PipelineAnalyzer.h"
#include "Vision/MultiMatcher.h"
#include "Vision/OCRer.h"
#include "Vision/RegionOCRer.h"

json::value asst::BattleControlTask::inspect_preparation(const Command& command)
{
    using Clock = std::chrono::steady_clock;
    auto frame = capture_frame();
    if (frame.image.empty()) {
        return failure("screenshot_failed", "Preparation screenshot is empty; no input sent");
    }
    const auto initial_slots = BattleFormationAnalyzer(frame.image).analyze_slots();
    if (!initial_slots) {
        return failure("preparation_not_recognized", "No supported formation page recognized; no input sent");
    }
    const auto formation_image = frame.image;
    json::value snapshot = json::object {
        { "page", "formation" },        { "source", "preparation_vision" },
        { "operators", json::array() }, { "roster_coverage", "detected_level_flags_including_unreadable_names" },
        { "input_sent", false },
    };
    json::array evidence;
    bool evidence_ok = true;

    struct PageState
    {
        std::string page = "unknown";
        BattleFormationAnalyzer::ResultsVecOpt slots;
        std::string target_name;
        bool identity_matches = false;
    };

    auto retain = [&](const CapturedFrame& captured, const std::string& purpose, const PageState& state) {
        json::value observed = json::object {
            { "observation_id", std::to_string(++m_observation_id) },
            { "captured_at_ms",
              std::chrono::duration_cast<std::chrono::milliseconds>(captured.captured_at.time_since_epoch()).count() },
            { "page", state.page },
            { "purpose", purpose },
        };
        if (state.page == "operator_detail") {
            observed["target_name"] = state.target_name;
            observed["target_identity_verified"] = state.identity_matches;
        }
        auto saved = save_evidence(captured, observed, true);
        evidence_ok = evidence_ok && saved.get("status", "") == "succeeded";
        observed["image_path"] = saved.get("image_path", json::value(nullptr));
        if (saved.contains("error")) {
            observed["evidence_error"] = saved.at("error");
        }
        evidence.emplace_back(observed);
        return observed;
    };
    const auto initial_evidence = retain(frame, "formation_initial", PageState { "formation", initial_slots });
    auto rect_json = [](const Rect& rect) {
        return json::array { rect.x, rect.y, rect.width, rect.height };
    };
    auto field = [&](json::value value, const std::string& reason, const json::value& proof, const Rect& roi) {
        return json::object {
            { "value", value },
            { "source", value.is_null() ? "unknown" : "preparation_vision" },
            { "verification", value.is_null() ? "unknown" : "observed" },
            { "reason", reason },
            { "evidence", proof },
            { "roi", rect_json(roi) },
        };
    };
    auto can_scan = [&] {
        return evidence_ok && !m_cancel_requested && !need_exit() &&
               Clock::now() + std::chrono::seconds(5) < command.deadline;
    };
    auto delay = [&](int milliseconds, bool cleanup = false) {
        const auto until = std::min(command.deadline, Clock::now() + std::chrono::milliseconds(milliseconds));
        while (Clock::now() < until && !need_exit() && (cleanup || !m_cancel_requested)) {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
    };
    auto classify = [&](const CapturedFrame& captured, const std::string& name) {
        PageState state;
        if (captured.image.empty()) {
            return state;
        }
        state.slots = BattleFormationAnalyzer(captured.image).analyze_slots();
        if (state.slots) {
            state.page = "formation";
            return state;
        }
        Matcher page(captured.image);
        page.set_task_info("BattlePreparationDetail");
        if (!page.analyze()) {
            return state;
        }
        state.page = "operator_detail";
        state.target_name = name;
        OCRer identity(captured.image);
        identity.set_task_info("BattlePreparationDetailName");
        identity.set_required({ name });
        const auto found = identity.analyze();
        state.identity_matches = found && found->size() == 1 && found->front().text == name;
        return state;
    };
    auto same_roster = [&](const BattleFormationAnalyzer::ResultsVecOpt& slots) {
        if (!slots || slots->size() != initial_slots->size()) {
            return false;
        }
        for (size_t i = 0; i < slots->size(); ++i) {
            const auto& before = initial_slots->at(i);
            const auto& after = slots->at(i);
            if (before.name != after.name || std::abs(before.flag_rect.x - after.flag_rect.x) > 5 ||
                std::abs(before.flag_rect.y - after.flag_rect.y) > 5) {
                return false;
            }
        }
        return true;
    };
    auto stop_reason = [&]() -> std::string {
        return !evidence_ok                        ? "evidence_write_failed"
               : m_cancel_requested || need_exit() ? "cancelled"
                                                   : "deadline_exceeded";
    };
    const auto& skill_roi = Task.get("BattleQuickFormationSkillLevel-Base")->roi;
    auto unchanged = [&](const cv::Mat& a, const cv::Mat& b) {
        cv::Mat diff;
        cv::absdiff(make_roi(a, skill_roi), make_roi(b, skill_roi), diff);
        double maximum = 0;
        cv::minMaxLoc(diff.reshape(1), nullptr, &maximum);
        return maximum <= 10 && cv::mean(diff.reshape(1))[0] <= 0.5;
    };
    bool restored = true;
    bool roster_unchanged = true;
    std::string error;
    std::string recovery_error;
    size_t complete = 0;
    for (size_t index = 0; index < initial_slots->size(); ++index) {
        const auto& slot = initial_slots->at(index);
        json::array ids;
        json::array ordinary_ids;
        for (const auto& candidate : BattleData.find_opers(battle::Role::Unknown, slot.name)) {
            ids.emplace_back(candidate->id);
            if (candidate->role != battle::Role::Drone && candidate->role != battle::Role::Unknown) {
                ordinary_ids.emplace_back(candidate->id);
            }
        }
        const auto resolved_id = ordinary_ids.size() == 1 ? ordinary_ids[0] : json::value(nullptr);
        const auto& ordinary_roi = Task.get("BattleFormationOCRNameFlag")->roi;
        const bool support = slot.flag_rect.x >= ordinary_roi.x + ordinary_roi.width;
        json::value oper = json::object {
            { "slot", index },
            { "name", slot.name.empty() ? json::value(nullptr) : json::value(slot.name) },
            { "game_ids", ids },
            { "operator_game_id", resolved_id },
            { "name_rect", rect_json(slot.rect) },
            { "flag_rect", rect_json(slot.flag_rect) },
            { "support", support },
            { "fields", json::object() },
        };
        auto& fields = oper["fields"];
        fields["identity"] = field(
            oper.at("operator_game_id"),
            slot.name.empty()          ? "name_unreadable"
            : ordinary_ids.empty()     ? "no_ordinary_operator_candidate"
            : ordinary_ids.size() != 1 ? "identity_ambiguous"
                                       : "unique_ordinary_operator_name_mapping",
            initial_evidence,
            slot.rect);
        fields["identity"]["ordinary_operator_game_ids"] = ordinary_ids;
        fields["identity"]["mapping_source"] = "bundled_battle_data_roles";
        if (!resolved_id.is_null()) {
            fields["identity"]["source"] = "preparation_vision_with_static_identity_mapping";
            fields["identity"]["verification"] = "derived_from_observed_name_and_ordinary_operator_class";
        }
        const Rect level_roi = slot.flag_rect.move(Task.get("BattlePreparationLevel")->rect_move);
        RegionOCRer level(formation_image);
        level.set_task_info("BattlePreparationLevel");
        level.set_roi(level_roi);
        int actual_level = -1;
        const bool level_roi_in_frame = !level_roi.empty() && level_roi.x >= 0 && level_roi.y >= 0 &&
                                        level_roi.x + level_roi.width <= formation_image.cols &&
                                        level_roi.y + level_roi.height <= formation_image.rows;
        const auto level_text = level_roi_in_frame ? level.analyze() : std::nullopt;
        if (!level_text || !utils::chars_to_number(level_text->text, actual_level) || actual_level < 1 ||
            actual_level > 90) {
            actual_level = -1;
        }
        fields["actual_level"] = field(
            actual_level == -1 ? json::value(nullptr) : json::value(actual_level),
            !level_roi_in_frame  ? "level_roi_out_of_bounds"
            : actual_level == -1 ? "level_unreadable_or_out_of_range"
                                 : "level_ocr",
            initial_evidence,
            level_roi);
        fields["actual_level"]["roi_within_frame"] = level_roi_in_frame;
        if (level_text) {
            fields["actual_level"]["text"] = level_text->text;
            fields["actual_level"]["score"] = level_text->score;
        }
        int elite = -1;
        Rect elite_roi = slot.avatar_rect;
        if (!elite_roi.empty()) {
            elite_roi.height = std::max(elite_roi.height, slot.flag_rect.y + slot.flag_rect.height - elite_roi.y);
        }
        std::optional<MatchRect> elite_match;
        if (!slot.avatar.empty()) {
            MultiMatcher elites(formation_image);
            elites.set_task_info("BattlePreparationElite");
            elites.set_roi(elite_roi);
            const auto found = elites.analyze();
            if (found && found->size() == 1) {
                elite_match = found->front();
                const auto& name = found->front().templ_name;
                if (name.ends_with("Elite1.png")) {
                    elite = 1;
                }
                else if (name.ends_with("Elite2.png")) {
                    elite = 2;
                }
            }
        }
        fields["actual_elite"] = field(
            elite == -1 ? json::value(nullptr) : json::value(elite),
            elite == -1 ? "no_unique_positive_elite_template_not_assumed_e0" : "elite_template",
            initial_evidence,
            elite_roi);
        if (elite_match) {
            fields["actual_elite"]["template"] = elite_match->templ_name;
            fields["actual_elite"]["score"] = elite_match->score;
        }
        fields["selected_skill"] = field(nullptr, "not_inspected", nullptr, skill_roi);
        fields["equipped_skill_id"] = field(
            nullptr,
            resolved_id.is_null() ? "identity_unresolved" : "selected_skill_not_observed",
            nullptr,
            skill_roi);
        const auto& skill_ids = BattleData.get_skill_ids(resolved_id.is_null() ? "" : resolved_id.as_string());

        if (error.empty() && (!can_scan() || !evidence_ok)) {
            error = stop_reason();
        }
        if (error.empty() && !support && !resolved_id.is_null() && !slot.avatar.empty()) {
            // Only the formation portrait is clicked, never a card on the selection page.
            const Rect portrait_center { slot.avatar_rect.x + slot.avatar_rect.width / 2,
                                         slot.avatar_rect.y + slot.avatar_rect.height / 2,
                                         1,
                                         1 };
            if (!ctrler()->click(portrait_center)) {
                error = "detail_open_input_failed";
            }
            snapshot["input_sent"] = true;
            restored = false;
            roster_unchanged = false;
            CapturedFrame detail;
            PageState page;
            json::value detail_evidence;
            auto refresh = [&](const std::string& purpose) {
                if (Clock::now() >= command.deadline || need_exit()) {
                    return;
                }
                detail = capture_frame();
                page = classify(detail, slot.name);
                detail_evidence = retain(detail, purpose, page);
            };
            auto wait_for_transition = [&](const std::string& purpose, Clock::time_point until, bool cleanup) {
                auto pending = [&] {
                    return page.page == "unknown" ||
                           (cleanup ? page.identity_matches : page.page == "formation" && same_roster(page.slots));
                };
                auto may_wait = [&] {
                    return evidence_ok && !need_exit() && Clock::now() < until &&
                           (cleanup || (error.empty() && can_scan()));
                };
                while (pending() && may_wait()) {
                    delay(100, cleanup);
                    if (may_wait()) {
                        refresh(purpose);
                    }
                }
            };
            const auto open_until =
                std::min(command.deadline - std::chrono::seconds(5), Clock::now() + std::chrono::seconds(3));
            refresh("detail_open_verify");
            wait_for_transition("detail_open_verify", open_until, false);
            if (error.empty() && !can_scan()) {
                error = stop_reason();
            }
            if (error.empty() && !page.identity_matches) {
                error = page.page == "formation"
                            ? (same_roster(page.slots) ? "detail_transition_timeout" : "roster_changed")
                        : page.page == "operator_detail" ? "detail_identity_not_verified"
                                                         : "detail_page_not_verified";
            }
            if (error.empty() && page.identity_matches && can_scan()) {
                PipelineAnalyzer tab(detail.image);
                tab.set_tasks({ "BattleQuickFormationSkillPage" });
                const auto found = tab.analyze();
                if (found && can_scan()) {
                    if (!ctrler()->click(found->rect)) {
                        error = "skill_page_input_failed";
                    }
                    else {
                        delay(300);
                        if (can_scan()) {
                            refresh("skill_page_verify");
                        }
                    }
                }
                bool top = false;
                std::string skill_error = error.empty() ? "skill_list_top_unverified" : error;
                for (int attempt = 0; attempt < 3 && error.empty() && can_scan() && page.identity_matches; ++attempt) {
                    const auto previous = detail.image;
                    const auto& swipe = Task.get("BattleQuickFormationSkill-SwipeToTheUp");
                    if (!can_scan() || !ctrler()->swipe(swipe->specific_rect, swipe->rect_move, 150)) {
                        error = skill_error = "skill_scroll_failed";
                        break;
                    }
                    delay(swipe->post_delay);
                    if (!can_scan()) {
                        break;
                    }
                    refresh("skill_list_top_verify");
                    if (page.identity_matches && unchanged(previous, detail.image)) {
                        top = true;
                        break;
                    }
                }
                cv::Mat panorama;
                json::array skill_frames;
                bool bottom = false;
                if (top) {
                    panorama = make_roi(detail.image, skill_roi).clone();
                    skill_frames.emplace_back(json::object { { "frame", detail_evidence }, { "offset_y", 0 } });
                    skill_error = "skill_list_incomplete";
                    for (int attempt = 0; attempt < 6 && can_scan() && page.identity_matches; ++attempt) {
                        const auto previous = detail.image;
                        const auto& swipe = Task.get("BattleQuickFormationSkillLevel-Swipe");
                        if (!ctrler()->swipe(swipe->specific_rect, swipe->rect_move, 300)) {
                            error = skill_error = "skill_scroll_failed";
                            break;
                        }
                        delay(swipe->post_delay);
                        if (!can_scan()) {
                            break;
                        }
                        refresh("skill_list_scroll_verify");
                        if (!page.identity_matches) {
                            skill_error = "detail_identity_changed";
                            break;
                        }
                        if (unchanged(previous, detail.image)) {
                            bottom = true;
                            break;
                        }
                        const auto current = make_roi(detail.image, skill_roi);
                        const auto strip = current(cv::Rect(0, 0, current.cols, 60));
                        cv::Scalar mean, deviation;
                        cv::meanStdDev(strip, mean, deviation);
                        if (deviation[0] + deviation[1] + deviation[2] < 15) {
                            skill_error = "skill_overlap_uninformative";
                            break;
                        }
                        MultiMatcher overlap(panorama);
                        overlap.set_templ(strip);
                        overlap.set_threshold(0.9);
                        const auto offsets = overlap.analyze();
                        if (!offsets || offsets->size() != 1) {
                            skill_error = "skill_overlap_ambiguous";
                            break;
                        }
                        const int offset = offsets->front().rect.y;
                        const int remaining = panorama.rows - offset;
                        if (offset <= 0 || remaining <= 0 || remaining >= current.rows || panorama.rows > 1400) {
                            skill_error = "skill_scroll_no_progress";
                            break;
                        }
                        cv::vconcat(
                            panorama,
                            current(cv::Rect(0, remaining, current.cols, current.rows - remaining)),
                            panorama);
                        skill_frames.emplace_back(
                            json::object { { "frame", detail_evidence }, { "offset_y", offset } });
                    }
                }
                if (top && bottom && can_scan() && page.identity_matches) {
                    MultiMatcher rows(panorama);
                    rows.set_task_info("BattlePreparationSkillAnchors");
                    rows.set_roi({ 0, 0, panorama.cols, panorama.rows });
                    MultiMatcher selected(panorama);
                    selected.set_task_info("BattlePreparationSkillSelected");
                    selected.set_roi({ 0, 0, panorama.cols, panorama.rows });
                    const auto anchors = rows.analyze();
                    const auto markers = selected.analyze();
                    skill_error = !markers               ? "selected_skill_not_observed"
                                  : markers->size() != 1 ? "multiple_selected_skill_markers"
                                                         : "skill_rows_ambiguous";
                    const bool row_count_verified =
                        anchors && (skill_ids.empty() ? anchors->size() == 3 : anchors->size() == skill_ids.size());
                    if (row_count_verified && markers && markers->size() == 1) {
                        auto sorted = *anchors;
                        std::ranges::sort(sorted, {}, [](const auto& row) { return row.rect.y; });
                        const auto marker = markers->front().rect;
                        const int header_offset = Task.get("BattleQuickFormationSkillLevel-Base")->rect_move.y;
                        for (size_t skill = 0; skill < sorted.size(); ++skill) {
                            const int top_y = sorted[skill].rect.y + header_offset;
                            const int bottom_y =
                                skill + 1 < sorted.size() ? sorted[skill + 1].rect.y + header_offset : panorama.rows;
                            if (marker.y < top_y || marker.y + marker.height > bottom_y) {
                                continue;
                            }
                            for (const auto& source : skill_frames) {
                                const int offset = source.at("offset_y").as_integer();
                                if (marker.y >= offset && marker.y + marker.height <= offset + skill_roi.height) {
                                    fields["selected_skill"] = field(
                                        static_cast<int>(skill + 1),
                                        "unique_selected_marker_in_complete_ordered_skill_list",
                                        source.at("frame"),
                                        { marker.x + skill_roi.x,
                                          marker.y - offset + skill_roi.y,
                                          marker.width,
                                          marker.height });
                                    fields["selected_skill"]["row_order_frames"] = skill_frames;
                                    fields["selected_skill"]["marker_score"] = markers->front().score;
                                    if (skill < skill_ids.size()) {
                                        fields["equipped_skill_id"] = fields.at("selected_skill");
                                        fields["equipped_skill_id"]["value"] = skill_ids[skill];
                                        fields["equipped_skill_id"]["verification"] =
                                            "derived_from_observed_slot_and_unique_identity";
                                        fields["equipped_skill_id"]["source"] =
                                            "preparation_vision_with_static_skill_mapping";
                                        fields["equipped_skill_id"]["mapping_source"] =
                                            "bundled_battle_data_ordered_skill_ids";
                                    }
                                    else {
                                        fields["equipped_skill_id"]["reason"] = "skill_id_mapping_unavailable";
                                        fields["equipped_skill_id"]["evidence"] = source.at("frame");
                                    }
                                    skill_error.clear();
                                    break;
                                }
                            }
                        }
                    }
                }
                if (!skill_error.empty()) {
                    fields["selected_skill"]["reason"] = skill_error;
                    fields["selected_skill"]["evidence"] = detail_evidence;
                    fields["selected_skill"]["row_order_frames"] = skill_frames;
                }
                if (error.empty() && !can_scan()) {
                    error = stop_reason();
                }
                if (error.empty() && !page.identity_matches) {
                    error = "detail_page_or_identity_changed";
                }
            }
            else {
                fields["selected_skill"]["reason"] = error;
                fields["selected_skill"]["evidence"] = detail_evidence;
            }
            // Cleanup uses a fresh frame, even after cancellation or evidence failure, but never extends the deadline.
            if (Clock::now() < command.deadline && !need_exit()) {
                refresh("recovery_check");
            }
            restored = page.slots.has_value();
            roster_unchanged = same_roster(page.slots);
            if (!restored && Clock::now() < command.deadline && !need_exit() && page.identity_matches) {
                Matcher back(detail.image);
                back.set_task_info("BattlePreparationReturn");
                const auto button = back.analyze();
                if (button && Clock::now() < command.deadline && ctrler()->click(button->rect)) {
                    const auto return_until = std::min(command.deadline, Clock::now() + std::chrono::seconds(3));
                    delay(100, true);
                    if (Clock::now() < return_until && !need_exit()) {
                        refresh("formation_return_verify");
                        wait_for_transition("formation_return_verify", return_until, true);
                    }
                    restored = page.slots.has_value();
                    roster_unchanged = same_roster(page.slots);
                }
            }
            if (error.empty() &&
                (!evidence_ok || m_cancel_requested || need_exit() || Clock::now() >= command.deadline)) {
                error = stop_reason();
            }
            if (!restored || !roster_unchanged) {
                recovery_error = !restored ? "formation_not_restored" : "roster_changed";
                if (error.empty()) {
                    error = recovery_error;
                }
            }
        }
        else {
            fields["selected_skill"]["reason"] = !error.empty()          ? error
                                                 : support               ? "support_detail_navigation_unsupported"
                                                 : resolved_id.is_null() ? "identity_not_unique"
                                                                         : "portrait_not_localized";
        }
        bool configured = true;
        for (const auto& key : { "identity", "actual_elite", "actual_level", "selected_skill" }) {
            configured = configured && !fields.at(key).at("value").is_null();
        }
        for (const auto& key : { "actual_elite", "actual_level", "selected_skill", "equipped_skill_id" }) {
            oper[key] = fields.at(key).at("value");
        }
        oper["verification"] = configured ? "configuration_observed" : "configuration_partial";
        complete += configured;
        snapshot["operators"].as_array().emplace_back(std::move(oper));
    }
    if (error.empty() && m_cancel_requested) {
        error = "cancelled";
    }
    if (error.empty() && Clock::now() >= command.deadline) {
        error = "deadline_exceeded";
    }
    if (!evidence_ok && error.empty()) {
        error = "evidence_write_failed";
    }
    snapshot["observation_id"] = initial_evidence.at("observation_id");
    snapshot["captured_at_ms"] = initial_evidence.at("captured_at_ms");
    snapshot["scan_status"] =
        error.empty() && !initial_slots->empty() && complete == initial_slots->size() ? "complete" : "partial";
    json::value result = json::object {
        { "status",
          error.empty()                  ? "succeeded"
          : restored && roster_unchanged ? "failed"
                                         : "outcome_unknown" },
        { "snapshot", std::move(snapshot) },
        { "image_path", initial_evidence.at("image_path") },
        { "evidence_frames", std::move(evidence) },
        { "formation_restored", restored },
        { "roster_unchanged", roster_unchanged },
        { "preparation_valid",
          !initial_slots->empty() && restored && roster_unchanged && evidence_ok && error.empty() },
        { "configuration_preservation", "no_roster_or_skill_selection_input_sent_not_continuous_visual_verification" },
    };
    if (!error.empty()) {
        result["error"] =
            json::object { { "code", error },
                           { "message", "Inspect evidence and recovery state; do not blindly retry navigation" } };
        LogWarn << __FUNCTION__ << result;
    }
    if (!recovery_error.empty()) {
        result["recovery_error"] =
            json::object { { "code", recovery_error },
                           { "message", "Inspect fresh page and roster evidence before recovery" } };
    }
    return result;
}
