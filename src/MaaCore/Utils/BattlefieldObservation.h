#pragma once

#include <algorithm>
#include <cmath>
#include <vector>

#include "Common/AsstTypes.h"

namespace asst::battle
{
struct FieldUnitDetection
{
    Rect rect;
    double score;
};

struct FieldTileCandidates
{
    Rect detection;
    Rect projection;
    double score;
    std::vector<Point> locations;
    bool contested = false;
    bool projection_in_frame = false;

    bool unique() const { return projection_in_frame && locations.size() == 1 && !contested; }
};

inline std::vector<FieldTileCandidates> project_field_unit_candidates(
    const std::vector<FieldUnitDetection>& detections,
    const std::vector<std::pair<Point, Point>>& tiles,
    const Rect& rect_move,
    const Rect& image_bounds)
{
    std::vector<FieldTileCandidates> result;
    for (const auto& detection : detections) {
        if (!std::isfinite(detection.score) || detection.score < 0.9 || detection.score > 1 || detection.rect.empty() ||
            !image_bounds.include(detection.rect)) {
            continue;
        }
        FieldTileCandidates candidate { detection.rect, detection.rect.move(rect_move), detection.score, {} };
        candidate.projection_in_frame = !candidate.projection.empty() && image_bounds.include(candidate.projection);
        if (candidate.projection_in_frame) {
            for (const auto& [location, screen] : tiles) {
                if (candidate.projection.include(screen)) {
                    candidate.locations.emplace_back(location);
                }
            }
            std::ranges::sort(candidate.locations, [](const auto& left, const auto& right) {
                return left.y == right.y ? left.x < right.x : left.y < right.y;
            });
            candidate.locations.erase(
                std::unique(candidate.locations.begin(), candidate.locations.end()),
                candidate.locations.end());
        }
        result.emplace_back(std::move(candidate));
    }
    for (size_t i = 0; i < result.size(); ++i) {
        for (size_t j = i + 1; j < result.size(); ++j) {
            if (std::ranges::any_of(result[i].locations, [&](const auto& location) {
                    return std::ranges::find(result[j].locations, location) != result[j].locations.end();
                })) {
                result[i].contested = true;
                result[j].contested = true;
            }
        }
    }
    return result;
}
}
