#pragma once

#include <optional>

#include "Common/AsstTypes.h"
#include "MaaUtils/NoWarningCVMat.hpp"

namespace asst::utils
{
enum class ScrollOverlapStatus
{
    Matched,
    InvalidImage,
    Uninformative,
    NoMatch,
    Ambiguous,
};

struct ScrollOverlapOptions
{
    int strip_height = 30;
    double threshold = 0.8;
    bool require_unique = false;
    double minimum_deviation = 0;
};

struct ScrollOverlap
{
    ScrollOverlapStatus status = ScrollOverlapStatus::NoMatch;
    int offset_y = 0;
};

ScrollOverlap
    match_scroll_image_overlap(const cv::Mat& stitched, const cv::Mat& current, const ScrollOverlapOptions& options);

// The offset is the current frame's top edge in stitched-image coordinates.
std::optional<cv::Mat> append_scroll_image(const cv::Mat& stitched, const cv::Mat& current, int offset_y);
Rect scroll_image_rect_to_frame(const Rect& rect, const Point& roi_origin, int offset_y);
}
