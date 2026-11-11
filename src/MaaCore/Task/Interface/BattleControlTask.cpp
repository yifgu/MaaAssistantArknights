#include "BattleControlTask.h"

#include <algorithm>
#include <cctype>
#include <ranges>
#include <thread>
#include <unordered_map>

#include "Config/Miscellaneous/BattleDataConfig.h"
#include "Config/TaskData.h"
#include "Controller/Controller.h"
#include "MaaUtils/ImageIo.h"
#include "Utils/Logger.hpp"
#include "Vision/Battle/BattleFormationAnalyzer.h"
#include "Vision/Battle/BattlefieldClassifier.h"
#include "Vision/Battle/BattlefieldMatcher.h"
#include "Vision/Miscellaneous/PipelineAnalyzer.h"
#include "Vision/RegionOCRer.h"

asst::BattleControlTask::BattleControlTask(const AsstCallback& callback, Assistant* inst) :
    InterfaceTask(callback, inst, TaskType),
    BattleHelper(inst)
{
    set_retry_times(0);
}

bool asst::BattleControlTask::validate_command(const json::value& params) const
{
    if (!params.is_object() || !params.find<std::string>("command_id") || !params.find<std::string>("action") ||
        !params.find<json::object>("args") || !params.find<int>("timeout_ms")) {
        return false;
    }
    const auto id = params.at("command_id").as_string();
    const auto timeout = params.at("timeout_ms");
    const auto action = params.at("action").as_string();
    const auto max_timeout = 30'000;
    if (id.empty() || id.size() > 128 || timeout.as_integer() < 1 || timeout.as_integer() > max_timeout) {
        return false;
    }
    const auto& args = params.at("args");
    if (action == "identify") {
        return !args.contains("adopt_manual_pause") || args.find<bool>("adopt_manual_pause").has_value();
    }
    if (action == "observe" || action == "capture" || action == "map" || action == "prepare") {
        return true;
    }
    if (action == "bind") {
        return m_preparation_only && args.find<std::string>("stage_name").has_value();
    }
    if (action == "finalize") {
        const auto duration = args.find<int>("duration_ms");
        return duration && *duration >= 100 && *duration <= 8000 && *duration <= timeout.as_integer();
    }
    if (action == "set_speed") {
        const auto speed = args.find<int>("multiplier");
        return speed && (*speed == 1 || *speed == 2);
    }
    if (action == "step") {
        if (!args.find<std::string>("action") || !args.find<bool>("adopt_manual_pause")) {
            return false;
        }
        const auto step_action = args.at("action").as_string();
        if (step_action == "wait") {
            const auto duration = args.find<int>("duration_ms");
            return duration && *duration >= 100 && *duration <= 8000 && *duration + 1000 <= timeout.as_integer() &&
                   !args.contains("unit_id") && !args.contains("location") && !args.contains("direction");
        }
        if (step_action != "deploy" && step_action != "retreat" && step_action != "skill") {
            return false;
        }
        if (!args.find<std::string>("unit_id") || args.at("unit_id").as_string().empty() ||
            args.contains("duration_ms")) {
            return false;
        }
        if (step_action != "deploy") {
            return !args.find<json::array>("location") && !args.find<std::string>("direction");
        }
        if (!args.find<json::array>("location") || !args.find<std::string>("direction")) {
            return false;
        }
        const auto& location = args.at("location").as_array();
        const auto direction = args.at("direction").as_string();
        return location.size() == 2 && location[0].is<int>() && location[1].is<int>() &&
               location[0].as_integer() >= 0 && location[0].as_integer() <= 255 && location[1].as_integer() >= 0 &&
               location[1].as_integer() <= 255 &&
               (direction == "Right" || direction == "Down" || direction == "Left" || direction == "Up" ||
                direction == "None");
    }
    if (action == "set_paused") {
        return args.find<bool>("paused").has_value();
    }
    if (action != "deploy" && action != "retreat" && action != "skill") {
        return false;
    }
    if (!args.find<std::string>("unit_id") || args.at("unit_id").as_string().empty()) {
        return false;
    }
    if (action != "deploy") {
        return true;
    }
    if (!args.find<json::array>("location") || !args.find<std::string>("direction")) {
        return false;
    }
    const auto& location = args.at("location").as_array();
    const auto direction = args.at("direction").as_string();
    return location.size() == 2 && location[0].is<int>() && location[1].is<int>() && location[0].as_integer() >= 0 &&
           location[0].as_integer() <= 255 && location[1].as_integer() >= 0 && location[1].as_integer() <= 255 &&
           (direction == "Right" || direction == "Down" || direction == "Left" || direction == "Up" ||
            direction == "None");
}

bool asst::BattleControlTask::set_params(const json::value& params)
{
    std::lock_guard lock(m_command_mutex);
    if (params.contains("cancel_command_id")) {
        if (!m_initialized || params.get("session_id", "") != m_session_id ||
            !params.find<std::string>("cancel_command_id")) {
            LogWarn << __FUNCTION__ << "Rejected invalid cancellation";
            return false;
        }
        const auto id = params.at("cancel_command_id").as_string();
        if (!id.empty() && id == m_active_command_id) {
            m_cancel_requested = true;
            m_command_cv.notify_all();
        }
        // A cancellation racing with completion must not affect the next command.
        return true;
    }
    if (!m_initialized) {
        if (!params.find<std::string>("session_id") ||
            (!params.get("preparation_only", false) && !params.find<std::string>("stage_name"))) {
            LogError << __FUNCTION__ << "Missing battle session parameters";
            return false;
        }
        const auto session = params.at("session_id").as_string();
        if (session.empty() || session.size() > 64 ||
            !std::ranges::all_of(session, [](unsigned char ch) { return std::isalnum(ch) || ch == '-'; }) ||
            (!params.get("preparation_only", false) && !set_stage_name(params.at("stage_name").as_string()))) {
            LogError << __FUNCTION__ << "Invalid session or unsupported stage";
            return false;
        }
        m_session_id = session;
        m_preparation_only = params.get("preparation_only", false);
        if (const auto allowed = params.find<json::array>("allowed_summons")) {
            for (const auto& id : *allowed) {
                if (!id.is_string()) {
                    LogError << __FUNCTION__ << "Invalid summon allowlist";
                    return false;
                }
                m_allowed_summons.emplace(id.as_string());
            }
        }
        m_initialized = true;
        return true;
    }
    if (!m_accepting || m_command || params.get("session_id", "") != m_session_id || !validate_command(params)) {
        LogError << __FUNCTION__ << "Rejected invalid, stale or concurrent battle command";
        return false;
    }
    const auto now = std::chrono::steady_clock::now();
    m_command = Command { params.at("command_id").as_string(),
                          params.at("action").as_string(),
                          params.at("args"),
                          now,
                          now + std::chrono::milliseconds(params.at("timeout_ms").as_integer()) };
    m_active_command_id = m_command->id;
    m_cancel_requested = false;
    m_accepting = false;
    m_command_cv.notify_one();
    return true;
}

bool asst::BattleControlTask::run()
{
    // Interactive commands must never be replayed by AbstractTask's retry loop.
    return _run();
}

