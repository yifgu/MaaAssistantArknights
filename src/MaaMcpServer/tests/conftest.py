import pytest

from maa_battle_mcp.models import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        core_dir=tmp_path / "core",
        resource_dir=tmp_path / "resources",
        user_dir=tmp_path / "state",
        adb_path="adb",
        address="127.0.0.1:5555",
    )
