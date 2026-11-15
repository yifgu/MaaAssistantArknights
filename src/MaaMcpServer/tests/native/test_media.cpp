#include <cstdlib>
#include <iostream>

#include "Utils/BattleMedia.h"
#include "Utils/Platform.hpp"

void require(bool condition, const char* message)
{
    if (!condition) {
        std::cerr << message << '\n';
        std::exit(1);
    }
}

int main(int argc, char** argv)
{
    if (argc > 4 && std::string(argv[1]) == "--encode") {
        cv::Mat first = cv::imread(asst::utils::path_to_crt_string(asst::utils::path(argv[4])));
        require(!first.empty(), "missing fixture image");
        cv::VideoWriter writer(
            asst::utils::path_to_crt_string(asst::utils::path(argv[2])),
            cv::CAP_OPENCV_MJPEG,
            cv::VideoWriter::fourcc('M', 'J', 'P', 'G'),
            std::stod(argv[3]),
            first.size());
        require(writer.isOpened(), "native fixture encoder unavailable");
        writer.set(cv::VIDEOWRITER_PROP_QUALITY, 100);
        for (int i = 4; i < argc; ++i) {
            const auto image = cv::imread(asst::utils::path_to_crt_string(asst::utils::path(argv[i])));
            require(!image.empty() && image.size() == first.size(), "invalid fixture geometry");
            writer.write(image);
        }
        return 0;
    }
#ifndef ASST_DEBUG
    std::cerr << "Media regression requires Debug\n";
    return 1;
#endif
    const auto root = asst::utils::path("media-test-output");
    std::error_code error;
    std::filesystem::remove_all(root, error);
    std::vector<asst::battle::MediaFrame> frames;
    for (int i = 0; i < 3; ++i) {
        frames.emplace_back(
            asst::battle::MediaFrame {
                cv::Mat(180, 320, CV_8UC3, cv::Scalar(20 + i * 50, 90, 160)),
                1000 + i * 200,
                i * 200,
            });
    }
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    const auto recording = asst::battle::write_recording(root, frames, deadline, [] { return false; });
    require(recording.at("video").get("status", "") == "ready", "native AVI/MJPEG roundtrip failed");
    require(recording.at("video").get("mime_type", "") == "video/x-msvideo", "AVI must not be called MP4");
    require(recording.at("frames").as_array().size() == 3, "missing original PNGs");
    auto imported = asst::battle::inspect_video(root, "clip.avi", deadline, [] { return false; });
    require(imported.get("decoded_complete", false), "native decoder did not finish");
    require(imported.get("actual_decoded_frames", 0) == 3, "actual decoded frame count");
    require(
        !imported.at("frames").as_array()[0].contains("captured_at_ms"),
        "import must not invent live capture time");
    require(imported.at("frames").as_array()[0].contains("media_pts_ms"), "missing imported media PTS");
    const auto original = cv::imread(asst::utils::path_to_crt_string(root / asst::utils::path("frames/0.png")));
    require(!original.empty(), "keyframe missing");
    std::filesystem::remove_all(root, error);
    std::cout << "Native media AVI roundtrip and timeline checks passed\n";
}
