#pragma once

#include <chrono>
#include <functional>

#include "Common/AsstTypes.h"
#include "MaaUtils/NoWarningCV.hpp"

namespace asst::battle
{
struct MediaFrame
{
    cv::Mat image;
    int64_t captured_at_ms = 0;
    int64_t offset_monotonic_ms = 0;
};

inline constexpr size_t MediaRawLimit = 64 * 1024 * 1024;
inline constexpr size_t MediaPublishedLimit = 32 * 1024 * 1024;

json::value write_recording(
    const std::filesystem::path& root,
    const std::vector<MediaFrame>& frames,
    std::chrono::steady_clock::time_point deadline,
    const std::function<bool()>& cancelled);

json::value inspect_video(
    const std::filesystem::path& root,
    const std::string& filename,
    std::chrono::steady_clock::time_point deadline,
    const std::function<bool()>& cancelled);
}
