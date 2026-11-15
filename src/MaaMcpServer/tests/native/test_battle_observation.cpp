#include <cstdlib>
#include <iostream>
#include <limits>

#include "MaaUtils/ImageIo.h"
#include "Task/Interface/BattleControlTask.h"
#include "Utils/BattlefieldObservation.h"
#include "Utils/WorkingDir.hpp"

void expect_observation(bool condition, const char* name)
{
    if (!condition) {
        std::cerr << "Failed: " << name << '\n';
        std::exit(1);
    }
}

namespace asst
{
struct BattleControlObservationTest
{
    static void command_contracts()
    {
        BattleControlTask task({}, nullptr);
        json::value request = json::object {
            { "command_id", "pause" },
            { "action", "step" },
            { "timeout_ms", 5000 },
            { "args", json::object { { "action", "pause" }, { "adopt_manual_pause", false } } },
        };
        expect_observation(task.validate_command(request), "pause step has no unit or duration");
        request["args"]["unit_id"] = "not-allowed";
        expect_observation(!task.validate_command(request), "pause rejects unit-specific fields");
        request["args"] = json::object {
            { "action", "speed" },
            { "adopt_manual_pause", false },
            { "multiplier", 2 },
        };
        expect_observation(task.validate_command(request), "speed step has no unit");
        request["args"]["multiplier"] = 3;
        expect_observation(!task.validate_command(request), "unsupported speed rejected");
        request["args"]["multiplier"] = 1;
        request["args"]["require_running"] = "true";
        expect_observation(!task.validate_command(request), "legacy running guard must be boolean");
        request["args"]["require_running"] = true;
        expect_observation(task.validate_command(request), "boolean legacy running guard accepted");
        request["action"] = "evaluate_placement";
        request["args"] = json::object {
            { "placements",
              json::array {
                  json::object {
                      { "operator_game_id", "unknown" },
                      { "location", json::array { 0, 0 } },
                      { "direction", "Right" },
                      { "elite", nullptr },
                      { "elite_source", "unknown" },
                  },
              } },
        };
        expect_observation(task.validate_command(request), "native geometry command accepted");

        const auto now = std::chrono::steady_clock::now();
        const BattleControlTask::Command geometry {
            "geometry", "evaluate_placement", request.at("args"), now, now + std::chrono::seconds(5),
        };
        task.m_preparation_only = true;
        auto result = task.execute(geometry);
        expect_observation(result.get("error", "code", "") == "preparation_only", "geometry requires attachment");
        task.m_preparation_only = false;
        result = task.execute(geometry);
        expect_observation(
            result.get("error", "code", "") == "invalid_map_geometry",
            "geometry reaches native validation without a controller or screenshot");
    }

    static void instance_contracts()
    {
        BattleControlTask task({}, nullptr);
        auto& instance = task.m_instances["instance:attempt"];
        instance.id = "instance:attempt";
        instance.operation_id = "attempt";
        instance.game_id = "unknown";
        instance.tag = { battle::Role::Warrior, "name" };
        instance.location = { 1, 1 };
        instance.presence = BattleControlTask::Presence::Unknown;
        instance.helper_returned = true;
        instance.selection_evidence.resize(2);
        for (auto& sample : instance.selection_evidence) {
            sample.accepted = true;
            sample.name = "name";
            sample.game_ids = { "unknown" };
            sample.button_detected = true;
        }
        const auto info = task.instance_info(instance);
        expect_observation(info.get("selected_kind_present", false), "two selected identities retained as evidence");
        expect_observation(
            !info.get("location_verified", true),
            "selected identity and helper return are not a tile anchor");
        expect_observation(info.get("presence", "") == "unknown", "helper and identity do not prove owned deployment");
        expect_observation(
            info.at("selection_evidence")[0].at("retreat_button_available").is_null(),
            "button template detection does not prove enabled state");
        expect_observation(task.tile_reserved({ 1, 1 }), "unresolved attempt does not silently release occupancy");
        auto& other = task.m_instances["instance:other"];
        other.id = "instance:other";
        other.game_id = "unknown";
        other.location = { 5, 5 };
        expect_observation(task.selection_ambiguous(instance), "distant same-kind attempt prevents identity ownership");
        other.game_id = "other";
        other.location = { 2, 1 };
        expect_observation(task.selection_ambiguous(instance), "neighbour attempt preserves snap ambiguity");
        other.presence = BattleControlTask::Presence::Gone;
        expect_observation(!task.selection_ambiguous(instance), "departed neighbour is not a live ambiguity");
    }

