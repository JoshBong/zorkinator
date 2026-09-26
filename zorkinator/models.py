"""Strict, runtime-validated models for contract boundaries."""

from datetime import datetime
from typing import Any, Literal, TypedDict

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
    chain: StrictStr | None = None
    game_index: NonNegativeInt | None = None


# --- Atlas document contracts owned by Hiamil (docs/CONTRACTS.md is the final authority) ---


class WorldFactDoc(ContractModel):
    """One `world_facts` document; upserted on (run_id, subject, attr), newest `move` wins."""

    run_id: StrictStr
    subject: StrictStr
    attr: StrictStr
    value: StrictStr | StrictBool | int | float
    move: NonNegativeInt


class RuleDoc(ContractModel):
    """One `rules` document: a version of a learned rule, guardrail, or memory."""

    id: StrictStr
    text: StrictStr
    when: dict[StrictStr, Any]
    verdict: Literal["warn", "block"]
    status: Literal["soft", "hard"]
    evidence: list[StrictStr] = []
    fired: NonNegativeInt = 0
    born_version: StrictStr
    promoted_version: StrictStr | None = None


class LessonDoc(ContractModel):
    """One `lessons` document: a cross-game, general-only procedure lesson."""

    id: StrictStr
    kind: Literal["procedure"]
    text: StrictStr
    evidence: list[StrictStr] = []
    born_version: StrictStr


class VersionScoreEntry(ContractModel):
    """One (run, score) pair recorded against a harness version."""

    run_id: StrictStr
    score: int


class HarnessVersionDoc(ContractModel):
    """One `harness_versions` document."""

    id: StrictStr
    parent_id: StrictStr | None = None
    rule_ids: list[StrictStr] = []
    context_policy: dict[StrictStr, Any] = {}
    scores: list[VersionScoreEntry] = []
    created_at: datetime
