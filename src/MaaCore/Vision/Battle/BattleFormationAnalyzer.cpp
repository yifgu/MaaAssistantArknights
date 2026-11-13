#include "BattleFormationAnalyzer.h"

#include <algorithm>
#include <tuple>

#include "Config/Miscellaneous/BattleDataConfig.h"
#include "Config/TaskData.h"
#include "MaaUtils/NoWarningCV.hpp"
#include "Utils/Logger.hpp"
#include "Vision/Miscellaneous/PipelineAnalyzer.h"
#include "Vision/MultiMatcher.h"
#include "Vision/RegionOCRer.h"
#include "Vision/TemplDetOCRer.h"

using namespace asst;

BattleFormationAnalyzer::ResultsVecOpt BattleFormationAnalyzer::analyze_slots() const
{
    PipelineAnalyzer page(m_image);
    page.set_tasks(Task.get("BattleStartAll")->next);
    if (!page.analyze()) {
        return std::nullopt;
    }
    MultiMatcher flags(m_image);
    flags.set_task_info("BattlePreparationNameFlags");
    const auto matches = flags.analyze();
    if (!matches) {
        return ResultsVec {};
    }
    if (matches->size() > 13) {
        LogWarn << __FUNCTION__ << "Too many formation level flags" << matches->size();
        return std::nullopt;
    }
    ResultsVec results;
    for (const auto& flag : *matches) {
        Result slot;
        slot.flag_rect = flag.rect;
        for (const auto& task : { "BattleFormationOperNames", "BattleFormationOperNamesOldVersion" }) {
            RegionOCRer name(m_image);
            name.set_task_info(task);
            name.set_roi(flag.rect.move(Task.get(task)->roi));
            const auto text = name.analyze();
            if (text && !BattleData.is_name_invalid(battle::Role::Unknown, text->text)) {
                slot.name = text->text;
                slot.rect = text->rect;
                break;
            }
        }
        if (!slot.name.empty()) {
            slot.avatar_rect = Rect { slot.rect.x + slot.rect.width, slot.rect.y, 0, 0 }.move(
                Task.get("BattleFormationOperAvatarMove")->rect_move);
            const auto& r = slot.avatar_rect;
            if (r.x >= 0 && r.y >= 0 && r.x + r.width <= m_image.cols && r.y + r.height <= m_image.rows) {
                slot.avatar = m_image(make_rect<cv::Rect>(r));
            }
        }
        results.emplace_back(std::move(slot));
    }
    std::ranges::sort(results, [](const Result& a, const Result& b) {
        return std::tie(a.flag_rect.y, a.flag_rect.x) < std::tie(b.flag_rect.y, b.flag_rect.x);
    });
    return results;
}

BattleFormationAnalyzer::ResultsVecOpt BattleFormationAnalyzer::analyze() const
{
    LogTraceFunction;

    PipelineAnalyzer start_button_analyzer(m_image);
    start_button_analyzer.set_tasks(Task.get("BattleStartAll")->next);
    if (!start_button_analyzer.analyze()) {
        return std::nullopt;
    }

    TemplDetOCRer::ResultsVec oper_names;

    TemplDetOCRer oper_names_analyzer(m_image);
    oper_names_analyzer.set_task_info("BattleFormationOCRNameFlag", "BattleFormationOperNames");
    oper_names_analyzer.set_bin_expansion(3);
    auto oper_names_opt = oper_names_analyzer.analyze();
    if (!oper_names_opt) {
        return std::nullopt;
    }
    ResultsVec results = proc_ocr_result(*oper_names_opt);

    // 识别到了结果，但是确实没有可用的干员名。说明游戏UI可能是老版本的，布局不一样
    if (results.empty()) {
        oper_names_analyzer.set_task_info("BattleFormationOCRNameFlag", "BattleFormationOperNamesOldVersion");
        oper_names_analyzer.set_bin_expansion(2);
        auto old_layout_oper_names_opt = oper_names_analyzer.analyze();
        if (!old_layout_oper_names_opt) {
            return std::nullopt;
        }
        results = proc_ocr_result(*old_layout_oper_names_opt);
    }

    if (results.empty()) {
        return std::nullopt;
    }

    return results;
}

BattleFormationAnalyzer::ResultsVec
    BattleFormationAnalyzer::proc_ocr_result(const TemplDetOCRer::ResultsVec& ocr_result) const
{
    ResultsVec results;

    for (const auto& name_res : ocr_result) {
        if (BattleData.is_name_invalid(battle::Role::Unknown, name_res.text)) {
            continue;
        }

        const std::string& name = name_res.text;
        Rect base_point { name_res.rect.x + name_res.rect.width, name_res.rect.y, 0, 0 };
        Rect avatar_rect = base_point.move(Task.get("BattleFormationOperAvatarMove")->rect_move);
        cv::Mat avatar = m_image(make_rect<cv::Rect>(avatar_rect));
        results.emplace_back(Result { .name = name, .avatar = avatar, .rect = name_res.rect });

#ifdef ASST_DEBUG
        cv::rectangle(m_image_draw, make_rect<cv::Rect>(name_res.rect), cv::Scalar(0, 255, 0), 2);
        cv::rectangle(m_image_draw, make_rect<cv::Rect>(avatar_rect), cv::Scalar(0, 0, 255), 2);
        cv::putText(
            m_image_draw,
            BattleData.get_first_id(battle::Role::Unknown, name, true).value_or("<Unknown>"),
            cv::Point(avatar_rect.x, avatar_rect.y - 20),
            1,
            1.2,
            cv::Scalar(0, 0, 255),
            2);
#endif
    }

    return results;
}
