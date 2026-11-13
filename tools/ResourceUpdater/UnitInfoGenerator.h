#pragma once

#include <algorithm>
#include <array>
#include <ostream>
#include <string_view>

#include <meojson/json.hpp>

inline json::value merge_unit_characters(json::value characters, const json::value& patches)
{
    for (const auto& [id, character] : patches.at("patchChars").as_object()) {
        characters[id] = character;
    }
    return characters;
}

inline json::value generate_unit_info(
    const json::value& characters,
    const json::value& skill_table,
    const json::value& source,
    const json::value& ranges)
{
    json::value result = json::object {
        { "schema_version", 1 },      { "source", source }, { "chars", json::object() },
        { "skills", json::object() }, { "ranges", ranges },
    };
    for (const auto& [id, character] : characters.as_object()) {
        const auto profession = character.get("profession", "");
        static constexpr std::array<std::string_view, 8> professions {
            "CASTER", "MEDIC", "PIONEER", "SNIPER", "SPECIAL", "SUPPORT", "TANK", "WARRIOR",
        };
        const bool ordinary = std::ranges::find(professions, profession) != professions.end();
        json::value unit = json::object {
            { "game_id", id },
            { "name", character.at("name") },
            { "entity_kind",
              profession == "TOKEN"  ? "token"
              : profession == "TRAP" ? "trap"
              : ordinary             ? "operator"
                                     : "unknown" },
            { "profession", profession },
            { "position", character.get("position", "") },
            { "description", character.get("description", "") },
            { "token_relations", json::array() },
            { "base_attributes", json::array() },
            { "skills", json::array() },
            { "talents", character.contains("talents") ? character.at("talents") : json::value(nullptr) },
        };
        const bool medic_drone = id == "token_10000_silent_healrb";
        const bool standard_adapter = ordinary || medic_drone;
        auto capability = [&](json::value value) {
            const bool reviewed = !value.is_null();
            return json::object {
                { "value", std::move(value) },
                { "source", reviewed ? "reviewed_BattleHelper_standard_adapter" : "unknown" },
                { "reason",
                  reviewed ? "Reviewed adapter contract, not an engine policy or live measurement"
                           : "Game tables do not specify this interaction; no reviewed adapter" },
                { "game_data_value", nullptr },
                { "live_verified", false },
            };
        };
        unit["capabilities"] = json::object {
            { "standard_drag", capability(standard_adapter ? json::value(true) : json::value(nullptr)) },
            { "direction",
              capability(
                  ordinary      ? json::value(true)
                  : medic_drone ? json::value(false)
                                : json::value(nullptr)) },
            { "manual_retreat", capability(standard_adapter ? json::value(true) : json::value(nullptr)) },
            { "same_tile_coexistence", capability(nullptr) },
            { "dynamic_terrain", capability(nullptr) },
        };
        unit["execution_support"] = json::object {
            { "deploy", standard_adapter ? "bounded_input_only_outcome_unknown" : "unsupported" },
            { "deployment_confirmation", "unsupported_missing_tile_anchor" },
            { "retreat", "unsupported_missing_tile_anchor_and_button_availability_rule" },
            { "departure_confirmation", "verified_terminal_only" },
            { "instance_skill", ordinary ? "unsupported_missing_tile_anchor" : "unsupported" },
        };
        unit["base_range_coordinate_system"] = "game_relative_row_col_not_maa_grid";
        if (medic_drone) {
            unit["adapter_reference"] = "BattleHelper standard drag; skill_table/skcom_withdraw; MCP guarded selection";
        }
        if (id == "trap_341_dsjtzd") {
            unit["mechanism_support"] = json::object {
                { "status", "unsupported" },
                { "reason", "No validated explosion, disappearance or flip-tile visual evidence rule" },
                { "respawn_time_is_explosion_timer", false },
            };
        }
        if (const auto token = character.find<std::string>("tokenKey"); token && !token->empty()) {
            unit["token_relations"].as_array().emplace_back(
                json::object { { "game_id", *token }, { "source_field", "tokenKey" }, { "condition", nullptr } });
        }
        if (const auto phases = character.find<json::array>("phases")) {
            for (const auto& phase : *phases) {
                unit["base_attributes"].as_array().emplace_back(
                    json::object {
                        { "range_id", phase.get("rangeId", "") },
                        { "max_level", phase.get("maxLevel", 0) },
                        { "key_frames",
                          phase.contains("attributesKeyFrames") ? phase.at("attributesKeyFrames")
                                                                : json::value(nullptr) },
                    });
            }
        }
        // Empty objects are used instead of arrays for some traps in the game tables.
        if (const auto skills = character.find<json::array>("skills")) {
            for (const auto& skill : *skills) {
                unit["skills"].as_array().emplace_back(skill);
                if (const auto token = skill.find<std::string>("overrideTokenKey"); token && !token->empty()) {
                    unit["token_relations"].as_array().emplace_back(
                        json::object { { "game_id", *token },
                                       { "source_field", "skills.overrideTokenKey" },
                                       { "condition", skill } });
                }
                if (const auto skill_id = skill.find<std::string>("skillId");
                    skill_id && skill_table.contains(*skill_id)) {
                    result["skills"][*skill_id] = skill_table.at(*skill_id);
                }
            }
        }
        result["chars"][id] = std::move(unit);
    }
    return result;
}

inline void write_unit_info(std::ostream& output, const json::value& data)
{
    output << "{\n\"schema_version\":1,\n\"source\":" << data.at("source").dumps();
    for (const std::string& section : { std::string("chars"), std::string("skills"), std::string("ranges") }) {
        output << ",\n\"" << section << "\":{";
        bool first = true;
        for (const auto& [id, record] : data.at(section).as_object()) {
            output << (first ? "\n" : ",\n") << json::value(id).dumps() << ':' << record.dumps();
            first = false;
        }
        output << "\n}";
    }
    output << "\n}\n";
}
