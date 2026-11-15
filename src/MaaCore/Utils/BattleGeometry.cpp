#include "BattleGeometry.h"

#include <limits>
#include <map>
#include <set>
#include <unordered_set>

#include "Config/Miscellaneous/BattleDataConfig.h"
#include "Utils/Logger.hpp"

asst::battle::AttackRange
    asst::battle::rotate_attack_range(const AttackRange& right_attack_range, DeployDirection direction)
{
    auto result = right_attack_range;
    for (auto current : { DeployDirection::Right, DeployDirection::Up, DeployDirection::Left, DeployDirection::Down }) {
        if (current == direction) {
            break;
        }
        for (auto& point : result) {
            point = { point.y, -point.x };
        }
    }
    return result;
}

json::value asst::battle::evaluate_placement(const json::value& map_info, const json::value& placements)
{
    const auto fail = [function = __FUNCTION__](const std::string& code, const std::string& message) -> json::value {
        LogWarn << function << code << message;
        return json::object {
            { "status", "failed" },
            { "error", json::object { { "code", code }, { "message", message } } },
        };
    };
    const auto integer = [](const json::value* value, int64_t minimum, int64_t maximum) {
        return value && value->is<int>() && value->as_integer() >= minimum && value->as_integer() <= maximum;
    };
    if (!map_info.is_object() || !placements.is_array()) {
        return fail("invalid_geometry_request", "Map must be an object and placements must be an array");
    }
    if (placements.as_array().empty() || placements.as_array().size() > 32) {
        return fail("invalid_geometry_request", "Placements must contain between 1 and 32 candidates");
    }
    const auto* stage_id = map_info.find_value("stage_id");
    const auto* width = map_info.find_value("width");
    const auto* height = map_info.find_value("height");
    const auto* tile_array = map_info.find_value("tiles");
    if (!stage_id || !stage_id->is_string() || stage_id->as_string().empty() ||
        !integer(width, 1, std::numeric_limits<int>::max()) || !integer(height, 1, std::numeric_limits<int>::max()) ||
        !tile_array || !tile_array->is_array()) {
        return fail("invalid_map_geometry", "Map requires a stage ID, positive integer dimensions and tiles");
    }
    if (const auto* coordinates = map_info.find_value("coordinate_system");
        coordinates && (!coordinates->is_string() || coordinates->as_string() != "maa_grid")) {
        return fail("invalid_map_geometry", "Only maa_grid coordinates are supported");
    }
    const auto valid_location = [&](const json::value* value) {
        return value && value->is_array() && value->as_array().size() == 2 &&
               integer(&value->at(0), 0, width->as_integer() - 1) &&
               integer(&value->at(1), 0, height->as_integer() - 1);
    };
    const auto point_of = [](const json::value& location) {
        return Point { static_cast<int>(location.at(0).as_integer()), static_cast<int>(location.at(1).as_integer()) };
    };
    std::map<Point, int> tiles;
    for (const auto& tile : tile_array->as_array()) {
        if (!tile.is_object() || !valid_location(tile.find_value("location")) ||
            !integer(tile.find_value("buildable_type"), -1, 3)) {
            return fail("invalid_map_geometry", "Every tile requires an in-bounds location and a valid buildable type");
        }
        if (!tiles.emplace(point_of(tile.at("location")), static_cast<int>(tile.at("buildable_type").as_integer()))
                 .second) {
            return fail("invalid_map_geometry", "Duplicate map tile locations are unsupported");
        }
    }

    struct Candidate
    {
        std::shared_ptr<OperProps> properties;
        Point location;
        DeployDirection direction;
        std::optional<size_t> elite;
    };

    static const std::map<std::string, DeployDirection> Directions {
        { "Right", DeployDirection::Right },
        { "Up", DeployDirection::Up },
        { "Left", DeployDirection::Left },
        { "Down", DeployDirection::Down },
    };
    static const std::unordered_set<std::string> Sources { "caller_assumption", "user_supplied_loadout", "unknown" };
    std::unordered_set<std::string> ids;
    std::vector<Candidate> candidates;
    candidates.reserve(placements.as_array().size());
    for (const auto& placement : placements.as_array()) {
        if (!placement.is_object()) {
            return fail("invalid_placement", "Each placement must be an object");
        }
        for (const auto& [key, value] : placement.as_object()) {
            if (key != "operator_game_id" && key != "location" && key != "direction" && key != "elite" &&
                key != "elite_source") {
                return fail("invalid_placement", "Unknown placement field: " + key);
            }
        }
        const auto* id = placement.find_value("operator_game_id");
        const auto* location = placement.find_value("location");
        const auto* direction = placement.find_value("direction");
        const auto* elite = placement.find_value("elite");
        const auto* source = placement.find_value("elite_source");
        if (!id || !id->is_string() || id->as_string().empty() || !valid_location(location) || !direction ||
            !direction->is_string() || !Directions.contains(direction->as_string()) || !elite ||
            (!elite->is_null() && !integer(elite, 0, 2)) || !source || !source->is_string() ||
            !Sources.contains(source->as_string()) || (elite->is_null() != (source->as_string() == "unknown"))) {
            return fail(
                "invalid_placement",
                "Placement requires an exact ID, in-bounds grid location, direction and explicit elite provenance");
        }
        if (!ids.emplace(id->as_string()).second) {
            return fail("duplicate_operator", "Duplicate operators require unsupported instance tracking");
        }
        const auto properties = BattleData.find_oper_by_id(id->as_string());
        if (!properties) {
            return fail("unknown_operator", "Unknown operator game ID: " + id->as_string());
        }
        if (properties->role == Role::Drone || properties->role == Role::Unknown) {
            return fail("unsupported_entity", "Summon/device coverage requires unsupported instance semantics");
        }
        candidates.emplace_back(
            Candidate {
                properties,
                point_of(*location),
                Directions.at(direction->as_string()),
                elite->is_null() ? std::nullopt : std::optional<size_t>(elite->as_integer()),
            });
    }

    json::array results;
    for (size_t index = 0; index < candidates.size(); ++index) {
        const auto& candidate = candidates[index];
        const auto& placement = placements.at(index);
        json::value range_id = nullptr;
        json::value cells = nullptr;
        json::value coverage = nullptr;
        if (candidate.elite) {
            const auto& id = candidate.properties->ranges.at(*candidate.elite);
            range_id = id;
            if (const auto range = BattleData.find_range_by_id(id)) {
                std::set<Point> absolute;
                for (const auto& point : rotate_attack_range(range->get(), candidate.direction)) {
                    const int64_t x = static_cast<int64_t>(candidate.location.x) + point.x;
                    const int64_t y = static_cast<int64_t>(candidate.location.y) + point.y;
                    if (x >= 0 && x < width->as_integer() && y >= 0 && y < height->as_integer()) {
                        const Point location { static_cast<int>(x), static_cast<int>(y) };
                        if (tiles.contains(location)) {
                            absolute.emplace(location);
                        }
                    }
                }
                cells = json::array();
                for (const auto& point : absolute) {
                    cells.as_array().emplace_back(json::array { point.x, point.y });
                }
                coverage = json::array();
                for (const auto& other : candidates) {
                    if (absolute.contains(other.location)) {
                        coverage.as_array().emplace_back(other.properties->id);
                    }
                }
            }
        }
        const auto tile = tiles.find(candidate.location);
        const auto position = candidate.properties->location_type;
        const bool compatible =
            tile != tiles.end() && position != LocationType::Invalid && position != LocationType::None &&
            (tile->second == 3 || ((tile->second == 1 || tile->second == 2) &&
                                   (position == LocationType::All || static_cast<int>(position) == tile->second)));
        json::array conflicts;
        for (size_t other = 0; other < candidates.size(); ++other) {
            if (other != index && candidates[other].location == candidate.location) {
                conflicts.emplace_back(candidates[other].properties->id);
            }
        }
        json::array uncertainties {
            "Base geometry only; no equipped-skill, talent, module, healing eligibility or terrain-effect inference.",
        };
        if (cells.is_null()) {
            uncertainties.emplace_back("Elite or range data is unknown; no fallback range was substituted.");
        }
        results.emplace_back(
            json::object {
                { "operator_game_id", placement.at("operator_game_id") },
                { "location", placement.at("location") },
                { "direction", placement.at("direction") },
                { "elite", placement.at("elite") },
                { "elite_source", placement.at("elite_source") },
                { "base_range_id", std::move(range_id) },
                { "base_range_cells", std::move(cells) },
                { "geometric_allied_coverage", std::move(coverage) },
                { "static_buildability_compatible", compatible },
                { "conflicting_operators", std::move(conflicts) },
                { "uncertainties", std::move(uncertainties) },
            });
    }
    return json::object {
        { "status", "succeeded" },
        { "scope", "candidate_formation_not_live_occupancy" },
        { "stage_id", *stage_id },
        { "coordinate_system", "maa_grid" },
        { "range_source", json::object { { "kind", "loaded_battle_data" } } },
        { "placements", std::move(results) },
        { "warnings",
          json::array {
              "Not a survival, healing, skill-effect or optimality proof. Existing battlefield occupancy must be "
              "checked separately.",
              "Static tile compatibility does not establish current occupancy or dynamic terrain legality.",
          } },
    };
}
