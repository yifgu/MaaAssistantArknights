#include <iostream>
#include <stdexcept>

#include "Config/TaskData.h"
#include "MaaUtils/NoWarningCV.hpp"
#include "Utils/ScrollImageStitch.h"

// Exercise the real image matchers without loading the task catalog or linking MaaCore's private C++ symbols.
bool asst::TaskData::load(const std::filesystem::path&)
{
    throw std::logic_error("Scroll image tests must not load task resources");
}

bool asst::TaskData::parse(const json::value&)
{
    throw std::logic_error("Scroll image tests must not parse task resources");
}

asst::TaskPtr asst::TaskData::get(std::string_view)
{
    throw std::logic_error("Scroll image tests must use explicit template matrices");
}

const std::unordered_set<std::string>& asst::TaskData::get_templ_required() const noexcept
{
    return m_templ_required;
}

asst::MatchTaskConstPtr asst::TaskData::_default_match_task_info()
{
    return std::make_shared<MatchTaskInfo>();
}

asst::OcrTaskConstPtr asst::TaskData::_default_ocr_task_info()
{
    return std::make_shared<OcrTaskInfo>();
}

asst::FeatureMatchTaskConstPtr asst::TaskData::_default_feature_match_task_info()
{
    return std::make_shared<FeatureMatchTaskInfo>();
}

asst::TaskConstPtr asst::TaskData::_default_task_info()
{
    return std::make_shared<TaskInfo>();
}

void expect(bool condition, const char* message)
{
    if (!condition) {
        throw std::runtime_error(message);
    }
}

int main()
{
#ifndef ASST_DEBUG
    std::cerr << "Scroll image regression requires Debug\n";
    return 1;
#endif
    try {
        using namespace asst::utils;
        cv::Mat source(180, 96, CV_8UC3);
        cv::RNG random(1234);
        random.fill(source, cv::RNG::UNIFORM, 0, 256);
        const auto first = source(cv::Rect { 0, 0, source.cols, 120 });
        const auto second = source(cv::Rect { 0, 60, source.cols, 120 });
        const auto overlap = match_scroll_image_overlap(first, second, { 30, 0.9, true, 15 });
        expect(overlap.status == ScrollOverlapStatus::Matched && overlap.offset_y == 60, "unique scroll overlap");
        const auto panorama = append_scroll_image(first, second, overlap.offset_y);
        expect(
            panorama && panorama->rows == source.rows && cv::norm(*panorama, source, cv::NORM_INF) == 0,
            "stitch appends only unseen rows without mutating the inputs");
        expect(first.rows == 120 && second.rows == 120, "input view dimensions remain unchanged");
        expect(!append_scroll_image(first, first, 0), "unchanged scrolling is not progress");
        expect(!append_scroll_image(first, second, 120), "no overlap is rejected");
        expect(!append_scroll_image(first, second, -1), "negative overlap offset is rejected");

        cv::Mat repeated = source.clone();
        second(cv::Rect { 0, 0, second.cols, 30 }).copyTo(repeated(cv::Rect { 0, 10, repeated.cols, 30 }));
        const auto ambiguous = match_scroll_image_overlap(repeated, second, { 30, 0.9, true });
        expect(ambiguous.status == ScrollOverlapStatus::Ambiguous, "repeated strips cannot prove unique overlap");
        expect(
            match_scroll_image_overlap(repeated, second, { 30, 0.9 }).status == ScrollOverlapStatus::Matched,
            "legacy GUI permits a single best match despite ambiguous alternatives");
        const cv::Mat blank(120, 96, CV_8UC3, cv::Scalar(50, 50, 50));
        expect(
            match_scroll_image_overlap(first, blank, { 30, 0.9, true, 15 }).status ==
                ScrollOverlapStatus::Uninformative,
            "strict matching rejects featureless strips before correlation");
        expect(
            match_scroll_image_overlap(first, second, { 121, 0.9 }).status == ScrollOverlapStatus::InvalidImage,
            "oversized strips return failure instead of throwing");
        const auto rect = scroll_image_rect_to_frame({ 4, 90, 12, 14 }, { 10, 20 }, 60);
        expect(rect == asst::Rect { 14, 50, 12, 14 }, "panorama coordinates map back to the original frame");
    }
    catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    std::cout << "Native scroll overlap, ambiguity and stitching regressions passed\n";
}
