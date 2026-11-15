#include <cstdlib>
#include <iostream>

#include "Vision/Battle/BattleControlState.h"
#include "Vision/Battle/BattleControlVision.h"

using namespace asst;

void expect(bool condition, const char* name)
{
    if (!condition) {
        std::cerr << "Failed: " << name << '\n';
        std::exit(1);
    }
}

int main()
{
#ifndef ASST_DEBUG
    std::cerr << "This regression must run in a Debug build\n";
    return 1;
#endif
    const Rect roi(100, 100, 60, 30);
    auto number = [&](std::string text, double score = 0.99) {
        return battle::numeric_hud_evidence(TextRect({ 105, 103, 30, 20 }, score, text), roi, 1, 90);
    };
    expect(number("60").at("value").as_integer() == 60, "full confident level 60");
    for (const auto& malformed : { "2*", "60x", "-2", "+2", " 60", "60 ", "", "9.0", "91", "0" }) {
        expect(number(malformed).at("value").is_null(), "malformed or implausible level is not observed");
    }
    expect(number("60", 0.690711).at("value").is_null(), "weak level OCR is unknown");
    expect(number("2*", 0.690711).at("raw").at("text").as_string() == "2*", "raw noisy OCR retained");
    expect(battle::numeric_hud_evidence(std::nullopt, roi, 0, 99).at("value").is_null(), "missing is not zero");
    expect(
        battle::numeric_hud_evidence(TextRect({ 90, 103, 30, 20 }, 0.99, "60"), roi, 1, 90).at("value").is_null(),
        "numeric candidate must be inside ROI");
    auto previous = json::object { { "value", 3 },
                                   { "verification", "observed" },
                                   { "observation_id", "1" },
                                   { "captured_at_ms", 100 },
                                   { "capture_order_ns", 100'000'000 } };
    auto current = json::object { { "value", 2 },
                                  { "verification", "observed" },
                                  { "observation_id", "2" },
                                  { "captured_at_ms", 200 },
                                  { "capture_order_ns", 200'000'000 } };
    expect(battle::life_difference(previous, current).at("value").as_integer() == -1, "3 to 2 delta");
    expect(battle::life_difference(previous, current).at("cause").as_string() == "unknown", "mechanic cause unknown");
    current["captured_at_ms"] = 50;
    expect(
        battle::life_difference(previous, current).at("value").as_integer() == -1,
        "wall clock adjustments do not reverse monotonic capture order");
    current["captured_at_ms"] = 200;
    expect(battle::life_difference(current, current).at("value").is_null(), "same observation is not new change");
    expect(battle::life_difference(json::object(), current).at("value").is_null(), "unknown previous life");
    current["observation_id"] = "3";
    current["captured_at_ms"] = 300;
    current["capture_order_ns"] = 300'000'000;
    expect(battle::life_difference(previous, current).at("value").as_integer() == -1, "trusted comparable captures");
    previous = current;
    current["observation_id"] = "4";
    current["captured_at_ms"] = 400;
    current["capture_order_ns"] = 400'000'000;
    expect(battle::life_difference(previous, current).at("value").as_integer() == 0, "unchanged paused capture");
    const std::vector<std::pair<std::string, Rect>> formation {
        { "A", { 110, 280, 10, 10 } },
        { "B", { 260, 280, 10, 10 } },
    };
    expect(
        battle::formation_identity_unchanged(
            formation,
            { { "B", { 260, 279, 10, 10 } }, { "A", { 110, 281, 10, 10 } } }),
        "same-row y jitter and detector permutation preserve identity");
    expect(
        !battle::formation_identity_unchanged(
            formation,
            { { "B", { 110, 280, 10, 10 } }, { "A", { 260, 280, 10, 10 } } }),
        "real roster reordering changes identity binding");
    expect(
        !battle::formation_identity_unchanged(
            formation,
            { { "C", { 110, 280, 10, 10 } }, { "B", { 260, 280, 10, 10 } } }),
        "changed operator invalidates roster");
    expect(
        !battle::formation_identity_unchanged(
            { { "A", { 110, 280, 10, 10 } }, { "A", { 112, 280, 10, 10 } } },
            formation),
        "two old detections cannot reuse one new identity match");
    battle::DepartureEvidence departure { true, true, true, true, true, true, false, 1 };
    expect(!battle::owned_instance_departure(departure), "first cooling capture cannot release instance");
    departure.consecutive_cooling = 2;
    expect(battle::owned_instance_departure(departure), "verified owned instance and two unique cooling captures");
    departure.owned_presence_verified = false;
    expect(!battle::owned_instance_departure(departure), "selected kind without verified ownership is insufficient");
    departure.owned_presence_verified = true;
    departure.unique_owned_instance = false;
    expect(!battle::owned_instance_departure(departure), "same-kind multi instance ambiguity");
    departure.unique_owned_instance = true;
    departure.ordinary = false;
    expect(!battle::owned_instance_departure(departure), "summon ambiguity");
    departure.ordinary = true;
    departure.substitution_excluded = false;
    expect(!battle::owned_instance_departure(departure), "substitute cannot prove departure");
    battle::DepartureEvidence advisory { true, true, true, true, false, true, false, 1 };
    expect(battle::suspected_defeat_hint(advisory, false, false), "unverified cooling may request host defeat review");
    expect(!battle::owned_instance_departure(advisory), "advisory never verifies owned departure");
    expect(!battle::suspected_defeat_hint(advisory, true, false), "session retreat input suppresses defeat hint");
    expect(!battle::suspected_defeat_hint(advisory, false, true), "terminal cleanup is not individual defeat");
    advisory.unique_owned_instance = false;
    expect(!battle::suspected_defeat_hint(advisory, false, false), "duplicate owned kinds suppress hint");
    advisory.unique_owned_instance = true;
    advisory.substitution_excluded = false;
    expect(!battle::suspected_defeat_hint(advisory, false, false), "substitute suppresses hint");
    advisory.substitution_excluded = true;
    advisory.ordinary = false;
    expect(!battle::suspected_defeat_hint(advisory, false, false), "summon suppresses hint");

    // These are deliberately synthetic shape/appearance fixtures, not user-frame replays.
    cv::Mat image = cv::Mat::zeros(720, 1280, CV_8UC3);
    const Rect button(1165, 20, 75, 65);
    const std::vector<cv::Point> triangle { { 1190, 39 }, { 1190, 68 }, { 1220, 53 } };
    cv::fillConvexPoly(image, triangle, cv::Scalar(255, 255, 255));
    expect(battle::continue_button_evidence(image, button).get("accepted", false), "positive continue geometry");
    image.setTo(cv::Scalar(0, 0, 0));
    cv::fillConvexPoly(
        image,
        std::vector<cv::Point> { { 1196, 39 }, { 1190, 68 }, { 1224, 54 } },
        cv::Scalar(255, 255, 255));
    expect(
        battle::continue_button_evidence(image, button).get("accepted", false),
        "modestly slanted rear edge continuation geometry");
    image.setTo(cv::Scalar(0, 0, 0));
    cv::rectangle(image, cv::Rect(1190, 39, 10, 30), cv::Scalar(255, 255, 255), -1);
    cv::rectangle(image, cv::Rect(1210, 39, 10, 30), cv::Scalar(255, 255, 255), -1);
    expect(!battle::continue_button_evidence(image, button).get("accepted", false), "running pause bars rejected");
    image.setTo(cv::Scalar(0, 0, 0));
    cv::fillConvexPoly(
        image,
        std::vector<cv::Point> { { 1220, 39 }, { 1220, 68 }, { 1190, 53 } },
        cv::Scalar(255, 255, 255));
    expect(!battle::continue_button_evidence(image, button).get("accepted", false), "left pointing triangle rejected");
    image.setTo(cv::Scalar(0, 0, 0));
    for (const int x : { 1183, 1205 }) {
        cv::fillConvexPoly(
            image,
            std::vector<cv::Point> { { x, 39 }, { x, 61 }, { x + 18, 50 } },
            cv::Scalar(255, 255, 255));
    }
    expect(!battle::continue_button_evidence(image, button).get("accepted", false), "ambiguous continuation controls");
    cv::Mat avatar(60, 60, CV_8UC3);
    cv::RNG random(42);
    random.fill(avatar, cv::RNG::UNIFORM, 30, 220);
    cv::Mat shaded;
    avatar.convertTo(shaded, avatar.type(), 0.75, 10);
    expect(battle::card_appearance_evidence(avatar, shaded).get("accepted", false), "benign uniform shading");
    shaded.at<cv::Vec3b>(20, 20) = { 255, 0, 0 };
    expect(battle::card_appearance_evidence(avatar, shaded).get("accepted", false), "small animation pixel change");
    cv::Mat swapped;
    random.fill(swapped = avatar.clone(), cv::RNG::UNIFORM, 30, 220);
    expect(!battle::card_appearance_evidence(avatar, swapped).get("accepted", false), "real avatar swap");
    expect(
        !battle::card_appearance_evidence(avatar, cv::Mat::zeros(60, 60, CV_8UC3)).get("accepted", false),
        "blank current avatar is not same identity");
    std::cout << "Native battle state and vision regressions passed\n";
}
