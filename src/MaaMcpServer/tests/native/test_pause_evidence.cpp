#include <cstdlib>
#include <iostream>
#include <limits>

#include "Vision/Battle/BattlePauseEvidence.h"
#include "Vision/Battle/BattlePauseRetry.h"

using asst::Rect;
using asst::TextRect;

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
    const Rect roi(560, 350, 160, 70);
    const TextRect prefix({ 593, 365, 70, 38 }, 0.998237, "暂停");
    const TextRect suffix({ 652, 374, 27, 16 }, 0.995549, "中");
    const auto evaluate = [&](std::vector<TextRect> candidates) {
        return asst::battle::localized_pause_evidence(std::move(candidates), roi);
    };
    const auto reject = [&](std::vector<TextRect> candidates, const char* reason, const char* name) {
        const auto result = evaluate(std::move(candidates));
        expect(!result.at("accepted").as_boolean(), name);
        expect(result.at("rejection_reason").as_string() == reason, name);
        expect(!result.contains("text"), name);
    };
    const auto sample = evaluate({ prefix, suffix });
    expect(sample.at("accepted").as_boolean(), "reported overlapping boxes");
    expect(sample.at("source").as_string() == "BattleControlPausedLocalizedSplit", "split provenance");
    expect(sample.at("score").as_double() == suffix.score, "minimum fragment score");
    expect(sample.at("rect").as_array() == json::array({ 593, 365, 86, 38 }), "union rectangle");
    expect(sample.at("fragments").as_array().size() == 2, "original fragments");
    expect(evaluate({ suffix, prefix }).at("accepted").as_boolean(), "detector order is not reading order");
    expect(
        evaluate({ TextRect(prefix.rect, 0.9, prefix.text), suffix }).at("accepted").as_boolean(),
        "confidence boundary");
    expect(evaluate({ TextRect({ 593, 365, 86, 38 }, 0.99, "暂停中") }).at("accepted").as_boolean(), "complete label");
    expect(
        evaluate(
            { TextRect({ 593, 365, 30, 38 }, 0.99, "暂"),
              TextRect({ 625, 365, 30, 38 }, 0.99, "停"),
              TextRect({ 657, 374, 27, 16 }, 0.99, "中") })
            .at("accepted")
            .as_boolean(),
        "three same-row fragments");
    reject({}, "missing_fragment", "no candidates");
    reject({ prefix, suffix, TextRect(suffix.rect, 0.99, "") }, "unexpected_text", "empty extra fragment");
    // Independent calls model successive frames; neither half can supply the other half's missing text.
    reject({ prefix }, "missing_positive_corroboration", "first frame missing suffix");
    reject({ suffix }, "unexpected_text", "next frame missing prefix");
    reject(
        { TextRect(prefix.rect, prefix.score, "中"), TextRect(suffix.rect, suffix.score, "暂停") },
        "unexpected_text",
        "reversed text");
    reject({ prefix, TextRect({ 690, 374, 27, 16 }, 0.99, "中") }, "unexpected_layout", "distant boxes");
    reject({ prefix, TextRect({ 652, 396, 27, 16 }, 0.99, "中") }, "unexpected_layout", "different rows");
    reject({ prefix, TextRect({ 630, 374, 45, 16 }, 0.99, "中") }, "unexpected_layout", "excessive overlap");
    reject({ prefix, TextRect({ 650, 374, 10, 16 }, 0.99, "中") }, "unexpected_layout", "nested boxes");
    reject({ prefix, TextRect({ 652, 374, 27, 10 }, 0.99, "中") }, "unexpected_layout", "incompatible heights");
    reject({ prefix, TextRect(suffix.rect, 0.899, "中") }, "low_confidence", "weak suffix");
    reject(
        { TextRect(prefix.rect, std::numeric_limits<double>::quiet_NaN(), "暂停"), suffix },
        "low_confidence",
        "nonfinite score");
    reject({ TextRect({ 550, 365, 70, 38 }, 0.99, "暂停"), suffix }, "outside_roi", "outside left edge");
    reject({ prefix, TextRect({ 710, 374, 27, 16 }, 0.99, "中") }, "outside_roi", "outside right edge");
    reject({ prefix, TextRect({ 652, 415, 27, 16 }, 0.99, "中") }, "outside_roi", "outside bottom edge");
    reject({ TextRect({ 593, 345, 70, 38 }, 0.99, "暂停"), suffix }, "outside_roi", "outside top edge");
    reject({ prefix, TextRect({ 652, 374, 0, 16 }, 0.99, "中") }, "outside_roi", "empty box");
    reject({ TextRect(prefix.rect, 0.99, "未暂停"), suffix }, "unexpected_text", "attached prefix");
    reject({ prefix, TextRect(suffix.rect, 0.99, "中止") }, "unexpected_text", "attached suffix");
    reject({ TextRect({ 570, 374, 16, 16 }, 0.99, "未"), prefix, suffix }, "unexpected_text", "separate prefix");
    reject({ prefix, suffix, TextRect({ 690, 374, 16, 16 }, 0.99, "止") }, "unexpected_text", "separate suffix");
    reject(
        { prefix, suffix, TextRect({ 590, 365, 80, 38 }, 0.99, "暂停中") },
        "unexpected_text",
        "ambiguous duplicate label");
    const auto occluded = asst::battle::localized_pause_evidence(
        { TextRect({ 591, 363, 67, 40 }, 0.999325, "暂停") },
        roi,
        { true, true, false, false });
    expect(occluded.at("accepted").as_boolean(), "occluded suffix requires independent positive evidence");
    expect(occluded.at("text").as_string() == "暂停", "never invent missing OCR glyph");
    expect(occluded.at("fragments").as_array().size() == 1, "accepted raw fragment retained");
    for (const auto& context : {
             asst::battle::PauseCorroboration { false, true, false, false },
             { true, false, false, false },
             { true, true, true, false },
             { true, true, false, true },
         }) {
        expect(
            !asst::battle::localized_pause_evidence({ prefix }, roi, context).at("accepted").as_boolean(),
            "missing HUD, missing continue, running or contradictory screen");
    }
    expect(
        !asst::battle::localized_pause_evidence(
             { TextRect({ 590, 100, 67, 40 }, 0.999, "暂停") },
             roi,
             { true, true, false, false })
             .at("accepted")
             .as_boolean(),
        "chat or operator name outside localized ROI");
    expect(
        !asst::battle::localized_pause_evidence(
             { TextRect(prefix.rect, 0.899, "暂停") },
             roi,
             { true, true, false, false })
             .at("accepted")
             .as_boolean(),
        "corroboration never reduces OCR confidence");
    // The reported text/score is a supplied regression value, not a replay of the unavailable obs60 image.
    const asst::battle::PauseCorroboration context { true, true, false, false };
    const std::vector<TextRect> decorated { TextRect({ 568, 365, 140, 32 }, 0.885374, "--暂停中") };
    const auto original = asst::battle::localized_pause_evidence(decorated, roi, context);
    expect(!original.get("accepted", false), "reported low score remains rejected");
    expect(original.get("rejection_reason", "") == "low_confidence", "original rejection unchanged");
    expect(original.at("fragments")[0].get("score", 0.0) == 0.885374, "original score unchanged");
    for (const auto& text : { "--暂停中", "—暂停中—", "暂停中--", "-- 暂停中 --" }) {
        expect(asst::battle::decorated_pause_label(text), "bounded horizontal decoration may trigger re-recognition");
    }
    for (const auto& text : { "暂停中",
                              "--暂停",
                              "--未暂停中",
                              "--暂停中止",
                              "a暂停中",
                              "---暂停中",
                              "--  暂停中",
                              "--暂 停中--",
                              "一暂停中一" }) {
        expect(
            !asst::battle::decorated_pause_label(text),
            "meaningful prefixes and incomplete labels never trigger retry");
    }
    cv::Mat image(720, 1280, CV_8UC3, cv::Scalar());
    // Deliberately artificial glyph blocks; these check cropping, not OCR recognition.
    for (int x : { 595, 625, 655 }) {
        cv::rectangle(image, cv::Rect(x, 366, 24, 32), cv::Scalar::all(255), -1);
    }
    cv::rectangle(image, cv::Rect(568, 381, 18, 2), cv::Scalar::all(255), -1);
    cv::rectangle(image, cv::Rect(688, 381, 20, 2), cv::Scalar::all(255), -1);
    const auto crop = asst::battle::localized_pause_decoration_crop(image, decorated, roi, context, 140, 255, 2);
    expect(crop.roi.has_value(), "measured isolated decoration supplies one crop");
    expect(crop.evidence.at("removed_bands").as_array().size() == 2, "both measured bands recorded");
    expect(crop.roi->include(Rect(595, 366, 84, 32)), "all non-decoration foreground retained");
    expect(crop.roi->x > 586 && crop.roi->x + crop.roi->width <= 688, "crop excludes decoration");
    // Foreign-row foreground is synthetic, not a reconstruction of obs60 or a native OCR measurement.
    auto other_rows = image.clone();
    cv::rectangle(other_rows, cv::Rect(roi.x, roi.y, roi.width, 6), cv::Scalar::all(255), -1);
    cv::rectangle(other_rows, cv::Rect(roi.x, roi.y + roi.height - 6, roi.width, 6), cv::Scalar::all(255), -1);
    const auto isolated =
        asst::battle::localized_pause_decoration_crop(other_rows, decorated, roi, context, 140, 255, 2);
    expect(isolated.roi == crop.roi, "foreground above and below the detected row cannot merge dash columns");
    expect(
        isolated.evidence.at("search_roi").as_array() == json::array({ roi.x, 363, roi.width, 36 }),
        "audit records full-width detected row plus bounded padding");
    const auto padded =
        asst::battle::localized_pause_decoration_crop(other_rows, decorated, roi, context, 140, 255, 70);
    expect(
        padded.evidence.at("search_roi").as_array() == isolated.evidence.at("search_roi").as_array() && padded.roi &&
            padded.roi->y >= 363 && padded.roi->y + padded.roi->height <= 399,
        "excessive expansion cannot reintroduce the foreign rows");
    auto many_dashes = image.clone();
    many_dashes(asst::make_rect<cv::Rect>(roi)).setTo(cv::Scalar());
    for (int x : { 595, 625, 655 }) {
        cv::rectangle(many_dashes, cv::Rect(x, 366, 24, 32), cv::Scalar::all(255), -1);
    }
    for (int x : { 563, 573, 583, 689, 699, 709 }) {
        cv::rectangle(many_dashes, cv::Rect(x, 381, 6, 1), cv::Scalar::all(255), -1);
    }
    const auto multiple =
        asst::battle::localized_pause_decoration_crop(many_dashes, decorated, roi, context, 140, 255, 2);
    expect(multiple.roi == crop.roi, "rendered dash count does not follow the two OCR dash characters");
    expect(multiple.evidence.at("removed_bands").as_array().size() == 6, "every measured outer dash retained in audit");
    auto outside_affixes = other_rows.clone();
    cv::rectangle(outside_affixes, cv::Rect(560, 366, 4, 32), cv::Scalar::all(255), -1);
    cv::rectangle(outside_affixes, cv::Rect(714, 366, 4, 32), cv::Scalar::all(255), -1);
    expect(
        !asst::battle::localized_pause_decoration_crop(outside_affixes, decorated, roi, context, 140, 255, 2).roi,
        "meaningful affixes beyond the detected horizontal box prevent trimming past them");
    for (const auto& unsafe : {
             asst::battle::PauseCorroboration { false, true, false, false },
             { true, false, false, false },
             { true, true, true, false },
             { true, true, false, true },
         }) {
        expect(
            !asst::battle::localized_pause_decoration_crop(image, decorated, roi, unsafe, 140, 255, 2).roi,
            "retry requires positive non-contradictory corroboration");
    }
    auto outside = decorated;
    outside.front().rect.x = 100;
    expect(
        !asst::battle::localized_pause_decoration_crop(image, outside, roi, context, 140, 255, 2).roi,
        "wrong-position original cannot trigger retry");
    // An unrecognized meaningful leading glyph must remain, not be stripped with the dash.
    cv::rectangle(image, cv::Rect(588, 366, 4, 32), cv::Scalar::all(255), -1);
    cv::rectangle(image, cv::Rect(681, 366, 4, 32), cv::Scalar::all(255), -1);
    const auto extra = asst::battle::localized_pause_decoration_crop(image, decorated, roi, context, 140, 255, 2);
    expect(extra.roi && extra.roi->include(Rect(588, 366, 4, 32)), "non-horizontal prefix pixels preserved");
    expect(extra.roi && extra.roi->include(Rect(681, 366, 4, 32)), "non-horizontal suffix pixels preserved");
    cv::rectangle(image, cv::Rect(568, 366, 18, 32), cv::Scalar::all(255), -1);
    cv::rectangle(image, cv::Rect(688, 366, 20, 32), cv::Scalar::all(255), -1);
    expect(
        !asst::battle::localized_pause_decoration_crop(image, decorated, roi, context, 140, 255, 2).roi,
        "OCR dashes without pixel decoration are insufficient");
    const auto retried =
        asst::battle::localized_pause_decoration_result({ TextRect(*crop.roi, 0.97, "暂停中") }, *crop.roi, context);
    expect(retried.get("accepted", false), "independent exact high-confidence retry can be accepted");
    expect(retried.get("score", 0.0) == 0.97, "retry score comes only from new OCR result");
    for (const auto& rejected : {
             std::vector<TextRect> {},
             { TextRect(*crop.roi, 0.885374, "暂停中") },
             { TextRect(*crop.roi, 0.99, "暂停") },
             { TextRect(*crop.roi, 0.99, "--暂停中") },
             { TextRect(*crop.roi, 0.99, "未暂停中") },
             { TextRect(*crop.roi, 0.99, "暂停中止") },
         }) {
        expect(
            !asst::battle::localized_pause_decoration_result(rejected, *crop.roi, context).get("accepted", false),
            "retry must independently recognize the full exact label above the unchanged threshold");
    }
    std::cout << "Native localized pause evidence regressions passed\n";
}