bool asst::BattleControlTask::_run()
{
    if (!m_preparation_only && (!calc_tiles_info(m_stage_name) || m_normal_tile_info.empty() || m_has_multi_stages)) {
        LogError << __FUNCTION__ << "Unsupported or multi-stage battle map";
        return false;
    }
    {
        std::lock_guard lock(m_command_mutex);
        m_accepting = true;
    }
    emit(
        "BattleControlReady",
        json::object { { "protocol_version", 1 }, { "map", m_preparation_only ? json::value(nullptr) : map_info() } });
    while (!need_exit()) {
        std::optional<Command> command;
        {
            std::unique_lock lock(m_command_mutex);
            m_command_cv.wait_for(lock, std::chrono::milliseconds(100), [&] { return m_command.has_value(); });
            if (!m_command) {
                continue;
            }
            command = std::move(m_command);
            m_command.reset();
        }
        auto result = execute(*command);
        result["command_id"] = command->id;
        LogInfo << __FUNCTION__ << "Battle command result" << command->id << command->action << result;
        {
            std::lock_guard lock(m_command_mutex);
            m_active_command_id.clear();
            m_accepting = !need_exit();
        }
        emit("BattleControlResult", std::move(result));
    }
    {
        std::lock_guard lock(m_command_mutex);
        m_accepting = false;
    }
    return true;
}

std::string asst::BattleControlTask::unit_id(battle::Role role, const std::string& name)
{
    return name.empty() ? std::string() : std::to_string(static_cast<int>(role)) + ":" + name;
}

json::value asst::BattleControlTask::failure(const std::string& code, const std::string& message) const
{
    LogWarn << __FUNCTION__ << code << message;
    return json::object {
        { "status", "failed" },
        { "error", json::object { { "code", code }, { "message", message } } },
    };
}

void asst::BattleControlTask::emit(const std::string& what, json::value details)
{
    details["session_id"] = m_session_id;
    auto info = basic_info_with_what(what);
    info["details"] = std::move(details);
    callback(AsstMsg::SubTaskExtraInfo, info);
}

json::value asst::BattleControlTask::map_info() const
{
    json::array tiles;
    for (int y = 0; y < m_map_data.get_height(); ++y) {
        for (int x = 0; x < m_map_data.get_width(); ++x) {
            const auto tile = m_map_data.get_item(y, x);
            const auto& normal = m_normal_tile_info.at({ x, y });
            const auto& side = m_side_tile_info.at({ x, y });
            tiles.emplace_back(
                json::object {
                    { "location", json::array { x, y } },
                    { "tile_key", tile.tileKey },
                    { "buildable_type", tile.buildableType },
                    { "height_type", tile.heightType },
                    { "screen", json::array { normal.pos.x, normal.pos.y } },
                    { "deployment_screen", json::array { side.pos.x, side.pos.y } },
                });
        }
    }
    return json::object {
        { "stage_id", m_map_data.key.stageId },
        { "level_id", m_map_data.key.levelId },
        { "code", m_map_data.key.code },
        { "width", m_map_data.get_width() },
        { "height", m_map_data.get_height() },
        { "coordinate_system", "maa_grid" },
        { "binding", "caller_supplied_not_visually_verified" },
        { "tiles", std::move(tiles) },
    };
}

asst::BattleControlTask::CapturedFrame asst::BattleControlTask::capture_frame()
{
    const auto started = std::chrono::steady_clock::now();
    auto image = ctrler()->get_image();
    const auto captured_at = std::chrono::system_clock::now();
    return { std::move(image), captured_at, started };
}

