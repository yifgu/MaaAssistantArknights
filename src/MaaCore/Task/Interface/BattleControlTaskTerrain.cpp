#include "BattleControlTask.h"

#include <array>

#include <Arknights-Tile-Pos/TileCalc2.hpp>
#include <opencv2/imgproc.hpp>

#include "Utils/Logger.hpp"

bool asst::BattleControlTask::terrain_allows_deploy(const Point& location, const battle::DeploymentOper& oper) const
{
    if (!m_map_data.key.stageId.starts_with("act55side")) {
        return true;
    }
    if (location.x < 0 || location.y < 0 || location.x >= m_map_data.get_width() ||
        location.y >= m_map_data.get_height()) {
        return false;
    }
    const auto tile = m_map_data.get_item(location.y, location.x);
    if (tile.heightType != 0) {
        return false;
    }
    if (tile.tileKey == "tile_trench") {
        // Ranged operators can enter both a trench and a covered trench; the latter can stun them.
        // This invariant does not authorize summons or claim that a board is present/absent.
        return tile.buildableType == static_cast<int>(battle::LocationType::Ranged) &&
               oper.role != battle::Role::Drone && oper.location_type == battle::LocationType::Ranged;
    }
    return (tile.tileKey == "tile_road" || tile.tileKey == "tile_fence_bound" || tile.tileKey == "tile_infection") &&
           (tile.buildableType == static_cast<int>(battle::LocationType::Melee) ||
            tile.buildableType == static_cast<int>(battle::LocationType::All));
}

json::value asst::BattleControlTask::observe_terrain(const CapturedFrame& frame, const json::value& snapshot)
{
    const auto scene = snapshot.get("scene", "unknown");
    json::value result = json::object {
        { "status", "unavailable" },
        { "source", "live_tile_pixels" },
        { "observation_id", snapshot.at("observation_id") },
        { "captured_at_ms", snapshot.at("captured_at_ms") },
        { "scene", scene },
        { "baseline", m_terrain_baseline },
        { "comparison", "exact_masked_pixels" },
        { "semantic_state", "unknown" },
        { "static_geometry_is_current_terrain", false },
        { "tiles", nullptr },
        { "reason",
          "Pixel changes may be boards, snow, units, effects, UI or capture noise; not terrain classification" },
    };
    if ((scene != "running" && scene != "paused") || frame.image.type() != CV_8UC3 || frame.image.cols != 1280 ||
        frame.image.rows != 720 || m_map_data.view.size() < 2) {
        result["unavailable_reason"] = "Requires an actionable 1280x720 battle frame and fixed-map projection";
        return result;
    }
    const bool baseline_created = m_terrain_baseline_image.empty();
    if (baseline_created) {
        const auto evidence = save_evidence(frame, snapshot, true);
        if (evidence.get("status", "") != "succeeded") {
            result["evidence_error"] = evidence.at("error");
            return result;
        }
        m_terrain_baseline_image = frame.image.clone();
        m_terrain_baseline = json::object {
            { "observation_id", snapshot.at("observation_id") },
            { "captured_at_ms", snapshot.at("captured_at_ms") },
            { "scene", scene },
            { "image_path", evidence.at("image_path") },
            { "source", "first_actionable_session_frame_not_initial_game_state" },
        };
        LogInfo << __FUNCTION__ << "Captured tile image baseline" << m_terrain_baseline;
    }
    result["baseline"] = m_terrain_baseline;
    result["baseline_created"] = baseline_created;
    result["scene_changed_from_baseline"] = scene != m_terrain_baseline.at("scene").as_string();
    result["status"] = "observed";
    result["tiles"] = json::array();

    const cv::Rect image_bounds(0, 0, frame.image.cols, frame.image.rows);
    for (int y = 0; y < m_map_data.get_height(); ++y) {
        for (int x = 0; x < m_map_data.get_width(); ++x) {
            const auto center = Map::TileCalc2::get_tile_world_pos(m_map_data, y, x);
            std::vector<cv::Point> polygon;
            json::array screen_polygon;
            for (const auto& offset : std::array<cv::Vec3d, 4> { cv::Vec3d { -0.5, -0.5, 0 },
                                                                 cv::Vec3d { 0.5, -0.5, 0 },
                                                                 cv::Vec3d { 0.5, 0.5, 0 },
                                                                 cv::Vec3d { -0.5, 0.5, 0 } }) {
                const auto point = Map::TileCalc2::world_to_screen(m_map_data, center + offset, false);
                polygon.emplace_back(point);
                screen_polygon.emplace_back(json::array { point.x, point.y });
            }
            const auto roi = cv::boundingRect(polygon);
            json::value tile = json::object {
                { "location", json::array { x, y } },
                { "screen_polygon", std::move(screen_polygon) },
                { "roi", json::array { roi.x, roi.y, roi.width, roi.height } },
                { "comparison_status", "outside_image" },
                { "changed_from_baseline", nullptr },
            };
            if ((roi & image_bounds) == roi && roi.area() > 0) {
                for (auto& point : polygon) {
                    point -= roi.tl();
                }
                cv::Mat mask = cv::Mat::zeros(roi.size(), CV_8UC1);
                cv::fillConvexPoly(mask, polygon, cv::Scalar(255));
                const bool changed = cv::norm(frame.image(roi), m_terrain_baseline_image(roi), cv::NORM_INF, mask) > 0;
                tile["comparison_status"] = baseline_created ? "baseline" : "compared";
                tile["changed_from_baseline"] = baseline_created ? json::value(nullptr) : json::value(changed);
            }
            result["tiles"].as_array().emplace_back(std::move(tile));
        }
    }
    return result;
}
