#pragma once

#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <optional>

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
        std::chrono::steady_clock::time_point deadline;
    };

    struct CapturedFrame
    {
        cv::Mat image;
        std::chrono::system_clock::time_point captured_at;
    };

    bool validate_command(const json::value& params) const;
    CapturedFrame capture_frame();
    json::value
        observe(const CapturedFrame& frame, bool active_identification = false, bool include_battlefield = true);
    json::value save_evidence(const CapturedFrame& frame, json::value snapshot);
    json::value execute(const Command& command);
    json::value execute_step(const Command& command, json::value snapshot);
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
    std::string m_session_id;
    uint64_t m_observation_id = 0;
    std::deque<std::filesystem::path> m_evidence_files;
};
}