json::value
    asst::BattleControlTask::observe(const CapturedFrame& frame, bool active_identification, bool include_battlefield)
{
    const auto& image = frame.image;
    json::value snapshot = json::object {
        { "observation_id", std::to_string(++m_observation_id) },
        { "stage_id", m_map_data.key.stageId },
        { "captured_at_ms",
          std::chrono::duration_cast<std::chrono::milliseconds>(frame.captured_at.time_since_epoch()).count() },
        { "in_battle", false },
        { "running_button_visible", false },
        { "pause_requested", m_pause_requested },
        { "cost", nullptr },
        { "kills", nullptr },
        { "total_kills", nullptr },
        { "identified_count", 0 },
        { "unresolved_count", 0 },
        { "ambiguous_count", 0 },
        { "deployment", json::array() },
        { "battlefield", json::array() },
        { "enemies", nullptr },
    };
    if (image.empty()) {
        snapshot["error"] = "empty_screenshot";
        snapshot["scene"] = "unknown";
        snapshot["terminal"] = m_terminal_result;
        m_terminal_seen = m_terminal_seen || m_battle_observed;
        update_input_state(snapshot);
        return snapshot;
    }
    BattlefieldMatcher matcher(image);
    matcher.set_object_of_interest({ .deployment = true, .oper_cost = true });
    const auto result = matcher.analyze();
    snapshot["in_battle"] = result.has_value();
    snapshot["running_button_visible"] = result && check_pause_button(image);
    snapshot["terminal"] = terminal_info(frame, snapshot);
    const auto terminal_state = snapshot.at("terminal").at("terminal_state").as_string();
    const bool terminal = terminal_state == "victory" || terminal_state == "defeat";
    const bool check_paused = !terminal && result && !snapshot.at("running_button_visible").as_boolean();
    auto pause_evidence = check_paused ? match_evidence(image, "BattleControlPaused") : json::value(nullptr);
    json::value localized_pause_evidence = nullptr;
    if (check_paused && (!pause_evidence.is_object() || pause_evidence.get("score", 0.0) < 0.9)) {
        localized_pause_evidence = match_evidence(image, "BattleControlPausedLocalized");
        if (localized_pause_evidence.is_object() && localized_pause_evidence.get("score", 0.0) >= 0.9) {
            pause_evidence = localized_pause_evidence;
        }
    }
    snapshot["pause_evidence"] = pause_evidence;
    const bool paused = pause_evidence.is_object() && pause_evidence.get("score", 0.0) >= 0.9;
    snapshot["pause_diagnostics"] = nullptr;
    if (check_paused && !paused) {
        auto diagnostic = pause_diagnostics(frame, snapshot, "BattleControlPaused", pause_evidence);
        diagnostic["localized"] =
            pause_diagnostics(frame, snapshot, "BattleControlPausedLocalized", localized_pause_evidence);
        snapshot["pause_diagnostics"] = std::move(diagnostic);
    }
    snapshot["scene"] = terminal                                                                   ? "terminal"
                        : snapshot.at("terminal").get("result_source", "") == "conflicting_titles" ? "unknown"
                        : !result                                                                  ? "unknown"
                        : snapshot.at("running_button_visible").as_boolean()                       ? "running"
                        : paused                                                                   ? "paused"
                                                                                                   : "transition";
    const auto scene = snapshot.at("scene").as_string();
    if (terminal || (scene == "unknown" && m_battle_observed) ||
        snapshot.at("terminal").get("result_source", "") == "conflicting_titles") {
        m_terminal_seen = true;
    }
    else if (scene == "transition" && m_battle_observed) {
        if (!input_blocked()) {
            LogWarn << __FUNCTION__
                    << "Pause evidence missing in battle; blocking input pending read-only verification";
        }
        m_pause_unverified = true;
        m_pause_confirmations = 0;
    }
    else if (scene == "paused" && m_pause_unverified && !m_terminal_seen) {
        if (++m_pause_confirmations >= 2) {
            m_pause_unverified = false;
            LogInfo << __FUNCTION__ << "Pause verified by consecutive fresh captures; no input replayed";
        }
    }
    else {
        m_pause_confirmations = 0;
    }
    if (scene == "running" || scene == "paused") {
        m_battle_observed = true;
    }
    update_input_state(snapshot);
    snapshot["speed"] = (scene == "running" || scene == "paused") ? speed_info(image) : json::value(nullptr);
    if (snapshot.at("terminal").get("result_source", "") == "conflicting_titles") {
        m_terminal_result = snapshot.at("terminal");
    }
    if (terminal) {
        const auto previous = m_terminal_result.get("terminal_state", "unknown");
        if ((previous == "victory" || previous == "defeat") && previous != terminal_state) {
            m_terminal_result = json::object { { "terminal_state", "unknown" },
                                               { "result_source", "conflicting_titles" },
                                               { "terminal_confidence", nullptr },
                                               { "previous", m_terminal_result },
                                               { "current", snapshot.at("terminal") } };
        }
        else if (
            (previous == "unknown" || (previous == terminal_state && !m_terminal_result.contains("image_path"))) &&
            m_terminal_result.get("result_source", "") != "conflicting_titles") {
            m_terminal_result = snapshot.at("terminal");
            auto evidence = save_evidence(frame, snapshot, true);
            m_terminal_result["observation_id"] = snapshot.at("observation_id");
            m_terminal_result["captured_at_ms"] = snapshot.at("captured_at_ms");
            if (evidence.get("status", "") == "succeeded") {
                m_terminal_result["image_path"] = evidence.at("image_path");
            }
            else {
                m_terminal_result["evidence_error"] = evidence.at("error");
            }
        }
    }
    snapshot["session_terminal"] = m_terminal_result;
    auto finish = [&]() {
        m_frame_cost = std::max(
            m_frame_cost,
            std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - frame.started));
        LogDebug << __FUNCTION__ << "Battle frame" << snapshot;
        return snapshot;
    };
    if (!result || scene == "terminal" || scene == "unknown" || scene == "transition") {
        snapshot["error"] = !result ? "battle_not_recognized" : "battle_scene_not_actionable";
        return finish();
    }
    auto deployment = result->deployment;
    const auto old_deployment = m_cur_deployment_opers;
    update_deployment_(deployment, old_deployment, false, false);
    if (include_battlefield) {
        matcher.set_object_of_interest({ .costs = true });
        if (auto cost = matcher.analyze()) {
            snapshot["cost"] = cost->costs.value;
        }
        matcher.set_object_of_interest({ .kills = true });
        if (auto kills = matcher.analyze()) {
            snapshot["kills"] = kills->kills.value.first;
            snapshot["total_kills"] = kills->kills.value.second;
        }
    }
    std::unordered_map<std::string, size_t> identity_counts;
    int identified_count = 0;
    int unresolved_count = 0;
    int ambiguous_count = 0;
    for (const auto& oper : m_cur_deployment_opers) {
        if (!oper.name.empty()) {
            ++identity_counts[unit_id(oper.role, oper.name)];
        }
    }
    for (const auto& oper : m_cur_deployment_opers) {
        json::array ids;
        for (const auto& id : BattleData.get_ids(oper.role, oper.name)) {
            ids.emplace_back(id);
        }
        std::string identity_reason = "identified";
        if (oper.name.empty()) {
            ++unresolved_count;
            identity_reason = oper.cooling            ? "cooling_identity_unavailable"
                              : active_identification ? "name_ocr_failed"
                                                      : "not_actively_identified";
        }
        else {
            ++identified_count;
            if (identity_counts[unit_id(oper.role, oper.name)] > 1) {
                ++ambiguous_count;
                identity_reason = "duplicate_identity";
            }
            else if (oper.role == battle::Role::Drone) {
                identity_reason = "instance_tracking_required";
            }
        }
        const auto card_id = snapshot.at("observation_id").as_string() + ":" + std::to_string(oper.index);
        if (!oper.name.empty()) {
            m_cards.insert_or_assign(card_id, Card { { oper.role, oper.name }, oper.rect, oper.index, frame.started });
        }
        snapshot["deployment"].as_array().emplace_back(
            json::object {
                { "card_id", card_id },
                { "unit_id", oper.name.empty() ? json::value(nullptr) : json::value(unit_id(oper.role, oper.name)) },
                { "name", oper.name.empty() ? json::value(nullptr) : json::value(oper.name) },
                { "role", static_cast<int>(oper.role) },
                { "game_ids", std::move(ids) },
                { "index", oper.index },
                { "cost", oper.cost },
                { "available", oper.available },
                { "cooling", oper.cooling },
                { "rect", json::array { oper.rect.x, oper.rect.y, oper.rect.width, oper.rect.height } },
                { "source", "vision" },
                { "identity_reason", std::move(identity_reason) },
            });
    }
    snapshot["identified_count"] = identified_count;
    snapshot["unresolved_count"] = unresolved_count;
    snapshot["ambiguous_count"] = ambiguous_count;
    if (include_battlefield) {
        for (const auto& [tag, loc] : m_battlefield_opers) {
            json::array ids;
            for (const auto& id : BattleData.get_ids(tag.role, tag.name)) {
                ids.emplace_back(id);
            }
            const auto id = unit_id(tag.role, tag.name);
            if (tag.role == battle::Role::Drone) {
                continue;
            }
            json::value ready = nullptr;
            json::value skill_evidence = nullptr;
            if (const auto tile = m_normal_tile_info.find(loc);
                tile != m_normal_tile_info.end() && Rect(0, 0, image.cols, image.rows).include(tile->second.pos)) {
                BattlefieldClassifier classifier(image);
                classifier.set_object_of_interest({ .skill_ready = true });
                classifier.set_base_point(tile->second.pos);
                if (auto skill = classifier.analyze()) {
                    const auto& classified = skill->skill_ready;
                    // Negative classes are not a verified cooldown/active-state measurement.
                    if (classified.ready && classified.score >= 0.9f) {
                        ready = true;
                    }
                    skill_evidence =
                        json::object { { "source", "skill_ready_cls" },
                                       { "score", classified.score },
                                       { "classifier_ready", classified.ready },
                                       { "probabilities",
                                         json::array { classified.prob[0], classified.prob[1], classified.prob[2] } },
                                       { "roi",
                                         json::array { classified.rect.x,
                                                       classified.rect.y,
                                                       classified.rect.width,
                                                       classified.rect.height } },
                                       { "base_point", json::array { tile->second.pos.x, tile->second.pos.y } },
                                       { "observation_id", snapshot.at("observation_id") },
                                       { "captured_at_ms", snapshot.at("captured_at_ms") },
                                       { "active_phase", nullptr },
                                       { "ammo", nullptr },
                                       { "cooldown", nullptr } };
                }
            }
            snapshot["battlefield"].as_array().emplace_back(
                json::object {
                    { "unit_id", id },
                    { "game_ids", std::move(ids) },
                    { "name", tag.name },
                    { "role", static_cast<int>(tag.role) },
                    { "location", json::array { loc.x, loc.y } },
                    { "position_source", "action_history_unverified" },
                    { "direction",
                      m_directions.contains(id) ? json::value(m_directions.at(id)) : json::value(nullptr) },
                    { "direction_source", "action_history_unverified" },
                    { "skill_ready", std::move(ready) },
                    { "skill_evidence", std::move(skill_evidence) },
                });
        }
        for (const auto& instance : m_summons) {
            snapshot["battlefield"].as_array().emplace_back(instance);
        }
    }
    std::erase_if(m_cards, [](const auto& pair) {
        return std::chrono::steady_clock::now() - pair.second.seen > std::chrono::seconds(30);
    });
    return finish();
}

json::value asst::BattleControlTask::save_evidence(const CapturedFrame& frame, json::value snapshot, bool retain)
{
    const auto& image = frame.image;
    if (image.empty()) {
        return failure("screenshot_failed", "No image available");
    }
    const auto relative = utils::path("battle-control") / utils::path(m_session_id) /
                          utils::path(snapshot.at("observation_id").as_string() + ".png");
    const auto file = UserDir.get() / relative;
    if (!MAA_NS::imwrite(file, image)) {
        return failure("evidence_write_failed", "Could not save screenshot");
    }
    if (!retain && !input_blocked()) {
        m_evidence_files.emplace_back(file);
    }
    if (m_evidence_files.size() > 128) {
        std::error_code ec;
        std::filesystem::remove(m_evidence_files.front(), ec);
        if (ec) {
            LogWarn << __FUNCTION__ << "Failed to remove old screenshot" << ec.message();
        }
        m_evidence_files.pop_front();
    }
    return json::object {
        { "status", "succeeded" },
        { "snapshot", std::move(snapshot) },
        { "image_path", relative.generic_string() },
    };
}