    static void pause_ownership()
    {
        const auto snapshot = [](const std::string& scene) {
            return json::object {
                { "observation_id", "pause-fixture" },
                { "captured_at_ms", 1234 },
                { "scene", scene },
                { "in_battle", true },
                { "cost", nullptr },
                { "kills", nullptr },
                { "total_kills", nullptr },
                { "deployment", json::array() },
                { "battlefield", json::array() },
            };
        };
        const auto now = std::chrono::steady_clock::now();
        BattleControlTask::Command command {
            "pause-fixture",
            "step",
            json::object { { "action", "pause" }, { "adopt_manual_pause", false } },
            now,
            now + std::chrono::seconds(5),
        };
        BattleControlTask task({}, nullptr);
        auto result = task.execute_pause(command, {}, snapshot("paused"));
        expect_observation(result.get("pause_verified", false), "existing manual pause can be verified without input");
        expect_observation(!task.m_pause_requested, "verifying a manual pause does not grant resume ownership");
        expect_observation(!result.get("input_attempted", true), "manual pause verification sends no pause input");
        expect_observation(
            result.get("status", "") == "outcome_unknown" &&
                result.get("evidence_error", "code", "") == "screenshot_failed",
            "failed evidence is retained and prevents successful pause result");
        task.m_pause_requested = true;
        result = task.execute_pause(command, {}, snapshot("paused"));
        expect_observation(task.m_pause_requested, "previously owned pause remains owned");
        task.m_pause_requested = false;
        command.args["adopt_manual_pause"] = true;
        result = task.execute_pause(command, {}, snapshot("paused"));
        expect_observation(task.m_pause_requested, "explicit adoption grants ownership of verified manual pause");
        task.m_pause_requested = false;
        command.args["adopt_manual_pause"] = false;
        command.deadline = now - std::chrono::seconds(1);
        result = task.execute_pause(command, {}, snapshot("running"));
        expect_observation(
            result.get("error", "code", "") == "deadline_exceeded" && !result.get("input_sent", true),
            "direct pause request checks action deadline before input");
        expect_observation(
            result.get("evidence_error", "code", "") == "screenshot_failed",
            "deadline error does not overwrite failed screenshot evidence");
        command.deadline = now + std::chrono::seconds(5);
        task.m_cancel_requested = true;
        result = task.execute_pause(command, {}, snapshot("running"));
        expect_observation(
            result.get("error", "code", "") == "cancelled" &&
                result.get("evidence_error", "code", "") == "screenshot_failed" && !result.get("input_sent", true),
            "cancelled direct pause sends no input and preserves evidence failure");
    }

