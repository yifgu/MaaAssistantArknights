#include "ScrollImageStitch.h"

#include <limits>

#include "MaaUtils/NoWarningCV.hpp"
#include "Vision/Matcher.h"
#include "Vision/MultiMatcher.h"

asst::utils::ScrollOverlap asst::utils::match_scroll_image_overlap(
    const cv::Mat& stitched,
    const cv::Mat& current,
    const ScrollOverlapOptions& options)
{
    if (stitched.empty() || current.empty() || stitched.cols != current.cols || stitched.type() != current.type() ||
        current.type() != CV_8UC3 || options.strip_height <= 0 || options.strip_height > current.rows ||
        options.strip_height > stitched.rows) {
        return { ScrollOverlapStatus::InvalidImage };
    }
    const auto strip = current(cv::Rect { 0, 0, current.cols, options.strip_height });
    if (options.minimum_deviation > 0) {
        cv::Scalar mean, deviation;
        cv::meanStdDev(strip, mean, deviation);
        if (deviation[0] + deviation[1] + deviation[2] < options.minimum_deviation) {
            return { ScrollOverlapStatus::Uninformative };
        }
    }
    if (options.require_unique) {
        MultiMatcher matcher(stitched);
        matcher.set_templ(strip);
        matcher.set_method(MatchMethod::Ccoeff);
        matcher.set_threshold(options.threshold);
        const auto matches = matcher.analyze();
        if (!matches) {
            return { ScrollOverlapStatus::NoMatch };
        }
        if (matches->size() != 1) {
            return { ScrollOverlapStatus::Ambiguous };
        }
        return { ScrollOverlapStatus::Matched, matches->front().rect.y };
    }
    Matcher matcher(stitched);
    matcher.set_templ(strip);
    matcher.set_method(MatchMethod::Ccoeff);
    matcher.set_threshold(options.threshold);
    const auto match = matcher.analyze();
    return match ? ScrollOverlap { ScrollOverlapStatus::Matched, match->rect.y } : ScrollOverlap {};
}

std::optional<cv::Mat> asst::utils::append_scroll_image(const cv::Mat& stitched, const cv::Mat& current, int offset_y)
{
    if (stitched.empty() || current.empty() || stitched.cols != current.cols || stitched.type() != current.type() ||
        offset_y < 0 || offset_y >= stitched.rows) {
        return std::nullopt;
    }
    const int overlap = stitched.rows - offset_y;
    if (overlap >= current.rows || current.rows - overlap > std::numeric_limits<int>::max() - stitched.rows) {
        return std::nullopt;
    }
    cv::Mat result;
    cv::vconcat(stitched, current(cv::Rect { 0, overlap, current.cols, current.rows - overlap }), result);
    return result;
}

asst::Rect asst::utils::scroll_image_rect_to_frame(const Rect& rect, const Point& roi_origin, int offset_y)
{
    return { rect.x + roi_origin.x, rect.y + roi_origin.y - offset_y, rect.width, rect.height };
}
