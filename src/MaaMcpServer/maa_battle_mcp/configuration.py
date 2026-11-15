"""Observed/confirmed account configuration, not operator knowledge or live state."""

FIELDS = (
    "actual_elite",
    "actual_level",
    "potential",
    "module_id",
    "module_level",
    "equipped_skill_id",
    "skill_level",
    "mastery",
    "selected_skill",
)


def effective_configuration(context: dict, snapshot: dict) -> dict:
    preparation = context.get("preparation") or {}
    observed = (
        preparation.get("observed_fields") or {}
        if preparation.get("identity_match") is True
        else {}
    )
    result = {}
    for key in FIELDS:
        vision = observed.get(key) or {}
        user = context["loadout"].get(key) or {}
        proof = vision.get("evidence") or {}
        vision_valid = (
            vision.get("value") is not None
            and proof.get("observation_id")
            and proof.get("image_path")
            and vision.get("verification")
            in ("observed", "derived_from_observed_slot_and_unique_identity")
        )
        user_valid = (
            user.get("value") is not None
            and user.get("verification") == "user_confirmed"
        )
        conflict = (
            vision_valid
            and user.get("value") is not None
            and vision["value"] != user["value"]
        )
        selected = vision if vision_valid else user if user_valid else {}
        result[key] = {
            "value": None if conflict else selected.get("value"),
            "source": "configuration_conflict"
            if conflict
            else selected.get("source", "unknown"),
            "verification": "unknown"
            if conflict or not selected
            else selected["verification"],
            "reason": "unresolved_configuration_conflict"
            if conflict
            else None
            if selected
            else "not_measured_or_user_configuration_unverified",
            "observation_id": proof.get("observation_id"),
            "captured_at_ms": proof.get("captured_at_ms"),
            "freshness": "preparation_reconfirmed_not_live_build_measurement"
            if vision_valid
            else "user_confirmation_not_live_measurement"
            if user_valid
            else "unknown_not_measured",
            "preparation_evidence": vision or None,
            "user_supplied": user or None,
            "user_provenance": context.get("loadout_provenance"),
            "current_observation_id": snapshot.get("observation_id"),
        }
    return result