json::value asst::BattleControlTask::terminal_info(const CapturedFrame& frame, const json::value& snapshot) const
{
    json::value result = json::object {
        { "terminal_state", snapshot.at("in_battle").as_boolean() ? "still_in_battle" : "unknown" },
        { "terminal_confidence", nullptr },
        { "result_source", "unrecognized" },
    };
    result["candidates"] = json::array();
    if (frame.image.empty()) {
        return result;
    }
    int matches = 0;
    for (const auto& [task, state] :
         { std::pair { "BattleControlVictory", "victory" }, std::pair { "BattleControlDefeat", "defeat" } }) {
        const auto evidence = match_evidence(frame.image, task);
        if (evidence.is_object()) {
            result["candidates"].as_array().emplace_back(evidence);
            const auto score = evidence.get("score", 0.0);
            if (score >= 0.9) {
                ++matches;
                result["terminal_state"] = state;
                result["result_source"] = task;
                result["terminal_confidence"] = score;
            }
        }
    }
    if (matches > 1) {
        result["terminal_state"] = "unknown";
        result["terminal_confidence"] = nullptr;
        result["result_source"] = "conflicting_titles";
    }
    return result;
}

json::value asst::BattleControlTask::match_evidence(const cv::Mat& image, const std::string& task) const
{
    PipelineAnalyzer analyzer(image);
    analyzer.set_tasks({ task });
    const auto match = analyzer.analyze();
    if (!match) {
        return nullptr;
    }
    const auto& rect = match->rect;
    json::value result = json::object { { "source", task },
                                        { "score", 0.0 },
                                        { "rect", json::array { rect.x, rect.y, rect.width, rect.height } } };
    if (const auto* ocr = std::get_if<OCRer::Result>(&match->result)) {
        result["score"] = ocr->score;
        result["text"] = ocr->text;
    }
    else if (const auto* templ = std::get_if<Matcher::Result>(&match->result)) {
        result["score"] = templ->score;
    }
    return result;
}

json::value asst::BattleControlTask::speed_info(const cv::Mat& image) const
{
    const auto one = match_evidence(image, "BattleControlSpeedOne");
    const auto two = match_evidence(image, "BattleSpeedUpCheck");
    const bool is_one = one.is_object() && one.get("score", 0.0) >= 0.9;
    const bool is_two = two.is_object() && two.get("score", 0.0) >= 0.9;
    return json::object { { "value", is_one != is_two ? json::value(is_one ? 1 : 2) : json::value(nullptr) },
                          { "one_x_evidence", one },
                          { "two_x_evidence", two },
                          { "source", "vision" },
                          { "conflict", is_one && is_two } };
}

void asst::BattleControlTask::update_input_state(json::value& snapshot)
{
    if (m_terminal_seen) {
        m_pause_unverified = false;
        m_pause_confirmations = 0;
    }
    if (input_blocked()) {
        m_pause_requested = false;
    }
    snapshot["pause_requested"] = m_pause_requested;
    snapshot["session_input_blocked"] = input_blocked();
    snapshot["input_block_reason"] = m_terminal_seen      ? json::value("terminal_or_safety_quarantine")
                                     : m_pause_unverified ? json::value("pause_verification_pending")
                                                          : json::value(nullptr);
}

json::value asst::BattleControlTask::pause_diagnostics(
    const CapturedFrame& frame,
    const json::value& snapshot,
    const std::string& task_name,
    const json::value& match) const
{
    const auto task = Task.get(task_name);
    const auto& roi = task->roi;
    json::value diagnostic = json::object {
        { "source", task_name },
        { "observation_id", snapshot.at("observation_id") },
        { "captured_at_ms", snapshot.at("captured_at_ms") },
        { "roi", json::array { roi.x, roi.y, roi.width, roi.height } },
        { "required_score", 0.9 },
        { "match", match },
        { "candidates", nullptr },
    };
    const auto ocr = std::dynamic_pointer_cast<OcrTaskInfo>(task);
    if (!ocr) {
        return diagnostic;
    }
    diagnostic["full_match"] = ocr->full_match;
    diagnostic["without_det"] = ocr->without_det;
    diagnostic["use_raw"] = ocr->use_raw;
    diagnostic["use_char_model"] = ocr->is_ascii;
    diagnostic["bin_threshold"] = json::array { ocr->bin_threshold[0], ocr->bin_threshold[1] };
    diagnostic["candidate_source"] = "same_frame_ocr_without_required_text_filter";
    diagnostic["candidates"] = json::array();
    // Diagnostics never substitute a rejected OCR candidate for positive pause evidence.
    if (ocr->without_det) {
        RegionOCRer analyzer(frame.image);
        analyzer.set_task_info(ocr);
        analyzer.set_required({});
        if (const auto candidate = analyzer.analyze()) {
            diagnostic["candidates"].as_array().emplace_back(candidate->to_json());
        }
    }
    else {
        OCRer analyzer(frame.image);
        analyzer.set_task_info(ocr);
        analyzer.set_required({});
        if (const auto candidates = analyzer.analyze()) {
            for (const auto& candidate : *candidates) {
                diagnostic["candidates"].as_array().emplace_back(candidate.to_json());
            }
        }
    }
    return diagnostic;
}

bool asst::BattleControlTask::settle_pause(const Command& command, CapturedFrame& frame, json::value& snapshot)
{
    int attempts = 0;
    while (true) {
        if (m_pause_unverified) {
            collect_safety_evidence(command, frame, snapshot, 3000);
        }
        if (snapshot.get("scene", "") == "paused") {
            return !m_pause_unverified;
        }
        if (snapshot.get("scene", "") != "running" || input_blocked() || attempts >= 2) {
            return false;
        }
        ++attempts;
        pause();
        frame = capture_frame();
        snapshot = observe(frame);
    }
}

void asst::BattleControlTask::collect_safety_evidence(
    const Command& command,
    CapturedFrame& frame,
    json::value& snapshot,
    int duration_ms)
{
    const auto until =
        std::min(command.deadline, std::chrono::steady_clock::now() + std::chrono::milliseconds(duration_ms));
    while (input_blocked() && m_terminal_result.get("terminal_state", "unknown") == "unknown" &&
           m_terminal_result.get("result_source", "") != "conflicting_titles" && !m_cancel_requested && !need_exit() &&
           std::chrono::steady_clock::now() + m_frame_cost < until) {
        {
            std::unique_lock lock(m_command_mutex);
            m_command_cv.wait_for(lock, std::chrono::milliseconds(100), [&] {
                return m_cancel_requested.load() || need_exit();
            });
        }
        if (m_cancel_requested || need_exit() || std::chrono::steady_clock::now() + m_frame_cost >= until) {
            break;
        }
        frame = capture_frame();
        snapshot = observe(frame);
    }
}

json::value asst::BattleControlTask::preparation(const CapturedFrame& frame)
{
    if (frame.image.empty()) {
        return failure("screenshot_failed", "Preparation screenshot is empty");
    }
    BattleFormationAnalyzer analyzer(frame.image);
    const auto formation = analyzer.analyze();
    json::value snapshot = json::object {
        { "observation_id", std::to_string(++m_observation_id) },
        { "captured_at_ms",
          std::chrono::duration_cast<std::chrono::milliseconds>(frame.captured_at.time_since_epoch()).count() },
        { "page", formation ? "formation" : "unknown" },
        { "operators", json::array() },
        { "source", "preparation_vision" },
        { "input_sent", false }
    };
    if (formation) {
        for (const auto& oper : *formation) {
            json::array ids;
            for (const auto& id : BattleData.get_ids(battle::Role::Unknown, oper.name)) {
                ids.emplace_back(id);
            }
            snapshot["operators"].as_array().emplace_back(
                json::object {
                    { "name", oper.name },
                    { "game_ids", std::move(ids) },
                    { "name_rect", json::array { oper.rect.x, oper.rect.y, oper.rect.width, oper.rect.height } },
                    { "support", nullptr },
                    { "equipped_skill_id", nullptr },
                    { "actual_elite", nullptr },
                    { "actual_level", nullptr },
                    { "verification", "name_observed_configuration_unknown" } });
        }
    }
    auto result = save_evidence(frame, std::move(snapshot), true);
    if (!formation) {
        result["status"] = "failed";
        result["error"] = json::object { { "code", "preparation_not_recognized" },
                                         { "message", "No supported formation page recognized; no input sent" } };
        LogWarn << __FUNCTION__ << result;
    }
    return result;
}

