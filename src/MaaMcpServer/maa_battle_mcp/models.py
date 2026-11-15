from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

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
    media_import_dir: Path | None = None


class RecordingOptions(StrictModel):
    interval_ms: Annotated[int, Field(ge=200, le=2_000)] = 500
    max_frames: Annotated[int, Field(ge=2, le=24)] = 24


class OperationRequest(StrictModel):
    session_id: Identifier
    operation_id: Identifier
    timeout_ms: Timeout = 10_000


class RecordClipRequest(OperationRequest):
    duration_ms: Annotated[int, Field(ge=100, le=8_000)] = 2_000
    interval_ms: Annotated[int, Field(ge=200, le=2_000)] = 500
    max_frames: Annotated[int, Field(ge=2, le=24)] = 24

    @model_validator(mode="after")
    def bounded_recording(self):
        if self.duration_ms + 1_000 > self.timeout_ms:
            raise ValueError("recording needs at least 1000 ms of deadline headroom")
        if (
            self.duration_ms + self.interval_ms - 1
        ) // self.interval_ms + 1 > self.max_frames:
            raise ValueError("duration/interval cannot fit the requested frame limit")
        return self


class ImportVideoRequest(StrictModel):
    operation_id: Identifier
    import_name: Identifier
    declared_context: Literal["full_battle", "mid_battle", "unknown"] = "unknown"
    timeout_ms: Annotated[int, Field(ge=1, le=120_000)] = 30_000


class AnalyzeVideoRequest(StrictModel):
    operation_id: Identifier
    artifact_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    timeout_ms: Annotated[int, Field(ge=1, le=120_000)] = 120_000


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


class StepRequest(OperationRequest):
    adopt_manual_pause: bool = False


class DeployStepRequest(StepRequest):
    action: Literal["deploy"]
    unit_id: UnitIdentifier
    location: Annotated[list[Coordinate], Field(min_length=2, max_length=2)]
    direction: Literal["Right", "Down", "Left", "Up", "None"]
    card_id: Identifier | None = None


class RetreatStepRequest(StepRequest):
    action: Literal["retreat"]
    unit_id: UnitIdentifier


class SkillStepRequest(StepRequest):
    action: Literal["skill"]
    unit_id: UnitIdentifier


class WaitStepRequest(StepRequest):
    action: Literal["wait"]
    duration_ms: Annotated[
        int,
        Field(
            ge=100,
            le=8_000,
            description="Host-time wait; must leave at least 1000 ms of timeout_ms headroom.",
        ),
    ]
    recording: RecordingOptions | None = Field(
        default=None,
        description="Wait-only sampling; duration_ms // interval_ms + 2 must not exceed max_frames.",
    )

    @model_validator(mode="after")
    def bounded_wait(self):
        if self.duration_ms + 1_000 > self.timeout_ms:
            raise ValueError("wait requires at least 1000 ms of deadline headroom")
        if self.recording is not None and (
            self.duration_ms // self.recording.interval_ms + 2
            > self.recording.max_frames
        ):
            raise ValueError(
                "wait recording duration/interval cannot fit the frame limit"
            )
        return self


class PauseStepRequest(StepRequest):
    action: Literal["pause"]


class SpeedStepRequest(StepRequest):
    action: Literal["speed"]
    multiplier: Annotated[int, Field(ge=1, le=2, json_schema_extra={"enum": [1, 2]})]


class BoundedStepRequest(
    RootModel[
        Annotated[
            DeployStepRequest
            | RetreatStepRequest
            | SkillStepRequest
            | WaitStepRequest
            | PauseStepRequest
            | SpeedStepRequest,
            Field(discriminator="action"),
        ]
    ]
):
    """One action-specific request; preserve the original keyword constructor."""

    def __getattr__(self, name: str):
        if name in (
            "unit_id",
            "duration_ms",
            "location",
            "direction",
            "card_id",
            "recording",
            "multiplier",
        ):
            return getattr(self.root, name, None)
        try:
            return super().__getattr__(name)
        except AttributeError:
            return getattr(self.root, name)


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
