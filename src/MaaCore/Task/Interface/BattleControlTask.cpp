#include "BattleControlTask.h"

#include <algorithm>
#include <cctype>
#include <ranges>
#include <thread>
#include <unordered_map>

#include "Config/Miscellaneous/BattleDataConfig.h"
#include "Controller/Controller.h"
#include "MaaUtils/ImageIo.h"
#include "Utils/Logger.hpp"
#include "Vision/Battle/BattlefieldClassifier.h"
#include "Vision/Battle/BattlefieldMatcher.h"
#include "Vision/Miscellaneous/PipelineAnalyzer.h"

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
    const auto max_timeout = action == "step" ? 10'000 : 30'000;
    if (id.empty() || id.size() > 128 || timeout.as_integer() < 1 || timeout.as_integer() > max_timeout) {
        return false;
    }
    const auto& args = params.at("args");
    if (action == "identify") {
        return !args.contains("adopt_manual_pause") || args.find<bool>("adopt_manual_pause").has_value();
    }
    if (action == "observe" || action == "capture" || action == "map") {
        return true;
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
        if (!params.find<std::string>("stage_name") || !params.find<std::string>("session_id")) {
            LogError << __FUNCTION__ << "Missing battle session parameters";
            return false;
        }
        const auto session = params.at("session_id").as_string();
        if (session.empty() || session.size() > 64 ||
            !std::ranges::all_of(session, [](unsigned char ch) { return std::isalnum(ch) || ch == '-'; }) ||
            !set_stage_name(params.at("stage_name").as_string())) {
            LogError << __FUNCTION__ << "Invalid session or unsupported stage";
            return false;
        }
        m_session_id = session;
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
    if (!calc_tiles_info(m_stage_name) || m_normal_tile_info.empty() || m_has_multi_stages) {
        LogError << __FUNCTION__ << "Unsupported or multi-stage battle map";
        return false;
    }
    {
        std::lock_guard lock(m_command_mutex);
        m_accepting = true;
    }
    emit("BattleControlReady", json::object { { "protocol_version", 1 }, { "map", map_info() } });
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
    auto image = ctrler()->get_image();
    const auto captured_at = std::chrono::system_clock::now();
    return { std::move(image), captured_at };
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
        return snapshot;
    }
    BattlefieldMatcher matcher(image);
    matcher.set_object_of_interest({ .deployment = true, .oper_cost = true });
    const auto result = matcher.analyze();
    if (!result) {
        snapshot["error"] = "battle_not_recognized";
        return snapshot;
    }
    snapshot["in_battle"] = true;
    snapshot["running_button_visible"] = check_pause_button(image);
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
        snapshot["deployment"].as_array().emplace_back(
            json::object {
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
            json::value ready = nullptr;
            if (const auto tile = m_normal_tile_info.find(loc);
                tile != m_normal_tile_info.end() && Rect(0, 0, image.cols, image.rows).include(tile->second.pos)) {
                BattlefieldClassifier classifier(image);
                classifier.set_object_of_interest({ .skill_ready = true });
                classifier.set_base_point(tile->second.pos);
                if (auto skill = classifier.analyze()) {
                    ready = skill->skill_ready.ready;
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
                });
        }
    }
    return snapshot;
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
    if (!retain) {
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
    if (snapshot.at("in_battle").as_boolean() || frame.image.empty()) {
        return result;
    }
    int matches = 0;
    for (const auto& [task, state] :
         { std::pair { "BattleControlVictory", "victory" }, std::pair { "BattleControlDefeat", "defeat" } }) {
        PipelineAnalyzer analyzer(frame.image);
        analyzer.set_tasks({ task });
        if (const auto match = analyzer.analyze()) {
            double score = 0;
            if (const auto* ocr = std::get_if<OCRer::Result>(&match->result)) {
                score = ocr->score;
            }
            else if (const auto* templ = std::get_if<Matcher::Result>(&match->result)) {
                score = templ->score;
            }
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

json::value asst::BattleControlTask::execute_wait(const Command& command, CapturedFrame frame, json::value snapshot)
{
    const auto started = command.started;
    const auto duration = std::chrono::milliseconds(command.args.at("duration_ms").as_integer());
    std::string error;
    bool pause_verified = false;
    bool resumed = false;
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
    if (!snapshot.at("in_battle").as_boolean()) {
        remember();
    }
    else if (
        !snapshot.at("running_button_visible").as_boolean() && !m_pause_requested &&
        !command.args.at("adopt_manual_pause").as_boolean()) {
        error = "pause_state_unknown";
    }
    else if (cancelled()) {
        error = "cancelled";
    }
    else if (std::chrono::steady_clock::now() + duration >= command.deadline) {
        error = "deadline_exceeded";
    }
    else {
        if (!snapshot.at("running_button_visible").as_boolean()) {
            m_pause_requested = true;
            pause();
            frame = capture_frame();
            snapshot = observe(frame, false, false);
        }
        resumed = snapshot.at("in_battle").as_boolean() && snapshot.at("running_button_visible").as_boolean();
        if (resumed) {
            m_pause_requested = false;
            const auto until = std::chrono::steady_clock::now() + duration;
            while (true) {
                remember();
                if (!snapshot.at("in_battle").as_boolean() || !snapshot.at("running_button_visible").as_boolean()) {
                    if (snapshot.at("in_battle").as_boolean()) {
                        error = "wait_interrupted";
                    }
                    break;
                }
                if (cancelled()) {
                    error = "cancelled";
                    break;
                }
                const auto now = std::chrono::steady_clock::now();
                if (now >= command.deadline) {
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
                        std::min({ until, command.deadline, now + std::chrono::milliseconds(200) }),
                        cancelled);
                }
                frame = capture_frame();
                snapshot = observe(frame, false, false);
            }
        }
        else if (snapshot.at("in_battle").as_boolean()) {
            error = "resume_unverified";
        }
    }

    const auto boundary_frame = frame;
    const auto boundary_snapshot = snapshot;
    if (snapshot.at("in_battle").as_boolean() && snapshot.at("running_button_visible").as_boolean()) {
        pause();
    }
    for (int attempt = 0; attempt < 2; ++attempt) {
        frame = capture_frame();
        snapshot = observe(frame);
        if (!snapshot.at("in_battle").as_boolean()) {
            break;
        }
        if (!snapshot.at("running_button_visible").as_boolean()) {
            pause_verified = true;
            break;
        }
        if (attempt == 0) {
            pause();
        }
    }
    // Refusing manual adoption must not grant ownership as a side effect.
    m_pause_requested = pause_verified && error != "pause_state_unknown";
    snapshot["pause_requested"] = m_pause_requested;
    auto terminal = terminal_info(boundary_frame, boundary_snapshot);
    const bool boundary_terminal =
        terminal.at("terminal_state").as_string() == "victory" || terminal.at("terminal_state").as_string() == "defeat";
    if (terminal.at("terminal_state").as_string() == "still_in_battle" ||
        terminal.at("terminal_state").as_string() == "unknown") {
        terminal = terminal_info(frame, snapshot);
    }
    const auto state = terminal.at("terminal_state").as_string();
    const bool terminal_verified = state == "victory" || state == "defeat";
    const bool left_battle = !boundary_snapshot.at("in_battle").as_boolean() || !snapshot.at("in_battle").as_boolean();
    m_terminal_seen = left_battle;
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
    const bool safe = (pause_verified && !left_battle) || terminal_verified;
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
        { "pause_verified", pause_verified },
        { "safe_state",
          terminal_verified ? "terminal"
          : pause_verified  ? "paused"
                            : "unknown" },
        { "pause_not_applicable", terminal_verified },
        { "session_input_blocked", m_terminal_seen },
        { "terminal", terminal },
        { "capture_time_snapshot", boundary_snapshot },
        { "return_time_snapshot", snapshot },
        { "capture_image_path", boundary_path },
        { "return_image_path", return_path },
        { "terminal_evidence_path", terminal_verified ? (boundary_terminal ? boundary_path : return_path) : "" },
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
    const bool initially_running = snapshot.at("running_button_visible").as_boolean();
    auto reject_preflight = [&](const std::string& code, const std::string& message) {
        json::value result = failure(code, message);
        if (!initially_running) {
            result["pause_verified"] = true;
            result["safe_state"] = "paused";
            result["snapshot"] = snapshot;
            return result;
        }

        CapturedFrame final_frame;
        json::value final_snapshot;
        bool pause_verified = false;
        final_frame = capture_frame();
        if (!final_frame.image.empty() && check_pause_button(final_frame.image)) {
            pause();
        }
        for (int attempt = 0; attempt < 2; ++attempt) {
            final_frame = capture_frame();
            final_snapshot = observe(final_frame, false, false);
            if (!final_snapshot.at("in_battle").as_boolean()) {
                break;
            }
            if (!final_snapshot.at("running_button_visible").as_boolean()) {
                pause_verified = true;
                break;
            }
            if (attempt == 0) {
                pause();
            }
        }
        m_pause_requested = pause_verified;
        result["pause_verified"] = pause_verified;
        result["safe_state"] = pause_verified ? "paused" : "unknown";
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
            return reject_preflight("unsupported_unit", "Summons and devices require instance-level identity tracking");
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

    if (!snapshot.at("running_button_visible").as_boolean()) {
        if (!m_pause_requested && !adopt_manual_pause) {
            return failure("pause_state_unknown", "Adopting a manual pause requires explicit authorization");
        }
        m_pause_requested = true;
        pause();
        const auto resumed_frame = capture_frame();
        const bool resumed_running = !resumed_frame.image.empty() && check_pause_button(resumed_frame.image);
        if (!resumed_running) {
            const auto resumed_snapshot = observe(resumed_frame, false, false);
            const bool pause_verified = resumed_snapshot.at("in_battle").as_boolean() &&
                                        !resumed_snapshot.at("running_button_visible").as_boolean();
            m_pause_requested = pause_verified;
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
            action_returned = deploy_oper(deployment_oper->role, deployment_oper->name, location, direction);
            if (action_returned) {
                m_directions[target] = args.at("direction").as_string();
            }
        }
        else if (action == "retreat") {
            action_returned = retreat_oper(battlefield_oper->first.role, battlefield_oper->first.name);
        }
        else {
            action_returned = use_skill(battlefield_oper->second, 0);
        }
    }

    const auto boundary_frame = capture_frame();
    const bool capture_running = !boundary_frame.image.empty() && check_pause_button(boundary_frame.image);
    if (capture_running) {
        pause();
    }

    CapturedFrame return_frame;
    json::value return_snapshot;
    bool pause_verified = false;
    for (int attempt = 0; attempt < 2; ++attempt) {
        return_frame = capture_frame();
        return_snapshot = observe(return_frame);
        if (!return_snapshot.at("in_battle").as_boolean()) {
            break;
        }
        if (!return_snapshot.at("running_button_visible").as_boolean()) {
            pause_verified = true;
            break;
        }
        if (attempt == 0) {
            pause();
        }
    }
    m_pause_requested = pause_verified;
    auto capture_snapshot = observe(boundary_frame);
    capture_snapshot["running_button_visible"] = capture_running;
    capture_snapshot["pause_requested"] = false;
    auto capture_result = save_evidence(boundary_frame, capture_snapshot);
    if (pause_verified) {
        return_snapshot["pause_requested"] = true;
    }
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
        { "safe_state", pause_verified ? "paused" : "unknown" },
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
    if (command.action == "map") {
        return json::object { { "status", "succeeded" }, { "map", map_info() } };
    }
    auto frame = capture_frame();
    auto snapshot = observe(frame, false, command.action != "step");
    if (command.action == "capture") {
        return save_evidence(frame, std::move(snapshot));
    }
    if (command.action == "observe") {
        return json::object { { "status", "succeeded" }, { "snapshot", std::move(snapshot) } };
    }
    if (m_terminal_seen) {
        return failure("battle_ended", "This session left battle; no further input is permitted");
    }
    if (waiting) {
        return execute_wait(command, std::move(frame), std::move(snapshot));
    }
    if (!snapshot.at("in_battle").as_boolean()) {
        return failure("not_in_battle", "Battle not recognized; no input sent");
    }
    if (command.action == "step") {
        return execute_step(command, std::move(snapshot));
    }
    if (std::chrono::steady_clock::now() >= command.deadline) {
        return failure("deadline_exceeded", "Observation exhausted command deadline; no input sent");
    }
    const bool running = snapshot.at("running_button_visible").as_boolean();
    bool sent = false;
    if (command.action == "set_paused") {
        const bool requested = command.args.at("paused").as_boolean();
        if (!requested && running) {
            m_pause_requested = false;
            return json::object { { "status", "succeeded" }, { "snapshot", std::move(snapshot) } };
        }
        if ((requested && !running) || (!requested && !m_pause_requested)) {
            return failure("pause_state_unknown", "Cannot safely toggle an unrecognized pause state");
        }
        sent = pause();
        if (sent) {
            m_pause_requested = requested;
        }
    }
    else if (command.action == "identify") {
        if (!running && !m_pause_requested && !command.args.get("adopt_manual_pause", false)) {
            return failure("pause_state_unknown", "Adopting a manual pause requires explicit authorization");
        }
        auto finish_identification = [&](json::value result, bool pause_verified) {
            m_pause_requested = pause_verified;
            snapshot["pause_requested"] = m_pause_requested;
            result["pause_verified"] = pause_verified;
            result["safe_state"] = pause_verified ? "paused" : "unknown";
            result["snapshot"] = snapshot;
            auto evidence = save_evidence(frame, snapshot);
            if (evidence.at("status").as_string() == "succeeded") {
                result["image_path"] = evidence.at("image_path");
            }
            else {
                result["evidence_error"] = evidence.at("error");
            }
            return result;
        };
        if (running) {
            pause();
            frame = capture_frame();
            snapshot = observe(frame);
            if (snapshot.at("in_battle").as_boolean() && snapshot.at("running_button_visible").as_boolean()) {
                pause();
                frame = capture_frame();
                snapshot = observe(frame);
            }
            if (!snapshot.at("in_battle").as_boolean() || snapshot.at("running_button_visible").as_boolean()) {
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
        bool pause_verified = false;
        for (int attempt = 0; attempt < 2; ++attempt) {
            frame = capture_frame();
            snapshot = observe(frame, true);
            if (!snapshot.at("in_battle").as_boolean()) {
                break;
            }
            if (!snapshot.at("running_button_visible").as_boolean()) {
                pause_verified = true;
                break;
            }
            if (attempt == 0) {
                pause();
            }
        }
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
