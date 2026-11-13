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
    auto retain = [&](const CapturedFrame& captured, const std::string& page) {
        json::value observed = json::object {
            { "observation_id", std::to_string(++m_observation_id) },
            { "captured_at_ms",
              std::chrono::duration_cast<std::chrono::milliseconds>(captured.captured_at.time_since_epoch()).count() },
            { "page", page },
        };
        auto saved = save_evidence(captured, observed, true);
        evidence_ok = evidence_ok && saved.get("status", "") == "succeeded";
        observed["image_path"] = saved.get("image_path", json::value(nullptr));
        if (saved.contains("error")) {
            observed["evidence_error"] = saved.at("error");
        }
        evidence.emplace_back(observed);
        return observed;
    };
    const auto initial_evidence = retain(frame, "formation");
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
    auto detail_matches = [&](const CapturedFrame& captured, const std::string& name) {
        if (captured.image.empty()) {
            return false;
        }
        Matcher page(captured.image);
        page.set_task_info("BattlePreparationDetail");
        if (!page.analyze()) {
            return false;
        }
        OCRer identity(captured.image);
        identity.set_task_info("BattlePreparationDetailName");
        identity.set_required({ name });
        const auto found = identity.analyze();
        return found && found->size() == 1 && found->front().text == name;
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
    size_t complete = 0;
    for (size_t index = 0; index < initial_slots->size(); ++index) {
        const auto& slot = initial_slots->at(index);
        json::array ids;
        for (const auto& id : BattleData.get_ids(battle::Role::Unknown, slot.name)) {
            ids.emplace_back(id);
        }
        const auto& ordinary_roi = Task.get("BattleFormationOCRNameFlag")->roi;
        const bool support = slot.flag_rect.x >= ordinary_roi.x + ordinary_roi.width;
        json::value oper = json::object {
            { "slot", index },
            { "name", slot.name.empty() ? json::value(nullptr) : json::value(slot.name) },
            { "game_ids", ids },
            { "operator_game_id", ids.size() == 1 ? ids[0] : json::value(nullptr) },
            { "name_rect", rect_json(slot.rect) },
            { "flag_rect", rect_json(slot.flag_rect) },
            { "support", support },
            { "fields", json::object() },
        };
        auto& fields = oper["fields"];
        fields["identity"] = field(
            oper.at("operator_game_id"),
            slot.name.empty() ? "name_unreadable"
            : ids.size() != 1 ? "identity_ambiguous"
                              : "unique_name_mapping",
            initial_evidence,
            slot.rect);
        const Rect level_roi = slot.flag_rect.move(Task.get("BattlePreparationLevel")->rect_move);
        RegionOCRer level(formation_image);
        level.set_task_info("BattlePreparationLevel");
        level.set_roi(level_roi);
        int actual_level = -1;
        const auto level_text = level.analyze();
        if (!level_text || !utils::chars_to_number(level_text->text, actual_level) || actual_level < 1 ||
            actual_level > 90) {
            actual_level = -1;
        }
        fields["actual_level"] = field(
            actual_level == -1 ? json::value(nullptr) : json::value(actual_level),
            actual_level == -1 ? "level_unreadable_or_out_of_range" : "level_ocr",
            initial_evidence,
            level_roi);
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
        fields["equipped_skill_id"] = field(nullptr, "skill_id_mapping_unavailable", nullptr, skill_roi);
        const auto& skill_ids = BattleData.get_skill_ids(ids.size() == 1 ? ids[0].as_string() : "");

        if (error.empty() && (!can_scan() || !evidence_ok)) {
            error = !evidence_ok ? "evidence_write_failed" : m_cancel_requested ? "cancelled" : "deadline_exceeded";
        }
        if (error.empty() && !support && ids.size() == 1 && !slot.avatar.empty()) {
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
            delay(500);
            auto detail = capture_frame();
            auto detail_evidence = retain(detail, "operator_detail");
            if (detail_matches(detail, slot.name) && can_scan()) {
                PipelineAnalyzer tab(detail.image);
                tab.set_tasks({ "BattleQuickFormationSkillPage" });
                const auto found = tab.analyze();
                if (found && can_scan() && ctrler()->click(found->rect)) {
                    delay(300);
                    detail = capture_frame();
                    detail_evidence = retain(detail, "operator_detail");
                }
                bool top = false;
                std::string skill_error = "skill_list_top_unverified";
                for (int attempt = 0; attempt < 3 && can_scan() && detail_matches(detail, slot.name); ++attempt) {
                    const auto previous = detail.image;
                    const auto& swipe = Task.get("BattleQuickFormationSkill-SwipeToTheUp");
                    if (!can_scan() || !ctrler()->swipe(swipe->specific_rect, swipe->rect_move, 150)) {
                        skill_error = "skill_scroll_failed";
                        break;
                    }
                    delay(swipe->post_delay);
                    detail = capture_frame();
                    detail_evidence = retain(detail, "operator_detail");
                    if (detail_matches(detail, slot.name) && unchanged(previous, detail.image)) {
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
                    for (int attempt = 0; attempt < 6 && can_scan(); ++attempt) {
                        const auto previous = detail.image;
                        const auto& swipe = Task.get("BattleQuickFormationSkillLevel-Swipe");
                        if (!ctrler()->swipe(swipe->specific_rect, swipe->rect_move, 300)) {
                            skill_error = "skill_scroll_failed";
                            break;
                        }
                        delay(swipe->post_delay);
                        detail = capture_frame();
                        detail_evidence = retain(detail, "operator_detail");
                        if (!detail_matches(detail, slot.name)) {
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
                if (top && bottom) {
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
            }
            else {
                fields["selected_skill"]["reason"] = "detail_page_or_identity_not_verified";
                fields["selected_skill"]["evidence"] = detail_evidence;
            }
            // Cancel still permits one visually guarded back action within the original deadline.
            if (Clock::now() < command.deadline && detail_matches(detail, slot.name)) {
                Matcher back(detail.image);
                back.set_task_info("BattlePreparationReturn");
                const auto button = back.analyze();
                if (button && Clock::now() < command.deadline && ctrler()->click(button->rect)) {
                    delay(500, true);
                    frame = capture_frame();
                    retain(frame, "formation_return");
                    const auto returned = BattleFormationAnalyzer(frame.image).analyze_slots();
                    restored = returned.has_value();
                    roster_unchanged = restored && returned->size() == initial_slots->size();
                    if (roster_unchanged) {
                        for (size_t i = 0; i < returned->size(); ++i) {
                            const auto& before = initial_slots->at(i);
                            const auto& after = returned->at(i);
                            if (before.name != after.name || std::abs(before.flag_rect.x - after.flag_rect.x) > 5 ||
                                std::abs(before.flag_rect.y - after.flag_rect.y) > 5) {
                                roster_unchanged = false;
                            }
                        }
                    }
                }
            }
            if (!restored || !roster_unchanged) {
                error = !restored ? "formation_not_restored" : "roster_changed";
            }
        }
        else {
            fields["selected_skill"]["reason"] = !error.empty()    ? error
                                                 : support         ? "support_detail_navigation_unsupported"
                                                 : ids.size() != 1 ? "identity_not_unique"
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
    return result;
}
