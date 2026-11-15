#pragma once

#include <optional>

#include "MaaUtils/NoWarningCV.hpp"
#include "Vision/Battle/BattlePauseEvidence.h"

namespace asst::battle
{
// A retry trigger only: neither the cleaned string nor the original score becomes pause evidence.
inline bool decorated_pause_label(std::string text)
{
    const auto trim_dash = [](std::string& value, bool front) {
        for (const std::string dash : { "-", "–", "—", "―", "−", "－" }) {
            if (front ? value.starts_with(dash) : value.ends_with(dash)) {
                value.erase(front ? 0 : value.size() - dash.size(), dash.size());
                return true;
            }
        }
        return false;
    };
    int left = 0, right = 0;
    for (int i = 0; i < 2 && trim_dash(text, true); ++i) {
        ++left;
    }
    if (left && text.starts_with(' ')) {
        text.erase(0, 1);
    }
    for (int i = 0; i < 2 && trim_dash(text, false); ++i) {
        ++right;
    }
    if (right && text.ends_with(' ')) {
        text.pop_back();
    }
    return left + right > 0 && text == "暂停中";
}

struct PauseDecorationCrop
{
    std::optional<Rect> roi;
    json::value evidence;
};

// Isolate the detected row vertically, but retain the full formal ROI width for meaningful affixes.
inline PauseDecorationCrop localized_pause_decoration_crop(
    const cv::Mat& image,
    const std::vector<TextRect>& candidates,
    const Rect& roi,
    PauseCorroboration corroboration,
    int lower,
    int upper,
    int expansion)
{
    PauseDecorationCrop result {
        std::nullopt,
        json::object {
            { "source", "same_frame_outer_horizontal_bands" },
            { "attempted", false },
            { "accepted", false },
            { "rejection_reason", "ineligible_original_candidate" },
            { "bin_threshold", json::array { lower, upper } },
            { "bin_expansion", expansion },
            { "use_raw", true },
            { "without_det", true },
            { "required_score", 0.9 },
            { "search_roi", nullptr },
            { "vertical_padding", nullptr },
            { "roi", nullptr },
            { "removed_bands", json::array() },
            { "candidates", json::array() },
            { "result", nullptr },
        },
    };
    if (candidates.size() != 1 || !decorated_pause_label(candidates.front().text) ||
        !std::isfinite(candidates.front().score) || candidates.front().score < 0 || candidates.front().score > 1 ||
        candidates.front().rect.empty() || !roi.include(candidates.front().rect)) {
        return result;
    }
    if (!corroboration.battle_hud || !corroboration.continue_button || corroboration.running_button ||
        corroboration.contradictory_scene) {
        result.evidence["rejection_reason"] = "missing_or_contradictory_corroboration";
        return result;
    }
    if (image.empty() || image.channels() != 3 || roi.empty() || !Rect(0, 0, image.cols, image.rows).include(roi) ||
        lower < 0 || upper > 255 || lower > upper || expansion < 0) {
        result.evidence["rejection_reason"] = "invalid_frame_or_preprocessing";
        return result;
    }
    const auto& detected = candidates.front().rect;
    const auto padding = std::min(expansion, 2);
    const auto search_top = std::max(roi.y, detected.y - padding);
    const auto search_bottom = std::min(roi.y + roi.height, detected.y + detected.height + padding);
    const Rect search(roi.x, search_top, roi.width, search_bottom - search_top);
    result.evidence["search_roi"] = json::array { search.x, search.y, search.width, search.height };
    result.evidence["vertical_padding"] = padding;
    cv::Mat gray, mask;
    cv::cvtColor(image(make_rect<cv::Rect>(search)), gray, cv::COLOR_BGR2GRAY);
    cv::inRange(gray, lower, upper, mask);
    std::vector<cv::Rect> bands;
    for (int x = 0; x < mask.cols;) {
        if (cv::countNonZero(mask.col(x)) == 0) {
            ++x;
            continue;
        }
        const auto start = x++;
        while (x < mask.cols && cv::countNonZero(mask.col(x)) != 0) {
            ++x;
        }
        auto band = cv::boundingRect(mask.colRange(start, x));
        band.x += start;
        bands.emplace_back(band);
    }
    result.evidence["rejection_reason"] = "no_isolated_outer_decoration";
    if (bands.size() < 2) {
        return result;
    }
    const auto tallest = *std::ranges::max_element(bands, {}, &cv::Rect::height);
    const auto decoration = [&](const cv::Rect& band) {
        return band.y > 0 && band.br().y < mask.rows && band.width >= 4 * band.height &&
               4 * band.height <= tallest.height && band.y >= tallest.y &&
               band.y + band.height <= tallest.y + tallest.height &&
               std::abs((band.y * 2 + band.height) - (tallest.y * 2 + tallest.height)) <= tallest.height / 2;
    };
    size_t first = 0, last = bands.size();
    // The fixed search width bounds this scan; OCR dash count does not count rendered bands.
    while (first + 1 < last && decoration(bands[first])) {
        ++first;
    }
    while (last > first + 1 && decoration(bands[last - 1])) {
        --last;
    }
    if (first == 0 && last == bands.size()) {
        return result;
    }
    auto kept = bands[first];
    for (size_t i = first + 1; i < last; ++i) {
        kept |= bands[i];
    }
    // Expansion uses blank columns only and can never reintroduce an excluded band.
    const auto left = std::max(first == 0 ? 0 : bands[first - 1].br().x, kept.x - expansion);
    const auto right = std::min(last == bands.size() ? mask.cols : bands[last].x, kept.br().x + expansion);
    const auto top = std::max(0, kept.y - expansion);
    const auto bottom = std::min(mask.rows, kept.br().y + expansion);
    result.roi = Rect(search.x + left, search.y + top, right - left, bottom - top);
    const auto& crop = *result.roi;
    result.evidence["roi"] = json::array { crop.x, crop.y, crop.width, crop.height };
    for (size_t i = 0; i < bands.size(); ++i) {
        if (i < first || i >= last) {
            const auto& band = bands[i];
            result.evidence["removed_bands"].as_array().emplace_back(
                json::array { search.x + band.x, search.y + band.y, band.width, band.height });
        }
    }
    result.evidence["rejection_reason"] = nullptr;
    return result;
}

inline json::value localized_pause_decoration_result(
    std::vector<TextRect> candidates,
    const Rect& roi,
    PauseCorroboration corroboration)
{
    auto result = localized_pause_evidence(std::move(candidates), roi, corroboration);
    result["source"] = "BattleControlPausedLocalizedDecorationRetry";
    result["rule"] = "complete_label_after_same_frame_outer_band_crop";
    result["rect_kind"] = "recognition_roi_not_detection_box";
    if (result.get("accepted", false) && result.get("text", "") != "暂停中") {
        result["accepted"] = false;
        result["rejection_reason"] = "retry_requires_complete_label";
    }
    return result;
}
}
