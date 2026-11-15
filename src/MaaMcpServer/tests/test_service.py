import asyncio
import json
import sqlite3
import threading

import pytest
from pydantic import ValidationError

from maa_battle_mcp.media import MediaError
from maa_battle_mcp.models import (
    BoundedStepRequest,
    DeployRequest,
    IdentifyRequest,
    OperationRequest,
    PauseRequest,
    PreparationRequest,
    SpeedRequest,
)
from maa_battle_mcp.native import NativeError
from maa_battle_mcp.service import BattleError, BattleService

BOUNDED_COMMANDS = [
    ("step", {"action": "skill", "unit_id": "unit", "adopt_manual_pause": False}),
    ("deploy", {"unit_id": "unit", "location": [1, 2], "direction": "Right"}),
    ("retreat", {"unit_id": "unit"}),
    ("skill", {"unit_id": "unit"}),
    ("step", {"action": "pause", "adopt_manual_pause": False}),
    ("step", {"action": "speed", "multiplier": 2, "adopt_manual_pause": False}),
]


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.aborted = False
        self.fail = False
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancellations = []

    def request(self, operation, payload, timeout):
        self.calls.append((operation, payload, timeout))
        if self.fail:
            raise NativeError("test failure")
        if operation == "attach":
            return {"protocol_version": 1, "session_id": payload["session_id"]}
        if operation == "command":
            if self.block:
                self.entered.set()
                if not self.release.wait(5):
                    raise NativeError("test wait expired")
                if self.aborted:
                    raise NativeError("worker aborted")
            return {
                "status": "succeeded",
                "command_id": payload["command_id"],
                "session_id": payload["session_id"],
            }
        return {}

    def abort(self):
        self.aborted = True
        self.release.set()

    def cancel(self, session_id, command_id):
        self.cancellations.append((session_id, command_id))

    def close(self):
        self.abort()


@pytest.fixture
async def connected(settings):
    backend = FakeBackend()
    service = BattleService(settings, lambda _: backend)
    await service.connect()
    await service.attach("main_01-07")
    yield service, backend
    await service.close()
    service.journal.close()


async def test_duplicate_operation_does_not_repeat_input(connected):
    service, backend = connected
    request = OperationRequest(session_id=service.session_id, operation_id="deploy-1")
    first = await service.command("deploy", request, {"unit_id": "unit"})
    assert await service.command("deploy", request, {"unit_id": "unit"}) == first
    assert len([call for call in backend.calls if call[0] == "command"]) == 1
    with pytest.raises(BattleError, match="different parameters"):
        await service.command("retreat", request, {"unit_id": "unit"})


@pytest.mark.parametrize("action", ["deploy", "retreat", "skill"])
async def test_legacy_journal_requests_replay_without_canonical_shape_changes(
    connected, action
):
    service, backend = connected
    request = OperationRequest(session_id=service.session_id, operation_id="old")
    args = {"unit_id": "unit"}
    original = {
        "session_id": request.session_id,
        "command_id": request.operation_id,
        "action": action,
        "args": args,
        "timeout_ms": request.timeout_ms,
    }
    previous = {"status": "succeeded", "legacy_result": True}
    encoded = json.dumps(original, sort_keys=True, ensure_ascii=False)
    service.journal.execute(
        "INSERT INTO operations(session,id,request,result) VALUES(?,?,?,?)",
        (request.session_id, request.operation_id, encoded, json.dumps(previous)),
    )
    service.journal.commit()
    before = list(backend.calls)
    assert await service.command(action, request, args) == previous
    assert backend.calls == before
    for changed_action, changed_request, changed_args in (
        (action, request, {"unit_id": "different"}),
        (action, request.model_copy(update={"timeout_ms": 1000}), args),
        (
            "step",
            request,
            {"action": action, "unit_id": "unit", "adopt_manual_pause": False},
        ),
    ):
        with pytest.raises(BattleError, match="different parameters"):
            await service.command(changed_action, changed_request, changed_args)
    assert backend.calls == before


