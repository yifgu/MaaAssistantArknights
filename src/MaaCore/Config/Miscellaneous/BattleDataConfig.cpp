#include "BattleDataConfig.h"

#include "Utils/Logger.hpp"
#include <meojson/json.hpp>

bool asst::BattleDataConfig::load_unit_info(const std::filesystem::path& path)
{
    if (!std::filesystem::exists(path)) {
        LogWarn << __FUNCTION__ << "Optional unit metadata missing" << path;
        return true;
    }
    m_unit_info = json::object();
    const auto data = json::open(path);
    if (!data || data->get("schema_version", 0) != 1 || !data->find<json::object>("source") ||
        !data->find<json::object>("chars") || !data->find<json::object>("skills") ||
        !data->find<json::object>("ranges")) {
        LogError << __FUNCTION__ << "Invalid unit metadata" << path;
        return false;
    }
    for (const auto& [id, unit] : data->at("chars").as_object()) {
        if (!unit.is_object() || unit.get("game_id", "") != id || !unit.find<std::string>("entity_kind") ||
            !unit.find<json::object>("capabilities") || !unit.find<json::array>("skills") ||
            !unit.find<json::array>("base_attributes")) {
            LogError << __FUNCTION__ << "Invalid unit record" << id;
            return false;
        }
        for (const auto& skill : unit.at("skills").as_array()) {
            if (!skill.is_object()) {
                LogError << __FUNCTION__ << "Invalid unit skill reference" << id;
                return false;
            }
        }
        for (const auto& phase : unit.at("base_attributes").as_array()) {
            if (!phase.is_object() || !phase.find<std::string>("range_id")) {
                LogError << __FUNCTION__ << "Invalid unit base attribute phase" << id;
                return false;
            }
        }
        for (const auto& [key, capability] : unit.at("capabilities").as_object()) {
            if (!capability.is_object() || !capability.contains("value") ||
                (!capability.at("value").is_null() && !capability.at("value").is_boolean()) ||
                !capability.find<std::string>("source") || !capability.find<std::string>("reason")) {
                LogError << __FUNCTION__ << "Invalid unit capability" << id << key;
                return false;
            }
        }
    }
    m_unit_info = *data;
    return true;
}

json::value asst::BattleDataConfig::get_unit_info(const std::string& game_id) const
{
    if (!m_unit_info.contains("chars") || !m_unit_info.at("chars").contains(game_id)) {
        return json::object {
            { "game_id", game_id },
            { "entity_kind", "unknown" },
            { "metadata_status", "unavailable" },
        };
    }
    auto unit = m_unit_info.at("chars").at(game_id);
    unit["source"] = m_unit_info.at("source");
    unit["metadata_status"] = "available";
    json::object definitions;
    for (const auto& skill : unit.at("skills").as_array()) {
        if (const auto id = skill.find<std::string>("skillId"); id && m_unit_info.at("skills").contains(*id)) {
            definitions[*id] = m_unit_info.at("skills").at(*id);
        }
    }
    unit["skill_definitions"] = std::move(definitions);
    json::object ranges;
    for (const auto& phase : unit.at("base_attributes").as_array()) {
        const auto id = phase.get("range_id", "");
        if (m_unit_info.at("ranges").contains(id)) {
            ranges[id] = m_unit_info.at("ranges").at(id);
        }
    }
    unit["base_range_definitions"] = std::move(ranges);
    return unit;
}

asst::BattleDataConfig::UnitCapabilities asst::BattleDataConfig::get_unit_capabilities(const std::string& game_id) const
{
    const auto info = get_unit_info(game_id);
    UnitCapabilities result;
    if (const auto capabilities = info.find<json::object>("capabilities")) {
        auto read = [&](const std::string& key) -> std::optional<bool> {
            return capabilities->contains(key) ? capabilities->at(key).find<bool>("value") : std::nullopt;
        };
        result.standard_drag = read("standard_drag");
        result.direction = read("direction");
        result.manual_retreat = read("manual_retreat");
    }
    return result;
}

