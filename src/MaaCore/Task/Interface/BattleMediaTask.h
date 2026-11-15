#pragma once

#include "Task/InterfaceTask.h"

namespace asst
{
class BattleMediaTask final : public InterfaceTask
{
public:
    inline static constexpr std::string_view TaskType = "BattleMedia";

    BattleMediaTask(const AsstCallback& callback, Assistant* inst) :
        InterfaceTask(callback, inst, TaskType)
    {
        set_retry_times(0);
    }

    bool set_params(const json::value& params) override;

    bool run() override { return AbstractTask::run(); }

protected:
    bool _run() override;

private:
    std::string m_filename;
    int m_timeout_ms = 30'000;
};
}