@pytest.mark.parametrize("action", ["deploy", "retreat", "skill"])
async def test_legacy_execution_preserves_running_only_authorization(connected, action):
    service, backend = connected
    request = OperationRequest(session_id=service.session_id, operation_id="legacy")
    args = {"unit_id": "unit"}
    if action == "deploy":
        args.update(location=[1, 2], direction="Right", card_id="card")
    first = await service.command(action, request, args)
    assert backend.calls[-1] == (
        "command",
        {
            "session_id": request.session_id,
            "command_id": request.operation_id,
            "action": "step",
            "args": {
                **args,
                "action": action,
                "adopt_manual_pause": False,
                "require_running": True,
            },
            "timeout_ms": request.timeout_ms,
        },
        20,
    )
    row = service.journal.execute(
        "SELECT request FROM operations WHERE session=? AND id=?",
        (request.session_id, request.operation_id),
    ).fetchone()
    persisted = json.loads(row[0])
    assert persisted["action"] == action
    assert persisted["args"] == args
    assert await service.command(action, request, args) == first
    assert len([call for call in backend.calls if call[0] == "command"]) == 1


@pytest.mark.parametrize("action", ["record", "step"])
@pytest.mark.parametrize("error_type", [MediaError, sqlite3.DatabaseError])
async def test_recording_storage_admission_failure_sends_no_input(
    connected, monkeypatch, action, error_type
):
    service, backend = connected
    before = list(backend.calls)

    def fail(*args, **kwargs):
        raise error_type("fixture reservation failure")

    monkeypatch.setattr(service.media.store, "allocate", fail)
    args = {"duration_ms": 400, "interval_ms": 200, "max_frames": 5}
    if action == "step":
        args = {
            "action": "wait",
            "duration_ms": 400,
            "recording": {"interval_ms": 200, "max_frames": 5},
        }
    request = OperationRequest(
        session_id=service.session_id, operation_id="recording-admission-failed"
    )
    result = await service.command(action, request, args)
    assert backend.calls == before
    assert result["status"] == "failed"
    assert result["input_sent"] is False
    assert result["error"]["code"] == "media_preflight_failed"
    assert service.operation(request.session_id, request.operation_id) == result
    assert service.storage_failed is (error_type is sqlite3.DatabaseError)


async def test_close_rejects_new_commands_while_offline_cleanup_is_waiting(
    connected, monkeypatch
):
    service, backend = connected
    entered = asyncio.Event()
    release = asyncio.Event()
    before = list(backend.calls)

    async def cleanup():
        entered.set()
        await release.wait()

    monkeypatch.setattr(service.media, "cancel_jobs", cleanup)
    close = asyncio.create_task(service.close())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(BattleError, match="closing"):
            await service.command(
                "step",
                OperationRequest(
                    session_id=service.session_id, operation_id="during-close"
                ),
                {"action": "wait", "duration_ms": 400},
            )
        assert backend.calls == before
    finally:
        release.set()
        await close


async def test_preparation_binding_requires_current_snapshot_and_confirmation(settings):
    class PreparationBackend(FakeBackend):
        def request(self, operation, payload, timeout):
            if operation == "preparation":
                return {
                    "status": "succeeded",
                    "snapshot": {
                        "page": "formation",
                        "operators": [{"name": "Fang", "game_ids": ["char_123_fang"]}],
                    },
                }
            return super().request(operation, payload, timeout)

    backend = PreparationBackend()
    service = BattleService(settings, lambda _: backend)
    try:
        await service.connect()
        first = await service.prepare("main_01-07", "normal")
        current = await service.prepare("main_01-07", "normal")
        with pytest.raises(BattleError, match="stale"):
            await service.attach("main_01-07", "normal", first["preparation_id"], True)
        with pytest.raises(BattleError, match="reconfirmed"):
            await service.attach("main_01-07", "normal", current["preparation_id"])
        await service.attach("main_01-07", "normal", current["preparation_id"], True)
        assert service.decision.preparation == current
        await service.invalidate_preparation()
        assert service.decision.preparation is None
    finally:
        await service.close()
        service.journal.close()


