#include "BattleMediaTask.h"

#include "Config/GeneralConfig.h"
#include "Utils/BattleMedia.h"
#include "Utils/WorkingDir.hpp"

bool asst::BattleMediaTask::set_params(const json::value& params)
{
    if (m_running) {
        return false;
    }
    const auto filename = params.find<std::string>("filename");
    const auto timeout = params.find<int>("timeout_ms");
    if (!filename || !timeout || *timeout < 1 || *timeout > 120'000 ||
        (*filename != "input.avi" && *filename != "input.mp4" && *filename != "input.mov" &&
         *filename != "input.mkv")) {
        return false;
    }
    m_filename = *filename;
    m_timeout_ms = *timeout;
    return true;
}

bool asst::BattleMediaTask::_run()
{
    auto result = battle::inspect_video(
        UserDir.get(),
        m_filename,
        std::chrono::steady_clock::now() + std::chrono::milliseconds(m_timeout_ms),
        [&] { return need_exit(); });
    auto info = basic_info_with_what("BattleMediaResult");
    info["details"] = std::move(result);
    callback(AsstMsg::SubTaskExtraInfo, info);
    return true;
}
