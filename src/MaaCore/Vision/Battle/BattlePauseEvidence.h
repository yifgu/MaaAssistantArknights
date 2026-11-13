#pragma once

#include <algorithm>
#include <cmath>
#include <vector>

#include "Common/AsstTypes.h"

namespace asst::battle
{
// The caller supplies one OCR result set from one capture; no fragments survive this call.
inline json::value localized_pause_evidence(std::vector<TextRect> candidates, const Rect& roi)
{
    std::ranges::sort(candidates, {}, [](const TextRect& candidate) { return candidate.rect.x; });
    json::value result = json::object {
        { "source", candidates.size() > 1 ? "BattleControlPausedLocalizedSplit" : "BattleControlPausedLocalized" },
        { "required_score", 0.9 },
        { "fragments", json::array() },
        { "accepted", false },
        { "rejection_reason", "missing_fragment" },
    };
    for (const auto& candidate : candidates) {
        result["fragments"].as_array().emplace_back(candidate.to_json());
    }
    if (candidates.empty()) {
        return result;
    }
    if (candidates.size() > 3) {
        result["rejection_reason"] = "ambiguous_candidates";
        return result;
    }
    double score = 1.0;
    std::string text;
    for (const auto& candidate : candidates) {
        if (candidate.text.empty()) {
            result["rejection_reason"] = "unexpected_text";
            return result;
        }
        if (!std::isfinite(candidate.score) || candidate.score < 0.9 || candidate.score > 1.0) {
            result["rejection_reason"] = "low_confidence";
            return result;
        }
        score = std::min(score, candidate.score);
        const auto& rect = candidate.rect;
        if (roi.width <= 0 || roi.height <= 0 || rect.width <= 0 || rect.height <= 0 || !roi.include(rect)) {
            result["rejection_reason"] = "outside_roi";
            return result;
        }
        text += candidate.text;
    }
    result["score"] = score;
    if (text != "暂停中") {
        result["rejection_reason"] = "unexpected_text";
        return result;
    }
    for (size_t i = 1; i < candidates.size(); ++i) {
        const auto& left = candidates[i - 1].rect;
        const auto& right = candidates[i].rect;
        const auto min_height = std::min(left.height, right.height);
        const auto max_height = std::max(left.height, right.height);
        const auto overlap_y = std::min(left.y + left.height, right.y + right.height) - std::max(left.y, right.y);
        const auto gap = right.x - (left.x + left.width);
        // A small final glyph may have a shorter box and overlap its neighbour, but must advance on the same row.
        const bool adjacent =
            right.x > left.x && right.x + right.width > left.x + left.width && max_height <= 3 * min_height &&
            4 * overlap_y >= 3 * min_height &&
            3 * std::abs((left.y * 2 + left.height) - (right.y * 2 + right.height)) <= 2 * max_height &&
            gap <= max_height / 2 && -gap <= std::min(left.width, right.width) / 2;
        if (!adjacent) {
            result["rejection_reason"] = "unexpected_layout";
            return result;
        }
    }
    auto rect = candidates.front().rect;
    for (const auto& candidate : candidates) {
        rect = Rect::bounding_box(rect, candidate.rect);
    }
    result["rect"] = json::array { rect.x, rect.y, rect.width, rect.height };
    result["text"] = text;
    result["accepted"] = true;
    result["rejection_reason"] = nullptr;
    return result;
}
}
