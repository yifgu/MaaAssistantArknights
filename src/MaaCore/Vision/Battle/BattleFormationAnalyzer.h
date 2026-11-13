#pragma once

#include "Vision/TemplDetOCRer.h"
#include "Vision/VisionHelper.h"

namespace asst
{
class BattleFormationAnalyzer : public VisionHelper
{
public:
    struct Result
    {
        std::string to_string() const { return name; }

        explicit operator std::string() const { return to_string(); }

        std::string name;
        cv::Mat avatar;
        Rect rect;
        Rect flag_rect;
        Rect avatar_rect;
    };

    using ResultsVec = std::vector<Result>;
    using ResultsVecOpt = std::optional<ResultsVec>;

public:
    using VisionHelper::VisionHelper;
    virtual ~BattleFormationAnalyzer() override = default;

    ResultsVecOpt analyze() const;
    ResultsVecOpt analyze_slots() const;

protected:
    ResultsVec proc_ocr_result(const TemplDetOCRer::ResultsVec& ocr_result) const;
};
}
