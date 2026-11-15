"""Multimodal review requests, never native facts or input authorization."""


def add_host_fallback(result: dict) -> None:
    snapshot = result.get("snapshot") or {}
    fallback = []
    initial = {
        "observation_id": snapshot.get("observation_id"),
        "captured_at_ms": snapshot.get("captured_at_ms"),
        "image_path": result.get("image_path"),
    }
    operators = snapshot.get("operators") or []
    if not operators:
        fallback.append(
            {
                "slot": None,
                "field": "roster",
                "reason": (result.get("error") or {}).get("code")
                or "no_detected_slots_does_not_prove_empty_formation",
                "evidence": initial,
                "raw_candidates": [],
            }
        )
    for index, oper in enumerate(operators):
        fields = oper.get("fields") or {}
        for key in (
            "identity",
            "actual_level",
            "actual_elite",
            "selected_skill",
            "equipped_skill_id",
        ):
            field = fields.get(key) or {}
            if key == "identity":
                value = field.get("value")
                if not fields:
                    ids = oper.get("game_ids") or []
                    value = ids[0] if oper.get("name") and len(ids) == 1 else None
            else:
                value = field.get("value", oper.get(key))
            if value is not None and field.get("verification") not in (
                "unknown",
                "unverified",
            ):
                continue
            proof = field.get("evidence") or initial
            raw = (
                field.get("raw_candidates")
                or field.get("numeric_evidence", {}).get("raw")
                or field.get("name_ocr")
                or oper.get("name_candidates")
                or []
            )
            if not isinstance(raw, list):
                raw = [raw]
            fallback.append(
                {
                    "slot": oper.get("slot", index),
                    "field": key,
                    "reason": field.get("reason")
                    or "not_inspected_or_native_recognition_unresolved",
                    "roi": field.get("roi")
                    or (oper.get("name_rect") if key == "identity" else None),
                    "evidence": proof,
                    "raw_candidates": raw,
                    "game_id_candidates": oper.get("game_ids") or [],
                    "supporting_evidence": field.get("row_order_frames") or [],
                }
            )
    for item in fallback:
        proof = item["evidence"]
        item["retrieval"] = {
            "tool": "battle_get_evidence_frame",
            "session_id": result.get("session_id"),
            "operation_id": result.get("preparation_id"),
            "observation_id": proof.get("observation_id"),
            "available": bool(proof.get("image_path") and proof.get("observation_id")),
        }
    result["host_fallback"] = {
        "required": bool(fallback),
        "scope": "multimodal_host_review_only_not_native_fact_or_input_authorization",
        "items": fallback,
        "additional_input_sent": False,
        "host_results_accepted_as_native": False,
        "safety_locks_changed": False,
        "automatic_navigation": False,
    }
