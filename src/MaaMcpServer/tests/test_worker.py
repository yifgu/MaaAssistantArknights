import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from maa_battle_mcp import native
from maa_battle_mcp.service import BattleService

FIXTURE = Path(__file__).with_name("worker_fixture.py")


@pytest.fixture
def worker_factory(monkeypatch):
    popen = subprocess.Popen

    def start_worker(_command, **kwargs):
        assert ("-I" in _command) == bool(sys.flags.isolated)
        assert _command[-2:] == ["-m", "maa_battle_mcp.native"]
        assert kwargs["stdin"] == subprocess.PIPE
        assert kwargs["stdout"] == subprocess.PIPE
        assert kwargs["stderr"] is None
        return popen([sys.executable, "-B", "-u", str(FIXTURE), "worker"], **kwargs)

    monkeypatch.setattr(native.subprocess, "Popen", start_worker)
    return native.NativeBackend


def test_worker_ready_dispatch_and_close(settings, worker_factory):
    backend = worker_factory(settings)
    try:
        assert backend.request("connect", {}, 5)["core_version"] == "fixture-only"
        assert backend.ready
        assert (
            backend.request(
                "attach", {"session_id": "s", "stage_name": "main_01-07"}, 5
            )["session_id"]
            == "s"
        )
    finally:
        backend.close()
    assert backend.process.poll() is not None
    assert not backend.reader.is_alive()
    backend.abort()


@pytest.mark.parametrize(
    ("address", "phase"),
    [("startup-hang", "core_load"), ("connect-hang", "connect")],
)
def test_worker_deadline_reaps_process(settings, worker_factory, address, phase):
    settings.address = address
    backend = worker_factory(settings)
    with pytest.raises(native.NativeError, match=phase) as error:
        backend.request("connect", {}, 2)
    assert "no battle command submitted" in str(error.value)
    assert "outcome unknown" not in str(error.value)
    assert backend.process.poll() is not None
    assert not backend.reader.is_alive()
    backend.close()


def test_worker_crash_during_bootstrap(settings, worker_factory):
    settings.address = "startup-crash"
    backend = worker_factory(settings)
    with pytest.raises(native.NativeError, match="core_load"):
        backend.request("connect", {}, 5)
    assert backend.process.poll() == 7
    assert not backend.reader.is_alive()


@pytest.mark.parametrize("action", ["hang", "crash"])
def test_command_failure_remains_unknown(settings, worker_factory, action):
    backend = worker_factory(settings)
    backend.request("connect", {}, 5)
    with pytest.raises(native.NativeError) as error:
        backend.request("command", {"action": action}, 0.2)
    if action == "hang":
        assert "battle outcome unknown" in str(error.value)
    assert backend.process.poll() is not None
    assert not backend.reader.is_alive()


def test_missing_core_uses_real_worker_and_cleans_up(settings):
    backend = native.NativeBackend(settings)
    with pytest.raises(native.NativeError, match="core_load"):
        backend.request("connect", {}, 5)
    assert backend.aborted
    assert backend.process.poll() is not None
    assert not backend.reader.is_alive()


async def test_connect_timeout_releases_lease_and_allows_reconnect(
    settings, worker_factory
):
    backends = []

    def factory(config):
        backend = worker_factory(config)
        backends.append(backend)
        request = backend.request
        backend.request = lambda operation, payload, timeout: request(
            operation, payload, 2
        )
        return backend

    service = BattleService(settings, factory)
    try:
        settings.address = "connect-hang"
        with pytest.raises(native.NativeError, match="connect"):
            await service.connect()
        assert service.backend is None
        assert not service.lease.is_locked
        assert backends[0].process.poll() is not None
        settings.address = "fixture"
        assert (await service.connect())["status"] == "connected"
    finally:
        await service.close()
        service.journal.close()
    assert all(backend.process.poll() is not None for backend in backends)


async def test_connect_cancellation_reaps_worker(settings, worker_factory):
    backends = []

    def factory(config):
        backend = worker_factory(config)
        backends.append(backend)
        return backend

    settings.address = "connect-hang"
    service = BattleService(settings, factory)
    connecting = asyncio.create_task(service.connect())
    try:
        for _ in range(500):
            if backends and backends[0].ready:
                break
            await asyncio.sleep(0.01)
        assert backends and backends[0].ready
        connecting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await connecting
        assert service.backend is None
        assert not service.lease.is_locked
        assert backends[0].process.poll() is not None
        assert not backends[0].reader.is_alive()
    finally:
        connecting.cancel()
        await asyncio.gather(connecting, return_exceptions=True)
        await service.close()
        service.journal.close()
