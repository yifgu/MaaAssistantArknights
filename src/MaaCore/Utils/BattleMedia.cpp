#include "BattleMedia.h"

#include <fstream>

#include "Utils/Logger.hpp"
#include "Utils/Platform.hpp"

bool asst_media_png(const std::filesystem::path& path, const cv::Mat& image, size_t& budget, size_t& bytes)
{
    std::vector<uchar> encoded;
    if (!cv::imencode(".png", image, encoded) || encoded.size() > budget) {
        return false;
    }
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(encoded.data()), static_cast<std::streamsize>(encoded.size()));
    output.close();
    if (!output) {
        return false;
    }
    bytes = encoded.size();
    budget -= bytes;
    return true;
}

json::value asst::battle::write_recording(
    const std::filesystem::path& root,
    const std::vector<MediaFrame>& frames,
    std::chrono::steady_clock::time_point deadline,
    const std::function<bool()>& cancelled)
{
    json::value result = json::object {
        { "status", "partial" },
        { "kind", "sampled_frame_sequence" },
        { "frames", json::array() },
        { "timeline", "native_host_acquisition_not_game_simulation_time" },
        { "monotonic_offset_basis", "host_acquisition_start" },
        { "video", json::object { { "status", "unavailable" }, { "reason", "insufficient_frames_or_budget" } } },
        { "warnings", json::array { "Sparse sampled-frame video; rendered FPS is not game speed or simulation time" } },
    };
    std::error_code ec;
    std::filesystem::create_directories(root / utils::path("frames"), ec);
    if (ec) {
        result["error"] = "media_directory_io_failed";
        return result;
    }
    size_t budget = MediaPublishedLimit - 65'536;
    std::vector<const MediaFrame*> retained;
    for (size_t i = 0; i < frames.size() && i < 24; ++i) {
        if (std::chrono::steady_clock::now() >= deadline) {
            result["error"] = "media_encoding_deadline";
            break;
        }
        const auto& frame = frames[i];
        const auto relative = "frames/" + std::to_string(i) + ".png";
        size_t bytes = 0;
        if (frame.image.empty() || !asst_media_png(root / utils::path(relative), frame.image, budget, bytes)) {
            result["error"] = "media_png_io_or_byte_limit";
            break;
        }
        retained.emplace_back(&frame);
        result["frames"].as_array().emplace_back(
            json::object {
                { "frame_id", std::to_string(i) },
                { "path", relative },
                { "bytes", bytes },
                { "captured_at_ms", frame.captured_at_ms },
                { "offset_monotonic_ms", frame.offset_monotonic_ms },
                { "width", frame.image.cols },
                { "height", frame.image.rows },
                { "source", "original_native_capture" },
            });
    }
    if (cancelled()) {
        result["error"] = "cancelled_partial_recording";
    }
    if (retained.size() == frames.size() && !frames.empty() && !result.contains("error")) {
        result["status"] = "complete";
    }
    if (retained.size() < 2 || cancelled() ||
        std::chrono::steady_clock::now() + std::chrono::milliseconds(300) >= deadline) {
        return result;
    }
    const auto working = root / utils::path("encoding.avi");
    const auto video_path = root / utils::path("clip.avi");
    const auto span = retained.back()->offset_monotonic_ms - retained.front()->offset_monotonic_ms;
    const double fps = span > 0 ? std::clamp((retained.size() - 1) * 1000.0 / span, 1.0, 60.0) : 1.0;
    try {
        cv::VideoWriter writer(
            utils::path_to_crt_string(working),
            cv::CAP_OPENCV_MJPEG,
            cv::VideoWriter::fourcc('M', 'J', 'P', 'G'),
            fps,
            retained.front()->image.size());
        if (!writer.isOpened()) {
            result["video"]["reason"] = "native_mjpeg_avi_encoder_unavailable";
            return result;
        }
        bool complete = true;
        for (const auto* frame : retained) {
            if (cancelled() || std::chrono::steady_clock::now() >= deadline ||
                frame->image.size() != retained.front()->image.size()) {
                complete = false;
                break;
            }
            writer.write(frame->image);
            if (std::filesystem::file_size(working, ec) > budget || ec) {
                complete = false;
                break;
            }
        }
        writer.release();
        const auto video_bytes = std::filesystem::file_size(working, ec);
        if (!complete || ec || video_bytes > budget) {
            result["video"]["reason"] = "video_quota_deadline_or_geometry_changed";
            std::filesystem::remove(working, ec);
            return result;
        }
        cv::VideoCapture reader(utils::path_to_crt_string(working), cv::CAP_OPENCV_MJPEG);
        const double rendered_fps = reader.get(cv::CAP_PROP_FPS);
        cv::Mat decoded;
        size_t count = 0;
        while (!cancelled() && std::chrono::steady_clock::now() < deadline && reader.read(decoded)) {
            if (decoded.empty() || decoded.size() != retained.front()->image.size() || ++count > retained.size()) {
                break;
            }
        }
        reader.release();
        if (count != retained.size() || !std::isfinite(rendered_fps) || rendered_fps <= 0) {
            result["video"]["reason"] = "video_roundtrip_unverified";
            std::filesystem::remove(working, ec);
            return result;
        }
        std::filesystem::rename(working, video_path, ec);
        if (ec) {
            result["video"]["reason"] = "video_publication_io_failed";
            return result;
        }
        result["video"] = json::object {
            { "status", "ready" },
            { "path", "clip.avi" },
            { "codec", "MJPG" },
            { "mime_type", "video/x-msvideo" },
            { "bytes", video_bytes },
            { "rendered_fps", rendered_fps },
            { "decoded_frames_verified", count },
            { "timestamps_authoritative", "frames_not_rendered_fps" },
        };
    }
    catch (const cv::Exception& error) {
        LogWarn << __FUNCTION__ << "Optional media encoding failed" << error.what();
        result["video"]["reason"] = "native_video_codec_error";
        std::filesystem::remove(working, ec);
    }
    return result;
}

