import base64
import copy
import json
import sqlite3

import pytest
from PIL import Image

from maa_battle_mcp.preparation import add_host_fallback
from maa_battle_mcp.server import create_server
from maa_battle_mcp.service import BattleError, BattleService


def test_unknown_fields_request_multimodal_review_without_native_fact_promotion():
    proof = {
        "observation_id": "9",
        "image_path": "battle-control/s/9.png",
        "captured_at_ms": 100,
    }
    result = {
        "session_id": "s",
        "preparation_id": "p",
        "snapshot": {
            "operators": [
                {
                    "slot": 2,
                    "game_ids": ["one", "two"],
                    "fields": {
                        "identity": {
                            "value": None,
                            "reason": "identity_ambiguous",
                            "evidence": proof,
                        },
                        "actual_level": {
                            "value": None,
                            "reason": "low_confidence",
                            "evidence": proof,
                            "numeric_evidence": {"raw": {"text": "2*", "score": 0.6}},
                        },
                        "actual_elite": {
                            "value": None,
                            "reason": "no_positive_elite",
                            "evidence": proof,
                        },
                        "selected_skill": {
                            "value": None,
                            "reason": "selected_marker_unreadable",
                            "evidence": proof,
                        },
                        "equipped_skill_id": {
                            "value": None,
                            "reason": "selected_skill_not_observed",
                            "evidence": proof,
                        },
                    },
                }
            ],
        },
    }
    original = copy.deepcopy(result["snapshot"])
    add_host_fallback(result)
    assert result["snapshot"] == original
    fallback = result["host_fallback"]
    assert fallback["required"] is True
    assert fallback["host_results_accepted_as_native"] is False
    assert fallback["safety_locks_changed"] is False
    assert len(fallback["items"]) == 5
    level = next(item for item in fallback["items"] if item["field"] == "actual_level")
    assert level["raw_candidates"] == [{"text": "2*", "score": 0.6}]
    assert level["retrieval"] == {
        "tool": "battle_get_evidence_frame",
        "session_id": "s",
        "operation_id": "p",
        "observation_id": "9",
        "available": True,
    }


async def test_original_preparation_frame_retrieval_is_durable_readonly_and_allowlisted(
    settings,
):
    class Backend:
        def __init__(self):
            self.calls = []
            self.sequence = 0

        def request(self, operation, payload, timeout):
            self.calls.append(operation)
            if operation != "preparation":
                return {}
            self.sequence += 1
            observation_id = str(self.sequence)
            relative = f"battle-control/{payload['session_id']}/{observation_id}.png"
            path = settings.user_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (4, 4), "red").save(path)
            return {
                "status": "succeeded",
                "image_path": relative,
                "snapshot": {
                    "page": "formation",
                    "observation_id": observation_id,
                    "captured_at_ms": 100,
                    "operators": [{"name": "Fang", "game_ids": ["char_fang"]}],
                },
            }

        def close(self):
            pass

    backend = Backend()
    service = BattleService(settings, lambda _: backend)
    try:
        await service.connect()
        original = await service.prepare("stage", "normal")
        await service.prepare("stage", "normal")
        await service.close()
        before = list(backend.calls)
        server = create_server(service)
        result = await server.call_tool(
            "battle_get_evidence_frame",
            {
                "session_id": original["session_id"],
                "operation_id": original["preparation_id"],
                "observation_id": original["snapshot"]["observation_id"],
            },
        )
        assert not result.isError
        assert result.structuredContent["frame_is_live"] is False
        image = next(content for content in result.content if content.type == "image")
        assert (
            base64.b64decode(image.data)
            == (settings.user_dir / original["image_path"]).read_bytes()
        )
        assert backend.calls == before
        with pytest.raises(BattleError, match="Unknown or ambiguous"):
            service.evidence_frame(
                original["session_id"], original["preparation_id"], "unrecorded"
            )
        conflicting = copy.deepcopy(original)
        other = (
            settings.user_dir / "battle-control" / original["session_id"] / "other.png"
        )
        Image.new("RGB", (4, 4), "blue").save(other)
        conflicting["evidence_frames"] = [
            {
                "observation_id": "1",
                "image_path": str(other.relative_to(settings.user_dir)),
            }
        ]
        service.journal.execute(
            "UPDATE operations SET result=? WHERE session=? AND id=?",
            (
                json.dumps(conflicting),
                original["session_id"],
                original["preparation_id"],
            ),
        )
        with pytest.raises(BattleError, match="ambiguous"):
            service.evidence_frame(
                original["session_id"], original["preparation_id"], "1"
            )
        other_session = settings.user_dir / "battle-control" / "other-session" / "1.png"
        other_session.parent.mkdir()
        Image.new("RGB", (4, 4)).save(other_session)
        conflicting["evidence_frames"] = []
        conflicting["image_path"] = str(other_session.relative_to(settings.user_dir))
        service.journal.execute(
            "UPDATE operations SET result=? WHERE session=? AND id=?",
            (
                json.dumps(conflicting),
                original["session_id"],
                original["preparation_id"],
            ),
        )
        with pytest.raises(BattleError, match="does not belong"):
            service.evidence_frame(
                original["session_id"], original["preparation_id"], "1"
            )
        service.journal.execute(
            "UPDATE operations SET result=? WHERE session=? AND id=?",
            (json.dumps(original), original["session_id"], original["preparation_id"]),
        )
        outside = settings.user_dir / "outside.png"
        Image.new("RGB", (4, 4)).save(outside)
        linked = settings.user_dir / original["image_path"]
        linked.unlink()
        linked.symlink_to(outside)
        with pytest.raises(BattleError, match="Invalid screenshot path"):
            service.evidence_frame(
                original["session_id"], original["preparation_id"], "1"
            )
    finally:
        await service.close()
        service.journal.close()


async def test_failed_preparation_persistence_does_not_create_a_reusable_binding(
    settings,
):
    class Backend:
        def request(self, operation, payload, timeout):
            return {"status": "succeeded", "snapshot": {"operators": []}}

        def close(self):
            pass

    class BrokenJournal:
        def execute(self, *args):
            raise sqlite3.DatabaseError("fixture storage failure")

        def rollback(self):
            raise sqlite3.DatabaseError("fixture rollback failure")

    service = BattleService(settings, lambda _: Backend())
    journal = service.journal
    try:
        await service.connect()
        service.journal = BrokenJournal()
        with pytest.raises(BattleError, match="could not be persisted"):
            await service.prepare("stage", "normal")
        assert service.preparation_snapshot is None
        assert service.storage_failed is True
    finally:
        service.journal = journal
        await service.close()
        service.journal.close()