    static void original_frame_and_retention()
    {
        const auto root = std::filesystem::current_path() /
                          utils::path(
                              "battle-observation-regression-" +
                              std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
        expect_observation(std::filesystem::create_directory(root), "create isolated evidence fixture directory");
        expect_observation(UserDir.set(root), "set evidence fixture directory");
        BattleControlTask task({}, nullptr);
        task.m_session_id = "native-fixture";
        const auto now = std::chrono::steady_clock::now();
        const BattleControlTask::CapturedFrame frame {
            cv::Mat(8, 8, CV_8UC3, cv::Scalar(11, 77, 201)),
            std::chrono::system_clock::now(),
            now,
        };
        const auto snapshot = [](const std::string& id) {
            return json::object { { "observation_id", id }, { "captured_at_ms", 1234 } };
        };
        const auto original = task.save_evidence(frame, snapshot("original"));
        expect_observation(original.get("status", "") == "succeeded", "save exact observation frame");
        const auto original_path = root / utils::path(original.at("image_path").as_string());
        const auto pixels = MAA_NS::imread(original_path);
        expect_observation(
            !pixels.empty() && cv::norm(pixels, frame.image, cv::NORM_INF) == 0,
            "image_path reads identical pixels rather than a subsequent capture");
        expect_observation(
            original.at("snapshot").get("captured_at_ms", 0) == 1234,
            "same observation capture timestamp retained");
        task.save_evidence(frame, snapshot("original"));
        expect_observation(task.m_evidence_files.size() == 1, "same frame is queued only once");
        task.save_evidence(frame, snapshot("original"), true);
        expect_observation(task.m_evidence_files.empty(), "important evidence promotion removes ordinary queue entry");
        task.save_evidence(frame, snapshot("original"));
        expect_observation(task.m_evidence_files.empty(), "important frame cannot be accidentally demoted");
        for (int i = 0; i < 129; ++i) {
            task.save_evidence(frame, snapshot("ordinary-" + std::to_string(i)));
        }
        expect_observation(task.m_evidence_files.size() == 128, "ordinary evidence bounded to 128 frames");
        expect_observation(
            !std::filesystem::exists(root / utils::path("battle-control/native-fixture/ordinary-0.png")),
            "oldest ordinary evidence evicted");
        expect_observation(std::filesystem::exists(original_path), "important frame survives ordinary eviction");
        task.m_terminal_seen = true;
        task.save_evidence(frame, snapshot("blocked-observation"));
        expect_observation(
            task.m_evidence_files.size() == 128 &&
                task.m_evidence_files.back().filename() == utils::path("blocked-observation.png"),
            "ordinary read-only observations remain bounded while input is blocked");
        expect_observation(std::filesystem::remove_all(root) > 0, "remove fixture evidence");
    }
};
}

int main()
{
#ifndef ASST_DEBUG
    std::cerr << "This regression must run in a Debug build\n";
    return 1;
#endif
    using namespace asst;
    const Rect bounds(0, 0, 1280, 720);
    const Rect move(0, -50, 60, 60);
    const std::vector<std::pair<Point, Point>> tiles {
        { { 2, 1 }, { 130, 110 } },
        { { 1, 1 }, { 110, 110 } },
    };
    auto projected = battle::project_field_unit_candidates({ { { 100, 155, 40, 5 }, 0.99 } }, tiles, move, bounds);
    expect_observation(
        projected.size() == 1 && projected[0].locations.size() == 2 && !projected[0].unique(),
        "all tile candidates retained instead of taking first neighbour");
    expect_observation(projected[0].locations[0] == Point(1, 1), "candidate ordering independent of tile iteration");
    const std::vector<std::pair<Point, Point>> one_tile { tiles.front() };
    projected = battle::project_field_unit_candidates({ { { 100, 155, 40, 5 }, 0.99 } }, one_tile, move, bounds);
    expect_observation(projected.size() == 1 && projected[0].unique(), "unique geometric candidate reported");
    projected = battle::project_field_unit_candidates(
        { { { 100, 155, 40, 5 }, 0.99 }, { { 105, 155, 40, 5 }, 0.98 } },
        one_tile,
        move,
        bounds);
    expect_observation(
        projected.size() == 2 && projected[0].contested && projected[1].contested && !projected[0].unique() &&
            !projected[1].unique(),
        "two positive detections cannot claim the same tile uniquely");
    projected = battle::project_field_unit_candidates(
        { { { 100, 155, 40, 5 }, std::numeric_limits<double>::quiet_NaN() },
          { { 100, 155, 40, 5 }, 0.89 },
          { { 100, 155, 40, 5 }, 1.1 },
          { { -1, 155, 40, 5 }, 0.99 } },
        tiles,
        move,
        bounds);
    expect_observation(projected.empty(), "weak nonfinite or out-of-frame detections rejected");
    projected = battle::project_field_unit_candidates({ { { 100, 5, 40, 5 }, 0.99 } }, tiles, move, bounds);
    expect_observation(
        projected.size() == 1 && projected[0].locations.empty() && !projected[0].unique(),
        "clipped projection is not a tile anchor");
    BattleControlObservationTest::command_contracts();
    BattleControlObservationTest::instance_contracts();
    BattleControlObservationTest::pause_ownership();
    BattleControlObservationTest::original_frame_and_retention();
    std::cout << "Native battle observation contract regressions passed (synthetic fixtures, not game validation)\n";
}
