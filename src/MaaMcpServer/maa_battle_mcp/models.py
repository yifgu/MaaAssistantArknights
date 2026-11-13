from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
UnitIdentifier = Annotated[str, Field(min_length=1, max_length=137)]
Timeout = Annotated[int, Field(ge=1, le=30_000)]
Coordinate = Annotated[int, Field(ge=0, le=255)]
ClientType = Literal["Official", "Bilibili", "YoStarEN", "YoStarJP", "YoStarKR", "txwy"]
Region = Literal["cn", "en", "jp", "kr", "tw"]
BattleMode = Literal["normal", "challenge"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Settings(StrictModel):
    core_dir: Path
    resource_dir: Path
    user_dir: Path
    adb_path: str
    address: str
    connection_config: str = "General"
    touch_mode: Literal["adb", "minitouch", "maatouch", "MaaFwAdb"] = "maatouch"
    client_type: ClientType = "Official"
    allowed_summons: list[Identifier] | None = None


class OperationRequest(StrictModel):
    session_id: Identifier
    operation_id: Identifier
    timeout_ms: Timeout = 10_000


class PreparationOperationRequest(OperationRequest):
    timeout_ms: Annotated[int, Field(ge=1, le=180_000)] = 120_000


class PreparationRequest(StrictModel):
    operation_id: Identifier
    stage_name: Identifier
    mode: BattleMode
    timeout_ms: Annotated[int, Field(ge=1, le=180_000)] = 120_000


class UnitRequest(OperationRequest):
    unit_id: UnitIdentifier


class IdentifyRequest(OperationRequest):
    adopt_manual_pause: bool = False


class DeployRequest(UnitRequest):
    location: Annotated[list[Coordinate], Field(min_length=2, max_length=2)]
    direction: Literal["Right", "Down", "Left", "Up", "None"]
    card_id: Identifier | None = None


class PauseRequest(OperationRequest):
    paused: bool


class SpeedRequest(OperationRequest):
    multiplier: Annotated[int, Field(ge=1, le=2)]


class TerminalRequest(OperationRequest):
    duration_ms: Annotated[int, Field(ge=100, le=8_000)] = 2_000

    @model_validator(mode="after")
    def bounded(self):
        if self.duration_ms > self.timeout_ms:
            raise ValueError("terminal collection exceeds command deadline")
        return self


class BoundedStepRequest(OperationRequest):
    timeout_ms: Timeout = 10_000
    action: Literal["deploy", "retreat", "skill", "wait"]
    unit_id: UnitIdentifier | None = None
    duration_ms: Annotated[int, Field(ge=100, le=8_000)] | None = None
    adopt_manual_pause: bool = False
    location: Annotated[list[Coordinate], Field(min_length=2, max_length=2)] | None = (
        None
    )
    direction: Literal["Right", "Down", "Left", "Up", "None"] | None = None
    card_id: Identifier | None = None

    @model_validator(mode="after")
    def validate_action_args(self):
        if self.card_id is not None and self.action != "deploy":
            raise ValueError("card_id is only valid for deployment")
        if self.action == "wait":
            if (
                self.unit_id is not None
                or self.location is not None
                or self.direction is not None
            ):
                raise ValueError("wait does not accept a unit, location or direction")
            if self.duration_ms is None or self.duration_ms + 1_000 > self.timeout_ms:
                raise ValueError(
                    "wait requires duration_ms and at least 1000 ms of deadline headroom"
                )
            return self
        if self.unit_id is None or self.duration_ms is not None:
            raise ValueError(
                "tactical actions require unit_id and do not accept duration_ms"
            )
        if self.action == "deploy":
            if self.location is None or self.direction is None:
                raise ValueError("deploy requires location and direction")
        elif self.location is not None or self.direction is not None:
            raise ValueError("location and direction are only valid for deploy")
        return self


class IntelligenceConfig(StrictModel):
    provider: Literal["intelligence", "prts"]
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    region: Literal["cn", "en"] = "cn"


class SnapshotFile(StrictModel):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RawIntelligenceConfig(StrictModel):
    provider: Literal["raw"]
    root: Path
    repository: Identifier
    commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    data_version: Identifier
    region: Literal["cn", "en"]
    stages: dict[Identifier, SnapshotFile]
    enemy_database: SnapshotFile

    @model_validator(mode="after")
    def absolute_root(self):
        if not self.root.is_absolute():
            raise ValueError("Raw snapshot root must be absolute")
        return self


class OperatorLoadout(StrictModel):
    operator_game_id: Identifier
    actual_elite: Annotated[int, Field(ge=0, le=2)] | None = None
    actual_level: Annotated[int, Field(ge=1, le=90)] | None = None
    potential: Annotated[int, Field(ge=1, le=6)] | None = None
    module_id: Identifier | None = None
    module_level: Annotated[int, Field(ge=0, le=3)] | None = None
    equipped_skill_id: Identifier | None = None
    skill_level: Annotated[int, Field(ge=1, le=7)] | None = None
    mastery: Annotated[int, Field(ge=0, le=3)] | None = None


class LoadoutConfig(StrictModel):
    region: Region
    stage_id: Identifier
    mode: BattleMode
    confirmed_by_user: bool = False
    operators: Annotated[list[OperatorLoadout], Field(max_length=32)]

    @model_validator(mode="after")
    def unique_operators(self):
        ids = [oper.operator_game_id for oper in self.operators]
        if len(set(ids)) != len(ids):
            raise ValueError("loadout contains duplicate operator IDs")
        return self


class Placement(StrictModel):
    operator_game_id: Identifier
    location: Annotated[list[Coordinate], Field(min_length=2, max_length=2)]
    direction: Literal["Right", "Up", "Left", "Down"]
    assumed_elite: Annotated[int, Field(ge=0, le=2)] | None = None
