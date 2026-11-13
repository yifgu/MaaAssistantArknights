#include <cstdlib>
#include <iostream>
#include <limits>

#include "Vision/Battle/BattlePauseEvidence.h"

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
    reject({ prefix }, "unexpected_text", "first frame missing suffix");
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
    std::cout << "Native localized pause evidence regressions passed\n";
}