async def test_batch_preparation_replay_never_revalidates_or_navigates(settings):
    class PreparationBackend(FakeBackend):
        def request(self, operation, payload, timeout):
            if operation == "preparation":
                self.calls.append((operation, payload, timeout))
                return {
                    "status": "succeeded",
                    "preparation_valid": True,
                    "formation_restored": True,
                    "snapshot": {"operators": [], "scan_status": "partial"},
                }
            return super().request(operation, payload, timeout)

    backend = PreparationBackend()
    service = BattleService(settings, lambda _: backend)
    request = PreparationRequest(
        operation_id="batch", stage_name="main_01-07", mode="normal"
    )
    try:
        with pytest.raises(BattleError, match="requires a connection"):
            await service.inspect_preparation(request)
        await service.connect()
        first = await service.inspect_preparation(request)
        assert service.preparation_snapshot == first
        assert first["binding"]["source"] == "caller_supplied"
        assert backend.calls[-1][2] == 130
        await service.invalidate_preparation()
        assert await service.inspect_preparation(request) == first
        assert service.preparation_snapshot is None
        assert len([c for c in backend.calls if c[0] == "preparation"]) == 1
        changed = request.model_copy(update={"mode": "challenge"})
        with pytest.raises(BattleError, match="different parameters"):
            await service.inspect_preparation(changed)
        latest = await service.inspect_preparation(
            request.model_copy(update={"operation_id": "new"})
        )
        await service.attach("main_01-07", "normal", latest["preparation_id"], True)
        assert service.decision.preparation == latest
        with pytest.raises(BattleError, match="without an attached battle"):
            await service.inspect_preparation(request)
    finally:
        await service.close()
        service.journal.close()


@pytest.mark.parametrize("timeout", [0, 180_001, True, "120000"])
def test_batch_preparation_rejects_invalid_deadlines(timeout):
    with pytest.raises(ValidationError):
        PreparationRequest(
            operation_id="batch", stage_name="stage", mode="normal", timeout_ms=timeout
        )


async def test_journal_failure_is_explicit_and_never_replays(connected, monkeypatch):
    service, backend = connected
    journal = service.journal

    class FailingJournal:
        def execute(self, query, args=()):
            if query.startswith("UPDATE operations"):
                raise sqlite3.OperationalError("disk full")
            return journal.execute(query, args)

        def commit(self):
            journal.commit()

        def rollback(self):
            journal.rollback()

    monkeypatch.setattr(service, "journal", FailingJournal())
    request = OperationRequest(session_id=service.session_id, operation_id="disk-full")
    with pytest.raises(BattleError, match="could not be persisted"):
        await service.command("skill", request, {"unit_id": "u"})
    with pytest.raises(BattleError, match="do not retry"):
        await service.command("skill", request, {"unit_id": "u"})
    assert len([call for call in backend.calls if call[0] == "command"]) == 1
    monkeypatch.setattr(service, "journal", journal)


@pytest.mark.parametrize("phase", ["lookup", "insert", "commit"])
async def test_journal_admission_failure_disables_input(connected, monkeypatch, phase):
    service, backend = connected
    journal = service.journal

    class FailingJournal:
        def execute(self, query, args=()):
            if phase == "lookup" and query.startswith("SELECT request"):
                raise sqlite3.OperationalError("read failed")
            if phase == "insert" and query.startswith("INSERT INTO operations"):
                raise sqlite3.OperationalError("disk full")
            return journal.execute(query, args)

        def commit(self):
            if phase == "commit":
                raise sqlite3.OperationalError("commit failed")
            journal.commit()

        def rollback(self):
            journal.rollback()

    monkeypatch.setattr(service, "journal", FailingJournal())
    request = OperationRequest(session_id=service.session_id, operation_id="not-sent")
    before = list(backend.calls)
    with pytest.raises(BattleError, match="no device command"):
        await service.command("skill", request, {"unit_id": "unit"})
    assert service.storage_failed
    monkeypatch.setattr(service, "journal", journal)
    with pytest.raises(BattleError, match="do not retry"):
        await service.command("skill", request, {"unit_id": "unit"})
    assert backend.calls == before


async def test_stale_session_is_rejected(connected):
    service, backend = connected
    with pytest.raises(BattleError, match="expired"):
        await service.command(
            "observe", OperationRequest(session_id="old", operation_id="1")
        )
    assert not any(call[0] == "command" for call in backend.calls)


