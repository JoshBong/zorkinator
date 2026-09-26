"""Strict, runtime-validated models for contract boundaries."""

from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    StrictBool,
    StrictStr,
    model_validator,
)


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
    end_reason: Literal["death", "won", "game_over", "gave_up", "cap", "usd_cap", "stuck40"]
    tokens_in: NonNegativeInt
    tokens_out: NonNegativeInt
    tokens_cache_write: NonNegativeInt
    tokens_cache_read: NonNegativeInt
    cost_usd: float
    started_at: datetime
    ended_at: datetime
    chain: StrictStr | None = None
    game_index: NonNegativeInt | None = None
    start_tool_calls: list[StrictStr] = []


# --- Atlas document contracts owned by Himali (docs/CONTRACTS.md is the final authority) ---


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


# --- Outer-loop memory contracts owned by Elliott (docs/OUTER_LOOP_MEMORY.md) ---
# `lessons` was replaced by `memories` + `memory_events`; see docs/CONTRACTS.md.

NonEmptyStr = Annotated[StrictStr, Field(min_length=1)]


class EvidenceRef(ContractModel):
    """A citation to one human-visible move in a completed run."""

    run_id: NonEmptyStr
    n: NonNegativeInt


class MemoryRef(ContractModel):
    """The exact immutable revision active in one harness version."""

    memory_id: NonEmptyStr
    revision_id: NonEmptyStr


class AddMemoryOperation(ContractModel):
    """Create a new logical memory; the service assigns its persistent ids."""

    op: Literal["add"]
    key: NonEmptyStr
    kind: NonEmptyStr
    subjects: list[NonEmptyStr]
    content: dict[StrictStr, JsonValue]
    status: Literal["hypothesis", "supported", "contradicted"]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1)]
    rationale: NonEmptyStr


class ReviseMemoryOperation(ContractModel):
    """Replace an active memory revision without mutating its history."""

    op: Literal["revise"]
    memory_id: NonEmptyStr
    expected_revision_id: NonEmptyStr
    kind: NonEmptyStr
    subjects: list[NonEmptyStr]
    content: dict[StrictStr, JsonValue]
    status: Literal["hypothesis", "supported", "contradicted"]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1)]
    rationale: NonEmptyStr


class RetireMemoryOperation(ContractModel):
    """Remove a memory from the child manifest while preserving old versions."""

    op: Literal["retire"]
    memory_id: NonEmptyStr
    expected_revision_id: NonEmptyStr
    rationale: NonEmptyStr
    evidence: list[EvidenceRef]


MemoryOperation = Annotated[
    AddMemoryOperation | ReviseMemoryOperation | RetireMemoryOperation,
    Field(discriminator="op"),
]


class ReflectionProposal(ContractModel):
    """One durable, retryable outer-loop change set for a completed game."""

    proposal_id: NonEmptyStr
    run_id: NonEmptyStr
    parent_id: NonEmptyStr
    memory_ops: list[MemoryOperation]
    rule_diffs: list[dict[StrictStr, JsonValue]]
    summary: NonEmptyStr

    @model_validator(mode="after")
    def unique_operation_targets(self) -> "ReflectionProposal":
        add_keys: set[str] = set()
        memory_ids: set[str] = set()
        for operation in self.memory_ops:
            if isinstance(operation, AddMemoryOperation):
                if operation.key in add_keys:
                    raise ValueError(f"duplicate add key: {operation.key}")
                add_keys.add(operation.key)
            else:
                if operation.memory_id in memory_ids:
                    raise ValueError(f"duplicate memory operation: {operation.memory_id}")
                memory_ids.add(operation.memory_id)
        return self


class MemoryRevision(ContractModel):
    """One immutable MongoDB `memories` document."""

    revision_id: NonEmptyStr
    schema_version: Literal[1] = 1
    experiment_id: NonEmptyStr
    memory_id: NonEmptyStr
    supersedes_revision_id: NonEmptyStr | None
    kind: NonEmptyStr
    subjects: list[NonEmptyStr]
    content: dict[StrictStr, JsonValue]
    status: Literal["hypothesis", "supported", "contradicted"]
    evidence: Annotated[list[EvidenceRef], Field(min_length=1)]
    rationale: NonEmptyStr
    source_run_id: NonEmptyStr
    proposal_id: NonEmptyStr
    operation_key: NonEmptyStr
    born_version_id: NonEmptyStr
    created_at: datetime


class VersionScore(ContractModel):
    run_id: NonEmptyStr
    score: int


class HarnessVersionRecord(ContractModel):
    """A published, exact manifest of memory and rule revisions."""

    version_id: NonEmptyStr
    experiment_id: NonEmptyStr
    parent_id: NonEmptyStr | None
    source_run_id: NonEmptyStr | None
    proposal_id: NonEmptyStr | None
    memory_refs: list[MemoryRef]
    rule_ids: list[NonEmptyStr]
    context_policy: dict[StrictStr, JsonValue]
    scores: list[VersionScore]
    created_at: datetime

    @model_validator(mode="after")
    def unique_memory_ids(self) -> "HarnessVersionRecord":
        memory_ids = [reference.memory_id for reference in self.memory_refs]
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("memory_refs must contain unique memory_id values")
        if (self.source_run_id is None) != (self.proposal_id is None):
            raise ValueError("source_run_id and proposal_id must both be set or both be null")
        return self


class MemoryEvent(ContractModel):
    """An immutable proposal/audit event, not a second current-state store."""

    event_id: NonEmptyStr
    experiment_id: NonEmptyStr
    proposal_id: NonEmptyStr
    phase: Literal["proposed", "validated", "rejected", "committed", "failed"]
    source_run_id: NonEmptyStr
    parent_id: NonEmptyStr
    child_version_id: NonEmptyStr | None
    operations: list[MemoryOperation]
    memory_revision_ids: list[NonEmptyStr]
    reason: StrictStr | None
    model: NonEmptyStr
    usage: dict[StrictStr, JsonValue]
    created_at: datetime
    proposal: ReflectionProposal | None = None