json::value asst::battle::inspect_video(
    const std::filesystem::path& root,
    const std::string& filename,
    std::chrono::steady_clock::time_point deadline,
    const std::function<bool()>& cancelled)
{
    json::value result = json::object {
        { "status", "failed" },
        { "frames", json::array() },
        { "timeline", "imported_media_pts_not_live_capture_time" },
        { "decoded_complete", false },
    };
    cv::VideoCapture reader(utils::path_to_crt_string(root / utils::path(filename)));
    if (!reader.isOpened()) {
        result["error"] = "native_video_decoder_unavailable";
        return result;
    }
    const double fps = reader.get(cv::CAP_PROP_FPS);
    const double header_frames = reader.get(cv::CAP_PROP_FRAME_COUNT);
    const double header_width = reader.get(cv::CAP_PROP_FRAME_WIDTH);
    const double header_height = reader.get(cv::CAP_PROP_FRAME_HEIGHT);
    result["decoder_backend"] = reader.getBackendName();
    const double tag = reader.get(cv::CAP_PROP_FOURCC);
    std::string codec;
    if (std::isfinite(tag) && tag >= 0 && tag <= 4'294'967'295.0) {
        const auto value = static_cast<uint32_t>(tag);
        for (int i = 0; i < 4; ++i) {
            const char character = static_cast<char>((value >> (i * 8)) & 255);
            if (character < 32 || character > 126) {
                codec.clear();
                break;
            }
            codec += character;
        }
    }
    result["reported_codec"] = codec.empty() ? json::value(nullptr) : json::value(codec);
    result["reported_frames"] = std::isfinite(header_frames) ? json::value(header_frames) : json::value(nullptr);
    result["reported_fps"] = std::isfinite(fps) ? json::value(fps) : json::value(nullptr);
    if (!std::isfinite(fps) || fps <= 0 || fps > 240 || !std::isfinite(header_frames) || header_frames < 1 ||
        header_frames > 18'000 || header_frames / fps > 600 || !std::isfinite(header_width) ||
        !std::isfinite(header_height) || header_width < 1 || header_height < 1 || header_width > 1920 ||
        header_height > 1080) {
        result["error"] = "video_header_limits_exceeded";
        return result;
    }
    std::error_code ec;
    std::filesystem::create_directories(root / utils::path("frames"), ec);
    if (ec) {
        result["error"] = "media_directory_io_failed";
        return result;
    }
    const size_t stride = std::max<size_t>(1, static_cast<size_t>(std::ceil(header_frames / 23)));
    size_t count = 0;
    size_t budget = MediaPublishedLimit;
    cv::Mat frame;
    cv::Mat last;
    double last_pts = 0;
    bool keyframes_stopped = false;
    auto retain = [&](const cv::Mat& image, size_t index, double pts, const std::string& basis) {
        const auto relative = "frames/" + std::to_string(index) + ".png";
        size_t bytes = 0;
        if (!asst_media_png(root / utils::path(relative), image, budget, bytes)) {
            return false;
        }
        result["frames"].as_array().emplace_back(
            json::object {
                { "frame_id", std::to_string(index) },
                { "path", relative },
                { "bytes", bytes },
                { "media_pts_ms", pts },
                { "media_time_basis", basis },
                { "width", image.cols },
                { "height", image.rows },
                { "source", "decoded_imported_video_frame" },
            });
        return true;
    };
    while (true) {
        if (cancelled() || std::chrono::steady_clock::now() >= deadline) {
            result["error"] = cancelled() ? "cancelled" : "video_inspection_deadline";
            break;
        }
        bool read = false;
        try {
            read = reader.read(frame);
        }
        catch (const cv::Exception&) {
            result["error"] = "native_video_read_error";
            break;
        }
        if (!read) {
            result["decoded_complete"] = count > 0 && count >= static_cast<size_t>(header_frames);
            if (!result.get("decoded_complete", false)) {
                result["error"] = "truncated_video_or_decoder_stopped_before_reported_end";
            }
            break;
        }
        ++count;
        if (frame.empty() || frame.cols > 1920 || frame.rows > 1080 || frame.cols != header_width ||
            frame.rows != header_height || count > 18'000) {
            result["error"] = "actual_decoded_video_limits_exceeded";
            break;
        }
        double pts = reader.get(cv::CAP_PROP_POS_MSEC);
        const bool decoder_pts = std::isfinite(pts) && pts >= 0 && (count == 1 || pts > last_pts);
        if (!decoder_pts) {
            pts = (count - 1) * 1000.0 / fps;
        }
        if (pts > 600'000 || (count - 1) / fps > 600) {
            result["error"] = "actual_decoded_video_duration_exceeded";
            break;
        }
        last_pts = pts;
        last = frame;
        if (!keyframes_stopped && (count - 1) % stride == 0 && result["frames"].as_array().size() < 23 &&
            !retain(frame, count - 1, pts, decoder_pts ? "decoder_pts" : "frame_index_over_reported_fps")) {
            result["keyframe_error"] = "keyframe_io_or_byte_limit";
            keyframes_stopped = true;
        }
    }
    if (result.get("decoded_complete", false) && !keyframes_stopped && !last.empty() &&
        !result["frames"].as_array().empty() &&
        result["frames"].as_array()[result["frames"].as_array().size() - 1].get("frame_id", "") !=
            std::to_string(count - 1) &&
        result["frames"].as_array().size() < 24 && !cancelled() && std::chrono::steady_clock::now() < deadline) {
        if (!retain(last, count - 1, last_pts, "last_decoded_media_frame")) {
            result["keyframe_error"] = "keyframe_io_or_byte_limit";
        }
    }
    result["actual_decoded_frames"] = count;
    result["actual_last_media_pts_ms"] = !last.empty() ? json::value(last_pts) : json::value(nullptr);
    if (result.get("decoded_complete", false) && !result.contains("error") && !result.contains("keyframe_error")) {
        result["status"] = "succeeded";
    }
    else if (!result["frames"].as_array().empty()) {
        result["status"] = "partial";
    }
    return result;
}
