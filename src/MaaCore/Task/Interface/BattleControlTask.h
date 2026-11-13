#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <optional>
#include <unordered_map>
#include <unordered_set>

#include "Task/BattleHelper.h"
#include "Task/InterfaceTask.h"

namespace asst
{
class BattleControlTask final : public InterfaceTask, private BattleHelper
{
public:
    inline static constexpr std::string_view TaskType = "BattleControl";

    BattleControlTask(const AsstCallback& callback, Assistant* inst);
    bool set_params(const json::value& params) override;
    bool run() override;

protected:
    AbstractTask& this_task() override { return *this; }

    bool _run() override;

private:
    struct Command
    {
        std::string id;
        std::string action;
        json::value args;
        std::chrono::steady_clock::time_point started;
        std::chrono::steady_clock::time_point deadline;
    };

    struct CapturedFrame
    {
        cv::Mat image;
        std::chrono::system_clock::time_point captured_at;
        std::chrono::steady_clock::time_point started;
    };

    struct Card
    {
        battle::OperNameTag tag;
        Rect rect;
        size_t index;
        std::chrono::steady_clock::time_point seen;
    };

    bool validate_command(const json::value& params) const;
    CapturedFrame capture_frame();
    json::value
        observe(const CapturedFrame& frame, bool active_identification = false, bool include_battlefield = true);
    json::value save_evidence(const CapturedFrame& frame, json::value snapshot, bool retain = false);
    json::value execute(const Command& command);
    json::value execute_step(const Command& command, json::value snapshot);
    json::value execute_wait(const Command& command, CapturedFrame frame, json::value snapshot);
    json::value execute_speed(const Command& command, CapturedFrame frame, json::value snapshot);
    json::value preparation(const CapturedFrame& frame);
    json::value inspect_preparation(const Command& command);
    bool settle_pause(const Command& command, CapturedFrame& frame, json::value& snapshot);
    void collect_safety_evidence(
        const Command& command,
        CapturedFrame& frame,
        json::value& snapshot,
        std::chrono::milliseconds duration);

    bool input_blocked() const { return m_terminal_seen || m_pause_unverified; }

    void update_input_state(json::value& snapshot);
    json::value pause_diagnostics(
        const CapturedFrame& frame,
        const json::value& snapshot,
        const std::string& task_name,
        const json::value& match) const;
    json::value terminal_info(const CapturedFrame& frame, const json::value& snapshot) const;
    json::value split_victory_evidence(const cv::Mat& image) const;
    json::value match_evidence(const cv::Mat& image, const std::string& task) const;
    json::value speed_info(const cv::Mat& image) const;
    json::value map_info() const;
    json::value failure(const std::string& code, const std::string& message) const;
    void emit(const std::string& what, json::value details);
    static std::string unit_id(battle::Role role, const std::string& name);

    std::mutex m_command_mutex;
    std::condition_variable m_command_cv;
    std::optional<Command> m_command;
    bool m_accepting = false;
    bool m_initialized = false;
    bool m_pause_requested = false;
    bool m_terminal_seen = false;
    bool m_pause_unverified = false;
    int m_pause_confirmations = 0;
    bool m_battle_observed = false;
    bool m_preparation_only = false;
    std::chrono::milliseconds m_frame_cost { 0 };
    json::value m_terminal_result = json::object { { "terminal_state", "unknown" },
                                                   { "terminal_confidence", nullptr },
                                                   { "result_source", "unrecognized" } };
    std::unordered_set<std::string> m_allowed_summons;
    std::unordered_map<std::string, Card> m_cards;
    json::array m_summons;
    std::atomic_bool m_cancel_requested = false;
    std::string m_active_command_id;
    std::string m_session_id;
    uint64_t m_observation_id = 0;
    std::deque<std::filesystem::path> m_evidence_files;
    std::unordered_map<std::string, std::string> m_directions;
};
}
