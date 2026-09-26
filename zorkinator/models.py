"""Strict, runtime-validated models for contract boundaries."""

from datetime import datetime
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, NonNegativeInt, StrictBool, StrictStr


class ContractModel(BaseModel):
    """Base for data crossing component or persistence boundaries."""

    model_config = ConfigDict(extra="forbid", strict=True)


class StepResult(ContractModel):
    """Validated result of one game command."""

    text: StrictStr
    score: int
    moves: NonNegativeInt
    done: StrictBool


class StepResultPayload(TypedDict):
    """Dictionary shape required by the public adapter contract."""

    text: str
    score: int
    moves: int
    done: bool


class MoveRecord(ContractModel):
    """One logged move (`moves` collection)."""

    run_id: StrictStr
    n: NonNegativeInt
    room: StrictStr | None
    command: StrictStr
    proposals: list[StrictStr]
    rejections: list[dict[str, str]]
    text: StrictStr
    score: int
    score_delta: int
    died: StrictBool
    latency_ms: NonNegativeInt
    ts: datetime


class RunRecord(ContractModel):
    """One logged game (`runs` collection)."""

    run_id: StrictStr
    version_id: StrictStr | None
    mode: Literal["paper", "harness"]
    prompt: StrictStr
    model: StrictStr
    seed: int
    move_cap: NonNegativeInt
    score: int
    moves: NonNegativeInt
    died: StrictBool
    death_move: NonNegativeInt | None
    end_reason: Literal["death", "won", "game_over", "gave_up", "cap", "usd_cap"]
    tokens_in: NonNegativeInt
    tokens_out: NonNegativeInt
    tokens_cache_write: NonNegativeInt
    tokens_cache_read: NonNegativeInt
    cost_usd: float
    started_at: datetime
    ended_at: datetime