json::value asst::BattleControlTask::execute_speed(const Command& command, CapturedFrame frame, json::value snapshot)
{
    if (snapshot.get("scene", "") != "paused") {
        return failure("pause_required", "Speed changes require a positively verified pause; no automatic resume");
    }
    const auto before = save_evidence(frame, snapshot, true);
    if (before.get("status", "") != "succeeded") {
        return before;
    }
    const auto desired = command.args.at("multiplier").as_integer();
    const auto observed = snapshot.at("speed").find<int>("value");
    if (!observed) {
        auto result = failure("speed_unknown", "Speed could not be positively identified; no input sent");
        result["snapshot"] = snapshot;
        result["image_path"] = before.at("image_path");
        result["pause_verified"] = true;
        result["safe_state"] = "paused";
        return result;
    }
    bool sent = false;
    bool unexpected_resume = false;
    if (*observed != desired) {
        if (m_cancel_requested || std::chrono::steady_clock::now() >= command.deadline) {
            return failure("deadline_exceeded", "Speed request expired before input");
        }
        sent = speed_up();
        frame = capture_frame();
        snapshot = observe(frame);
        unexpected_resume = snapshot.get("scene", "") == "running";
        if (unexpected_resume) {
            settle_pause(command, frame, snapshot);
        }
    }
    const bool verified = !unexpected_resume && snapshot.get("scene", "") == "paused" && !input_blocked() &&
                          snapshot.at("speed").find<int>("value") == desired;
    if (!verified) {
        m_terminal_seen = true;
        update_input_state(snapshot);
    }
    auto result = save_evidence(frame, snapshot, true);
    result["status"] = verified && result.get("status", "") == "succeeded" ? "succeeded" : "outcome_unknown";
    result["input_attempted"] = *observed != desired;
    result["helper_returned"] = sent;
    result["unexpected_resume"] = unexpected_resume;
    result["requested_speed"] = desired;
    result["observed_speed"] = snapshot.contains("speed") && snapshot.at("speed").is_object()
                                   ? snapshot.at("speed").at("value")
                                   : json::value(nullptr);
    result["pause_verified"] = snapshot.get("scene", "") == "paused";
    result["safe_state"] = snapshot.get("scene", "") == "paused" ? "paused" : "unknown";
    result["before_image_path"] = before.at("image_path");
    if (!verified) {
        result["error"] = json::object { { "code", "speed_unverified" },
                                         { "message", "Speed or pause changed unexpectedly; further input blocked" } };
    }
    result["session_input_blocked"] = input_blocked();
    if (std::chrono::steady_clock::now() >= command.deadline) {
        result["status"] = "failed";
        result["error"] =
            json::object { { "code", "deadline_exceeded" },
                           { "message",
                             "Speed verification exceeded deadline; inspect evidence before another command" } };
    }
    return result;
}

json::value asst::BattleControlTask::execute_wait(const Command& command, CapturedFrame frame, json::value snapshot)
{
    const auto started = command.started;
    const auto duration = std::chrono::milliseconds(command.args.at("duration_ms").as_integer());
    std::string error;
    bool pause_verified = false;
    bool resumed = false;
    const auto initial_scene = snapshot.get("scene", "unknown");
    std::optional<std::chrono::steady_clock::time_point> resumed_at;
    if (initial_scene == "running") {
        resumed_at = command.started;
    }
    const auto reserve = std::max(std::chrono::milliseconds(1000), m_frame_cost * 2);
    std::deque<std::pair<CapturedFrame, json::value>> recent;
    auto remember = [&] {
        recent.emplace_back(frame, snapshot);
        if (recent.size() > 8) {
            recent.pop_front();
        }
    };
    auto cancelled = [&] {
        return m_cancel_requested.load() || need_exit();
    };
    if (input_blocked() || (snapshot.get("scene", "") != "running" && snapshot.get("scene", "") != "paused")) {
        remember();
    }
    else if (
        snapshot.get("scene", "") == "paused" && !m_pause_requested &&
        !command.args.at("adopt_manual_pause").as_boolean()) {
        error = "pause_state_unknown";
    }
    else if (cancelled()) {
        error = "cancelled";
    }
    else if (std::chrono::steady_clock::now() + duration + reserve >= command.deadline) {
        error = "deadline_exceeded";
    }
    else {
        if (!resumed_at) {
            resumed_at = std::chrono::steady_clock::now();
        }
        if (snapshot.get("scene", "") == "paused") {
            m_pause_requested = true;
            pause();
            frame = capture_frame();
            snapshot = observe(frame, false, false);
        }
        resumed = snapshot.get("scene", "") == "running" && !input_blocked();
        if (resumed) {
            m_pause_requested = false;
            const auto until = std::chrono::steady_clock::now() + duration;
            while (true) {
                remember();
                if (snapshot.get("scene", "") != "running" || input_blocked()) {
                    if (snapshot.get("scene", "") == "paused") {
                        error = "wait_interrupted";
                    }
                    else if (m_pause_unverified) {
                        error = "pause_unverified";
                    }
                    break;
                }
                if (cancelled()) {
                    error = "cancelled";
                    break;
                }
                const auto now = std::chrono::steady_clock::now();
                if (now + reserve >= command.deadline) {
                    error = "deadline_exceeded";
                    break;
                }
                if (now >= until) {
                    break;
                }
                {
                    std::unique_lock lock(m_command_mutex);
                    m_command_cv.wait_until(
                        lock,
                        std::min({ until, command.deadline - reserve, now + std::chrono::milliseconds(200) }),
                        cancelled);
                }
                frame = capture_frame();
                snapshot = observe(frame, false, false);
            }
        }
        else if (!m_terminal_seen) {
            error = m_pause_unverified ? "pause_unverified" : "resume_unverified";
        }
    }

    const auto boundary_frame = frame;
    const auto boundary_snapshot = snapshot;
    pause_verified = settle_pause(command, frame, snapshot);
    if (m_terminal_seen) {
        collect_safety_evidence(command, frame, snapshot, 1000);
    }
    const auto settled_at = std::chrono::steady_clock::now();
    pause_verified = snapshot.get("scene", "") == "paused" && !m_pause_unverified;
    // Refusing manual adoption must not grant ownership as a side effect.
    m_pause_requested = pause_verified && !input_blocked() && error != "pause_state_unknown";
    snapshot["pause_requested"] = m_pause_requested;
    auto terminal = m_terminal_seen ? m_terminal_result : snapshot.at("terminal");
    const auto state = terminal.at("terminal_state").as_string();
    const bool terminal_verified = (state == "victory" || state == "defeat") && terminal.contains("image_path");
    const bool left_battle = m_terminal_seen;
    json::array evidence_errors;
    auto save = [&](const CapturedFrame& saved_frame, json::value saved_snapshot) {
        auto evidence = save_evidence(saved_frame, std::move(saved_snapshot), left_battle);
        if (evidence.at("status").as_string() != "succeeded") {
            evidence_errors.emplace_back(evidence.at("error"));
        }
        return evidence.get("image_path", "");
    };
    json::array samples;
    for (const auto& [sample, sample_snapshot] : recent) {
        if (sample_snapshot.at("observation_id") == boundary_snapshot.at("observation_id")) {
            continue;
        }
        samples.emplace_back(
            json::object {
                { "captured_at_ms", sample_snapshot.at("captured_at_ms") },
                { "image_path", save(sample, sample_snapshot) },
            });
    }
    const auto boundary_path = save(boundary_frame, boundary_snapshot);
    const auto return_path = save(frame, snapshot);
    if (cancelled()) {
        error = "cancelled";
    }
    if (error.empty() && std::chrono::steady_clock::now() >= command.deadline) {
        error = "deadline_exceeded";
    }
    const bool safe = (pause_verified && !input_blocked()) || terminal_verified;
    json::value result = json::object {
        { "status",
          !error.empty()                    ? "failed"
          : safe && evidence_errors.empty() ? "succeeded"
                                            : "outcome_unknown" },
        { "requested_duration_ms", duration.count() },
        { "duration_basis", "host_monotonic_not_game_simulation_time" },
        { "actual_observed_envelope_ms",
          std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - started).count() },
        { "resumed", resumed },
        { "initial_scene", initial_scene },
        { "cleanup_reserve_ms", reserve.count() },
        { "resumed_interval_upper_bound_ms",
          resumed_at
              ? json::value(std::chrono::duration_cast<std::chrono::milliseconds>(settled_at - *resumed_at).count())
              : json::value(nullptr) },
        { "deadline_margin_ms",
          std::chrono::duration_cast<std::chrono::milliseconds>(command.deadline - std::chrono::steady_clock::now())
              .count() },
        { "pause_verified", pause_verified },
        { "safe_state",
          terminal_verified                    ? "terminal"
          : pause_verified && !input_blocked() ? "paused"
                                               : "unknown" },
        { "pause_not_applicable", terminal_verified },
        { "session_input_blocked", input_blocked() },
        { "terminal", terminal },
        { "capture_time_snapshot", boundary_snapshot },
        { "return_time_snapshot", snapshot },
        { "capture_image_path", boundary_path },
        { "return_image_path", return_path },
        { "terminal_evidence_path", terminal_verified ? terminal.get("image_path", "") : "" },
        { "samples", std::move(samples) },
        { "evidence_errors", std::move(evidence_errors) },
        { "warnings",
          left_battle && !terminal_verified
              ? json::array { "Observed a non-battle frame without terminal proof; further session input is blocked" }
              : json::array() },
    };
    if (!error.empty()) {
        result["error"] = json::object { { "code", error },
                                         { "message", "Wait did not complete; inspect return safety and evidence" } };
        LogWarn << __FUNCTION__ << error;
    }
    return result;
}

