#include "BattleControlTask.h"

#include "Config/GeneralConfig.h"
#include "Utils/Platform.hpp"

asst::BattleControlTask::Recording asst::BattleControlTask::recording_options(const json::value& options) const
{
    Recording recording;
    if (options.is_object()) {
        recording.id = options.get("artifact_id", "");
        recording.interval_ms = options.get("interval_ms", 500);
        recording.max_frames = options.get("max_frames", 24);
    }
    return recording;
}

void asst::BattleControlTask::sample_recording(Recording& recording, const CapturedFrame& frame, bool final) const
{
    if (recording.id.empty()) {
        return;
    }
    if (frame.image.empty()) {
        recording.partial_reason = "empty_screenshot";
        return;
    }
    if (!recording.frames.empty() &&
        (frame.started <= recording.last ||
         (!final && frame.started - recording.last < std::chrono::milliseconds(recording.interval_ms)))) {
        return;
    }
    const size_t bytes = frame.image.total() * frame.image.elemSize();
    const size_t reserve = std::min(battle::MediaRawLimit, bytes * recording.reserve_frames);
    const size_t raw_limit = battle::MediaRawLimit - reserve;
    if (bytes > raw_limit) {
        recording.partial_reason = "raw_buffer_limit";
        return;
    }
    while (final && !recording.frames.empty() &&
           (recording.frames.size() >= recording.max_frames || recording.raw_bytes > raw_limit - bytes)) {
        recording.raw_bytes -= recording.frames.back().image.total() * recording.frames.back().image.elemSize();
        recording.frames.pop_back();
        recording.partial_reason = "frame_or_buffer_limit";
    }
    if (recording.frames.size() >= recording.max_frames || recording.raw_bytes > raw_limit ||
        bytes > raw_limit - recording.raw_bytes) {
        recording.partial_reason = "frame_count_or_raw_buffer_limit";
        return;
    }
    if (recording.frames.empty()) {
        recording.first = frame.started;
    }
    try {
        recording.frames.emplace_back(
            battle::MediaFrame {
                frame.image.clone(),
                std::chrono::duration_cast<std::chrono::milliseconds>(frame.captured_at.time_since_epoch()).count(),
                std::chrono::duration_cast<std::chrono::milliseconds>(frame.started - recording.first).count(),
            });
    }
    catch (const cv::Exception&) {
        recording.partial_reason = "buffer_failure";
        return;
    }
    catch (const std::bad_alloc&) {
        recording.partial_reason = "buffer_failure";
        return;
    }
    recording.last = frame.started;
    recording.raw_bytes += bytes;
}

json::value asst::BattleControlTask::finish_recording(const Command& command, Recording& recording) const
{
    if (recording.id.empty()) {
        return nullptr;
    }
    json::value result;
    try {
        result = battle::write_recording(
            UserDir.get() / utils::path("battle-media") / utils::path(recording.id),
            recording.frames,
            command.deadline,
            [&] { return m_cancel_requested.load() || need_exit(); });
    }
    catch (const cv::Exception&) {
        result = json::object {
            { "status", "unavailable" },
            { "frames", json::array() },
            { "error", "native_media_codec_error" },
            { "timeline", "native_host_acquisition_not_game_simulation_time" },
            { "video", json::object { { "status", "unavailable" }, { "reason", "native_media_codec_error" } } },
        };
    }
    result["artifact_id"] = recording.id;
    result["source_kind"] = "recorded_mid_battle";
    result["declared_context"] = "mid_battle";
    result["context_verified"] = false;
    result["raw_buffer_bytes"] = recording.raw_bytes;
    result["raw_buffer_limit_bytes"] = battle::MediaRawLimit;
    result["raw_buffer_reserve_frames"] = recording.reserve_frames;
    if (!recording.partial_reason.empty()) {
        result["status"] = "partial";
        result["sampling_error"] = recording.partial_reason;
    }
    return result;
}

json::value asst::BattleControlTask::execute_record(const Command& command, CapturedFrame frame, json::value snapshot)
{
    auto recording = recording_options(command.args);
    const auto initial_scene = snapshot.get("scene", "unknown");
    const auto requested_until =
        std::chrono::steady_clock::now() + std::chrono::milliseconds(command.args.get("duration_ms", 1000));
    const auto until = std::min(command.deadline - std::chrono::seconds(1), requested_until);
    bool running_seen = initial_scene == "running";
    while (!m_cancel_requested && !need_exit()) {
        sample_recording(recording, frame);
        running_seen = running_seen || snapshot.get("scene", "") == "running";
        const auto now = std::chrono::steady_clock::now();
        if (now >= until) {
            break;
        }
        {
            std::unique_lock lock(m_command_mutex);
            m_command_cv.wait_until(lock, std::min(until, now + std::chrono::milliseconds(recording.interval_ms)), [&] {
                return m_cancel_requested.load() || need_exit();
            });
        }
        if (m_cancel_requested || need_exit()) {
            break;
        }
        frame = capture_frame();
        snapshot = observe(frame, false, false);
    }
    if (std::chrono::steady_clock::now() < requested_until && !m_cancel_requested && !need_exit()) {
        recording.partial_reason = "capture_deadline_before_requested_duration";
    }
    sample_recording(recording, frame, true);
    complete_runtime_evidence(command, frame, snapshot);
    auto result = save_evidence(frame, snapshot);
    result["media"] = finish_recording(command, recording);
    result["input_sent"] = false;
    result["initial_scene"] = initial_scene;
    result["final_scene"] = snapshot.get("scene", "unknown");
    result["run_pause_input_preserved"] = true;
    result["running_game_keeps_advancing"] = running_seen;
    result["requested_duration_ms"] = command.args.get("duration_ms", 1000);
    result["duration_basis"] = "host_monotonic_not_game_simulation_time";
    if (result.get("status", "") == "succeeded" && result["media"].get("status", "") != "complete") {
        result["status"] = "partial";
    }
    if (m_cancel_requested || need_exit()) {
        result["status"] = "partial";
        result["error"] = json::object { { "code", "cancelled" },
                                         { "message", "Read-only capture cancelled; partial evidence retained" } };
    }
    return result;
}
