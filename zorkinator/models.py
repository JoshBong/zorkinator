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


# --- Atlas document contracts (docs/CONTRACTS.md is the final authority) ---


class RunDoc(ContractModel):
    """One `runs` document: a single paper-mode or harness-mode game."""

    id: StrictStr
    version_id: StrictStr | None = None
    mode: Literal["paper", "harness"]
    model: StrictStr
    seed: int
    move_cap: NonNegativeInt
    score: int = 0
    moves: NonNegativeInt = 0
    died: StrictBool = False
    death_move: NonNegativeInt | None = None
    end_reason: Literal["death", "cap", "stuck40", "gave_up"] | None = None
    tokens_in: NonNegativeInt = 0
    tokens_out: NonNegativeInt = 0
    cost_usd: float = 0.0
    started_at: datetime
    ended_at: datetime | None = None


class RejectionEntry(ContractModel):
    """One command the Verifier blocked or warned about before the Player retried."""

    cmd: StrictStr
    rule_id: StrictStr
    reason: StrictStr


class MoveDoc(ContractModel):
    """One `moves` document: a single applied command, indexed by (run_id, n)."""

    run_id: StrictStr
    n: NonNegativeInt
    room: StrictStr | None = None
    command: StrictStr
    proposals: list[StrictStr] = []
    rejections: list[RejectionEntry] = []
    text: StrictStr
    score: int
    score_delta: int = 0
    died: StrictBool = False
    latency_ms: NonNegativeInt
    ts: datetime


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