json::value asst::BattleControlTask::execute_step(const Command& command, json::value snapshot)
{
    const auto& args = command.args;
    const auto action = args.at("action").as_string();
    const auto target = args.at("unit_id").as_string();
    const bool adopt_manual_pause = args.at("adopt_manual_pause").as_boolean();

    auto deployment_oper = m_cur_deployment_opers.end();
    auto battlefield_oper = m_battlefield_opers.end();
    Point location;
    battle::DeployDirection direction = battle::DeployDirection::None;
    std::string summon_game_id;
    const bool may_own_pause = m_pause_requested || adopt_manual_pause || snapshot.get("scene", "") == "running";
    auto reject_preflight = [&](const std::string& code, const std::string& message) {
        json::value result = failure(code, message);
        CapturedFrame final_frame;
        json::value final_snapshot;
        final_frame = capture_frame();
        final_snapshot = observe(final_frame, false, false);
        const bool pause_verified = settle_pause(command, final_frame, final_snapshot);
        m_pause_requested = pause_verified && !input_blocked() && may_own_pause;
        final_snapshot["pause_requested"] = m_pause_requested;
        result["pause_verified"] = pause_verified;
        result["safe_state"] = pause_verified && !input_blocked() ? "paused" : "unknown";
        result["snapshot"] = final_snapshot;
        auto evidence = save_evidence(final_frame, final_snapshot);
        if (evidence.at("status").as_string() == "succeeded") {
            result["return_image_path"] = evidence.at("image_path");
        }
        else {
            result["evidence_error"] = evidence.at("error");
        }
        return result;
    };

    if (action == "deploy") {
        deployment_oper = std::ranges::find_if(m_cur_deployment_opers, [&](const auto& oper) {
            return unit_id(oper.role, oper.name) == target;
        });
        const auto matches = std::ranges::count_if(m_cur_deployment_opers, [&](const auto& oper) {
            return unit_id(oper.role, oper.name) == target;
        });
        if (matches != 1 || deployment_oper == m_cur_deployment_opers.end()) {
            return reject_preflight("unit_not_unique", "Deployment unit is missing or ambiguous; identify first");
        }
        if (deployment_oper->role == battle::Role::Drone) {
            const auto card = m_cards.find(args.get("card_id", ""));
            const auto ids = BattleData.get_ids(deployment_oper->role, deployment_oper->name);
            if (ids.size() != 1 || !m_allowed_summons.contains(*ids.begin())) {
                return reject_preflight(
                    "unsupported_unit",
                    "Summon deployment requires a unique game ID in the trusted startup allowlist");
            }
            summon_game_id = *ids.begin();
            if (card == m_cards.end() || card->second.tag.name != deployment_oper->name ||
                card->second.tag.role != deployment_oper->role || card->second.index != deployment_oper->index ||
                card->second.rect != deployment_oper->rect ||
                std::chrono::steady_clock::now() - card->second.seen > std::chrono::seconds(30)) {
                return reject_preflight("stale_card", "Summon card is stale or changed; capture fresh evidence");
            }
        }
        if (!deployment_oper->available || deployment_oper->cooling) {
            return reject_preflight("unit_unavailable", "Unit is cooling down or cannot currently be deployed");
        }
        const auto& requested_location = args.at("location");
        location = { requested_location[0].as_integer(), requested_location[1].as_integer() };
        const auto tile = m_normal_tile_info.find(location);
        if (tile == m_normal_tile_info.end() || m_used_tiles.contains(location) ||
            tile->second.buildable == battle::LocationType::None ||
            tile->second.buildable == battle::LocationType::Invalid ||
            deployment_oper->location_type == battle::LocationType::None ||
            (tile->second.buildable != battle::LocationType::All &&
             deployment_oper->location_type != battle::LocationType::All &&
             tile->second.buildable != deployment_oper->location_type)) {
            return reject_preflight("invalid_tile", "Tile is occupied, unknown or incompatible with this unit");
        }
        const std::map<std::string, battle::DeployDirection> directions {
            { "Right", battle::DeployDirection::Right }, { "Down", battle::DeployDirection::Down },
            { "Left", battle::DeployDirection::Left },   { "Up", battle::DeployDirection::Up },
            { "None", battle::DeployDirection::None },
        };
        direction = directions.at(args.at("direction").as_string());
    }
    else {
        if (target.starts_with("instance:")) {
            return reject_preflight("unsupported_instance_action", "Summon skill and retreat are not supported");
        }
        battlefield_oper = std::ranges::find_if(m_battlefield_opers, [&](const auto& pair) {
            return unit_id(pair.first.role, pair.first.name) == target;
        });
        if (battlefield_oper == m_battlefield_opers.end()) {
            return reject_preflight("unit_not_tracked", "Unit has no tracked battlefield position");
        }
    }

    if (m_cancel_requested) {
        return reject_preflight("cancelled", "Step cancelled before action");
    }
    if (std::chrono::steady_clock::now() >= command.deadline) {
        return reject_preflight("deadline_exceeded", "Step expired during preflight; no action was sent");
    }

    if (snapshot.get("scene", "") == "paused") {
        if (!m_pause_requested && !adopt_manual_pause) {
            return failure("pause_state_unknown", "Adopting a manual pause requires explicit authorization");
        }
        m_pause_requested = true;
        pause();
        const auto resumed_frame = capture_frame();
        const auto resumed_snapshot = observe(resumed_frame, false, false);
        const bool resumed_running = resumed_snapshot.get("scene", "") == "running" && !input_blocked();
        if (!resumed_running) {
            const bool pause_verified = resumed_snapshot.get("scene", "") == "paused" && !m_pause_unverified;
            m_pause_requested = pause_verified && !input_blocked();
            return json::object {
                { "status", pause_verified ? "failed" : "outcome_unknown" },
                { "error",
                  json::object {
                      { "code", "resume_unverified" },
                      { "message", "The requested action was not sent because resume could not be verified" },
                  } },
                { "pause_verified", pause_verified },
                { "snapshot", resumed_snapshot },
            };
        }
        m_pause_requested = false;
    }
    else {
        m_pause_requested = false;
    }

    bool action_returned = false;
    if (!m_cancel_requested && std::chrono::steady_clock::now() < command.deadline) {
        if (action == "deploy") {
            // Observation refreshes the deployment vector; do not retain its iterator across resume.
            deployment_oper = std::ranges::find_if(m_cur_deployment_opers, [&](const auto& oper) {
                return unit_id(oper.role, oper.name) == target;
            });
            if (deployment_oper == m_cur_deployment_opers.end() || !deployment_oper->available ||
                deployment_oper->cooling || std::ranges::count_if(m_cur_deployment_opers, [&](const auto& oper) {
                                                return unit_id(oper.role, oper.name) == target;
                                            }) != 1) {
                return reject_preflight("unit_unavailable", "Deployment card changed after resume");
            }
            const auto role = deployment_oper->role;
            const auto name = deployment_oper->name;
            if (role == battle::Role::Drone) {
                const auto card = m_cards.find(args.get("card_id", ""));
                if (card == m_cards.end() || card->second.index != deployment_oper->index ||
                    card->second.rect != deployment_oper->rect) {
                    return reject_preflight("stale_card", "Summon card layout changed after resume");
                }
                m_summons.emplace_back(
                    json::object { { "unit_id", "instance:" + command.id },
                                   { "operation_id", command.id },
                                   { "name", name },
                                   { "game_ids", json::array { summon_game_id } },
                                   { "role", static_cast<int>(role) },
                                   { "location", json::array { location.x, location.y } },
                                   { "direction", args.at("direction") },
                                   { "direction_source", "action_history_unverified" },
                                   { "position_source", "action_history_unverified" },
                                   { "lifecycle", "deployment_attempted_unverified" },
                                   { "parent_id", nullptr },
                                   { "skill_ready", nullptr },
                                   { "helper_returned", nullptr } });
            }
            action_returned = deploy_oper(role, name, location, direction);
            if (role == battle::Role::Drone) {
                m_summons[m_summons.size() - 1]["helper_returned"] = action_returned;
                m_battlefield_opers.erase(battle::OperNameTag { role, name });
                m_used_tiles.emplace(location, battle::OperNameTag { role, name });
            }
            else if (action_returned) {
                m_directions[target] = args.at("direction").as_string();
            }
        }
        else {
            battlefield_oper = std::ranges::find_if(m_battlefield_opers, [&](const auto& pair) {
                return unit_id(pair.first.role, pair.first.name) == target;
            });
            if (battlefield_oper == m_battlefield_opers.end()) {
                return reject_preflight("unit_not_tracked", "Tracked unit changed after resume");
            }
            action_returned = action == "retreat"
                                  ? retreat_oper(battlefield_oper->first.role, battlefield_oper->first.name)
                                  : use_skill(battlefield_oper->second, 0);
        }
    }

    const auto boundary_frame = capture_frame();
    auto capture_snapshot = observe(boundary_frame);
    auto return_frame = boundary_frame;
    auto return_snapshot = capture_snapshot;
    const bool pause_verified = settle_pause(command, return_frame, return_snapshot);
    m_pause_requested = pause_verified && !input_blocked();
    capture_snapshot["pause_requested"] = false;
    auto capture_result = save_evidence(boundary_frame, capture_snapshot);
    return_snapshot["pause_requested"] = m_pause_requested;
    auto return_result = save_evidence(return_frame, return_snapshot);
    json::array evidence_errors;
    if (capture_result.at("status").as_string() == "failed") {
        evidence_errors.emplace_back(capture_result.at("error"));
    }
    if (return_result.at("status").as_string() == "failed") {
        evidence_errors.emplace_back(return_result.at("error"));
    }

    return json::object {
        { "status", "outcome_unknown" },
        { "action_returned", action_returned },
        { "message", "Action input may have occurred. Inspect both captures; never blindly retry." },
        { "capture_time_snapshot", capture_snapshot },
        { "return_time_snapshot", std::move(return_snapshot) },
        { "pause_verified", pause_verified },
        { "safe_state",
          m_terminal_result.contains("image_path") && (m_terminal_result.get("terminal_state", "") == "victory" ||
                                                       m_terminal_result.get("terminal_state", "") == "defeat")
              ? "terminal"
          : pause_verified && !input_blocked() ? "paused"
                                               : "unknown" },
        { "session_input_blocked", input_blocked() },
        { "terminal", m_terminal_result },
        { "evidence_errors", std::move(evidence_errors) },
        { "capture_image_path", capture_result.get("image_path", "") },
        { "return_image_path", return_result.get("image_path", "") },
    };
}

