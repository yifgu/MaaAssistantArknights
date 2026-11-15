#pragma once

#include <charconv>
#include <optional>

#include "Common/AsstTypes.h"

namespace asst::battle
{
inline bool formation_identity_unchanged(
    const std::vector<std::pair<std::string, Rect>>& before,
    const std::vector<std::pair<std::string, Rect>>& after)
{
    if (before.size() != after.size() || before.empty()) {
        return false;
    }
    auto uniquely_matches = [](const auto& source, const auto& target) {
        return std::ranges::all_of(source, [&](const auto& previous) {
            // Unreadable names preserve only slot layout, never a resolved per-slot identity.
            return std::ranges::count_if(target, [&](const auto& current) {
                       return previous.first == current.first && std::abs(previous.second.x - current.second.x) <= 5 &&
                              std::abs(previous.second.y - current.second.y) <= 5;
                   }) == 1;
        });
    };
    return uniquely_matches(before, after) && uniquely_matches(after, before);
}

inline json::value numeric_hud_evidence(const std::optional<TextRect>& text, const Rect& roi, int minimum, int maximum)
{
    json::value evidence = json::object {
        { "value", nullptr },
        { "source", "unknown" },
        { "verification", "unknown" },
        { "reason", "no_numeric_ocr" },
        { "roi", json::array { roi.x, roi.y, roi.width, roi.height } },
        { "required_score", 0.9 },
        { "confidence", text && std::isfinite(text->score) ? json::value(text->score) : json::value(nullptr) },
        { "raw", text ? text->to_json() : json::value(nullptr) },
    };
    if (!text) {
        return evidence;
    }
    if (!std::isfinite(text->score) || text->score < 0.9 || text->score > 1) {
        evidence["reason"] = "low_confidence";
        return evidence;
    }
    if (text->rect.empty() || !roi.include(text->rect)) {
        evidence["reason"] = "numeric_roi_implausible";
        return evidence;
    }
    int value = -1;
    const auto& raw = text->text;
    const auto [end, error] = std::from_chars(raw.data(), raw.data() + raw.size(), value);
    if (raw.empty() || !std::ranges::all_of(raw, [](char c) { return c >= '0' && c <= '9'; }) || error != std::errc() ||
        end != raw.data() + raw.size()) {
        evidence["reason"] = "not_full_unsigned_integer";
        return evidence;
    }
    if (value < minimum || value > maximum) {
        evidence["reason"] = "out_of_range";
        return evidence;
    }
    evidence["value"] = value;
    evidence["source"] = "same_frame_numeric_ocr";
    evidence["verification"] = "observed";
    evidence["reason"] = nullptr;
    return evidence;
}

inline json::value life_difference(const json::value& previous, const json::value& current)
{
    json::value result = json::object {
        { "value", nullptr },
        { "source", "derived_from_trusted_hud_observations_not_native_game_event" },
        { "reason", "no_comparable_consecutive_observations" },
        { "cause", "unknown" },
        { "previous_observation_id", previous.get("observation_id", json::value(nullptr)) },
        { "observation_id", current.get("observation_id", json::value(nullptr)) },
        { "previous", previous },
        { "current", current },
        { "comparison_interval_ms", nullptr },
    };
    if (previous.get("verification", "") != "observed" || current.get("verification", "") != "observed" ||
        !previous.find<int>("value") || !current.find<int>("value") ||
        previous.get("observation_id", "") == current.get("observation_id", "") ||
        current.get("capture_order_ns", int64_t(0)) <= previous.get("capture_order_ns", int64_t(0))) {
        return result;
    }
    result["value"] = current.at("value").as_integer() - previous.at("value").as_integer();
    result["comparison_interval_ms"] =
        (current.at("capture_order_ns").as_long_long() - previous.at("capture_order_ns").as_long_long()) / 1'000'000;
    result["reason"] = nullptr;
    return result;
}

struct DepartureEvidence
{
    bool ordinary = false;
    bool unique_card = false;
    bool unique_owned_instance = false;
    bool substitution_excluded = false;
    bool owned_presence_verified = false;
    bool cooling = false;
    bool available = false;
    unsigned consecutive_cooling = 0;
};

inline bool owned_instance_departure(const DepartureEvidence& evidence)
{
    return evidence.ordinary && evidence.unique_card && evidence.unique_owned_instance &&
           evidence.substitution_excluded && evidence.owned_presence_verified && evidence.cooling &&
           !evidence.available && evidence.consecutive_cooling >= 2;
}
}
