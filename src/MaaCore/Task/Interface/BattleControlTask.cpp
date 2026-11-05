#include "BattleControlTask.h"

#include <algorithm>
#include <cctype>
#include <ranges>
#include <thread>

#include "Config/Miscellaneous/BattleDataConfig.h"
#include "Controller/Controller.h"
#include "MaaUtils/ImageIo.h"
#include "Utils/Logger.hpp"
#include "Vision/Battle/BattlefieldClassifier.h"
#include "Vision/Battle/BattlefieldMatcher.h"

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
    if (id.empty() || id.size() > 128 || timeout.as_integer() < 1 || timeout.as_integer() > 30'000) {
        return false;
    }
    const auto action = params.at("action").as_string();
    const auto& args = params.at("args");
    if (action == "observe" || action == "identify" || action == "capture" || action == "map") {
        return true;
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
    m_command =
        Command { params.at("command_id").as_string(),
                  params.at("action").as_string(),
                  params.at("args"),
                  std::chrono::steady_clock::now() + std::chrono::milliseconds(params.at("timeout_ms").as_integer()) };
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

json::value asst::BattleControlTask::observe(const cv::Mat& image)
{
    json::value snapshot = json::object {
        { "observation_id", std::to_string(++m_observation_id) },
        { "stage_id", m_map_data.key.stageId },
        { "captured_at_ms",
          std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch())
              .count() },
        { "in_battle", false },
        { "running_button_visible", false },
        { "pause_requested", m_pause_requested },
        { "cost", nullptr },
        { "kills", nullptr },
        { "total_kills", nullptr },
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
    matcher.set_object_of_interest({ .costs = true });
    if (auto cost = matcher.analyze()) {
        snapshot["cost"] = cost->costs.value;
    }
    matcher.set_object_of_interest({ .kills = true });
    if (auto kills = matcher.analyze()) {
        snapshot["kills"] = kills->kills.value.first;
        snapshot["total_kills"] = kills->kills.value.second;
    }
    for (const auto& oper : m_cur_deployment_opers) {
        json::array ids;
        for (const auto& id : BattleData.get_ids(oper.role, oper.name)) {
            ids.emplace_back(id);
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
            });
    }
    for (const auto& [tag, loc] : m_battlefield_opers) {
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
                { "unit_id", unit_id(tag.role, tag.name) },
                { "name", tag.name },
                { "role", static_cast<int>(tag.role) },
                { "location", json::array { loc.x, loc.y } },
                { "position_source", "action_history_unverified" },
                { "skill_ready", std::move(ready) },
            });
    }
    return snapshot;
}

json::value asst::BattleControlTask::execute(const Command& command)
{
    if (need_exit() || std::chrono::steady_clock::now() >= command.deadline) {
        return failure("deadline_exceeded", "Command expired before execution");
    }
    if (command.action == "map") {
        return json::object { { "status", "succeeded" }, { "map", map_info() } };
    }
    auto image = ctrler()->get_image();
    auto snapshot = observe(image);
    if (command.action == "capture") {
        if (image.empty()) {
            return failure("screenshot_failed", "No image available");
        }
        const auto relative = utils::path("battle-control") / utils::path(m_session_id) /
                              utils::path(snapshot.at("observation_id").as_string() + ".png");
        const auto file = UserDir.get() / relative;
        if (!MAA_NS::imwrite(file, image)) {
            return failure("evidence_write_failed", "Could not save screenshot");
        }
        m_evidence_files.emplace_back(file);
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
    if (command.action == "observe") {
        return json::object { { "status", "succeeded" }, { "snapshot", std::move(snapshot) } };
    }
    if (!snapshot.at("in_battle").as_boolean()) {
        return failure("not_in_battle", "Battle not recognized; no input sent");
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
        if (!running && !m_pause_requested) {
            return failure("pause_state_unknown", "Identify requires a running battle or a server-requested pause");
        }
        if (running && !pause()) {
            return failure("pause_failed", "Could not pause for active identification");
        }
        m_pause_requested = true;
        image = ctrler()->get_image();
        BattlefieldMatcher matcher(image);
        matcher.set_object_of_interest({ .deployment = true, .oper_cost = true });
        if (auto result = matcher.analyze()) {
            auto deployment = result->deployment;
            const auto old_deployment = m_cur_deployment_opers;
            sent = update_deployment_(deployment, old_deployment, true);
        }
        // Leave the battle paused; resuming is always a separate explicit command.
        cancel_oper_selection();
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
        { "snapshot", observe(ctrler()->get_image()) },
    };
}
