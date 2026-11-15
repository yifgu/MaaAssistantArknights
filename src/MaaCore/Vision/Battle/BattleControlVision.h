#pragma once

#include <algorithm>
#include <cmath>

#include "Common/AsstTypes.h"
#include "MaaUtils/NoWarningCV.hpp"

namespace asst::battle
{
// A positive shape measurement, not "the pause bars were absent".
inline json::value continue_button_evidence(const cv::Mat& image, const Rect& roi)
{
    json::value evidence = json::object {
        { "source", "same_frame_continue_triangle_geometry" },
        { "roi", json::array { roi.x, roi.y, roi.width, roi.height } },
        { "accepted", false },
        { "reason", "no_unique_right_pointing_triangle" },
        { "candidates", json::array() },
        { "luminance_threshold", 210 },
    };
    if (image.empty() || image.channels() != 3 || !Rect(0, 0, image.cols, image.rows).include(roi)) {
        evidence["reason"] = "invalid_frame_or_roi";
        return evidence;
    }
    cv::Mat gray, mask;
    cv::cvtColor(image(make_rect<cv::Rect>(roi)), gray, cv::COLOR_BGR2GRAY);
    cv::threshold(gray, mask, 210, 255, cv::THRESH_BINARY);
    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(mask, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);
    int accepted = 0;
    for (const auto& contour : contours) {
        const auto box = cv::boundingRect(contour);
        if (box.width < 18 || box.width > 40 || box.height < 18 || box.height > 45) {
            continue;
        }
        std::vector<cv::Point> polygon;
        cv::approxPolyDP(contour, polygon, 0.04 * cv::arcLength(contour, true), true);
        if (polygon.size() != 3 || !cv::isContourConvex(polygon)) {
            continue;
        }
        std::ranges::sort(polygon, {}, [](const auto& point) { return point.x; });
        const auto& a = polygon[0];
        const auto& b = polygon[1];
        const auto& tip = polygon[2];
        const double area_ratio = cv::contourArea(contour) / (box.width * box.height);
        const bool positive = std::abs(a.x - b.x) <= box.width / 3 && 4 * std::abs(a.y - b.y) >= 3 * box.height &&
                              3 * (tip.x - std::max(a.x, b.x)) >= 2 * box.width &&
                              2 * std::abs(2 * tip.y - a.y - b.y) <= box.height && tip.y > std::min(a.y, b.y) &&
                              tip.y < std::max(a.y, b.y) && area_ratio >= 0.40 && area_ratio <= 0.65 && box.x >= 15 &&
                              box.x + box.width <= roi.width - 8 && box.y >= 8 && box.y + box.height <= roi.height - 8;
        json::array vertices;
        for (const auto& vertex : polygon) {
            vertices.emplace_back(json::array { roi.x + vertex.x, roi.y + vertex.y });
        }
        evidence["candidates"].as_array().emplace_back(
            json::object {
                { "rect", json::array { roi.x + box.x, roi.y + box.y, box.width, box.height } },
                { "vertices", std::move(vertices) },
                { "area_ratio", area_ratio },
                { "accepted", positive },
            });
        accepted += positive;
    }
    evidence["accepted"] = accepted == 1;
    if (accepted == 1) {
        evidence["reason"] = nullptr;
    }
    return evidence;
}

// Normalized correlation tolerates uniform shading, but not identity/layout changes.
inline json::value card_appearance_evidence(const cv::Mat& bound, const cv::Mat& current)
{
    json::value evidence = json::object {
        { "source", "bound_avatar_normalized_correlation" },
        { "accepted", false },
        { "required_score", 0.95 },
        { "score", nullptr },
        { "reason", "incompatible_avatar" },
    };
    if (bound.empty() || current.empty() || bound.size() != current.size() || bound.type() != current.type() ||
        bound.channels() != 3) {
        return evidence;
    }
    cv::Mat a, b;
    cv::cvtColor(bound, a, cv::COLOR_BGR2GRAY);
    cv::cvtColor(current, b, cv::COLOR_BGR2GRAY);
    cv::Scalar mean, deviation;
    cv::meanStdDev(a, mean, deviation);
    if (deviation[0] < 8) {
        evidence["reason"] = "uninformative_bound_avatar";
        return evidence;
    }
    cv::meanStdDev(b, mean, deviation);
    if (deviation[0] < 8) {
        evidence["reason"] = "uninformative_current_avatar";
        return evidence;
    }
    cv::Mat scores;
    cv::matchTemplate(current, bound, scores, cv::TM_CCOEFF_NORMED);
    const auto score = static_cast<double>(scores.at<float>(0, 0));
    evidence["score"] = score;
    evidence["accepted"] = std::isfinite(score) && score >= 0.95;
    evidence["reason"] = evidence.get("accepted", false) ? json::value(nullptr) : json::value("appearance_mismatch");
    return evidence;
}
}
