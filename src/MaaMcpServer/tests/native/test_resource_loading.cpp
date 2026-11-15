#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

#include "Config/Miscellaneous/BattleDataConfig.h"
#include "Config/TemplResource.h"
#include "MaaUtils/ImageIo.h"
#include "Utils/BattleGeometry.h"
#include "Utils/Platform.hpp"

void expect(bool condition, const char* name)
{
    if (!condition) {
        std::cerr << "Failed: " << name << '\n';
        throw std::runtime_error(name);
    }
}

class FixtureDirectory
{
public:
    FixtureDirectory() :
        path(
            asst::utils::path(
                "resource-loading-test-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count())))
    {
        expect(std::filesystem::create_directory(path), "create isolated fixture directory");
    }

    ~FixtureDirectory()
    {
        std::error_code error;
        std::filesystem::remove_all(path, error);
    }

    std::filesystem::path path;
};

void write_template(const std::filesystem::path& path, unsigned char value)
{
    expect(MAA_NS::imwrite(path, cv::Mat(2, 2, CV_8UC3, cv::Scalar(value, value, value))), "write template fixture");
}

void expect_template(asst::TemplResource& resource, const std::string& name, unsigned char value)
{
    const auto& image = resource.get_templ(name);
    expect(!image.empty() && image.at<cv::Vec3b>(0, 0)[0] == value, name.c_str());
}

void expect_unregistered(asst::TemplResource& resource, const std::string& name)
{
    bool rejected = false;
    try {
        resource.get_templ(name);
    }
    catch (const std::runtime_error&) {
        rejected = true;
    }
    expect(rejected, name.c_str());
}

void test_template_matching(const std::filesystem::path& root)
{
    const auto directory = root / asst::utils::path("matching");
    write_template(directory / asst::utils::path("first/path/Needle.png"), 11);
    write_template(directory / asst::utils::path("second/notpath/Needle.png"), 22);
    write_template(directory / asst::utils::path("Plain.png"), 33);
    write_template(directory / asst::utils::path("NotPlain.png"), 44);
    write_template(directory / asst::utils::path("explicit.bmp"), 55);
    write_template(directory / asst::utils::path("asset.custom.png"), 66);
    write_template(directory / asst::utils::path("Upper.PNG"), 77);

    asst::TemplResource resource;
    resource.set_load_required({ "path/Needle", "path\\Needle.png", "first/path/Needle.png", "Plain", "explicit.bmp" });
    expect(resource.load(directory), "suffix matching and explicit extensions");
    expect_template(resource, "path/Needle", 11);
    expect_template(resource, "path\\Needle.png", 11);
    expect_template(resource, "first/path/Needle.png", 11);
    expect_template(resource, "Plain", 33);
    expect_template(resource, "explicit.bmp", 55);
    expect_template(resource, "second/notpath/Needle.png", 22);
    expect_unregistered(resource, "Needle.png");
    expect_unregistered(resource, "Upper.PNG");

    const auto revision = resource.revision();
    resource.set_load_required({ "ath/Needle" });
    expect(!resource.load(directory), "suffix must begin at a path boundary");
    expect(resource.revision() == revision, "missing suffix leaves revision unchanged");
    resource.set_load_required({ "asset.custom" });
    expect(!resource.load(directory), "existing extensions do not receive PNG fallback");
    resource.set_load_required({ "missing" });
    expect(!resource.load(directory), "missing required template");

    resource.set_load_required({ "Needle" });
    expect(!resource.load(directory), "duplicate basename is rejected");
    expect(resource.revision() == revision, "duplicate basename leaves revision unchanged");
    write_template(directory / asst::utils::path("third/path/Needle.png"), 88);
    resource.set_load_required({ "path/Needle" });
    expect(!resource.load(directory), "duplicate qualified suffix is rejected");
    expect(resource.revision() == revision, "duplicate suffix leaves revision unchanged");

    resource.set_load_required({ "Upper.PNG" });
    expect(resource.load(directory), "required nonlowercase extension still loads");
    expect_template(resource, "Upper.PNG", 77);
}

void test_template_layers(const std::filesystem::path& root)
{
    const auto base = root / asst::utils::path("base");
    const auto overlay = root / asst::utils::path("overlay");
    const auto empty = root / asst::utils::path("empty");
    expect(std::filesystem::create_directory(empty), "create empty overlay");
    write_template(base / asst::utils::path("nested/Alias.png"), 10);
    write_template(base / asst::utils::path("shared/image.png"), 20);
    write_template(base / asst::utils::path("unrequired.bmp"), 30);
    write_template(overlay / asst::utils::path("replacement/Alias.png"), 40);
    write_template(overlay / asst::utils::path("shared/image.png"), 50);
    write_template(overlay / asst::utils::path("dynamic/new.png"), 60);

    asst::TemplResource resource;
    resource.set_load_required({ "Alias" });
    const auto revision = resource.revision();
    expect(resource.load(base), "base template layer");
    expect_template(resource, "Alias", 10);
    expect_template(resource, "shared/image.png", 20);
    expect_unregistered(resource, "unrequired.bmp");
    expect(resource.load(overlay), "overlay template layer");
    expect(resource.revision() == revision + 2, "each successful load advances revision");
    expect_template(resource, "Alias", 40);
    expect_template(resource, "nested/Alias.png", 10);
    expect_template(resource, "shared/image.png", 20);
    expect_template(resource, "dynamic/new.png", 60);

    write_template(overlay / asst::utils::path("replacement/Alias.png"), 41);
    expect(resource.load(overlay), "reload unchanged template path");
    expect_template(resource, "Alias", 41);
    write_template(base / asst::utils::path("shared/image.png"), 21);
    expect(resource.load(empty), "missing overlay entries fall back to earlier layers");
    expect(resource.revision() == revision + 4, "fallback load advances revision");
    expect_template(resource, "Alias", 41);
    expect_template(resource, "shared/image.png", 21);

    resource.set_load_required({ "shared/image.png" });
    expect(resource.load(overlay), "explicitly required relative path can override dynamic registration");
    expect_template(resource, "shared/image.png", 50);
}

json::value unit_metadata()
{
    return json::object {
        { "schema_version", 1 },
        { "source", json::object { { "revision", "fixture" } } },
        { "chars",
          json::object {
              { "unit",
                json::object {
                    { "game_id", "unit" },
                    { "entity_kind", "operator" },
                    { "execution_support", json::object { { "status", "supported" } } },
                    { "capabilities",
                      json::object {
                          { "standard_drag",
                            json::object { { "value", true }, { "source", "fixture" }, { "reason", "" } } },
                          { "direction",
                            json::object { { "value", false }, { "source", "fixture" }, { "reason", "" } } },
                          { "manual_retreat",
                            json::object { { "value", nullptr }, { "source", "fixture" }, { "reason", "" } } },
                      } },
                    { "skills",
                      json::array { json::object { { "skillId", "skill" } },
                                    json::object { { "skillId", "missing" } } } },
                    { "base_attributes",
                      json::array { json::object { { "range_id", "range" } },
                                    json::object { { "range_id", "missing" } } } },
                } },
          } },
        { "skills", json::object { { "skill", json::object { { "name", "fixture skill" } } } } },
        { "ranges", json::object { { "range", json::array { json::array { 0, 0 } } } } },
    };
}

void write_json(const std::filesystem::path& path, const json::value& value)
{
    std::ofstream file(path);
    file << value;
    expect(file.good(), "write metadata fixture");
}

void test_unit_metadata(const std::filesystem::path& root)
{
    asst::BattleDataConfig resource;
    const auto valid_path = root / asst::utils::path("unit_info.json");
    const auto invalid_path = root / asst::utils::path("invalid_unit_info.json");
    const auto valid = unit_metadata();
    write_json(valid_path, valid);
    expect(resource.load_unit_info(valid_path), "load valid unit metadata");
    const auto info = resource.get_unit_info("unit");
    expect(info.at("source") == valid.at("source"), "metadata source is retained");
    expect(info.at("metadata_status").as_string() == "available", "metadata availability");
    expect(info.at("skills") == valid.at("chars").at("unit").at("skills"), "unit skill references are retained");
    expect(info.at("skill_definitions") == valid.at("skills"), "referenced skill definitions are retained");
    expect(info.at("base_range_definitions") == valid.at("ranges"), "referenced ranges are retained");
    const auto capabilities = resource.get_unit_capabilities("unit");
    expect(capabilities.standard_drag == true && capabilities.direction == false, "boolean capabilities are retained");
    expect(!capabilities.manual_retreat, "unknown capability is retained");
    const auto summary = resource.get_unit_summary("unit");
    for (const auto* key :
         { "game_id", "entity_kind", "capabilities", "source", "metadata_status", "execution_support" }) {
        expect(summary.at(key) == info.at(key), "lightweight summary retains entity contract");
    }
    expect(
        !summary.contains("skills") && !summary.contains("skill_definitions") && !summary.contains("base_attributes") &&
            !summary.contains("base_range_definitions"),
        "lightweight summary omits static skill and range knowledge");
    expect(
        resource.get_unit_summary("missing").at("metadata_status").as_string() == "unavailable" &&
            !resource.get_unit_capabilities("missing").standard_drag,
        "missing unit summary and capabilities stay unknown");
    expect(resource.load_unit_info(root / asst::utils::path("missing.json")), "optional missing metadata is accepted");
    expect(resource.get_unit_info("unit") == info, "optional missing metadata preserves previous layer");

    const auto reject = [&](json::value value, const char* name) {
        write_json(invalid_path, value);
        expect(!resource.load_unit_info(invalid_path), name);
        expect(
            resource.get_unit_info("unit").at("metadata_status").as_string() == "unavailable",
            "invalid metadata clears previous unit data");
    };
    auto invalid = valid;
    invalid["schema_version"] = 2;
    reject(invalid, "unsupported metadata schema");
    for (const auto* key : { "source", "chars", "skills", "ranges" }) {
        invalid = valid;
        invalid.erase(key);
        reject(invalid, key);
        invalid = valid;
        invalid[key] = json::array();
        reject(invalid, key);
    }
    invalid = valid;
    invalid["chars"]["unit"] = false;
    reject(invalid, "unit must be an object");
    invalid = valid;
    invalid["chars"]["unit"]["game_id"] = "other";
    reject(invalid, "unit ID must match its key");
    for (const auto* key : { "entity_kind", "capabilities", "skills", "base_attributes" }) {
        invalid = valid;
        invalid["chars"]["unit"].erase(key);
        reject(invalid, key);
        invalid = valid;
        invalid["chars"]["unit"][key] = false;
        reject(invalid, key);
    }
    invalid = valid;
    invalid["chars"]["unit"]["skills"][0] = false;
    reject(invalid, "skill references must be objects");
    invalid = valid;
    invalid["chars"]["unit"]["base_attributes"][0] = false;
    reject(invalid, "base phases must be objects");
    invalid = valid;
    invalid["chars"]["unit"]["base_attributes"][0].erase("range_id");
    reject(invalid, "base phases must have a range ID");
    invalid = valid;
    invalid["chars"]["unit"]["base_attributes"][0]["range_id"] = 1;
    reject(invalid, "range IDs must be strings");
    invalid = valid;
    invalid["chars"]["unit"]["capabilities"]["standard_drag"] = false;
    reject(invalid, "capabilities must be objects");
    for (const auto* key : { "value", "source", "reason" }) {
        invalid = valid;
        invalid["chars"]["unit"]["capabilities"]["standard_drag"].erase(key);
        reject(invalid, key);
        invalid = valid;
        invalid["chars"]["unit"]["capabilities"]["standard_drag"][key] = 1;
        reject(invalid, key);
    }
    expect(resource.load_unit_info(valid_path), "valid metadata can reload after validation failure");
    expect(resource.get_unit_info("unit") == info, "reloaded metadata is intact");
}

json::value geometry_battle_data()
{
    json::object characters;
    const auto add =
        [&](const std::string& id, const std::string& role, const std::string& position, const std::string& range) {
            characters[id] = json::object {
                { "name", id },
                { "profession", role },
                { "subProfessionId",
                  role == "TANK"      ? "protector"
                  : role == "MEDIC"   ? "physician"
                  : role == "SPECIAL" ? "pusher"
                                      : "pioneer" },
                { "position", position },
                { "rangeId", json::array { range, range, range } },
                { "rarity", 5 },
            };
        };
    add("char_4228_closur", "PIONEER", "RANGED", "3-3");
    add("char_1052_kalts2", "MEDIC", "RANGED", "y-6");
    add("char_2027_wang", "SPECIAL", "RANGED", "3-3");
    add("char_2025_shu", "TANK", "MELEE", "unknown");
    add("token", "TOKEN", "NONE", "3-3");
    json::array forward, medic;
    for (int x = -1; x <= 3; ++x) {
        for (int y = -1; y <= 1; ++y) {
            if (x >= 0) {
                forward.emplace_back(json::array { x, y });
            }
            if (x <= 2) {
                medic.emplace_back(json::array { x, y });
            }
        }
    }
    for (int x : { 0, 1 }) {
        for (int y : { -2, 2 }) {
            medic.emplace_back(json::array { x, y });
        }
    }
    return json::object {
        { "chars", std::move(characters) },
        { "ranges", json::object { { "3-3", std::move(forward) }, { "y-6", std::move(medic) } } },
    };
}

json::value geometry_map()
{
    json::array tiles;
    for (int y = 0; y < 7; ++y) {
        for (int x = 0; x < 11; ++x) {
            tiles.emplace_back(
                json::object {
                    { "location", json::array { x, y } },
                    { "buildable_type", x == 8 && y == 3 ? 1 : 2 },
                });
        }
    }
    return json::object {
        { "stage_id", "act55side_ex01" }, { "coordinate_system", "maa_grid" }, { "width", 11 }, { "height", 7 },
        { "tiles", std::move(tiles) },
    };
}

json::value geometry_placement(const std::string& id, int x, int y, const std::string& direction)
{
    return json::object {
        { "operator_game_id", id },
        { "location", json::array { x, y } },
        { "direction", direction },
        { "elite", 2 },
        { "elite_source", "caller_assumption" },
    };
}

bool array_contains(const json::value& values, const json::value& expected)
{
    return std::ranges::any_of(values.as_array(), [&](const auto& value) { return value == expected; });
}

void test_battle_geometry(const std::filesystem::path& root)
{
    const auto path = root / asst::utils::path("battle_data.json");
    write_json(path, geometry_battle_data());
    expect(asst::BattleData.load(path), "load exact-ID battle geometry fixtures");
    using asst::battle::DeployDirection;
    const asst::battle::AttackRange base { { 3, 1 }, { -1, 2 }, { 0, 0 } };
    expect(asst::battle::rotate_attack_range(base, DeployDirection::Right) == base, "right rotation preserves range");
    expect(
        asst::battle::rotate_attack_range(base, DeployDirection::Up) ==
            asst::battle::AttackRange { { 1, -3 }, { 2, 1 }, { 0, 0 } },
        "up rotation uses grid coordinates");
    expect(
        asst::battle::rotate_attack_range(base, DeployDirection::Left) ==
            asst::battle::AttackRange { { -3, -1 }, { 1, -2 }, { 0, 0 } },
        "left rotation");
    expect(
        asst::battle::rotate_attack_range(base, DeployDirection::Down) ==
            asst::battle::AttackRange { { -1, 3 }, { -2, -1 }, { 0, 0 } },
        "down rotation");
    expect(
        asst::battle::rotate_attack_range(base, DeployDirection::None) == base,
        "legacy nondirectional rotation remains unchanged");

    const auto map = geometry_map();
    const std::vector<std::pair<std::string, json::value>> rotations {
        { "Right", json::array { 6, 2 } },
        { "Up", json::array { 3, 0 } },
        { "Left", json::array { 0, 2 } },
        { "Down", json::array { 3, 5 } },
    };
    for (const auto& [direction, expected] : rotations) {
        const auto result = asst::battle::evaluate_placement(
            map,
            json::array { geometry_placement("char_4228_closur", 3, 2, direction) });
        expect(result.get("status", "") == "succeeded", "directional evaluation succeeds");
        expect(
            array_contains(result.at("placements").at(0).at("base_range_cells"), expected),
            "evaluated ranges share four-direction rotation");
    }

    auto unknown_elite = geometry_placement("char_4228_closur", 3, 2, "Right");
    unknown_elite["elite"] = nullptr;
    unknown_elite["elite_source"] = "unknown";
    const auto unknown = asst::battle::evaluate_placement(
        map,
        json::array { unknown_elite, geometry_placement("char_2025_shu", 8, 3, "Left") });
    expect(unknown.at("placements").at(0).at("base_range_id").is_null(), "unknown elite has no base range ID");
    expect(
        unknown.at("placements").at(1).at("base_range_id").as_string() == "unknown",
        "missing range retains the referenced range ID");
    for (const auto& placement : unknown.at("placements").as_array()) {
        expect(
            placement.at("base_range_cells").is_null() && placement.at("geometric_allied_coverage").is_null(),
            "unknown elite or range never uses empty or profession fallback geometry");
        expect(placement.at("uncertainties").as_array().size() == 2, "unknown geometry remains explicit");
    }
    auto elite_zero = geometry_placement("char_4228_closur", 3, 2, "Right");
    elite_zero["elite"] = 0;
    elite_zero["elite_source"] = "user_supplied_loadout";
    const auto zero = asst::battle::evaluate_placement(map, json::array { elite_zero });
    expect(!zero.at("placements").at(0).at("base_range_cells").is_null(), "explicit elite zero is not unknown");

    const auto conflicts = asst::battle::evaluate_placement(
        map,
        json::array {
            geometry_placement("char_4228_closur", 8, 3, "Right"),
            geometry_placement("char_1052_kalts2", 8, 3, "Left"),
        });
    const auto& first = conflicts.at("placements").at(0);
    expect(!first.at("static_buildability_compatible").as_boolean(), "ranged unit is incompatible with melee tile");
    expect(
        first.at("conflicting_operators") == json::value(json::array { "char_1052_kalts2" }),
        "same-cell operators are reported independently of static buildability");
    expect(
        array_contains(first.at("geometric_allied_coverage"), "char_4228_closur") &&
            array_contains(first.at("geometric_allied_coverage"), "char_1052_kalts2"),
        "coverage is geometric and includes self and incompatible candidates");
    const auto candidates = asst::battle::evaluate_placement(
        map,
        json::array {
            geometry_placement("char_4228_closur", 7, 2, "Down"),
            geometry_placement("char_1052_kalts2", 6, 2, "Right"),
            geometry_placement("char_2027_wang", 7, 1, "Left"),
            geometry_placement("char_2025_shu", 8, 3, "Left"),
        });
    for (const auto& placement : candidates.at("placements").as_array()) {
        expect(
            placement.at("static_buildability_compatible").as_boolean(),
            "known compatible static tiles remain compatible");
        expect(placement.at("conflicting_operators").as_array().empty(), "distinct cells do not conflict");
    }
    expect(
        candidates.at("placements").at(1).at("geometric_allied_coverage").as_array().size() == 4,
        "medic base geometry covers all four candidate cells");

    const auto reject = [&](const json::value& placements, const char* code) {
        const auto result = asst::battle::evaluate_placement(map, placements);
        expect(result.get("status", "") == "failed" && result.at("error").get("code", "") == code, code);
    };
    const auto placement = geometry_placement("char_4228_closur", 3, 2, "Right");
    reject(json::array(), "invalid_geometry_request");
    json::array oversized;
    for (int index = 0; index < 33; ++index) {
        oversized.emplace_back(placement);
    }
    reject(oversized, "invalid_geometry_request");
    oversized.erase(32);
    reject(oversized, "duplicate_operator");
    reject(json::array { placement, placement }, "duplicate_operator");
    reject(json::array { geometry_placement("token", 0, 0, "Up") }, "unsupported_entity");
    reject(json::array { geometry_placement("missing", 0, 0, "Up") }, "unknown_operator");
    for (const auto& [key, value] : std::vector<std::pair<std::string, json::value>> {
             { "elite", true },
             { "elite", -1 },
             { "elite", 3 },
             { "elite", nullptr },
             { "direction", "None" },
             { "elite_source", "unknown" },
             { "elite_source", "live_observed" },
             { "location", json::array { 11, 0 } },
             { "location", json::array { 0.5, 0 } },
             { "unexpected", true },
         }) {
        auto invalid = placement;
        invalid[key] = value;
        reject(json::array { invalid }, "invalid_placement");
    }
    for (const auto* key : { "operator_game_id", "location", "direction", "elite", "elite_source" }) {
        auto invalid = placement;
        invalid.erase(key);
        reject(json::array { invalid }, "invalid_placement");
    }
    reject(json::object(), "invalid_geometry_request");
    auto invalid_map = map;
    invalid_map["tiles"].as_array().emplace_back(map.at("tiles").at(0));
    expect(
        asst::battle::evaluate_placement(invalid_map, json::array { placement }).get("error", "code", "") ==
            "invalid_map_geometry",
        "duplicate map cells are rejected");
}

int main()
{
#ifndef ASST_DEBUG
    std::cerr << "This regression must run in a Debug build\n";
    return 1;
#endif
    try {
        const FixtureDirectory fixture;
        test_template_matching(fixture.path);
        test_template_layers(fixture.path);
        test_unit_metadata(fixture.path);
        test_battle_geometry(fixture.path);
    }
    catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
    std::cout << "Native resource loading regressions passed\n";
}
