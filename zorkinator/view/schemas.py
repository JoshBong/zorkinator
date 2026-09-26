"""Response models mirroring frontend/src/api/types.ts exactly (field names, casing, shapes).

Keep this file and the frontend's types.ts in sync by hand; there is no shared
codegen yet.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Condition = Literal["baseline", "harness", "ablation"]


class HarnessConfig(BaseModel):
    reflection: bool
    rules: bool
    memory: bool
    guardrails: bool


class RunOut(BaseModel):
    run_id: str
    condition: Condition
    model: str
    config: HarnessConfig
    status: Literal["running", "done"]
    lives_count: int
    created_at: str


class LifeOut(BaseModel):
    life: int
    score: int
    moves: int
    death_cause: str
    rules_learned: list[str]


class RetrievedRuleOut(BaseModel):
    rule_id: str
    version: int
    text: str
    similarity: float


class GuardrailOut(BaseModel):
    blocked: bool
    rule_id: str
    original_command: str
    replacement_command: str


class MoveOut(BaseModel):
    move: int
    observation: str
    command: str
    room: str
    score: int
    retrieved_rules: list[RetrievedRuleOut]
    guardrail: GuardrailOut | None
    repeated_action: bool


class ReflectionOut(BaseModel):
    cause: str
    effect: str
    lesson_text: str
    rule_id: str


RuleType = Literal["rule", "guardrail", "memory"]
RuleStatus = Literal["active", "superseded", "rolled_back"]


class LearnedFromOut(BaseModel):
    run_id: str
    life: int
    move: int


class RuleOut(BaseModel):
    rule_id: str
    version: int
    type: RuleType
    text: str
    status: RuleStatus
    learned_from: LearnedFromOut
    score_impact: float
    parent_version: int | None
    created_at: str


class MapRoomOut(BaseModel):
    name: str
    dark: bool
    first_seen_life: int
    death_lives: list[int]


class MapEdgeOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    direction: str


class RunMapOut(BaseModel):
    rooms: list[MapRoomOut]
    edges: list[MapEdgeOut]


class PerLifePointOut(BaseModel):
    life: int
    mean: float
    min: float
    max: float


class EvalConditionOut(BaseModel):
    condition: str
    config: HarnessConfig
    n_runs: int
    per_life: list[PerLifePointOut]
    avg_score: float
    avg_moves: float
    repeated_actions: int
    guardrail_blocks: int


class EvalSummaryOut(BaseModel):
    conditions: list[EvalConditionOut]