json::value asst::BattleControlTask::execute(const Command& command)
{
    const bool waiting = command.action == "step" && command.args.get("action", "") == "wait";
    if (!waiting && (need_exit() || std::chrono::steady_clock::now() >= command.deadline)) {
        return failure("deadline_exceeded", "Command expired before execution");
    }
    if (command.action == "bind" && m_preparation_only) {
        if (!set_stage_name(command.args.at("stage_name").as_string()) || !calc_tiles_info(m_stage_name) ||
            m_normal_tile_info.empty() || m_has_multi_stages) {
            return failure("unsupported_stage", "Cannot bind this fixed map");
        }
        m_preparation_only = false;
        return json::object { { "status", "succeeded" }, { "protocol_version", 1 }, { "map", map_info() } };
    }
    if (m_preparation_only) {
        return command.action == "prepare"
                   ? preparation(capture_frame())
                   : failure("preparation_only", "Only preparation capture or explicit battle binding is allowed");
    }
    if (command.action == "prepare") {
        return failure("already_attached", "Preparation capture is only available before battle binding");
    }
    if (command.action == "map") {
        return json::object { { "status", "succeeded" }, { "map", map_info() } };
    }
    const bool already_blocked = input_blocked();
    auto frame = capture_frame();
    auto snapshot = observe(frame, false, command.action != "step");
    if (command.action == "capture") {
        return save_evidence(frame, std::move(snapshot));
    }
    if (command.action == "observe") {
        return json::object { { "status", "succeeded" }, { "snapshot", std::move(snapshot) } };
    }
    if (command.action == "finalize") {
        if (!already_blocked && !input_blocked()) {
            return failure("terminal_not_pending", "Read-only terminal collection requires blocked battle input");
        }
        collect_safety_evidence(command, frame, snapshot, command.args.at("duration_ms").as_integer());
        auto result = save_evidence(frame, snapshot, true);
        result["terminal"] = m_terminal_result;
        result["session_input_blocked"] = input_blocked();
        result["pause_verified"] = snapshot.get("scene", "") == "paused" && !m_pause_unverified;
        result["input_sent"] = false;
        return result;
    }
    if (m_terminal_seen) {
        if (waiting && !already_blocked) {
            return execute_wait(command, std::move(frame), std::move(snapshot));
        }
        return failure("battle_ended", "Session input is blocked; inspect scene and terminal evidence");
    }
    if (already_blocked || m_pause_unverified) {
        auto result = failure(
            "pause_unverified",
            "Input was blocked at command admission; collect read-only pause evidence before a new command");
        result["snapshot"] = snapshot;
        result["session_input_blocked"] = input_blocked();
        result["input_sent"] = false;
        return result;
    }
    if (waiting) {
        return execute_wait(command, std::move(frame), std::move(snapshot));
    }
    if (snapshot.get("scene", "") != "running" && snapshot.get("scene", "") != "paused") {
        return failure("not_in_battle", "Battle not recognized; no input sent");
    }
    if (command.action == "set_speed") {
        return execute_speed(command, std::move(frame), std::move(snapshot));
    }
    if (command.action == "step") {
        return execute_step(command, std::move(snapshot));
    }
    if (std::chrono::steady_clock::now() >= command.deadline) {
        return failure("deadline_exceeded", "Observation exhausted command deadline; no input sent");
    }
    const bool running = snapshot.get("scene", "") == "running";
    bool sent = false;
    if (command.action == "set_paused") {
        const bool requested = command.args.at("paused").as_boolean();
        if (!requested && running) {
            m_pause_requested = false;
            snapshot["pause_requested"] = false;
            return json::object { { "status", "succeeded" }, { "snapshot", std::move(snapshot) } };
        }
        if (requested && !running) {
            return json::object { { "status", "succeeded" }, { "pause_verified", true }, { "snapshot", snapshot } };
        }
        if (!requested && !m_pause_requested) {
            return failure("pause_state_unknown", "Cannot safely toggle an unrecognized pause state");
        }
        sent = pause();
        frame = capture_frame();
        snapshot = observe(frame);
        if (requested && m_pause_unverified) {
            collect_safety_evidence(command, frame, snapshot, 3000);
        }
        const bool verified = !input_blocked() && snapshot.get("scene", "") == (requested ? "paused" : "running");
        m_pause_requested = verified && requested;
        snapshot["pause_requested"] = m_pause_requested;
        auto result = save_evidence(frame, snapshot);
        result["status"] = verified && result.get("status", "") == "succeeded" ? "succeeded" : "outcome_unknown";
        result["pause_verified"] = snapshot.get("scene", "") == "paused" && !m_pause_unverified;
        result["helper_returned"] = sent;
        return result;
    }
    else if (command.action == "identify") {
        if (!running && !m_pause_requested && !command.args.get("adopt_manual_pause", false)) {
            return failure("pause_state_unknown", "Adopting a manual pause requires explicit authorization");
        }
        auto finish_identification = [&](json::value result, bool pause_verified) {
            m_pause_requested = pause_verified && !input_blocked();
            snapshot["pause_requested"] = m_pause_requested;
            result["pause_verified"] = pause_verified;
            result["safe_state"] = pause_verified && !input_blocked() ? "paused" : "unknown";
            result["snapshot"] = snapshot;
            auto evidence = save_evidence(frame, snapshot);
            if (evidence.at("status").as_string() == "succeeded") {
                result["image_path"] = evidence.at("image_path");
            }
            else {
                result["evidence_error"] = evidence.at("error");
                if (result.get("status", "") == "succeeded") {
                    result["status"] = "outcome_unknown";
                }
            }
            return result;
        };
        if (running) {
            if (!settle_pause(command, frame, snapshot) || input_blocked()) {
                auto result =
                    failure("pause_unverified", "Identification was not started because pause could not be verified");
                result["status"] = "outcome_unknown";
                return finish_identification(std::move(result), false);
            }
        }
        m_pause_requested = true;
        BattlefieldMatcher matcher(frame.image);
        matcher.set_object_of_interest({ .deployment = true, .oper_cost = true });
        bool identified = false;
        if (auto result = matcher.analyze()) {
            auto deployment = result->deployment;
            const auto old_deployment = m_cur_deployment_opers;
            update_deployment_(deployment, old_deployment, true);
            identified = true;
        }
        // Leave the battle paused; resuming is always a separate explicit command.
        cancel_oper_selection();
        frame = capture_frame();
        snapshot = observe(frame, true);
        const bool pause_verified = settle_pause(command, frame, snapshot);
        if (!identified) {
            return finish_identification(
                failure("identification_failed", "Deployment cards could not be analyzed"),
                pause_verified);
        }
        return finish_identification(
            json::object {
                { "status", pause_verified ? "succeeded" : "outcome_unknown" },
                { "identification_status",
                  snapshot.at("unresolved_count").as_integer() == 0 && snapshot.at("ambiguous_count").as_integer() == 0
                      ? "complete"
                      : "partial" },
                { "identified_count", snapshot.at("identified_count") },
                { "unresolved_count", snapshot.at("unresolved_count") },
                { "ambiguous_count", snapshot.at("ambiguous_count") },
                { "deployment", snapshot.at("deployment") },
            },
            pause_verified);
    }
    else {
        if (!running || m_pause_requested) {
            return failure("battle_not_running", "Resume the battle before deploying, retreating or using a skill");
        }
        const auto target = command.args.at("unit_id").as_string();
        if (command.action == "deploy") {
            auto matching = m_cur_deployment_opers | std::views::filter([&](const auto& oper) {
                                return unit_id(oper.role, oper.name) == target;
                            });
            if (std::ranges::distance(matching) != 1) {
                return failure("unit_not_unique", "Deployment unit is missing or ambiguous; identify first");
            }
            const auto oper = *matching.begin();
            if (oper.role == battle::Role::Drone) {
                return failure("unsupported_unit", "Summons and devices require instance-level identity tracking");
            }
            if (!oper.available || oper.cooling) {
                return failure("unit_unavailable", "Unit is cooling down or cannot currently be deployed");
            }
            const auto& location = command.args.at("location");
            const Point loc { location[0].as_integer(), location[1].as_integer() };
            const auto tile = m_normal_tile_info.find(loc);
            if (tile == m_normal_tile_info.end() || m_used_tiles.contains(loc) ||
                tile->second.buildable == battle::LocationType::None ||
                tile->second.buildable == battle::LocationType::Invalid ||
                oper.location_type == battle::LocationType::None ||
                (tile->second.buildable != battle::LocationType::All &&
                 oper.location_type != battle::LocationType::All && tile->second.buildable != oper.location_type)) {
                return failure("invalid_tile", "Tile is occupied, unknown or incompatible with this unit");
            }
            const std::map<std::string, battle::DeployDirection> directions {
                { "Right", battle::DeployDirection::Right }, { "Down", battle::DeployDirection::Down },
                { "Left", battle::DeployDirection::Left },   { "Up", battle::DeployDirection::Up },
                { "None", battle::DeployDirection::None },
            };
            sent = deploy_oper(oper.role, oper.name, loc, directions.at(command.args.at("direction").as_string()));
            if (sent) {
                m_directions[target] = command.args.at("direction").as_string();
            }
        }
        else {
            const auto unit = std::ranges::find_if(m_battlefield_opers, [&](const auto& pair) {
                return unit_id(pair.first.role, pair.first.name) == target;
            });
            if (unit == m_battlefield_opers.end()) {
                return failure("unit_not_tracked", "Unit has no tracked battlefield position");
            }
            const auto tag = unit->first;
            if (command.action == "retreat") {
                sent = retreat_oper(tag.role, tag.name);
            }
            else {
                sent = use_skill(tag.role, tag.name, 0);
            }
        }
    }
    return json::object {
        { "status", "outcome_unknown" },
        { "helper_returned", sent },
        { "message", "Input may have been sent. Inspect fresh evidence; do not blindly retry." },
        { "snapshot", observe(capture_frame()) },
    };
}