async def test_native_failure_invalidates_and_persists_unknown(connected):
    service, backend = connected
    request = OperationRequest(session_id=service.session_id, operation_id="1")
    backend.fail = True
    result = await service.command("deploy", request)
    assert result["status"] == "outcome_unknown"
    assert backend.aborted
    assert service.session_id is None
    assert service.operation(request.session_id, "1") == result
    with pytest.raises(BattleError):
        await service.command("deploy", request)


@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("identify", {"adopt_manual_pause": False}),
        ("set_paused", {"paused": True}),
        ("set_speed", {"multiplier": 2}),
    ],
)
async def test_cancel_stops_native_worker(connected, action, args):
    service, backend = connected
    backend.block = True
    request = OperationRequest(session_id=service.session_id, operation_id="1")
    task = asyncio.create_task(service.command(action, request, args))
    assert await asyncio.to_thread(backend.entered.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert backend.aborted
    assert backend.cancellations == []
    assert backend.calls[-1][2] == 10
    assert service.session_id is None
    assert service.operation(request.session_id, "1")["status"] == "outcome_unknown"


@pytest.mark.parametrize(("action", "args"), BOUNDED_COMMANDS)
async def test_cancelled_step_finishes_cleanup_before_releasing_controller(
    connected, action, args
):
    service, backend = connected
    backend.block = True
    request = OperationRequest(
        session_id=service.session_id, operation_id="step-1", timeout_ms=10_000
    )
    task = asyncio.create_task(service.command(action, request, args))
    assert await asyncio.to_thread(backend.entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert not backend.aborted
    assert backend.cancellations == [(request.session_id, request.operation_id)]
    backend.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.backend is backend
    assert service.operation(request.session_id, "step-1")["status"] == "succeeded"
    assert backend.calls[-1][2] == 20


@pytest.mark.parametrize(("action", "args"), BOUNDED_COMMANDS)
async def test_close_waits_for_bounded_step_cleanup(connected, action, args):
    service, backend = connected
    backend.block = True
    request = OperationRequest(
        session_id=service.session_id, operation_id="step-close", timeout_ms=10_000
    )
    task = asyncio.create_task(service.command(action, request, args))
    assert await asyncio.to_thread(backend.entered.wait, 2)
    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    assert not backend.aborted
    backend.release.set()
    assert (await task)["status"] == "succeeded"
    assert (await closing)["status"] == "closed"
    assert backend.aborted


@pytest.mark.parametrize(("action", "args"), BOUNDED_COMMANDS)
@pytest.mark.parametrize("closing", [True, False])
async def test_broken_cancel_channel_persists_unknown_and_releases_control(
    connected, monkeypatch, closing, action, args
):
    service, backend = connected
    backend.block = True
    request = OperationRequest(
        session_id=service.session_id, operation_id="broken-cancel"
    )
    task = asyncio.create_task(service.command(action, request, args))
    assert await asyncio.to_thread(backend.entered.wait, 2)

    def broken_cancel(*_):
        raise NativeError("cancel pipe broken")

    monkeypatch.setattr(backend, "cancel", broken_cancel)
    if closing:
        assert (await service.close())["status"] == "closed"
        assert (await task)["status"] == "outcome_unknown"
    else:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert backend.aborted
    assert not service.lease.is_locked
    result = service.operation(request.session_id, request.operation_id)
    assert result["status"] == "outcome_unknown"
    assert result["pause_verified"] is False


async def test_busy_rejects_and_close_interrupts(connected):
    service, backend = connected
    backend.block = True
    request = OperationRequest(session_id=service.session_id, operation_id="1")
    task = asyncio.create_task(service.command("identify", request))
    assert await asyncio.to_thread(backend.entered.wait, 2)
    with pytest.raises(BattleError, match="busy"):
        await service.read(request.session_id, "observe")
    assert (await service.close())["status"] == "closed"
    assert (await task)["status"] == "outcome_unknown"


async def test_stale_close_does_not_release_reconnected_controller(
    connected, settings, monkeypatch
):
    service, backend = connected
    backend.block = True
    request = OperationRequest(session_id=service.session_id, operation_id="1")
    task = asyncio.create_task(service.command("identify", request))
    assert await asyncio.to_thread(backend.entered.wait, 2)

    abort_started = threading.Event()
    finish_close = threading.Event()
    abort_lock = threading.Lock()
    original_abort = backend.abort

    def delayed_abort():
        with abort_lock:
            first = not abort_started.is_set()
            abort_started.set()
        original_abort()
        if first and not finish_close.wait(5):
            raise RuntimeError("test close wait expired")

    monkeypatch.setattr(backend, "abort", delayed_abort)
    closing = asyncio.create_task(service.close())
    second = BattleService(settings, lambda _: FakeBackend())
    try:
        assert await asyncio.to_thread(abort_started.wait, 2)
        assert (await task)["status"] == "outcome_unknown"
        replacement = FakeBackend()
        service.backend_factory = lambda _: replacement
        await service.connect()
        finish_close.set()
        with pytest.raises(BattleError, match="changed during"):
            await closing
        assert service.backend is replacement
        assert service.lease.is_locked
        await service.attach("main_01-07")
        assert (await service.read(service.session_id, "observe"))[
            "status"
        ] == "succeeded"
        with pytest.raises(BattleError, match="Another controller"):
            await second.connect()
    finally:
        finish_close.set()
        await asyncio.gather(task, closing, return_exceptions=True)
        await second.close()
        second.journal.close()


async def test_results_survive_server_restart(connected, settings):
    service, _ = connected
    request = OperationRequest(session_id=service.session_id, operation_id="1")
    result = await service.command("observe", request)
    second = BattleService(settings, lambda _: FakeBackend())
    try:
        assert second.operation(request.session_id, "1") == result
        with pytest.raises(BattleError, match="expired"):
            await second.command("observe", request)
    finally:
        second.journal.close()


async def test_user_directory_has_only_one_controller(connected, settings):
    service, _ = connected
    backend = FakeBackend()
    second = BattleService(settings, lambda _: backend)
    try:
        with pytest.raises(BattleError, match="Another controller"):
            await second.connect()
        assert not backend.calls
        await service.close()
        assert (await second.connect())["status"] == "connected"
    finally:
        await second.close()
        second.journal.close()


async def test_evidence_path_cannot_escape_state_directory(connected, settings):
    service, _ = connected
    outside = settings.user_dir.parent / "outside.png"
    outside.write_bytes(b"not an image")
    with pytest.raises(BattleError, match="Invalid screenshot"):
        service.evidence_path({"image_path": "../outside.png"})
    with pytest.raises(BattleError, match="did not return"):
        service.evidence_path({})


async def test_passive_observation_retains_same_frame_evidence(connected, settings):
    service, backend = connected
    session_id = service.session_id
    path = settings.user_dir / "battle-control" / session_id / "observed.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    result = {
        "status": "succeeded",
        "session_id": session_id,
        "command_id": "observe-frame",
        "observation_id": "frame-1",
        "captured_at_ms": 123,
        "image_path": str(path.relative_to(settings.user_dir)),
    }
    request = OperationRequest(session_id=session_id, operation_id="observe-frame")
    service._admit_operation(request, json.dumps({}))
    service._save_result(request, result)
    before = list(backend.calls)
    frame = service.evidence_frame(session_id, "observe-frame", "frame-1")
    assert frame["image_path"] == result["image_path"]
    assert frame["captured_at_ms"] == result["captured_at_ms"]
    assert frame["frame_is_live"] is False
    assert backend.calls == before


async def test_placement_evaluation_uses_one_read_only_native_command(
    connected, monkeypatch
):
    service, backend = connected
    service.map_info = {"stage_id": "fixture"}
    placements = [object()]
    prepared = [{"fixture": "prepared_static_range"}]
    source = {"fixture": "loadout"}
    source_reads = []

    def placement_source():
        assert len(backend.calls) == before
        source_reads.append(source)
        return source

    monkeypatch.setattr(
        service.decision,
        "prepare_placements",
        lambda value: prepared if value is placements else pytest.fail("wrong input"),
    )
    monkeypatch.setattr(service.decision, "placement_source", placement_source)
    before = len(backend.calls)
    result = await service.evaluate_placement(service.session_id, placements)
    assert len(backend.calls) == before + 1
    operation, payload, timeout = backend.calls[-1]
    assert operation == "command"
    assert payload["action"] == "evaluate_placement"
    assert payload["args"] == {"placements": prepared}
    assert timeout == 10
    assert result["range_source"] == source
    assert source_reads == [source]
    assert service.operation(service.session_id, result["command_id"]) == result


@pytest.mark.parametrize("failure", ["placements", "source"])
async def test_placement_metadata_failure_never_submits_native_command(
    connected, monkeypatch, failure
):
    service, backend = connected
    service.map_info = {"stage_id": "fixture"}

    def fail(_=None):
        raise ValueError("fixture metadata unavailable")

    monkeypatch.setattr(
        service.decision,
        "prepare_placements",
        fail if failure == "placements" else lambda _: [],
    )
    monkeypatch.setattr(
        service.decision,
        "placement_source",
        fail if failure == "source" else lambda: {},
    )
    before = list(backend.calls)
    with pytest.raises(ValueError, match="metadata unavailable"):
        await service.evaluate_placement(service.session_id, [])
    assert backend.calls == before
    assert service.journal.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


@pytest.mark.parametrize("timeout", [0, -1, 30_001, True, 1.5, "1000"])
def test_invalid_deadlines_are_rejected(timeout):
    with pytest.raises(ValidationError):
        OperationRequest(session_id="s", operation_id="o", timeout_ms=timeout)


@pytest.mark.parametrize(
    "location", [[-1, 0], [1], [1, 2, 3], [True, 1], [1.5, 1], [256, 1]]
)
def test_invalid_tiles_are_rejected(location):
    with pytest.raises(ValidationError):
        DeployRequest(
            session_id="s",
            operation_id="o",
            unit_id="u",
            direction="Left",
            location=location,
        )


def test_unknown_fields_and_boolean_coercion_are_rejected():
    with pytest.raises(ValidationError):
        OperationRequest(session_id="s", operation_id="o", script="click")
    with pytest.raises(ValidationError):
        PauseRequest(session_id="s", operation_id="o", paused="false")


def test_identify_requires_explicit_manual_pause_authorization():
    request = IdentifyRequest(session_id="s", operation_id="o")
    assert request.adopt_manual_pause is False
    assert IdentifyRequest(
        session_id="s", operation_id="o", adopt_manual_pause=True
    ).adopt_manual_pause


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_identify_rejects_non_boolean_authorization(value):
    with pytest.raises(ValidationError):
        IdentifyRequest(session_id="s", operation_id="o", adopt_manual_pause=value)


def test_bounded_step_requires_complete_action_arguments():
    with pytest.raises(ValidationError):
        BoundedStepRequest(
            session_id="s", operation_id="o", action="deploy", unit_id="u"
        )
    with pytest.raises(ValidationError):
        BoundedStepRequest(
            session_id="s",
            operation_id="o",
            action="skill",
            unit_id="u",
            location=[1, 2],
            direction="Left",
        )
    with pytest.raises(ValidationError):
        BoundedStepRequest(
            session_id="s",
            operation_id="o",
            timeout_ms=30_001,
            action="skill",
            unit_id="u",
        )


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"duration_ms": 0},
        {"duration_ms": 8_001},
        {"duration_ms": True},
        {"duration_ms": 1_000, "timeout_ms": 1_500},
        {"duration_ms": 1_000, "unit_id": "unit"},
        {"duration_ms": 1_000, "location": [1, 1], "direction": "Right"},
    ],
)
def test_wait_requires_a_bounded_duration_without_unit_args(extra):
    with pytest.raises(ValidationError):
        BoundedStepRequest(session_id="s", operation_id="o", action="wait", **extra)


def test_wait_request_does_not_require_a_dummy_unit():
    request = BoundedStepRequest(
        session_id="s", operation_id="o", action="wait", duration_ms=500
    )
    assert request.unit_id is None


@pytest.mark.parametrize("speed", [True, False, 0, 3, "2", 2.0])
def test_speed_rejects_non_integer_or_unsupported_multiplier(speed):
    with pytest.raises(ValidationError):
        SpeedRequest(session_id="s", operation_id="o", multiplier=speed)
