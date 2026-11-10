from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128)]
Timeout = Annotated[int, Field(ge=1, le=30_000)]
Coordinate = Annotated[int, Field(ge=0, le=255)]
ClientType = Literal["Official", "Bilibili", "YoStarEN", "YoStarJP", "YoStarKR", "txwy"]


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


class OperationRequest(StrictModel):
    session_id: Identifier
    operation_id: Identifier
    timeout_ms: Timeout = 10_000


class UnitRequest(OperationRequest):
    unit_id: Identifier


class IdentifyRequest(OperationRequest):
    adopt_manual_pause: bool = False


class DeployRequest(UnitRequest):
    location: Annotated[list[Coordinate], Field(min_length=2, max_length=2)]
    direction: Literal["Right", "Down", "Left", "Up", "None"]


class PauseRequest(OperationRequest):
    paused: bool


class BoundedStepRequest(OperationRequest):
    timeout_ms: Annotated[int, Field(ge=1, le=10_000)] = 10_000
    action: Literal["deploy", "retreat", "skill"]
    unit_id: Identifier
    adopt_manual_pause: bool = False
    location: Annotated[list[Coordinate], Field(min_length=2, max_length=2)] | None = (
        None
    )
    direction: Literal["Right", "Down", "Left", "Up", "None"] | None = None

    @model_validator(mode="after")
    def validate_action_args(self):
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