bool asst::BattleDataConfig::parse(const json::value& json)
{
    LogTraceFunction;
    m_chars_by_role.clear();
    m_chars.clear();
    m_ranges.clear();
    m_skill_ids.clear();
    m_unit_info = json::object();
    m_opers.clear();
    m_drones_confusing.clear();
    for (const auto& [id, char_data_json] : json.at("chars").as_object()) {
        std::shared_ptr<battle::OperProps> data_ptr = std::make_shared<battle::OperProps>();
        data_ptr->id = id;
        if (const auto skills = char_data_json.find<json::array>("skillIds")) {
            std::vector<std::string> ids;
            for (const auto& skill : *skills) {
                if (!skill.is_string() || skill.as_string().empty() || ids.size() >= 3 ||
                    std::ranges::find(ids, skill.as_string()) != ids.end()) {
                    LogError << __FUNCTION__ << "Invalid ordered skill IDs for operator" << id;
                    return false;
                }
                ids.emplace_back(skill.as_string());
            }
            m_skill_ids.emplace(id, std::move(ids));
        }
        std::string name = char_data_json.get("name", "");
        std::string name_en = char_data_json.get("name_en", "");
        std::string name_jp = char_data_json.get("name_jp", "");
        std::string name_kr = char_data_json.get("name_kr", "");
        std::string name_tw = char_data_json.get("name_tw", "");

        data_ptr->name = name;
        data_ptr->name_en = name_en;
        data_ptr->name_jp = name_jp;
        data_ptr->name_kr = name_kr;
        data_ptr->name_tw = name_tw;
        static const std::unordered_map<std::string, battle::Role> RoleMap = {
            { "CASTER", battle::Role::Caster },   { "MEDIC", battle::Role::Medic },
            { "PIONEER", battle::Role::Pioneer }, { "SNIPER", battle::Role::Sniper },
            { "SPECIAL", battle::Role::Special }, { "SUPPORT", battle::Role::Support },
            { "TANK", battle::Role::Tank },       { "WARRIOR", battle::Role::Warrior },
        };

        auto role_str = char_data_json.get("profession", "");
        data_ptr->role = battle::parse_role_type(role_str, battle::Role::Drone);
        if (data_ptr->role != battle::Role::Drone) {
            m_opers.emplace(name); // 所有干员名
        };

        data_ptr->sub_role = get_subrole_type(char_data_json.get("subProfessionId", ""));
        if (data_ptr->role != battle::Role::Drone && data_ptr->sub_role == battle::SubRole::Unknown) {
            LogError << "Unknown subProfessionId:" << char_data_json.get("subProfessionId", "") << "for oper:" << name;
        }
        const auto& ranges_json = char_data_json.at("rangeId").as_array();
        for (size_t i = 0; i != data_ptr->ranges.size(); ++i) {
            data_ptr->ranges.at(i) = ranges_json.at(i).as_string();
        }

        static const std::unordered_map<std::string, battle::LocationType> PositionMap = {
            { "NONE", battle::LocationType::All }, // 这种很多都是道具之类的，一般哪都能放
            { "MELEE", battle::LocationType::Melee },
            { "RANGED", battle::LocationType::Ranged },
            { "ALL", battle::LocationType::All },
        };
        if (auto iter = PositionMap.find(char_data_json.get("position", "")); iter == PositionMap.cend()) {
            Log.warn("Unknown position", char_data_json.get("position", ""));
            data_ptr->location_type = battle::LocationType::Invalid;
        }
        else {
            data_ptr->location_type = iter->second;
        }

        const auto& rarity = char_data_json.at("rarity").as_integer();
        data_ptr->rarity = rarity;
        // sortIndex 是唯一值，缺失时给 -1（0 是合法值，保留）
        data_ptr->sort_index = char_data_json.get("sortIndex", -1);
        if (auto tokens_opt = char_data_json.find<json::array>("tokens")) {
            for (const auto& token : *tokens_opt) {
                data_ptr->tokens.emplace_back(token.as_string());
                if (tokens_opt->size() > 1) {
                    m_drones_confusing.emplace(token.as_string());
                }
            }
        }

        m_chars_by_role[data_ptr->role].emplace(data_ptr->id, data_ptr);
        m_chars.emplace(data_ptr->id, std::move(data_ptr));
    }
    for (const auto& [id, points_json] : json.at("ranges").as_object()) {
        battle::AttackRange points;
        for (const auto& point : points_json.as_array()) {
            points.emplace_back(point[0].as_integer(), point[1].as_integer());
        }
        m_ranges.emplace(id, std::move(points));
    }

    return true;
}

asst::battle::SubRole asst::BattleDataConfig::get_subrole_type(const std::string& subrole_name)
{
    if (const auto iter = SubRoleNameToSubRole.find(subrole_name); iter != SubRoleNameToSubRole.end()) {
        return iter->second;
    }
    return battle::SubRole::Unknown;
}
