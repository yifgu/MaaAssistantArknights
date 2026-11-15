#pragma once

#include <meojson/json.hpp>

#include "Common/AsstBattleDef.h"

namespace asst::battle
{
AttackRange rotate_attack_range(const AttackRange& right_attack_range, DeployDirection direction);

// Static candidate geometry only: neither live occupancy nor dynamic terrain is inferred.
json::value evaluate_placement(const json::value& map_info, const json::value& placements);
}
