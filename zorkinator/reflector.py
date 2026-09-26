"""Bounded, auditable reflection over one completed harness game.

The reflector owns prompt construction and parsing only.  Runs, moves, versions,
model calls, persistence, clocks, and ids are injected so this module can be
tested without Atlas or an LLM.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

import anthropic
from openai import OpenAI
from pydantic import JsonValue, TypeAdapter, ValidationError

from .models import (
    AddMemoryOperation,
    EvidenceRef,
    HarnessVersionRecord,
    MemoryOperation,
    MemoryRevision,
    MoveRecord,
    ReflectionProposal,
    RuleDoc,
    RunRecord,
)
from .versions import DEFAULT_MAX_RULE_DIFF_BYTES, CommitError, validate_rule_diffs

_MEMORY_OPERATIONS = TypeAdapter(list[MemoryOperation])
_JSON_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class ReflectionError(ValueError):
    """The source game or model response cannot produce a valid proposal."""


class ReflectionRepository(Protocol):
    """Read/write boundary required by :class:`Reflector`."""

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def get_moves(self, run_id: str) -> Sequence[MoveRecord]: ...

    def get_version(self, version_id: str) -> HarnessVersionRecord | None: ...

    def recall(self, version_id: str, *, limit: int = 10) -> Sequence[MemoryRevision]: ...

    def get_rules(self, rule_ids: Sequence[str]) -> Sequence[RuleDoc]: ...

    def record_proposal(
        self,
        proposal: ReflectionProposal,
        *,
        experiment_id: str,
        model: str,
        created_at: datetime,
        usage: dict[str, JsonValue] | None = None,
    ) -> object: ...


@dataclass(frozen=True)
class ReflectionModelResponse:
    """Structured model transport result; ``content`` is still validated locally."""

    content: str | Mapping[str, JsonValue]
    usage: Mapping[str, JsonValue] = field(default_factory=dict)


class ReflectionModel(Protocol):
    model: str

    def generate(self, prompt: str) -> ReflectionModelResponse: ...


class AnthropicReflectionModel:
    """One-shot, JSON-constrained Anthropic transport for between-game reflection."""

    def __init__(
        self,
        model: str,
        *,
        client: anthropic.Anthropic | None = None,
        max_tokens: int = 4096,
    ) -> None:
        if not model:
            raise ValueError("model must be non-empty")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.model = model
        self._client = client or anthropic.Anthropic(max_retries=8)
        self._max_tokens = max_tokens

    def generate(self, prompt: str) -> ReflectionModelResponse:
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        content = "".join(block.text for block in response.content if block.type == "text")
        if not content:
            raise ReflectionError("reflection model returned no text content")
        usage = response.usage
        return ReflectionModelResponse(
            content=content,
            usage={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": usage.cache_creation_input_tokens or 0,
                "cache_read_input_tokens": usage.cache_read_input_tokens or 0,
            },
        )


class OpenAIReflectionModel:
    """One-shot OpenAI Responses API transport for between-game reflection."""

    def __init__(
        self,
        model: str,
        *,
        client: OpenAI | None = None,
        max_output_tokens: int = 4096,
    ) -> None:
        if not model:
            raise ValueError("model must be non-empty")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        self.model = model
        self._client = client or OpenAI(max_retries=8)
        self._max_output_tokens = max_output_tokens

    def generate(self, prompt: str) -> ReflectionModelResponse:
        response = self._client.responses.create(
            model=self.model,
            input=prompt,
            max_output_tokens=self._max_output_tokens,
            reasoning={"effort": "none"},
            store=False,
        )
        content = response.output_text
        if not content:
            raise ReflectionError("reflection model returned no text content")
        usage = response.usage
        details = None if usage is None else usage.input_tokens_details
        return ReflectionModelResponse(
            content=content,
            usage={
                "input_tokens": 0 if usage is None else usage.input_tokens,
                "output_tokens": 0 if usage is None else usage.output_tokens,
                "cache_creation_input_tokens": (
                    0 if details is None else details.cache_write_tokens
                ),
                "cache_read_input_tokens": 0 if details is None else details.cached_tokens,
            },
        )


@dataclass(frozen=True)
class ReflectorLimits:
    max_moves: int = 120
    max_memories: int = 50
    max_operations: int = 20
    max_rule_diffs: int = 10
    max_prompt_bytes: int = 128 * 1024
    max_response_bytes: int = 64 * 1024
    max_rule_diff_bytes: int = DEFAULT_MAX_RULE_DIFF_BYTES

    def __post_init__(self) -> None:
        if (
            min(
                self.max_moves,
                self.max_memories,
                self.max_operations,
                self.max_rule_diffs,
                self.max_prompt_bytes,
                self.max_response_bytes,
                self.max_rule_diff_bytes,
            )
            < 1
        ):
            raise ValueError("reflector limits must be positive")


class Reflector:
    """Create and durably record one proposal from public game evidence."""

    def __init__(
        self,
        repository: ReflectionRepository,
        model: ReflectionModel,
        *,
        limits: ReflectorLimits | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._model = model
        self._limits = limits or ReflectorLimits()
        self._id_factory = id_factory or (lambda: f"proposal_{uuid4().hex}")
        self._clock = clock or (lambda: datetime.now(UTC))

    def propose(self, run_id: str) -> ReflectionProposal:
        run = self._repository.get_run(run_id)
        if run is None:
            raise ReflectionError(f"unknown run: {run_id}")
        if run.mode != "harness" or run.version_id is None:
            raise ReflectionError("reflection requires a versioned harness run")
        if run.model != self._model.model:
            raise ReflectionError(
                f"reflector model {self._model.model!r} differs from run model {run.model!r}"
            )

        parent = self._repository.get_version(run.version_id)
        if parent is None:
            raise ReflectionError(f"run references unknown version: {run.version_id}")
        moves = sorted(self._repository.get_moves(run_id), key=lambda move: move.n)
        self._validate_transcript(run, moves)
        selected_moves = _select_moves(moves, self._limits.max_moves)
        memories = list(self._repository.recall(run.version_id, limit=self._limits.max_memories))
        selected_memory_ids = {memory.memory_id for memory in memories}
        omitted_memory_ids = [
            reference.memory_id
            for reference in parent.memory_refs
            if reference.memory_id not in selected_memory_ids
        ]
        rules = list(self._repository.get_rules(parent.rule_ids))
        prompt = self._build_prompt(
            run, parent, selected_moves, memories, omitted_memory_ids, rules
        )
        if len(prompt.encode("utf-8")) > self._limits.max_prompt_bytes:
            raise ReflectionError("bounded reflection prompt exceeds max_prompt_bytes")

        response = self._model.generate(prompt)
        if len(_serialized_response(response.content)) > self._limits.max_response_bytes:
            raise ReflectionError("reflection response exceeds max_response_bytes")
        payload = _response_payload(response.content)
        try:
            operations = _MEMORY_OPERATIONS.validate_python(payload.get("memory_ops", []))
            rule_diffs = _rule_diffs(payload.get("rule_diffs", []))
            summary = payload.get("summary")
            if not isinstance(summary, str) or not summary.strip():
                raise ReflectionError("model response summary must be a non-empty string")
            proposal = ReflectionProposal(
                proposal_id=self._id_factory(),
                run_id=run.run_id,
                parent_id=parent.version_id,
                memory_ops=operations,
                rule_diffs=rule_diffs,
                summary=summary.strip(),
            )
        except (ValidationError, TypeError, ValueError) as exc:
            if isinstance(exc, ReflectionError):
                raise
            raise ReflectionError(f"invalid reflection response: {exc}") from exc

        if len(proposal.memory_ops) > self._limits.max_operations:
            raise ReflectionError("reflection exceeds memory operation limit")
        if len(proposal.rule_diffs) > self._limits.max_rule_diffs:
            raise ReflectionError("reflection exceeds rule diff limit")
        visible_refs = {(move.run_id, move.n) for move in selected_moves}
        for operation in proposal.memory_ops:
            for evidence in operation.evidence:
                if (evidence.run_id, evidence.n) not in visible_refs:
                    raise ReflectionError(
                        f"memory evidence {evidence.run_id}:{evidence.n} was not in the prompt"
                    )
        _validate_memory_targets(proposal, parent)
        for diff in proposal.rule_diffs:
            evidence_value = diff.get("evidence", [])
            if not isinstance(evidence_value, list):
                raise ReflectionError("rule evidence must be a list")
            for raw_evidence in evidence_value:
                try:
                    evidence = EvidenceRef.model_validate(raw_evidence)
                except ValidationError as exc:
                    raise ReflectionError(f"invalid rule evidence: {exc}") from exc
                if (evidence.run_id, evidence.n) not in visible_refs:
                    raise ReflectionError(
                        f"rule evidence {evidence.run_id}:{evidence.n} was not in the prompt"
                    )

        try:
            validate_rule_diffs(
                proposal.rule_diffs,
                parent.rule_ids,
                proposal.proposal_id,
                max_rule_diff_bytes=self._limits.max_rule_diff_bytes,
            )
        except CommitError as exc:
            raise ReflectionError(f"invalid rule diff: {exc}") from exc

        self._repository.record_proposal(
            proposal,
            experiment_id=parent.experiment_id,
            model=run.model,
            created_at=self._clock(),
            usage=dict(response.usage),
        )
        return proposal

    def _build_prompt(
        self,
        run: RunRecord,
        parent: HarnessVersionRecord,
        moves: Sequence[MoveRecord],
        memories: Sequence[MemoryRevision],
        omitted_memory_ids: Sequence[str],
        rules: Sequence[RuleDoc],
    ) -> str:
        packet = {
            "run": {
                "run_id": run.run_id,
                "version_id": run.version_id,
                "score": run.score,
                "moves": run.moves,
                "died": run.died,
                "death_move": run.death_move,
                "end_reason": run.end_reason,
            },
            # Only command, public output, score, and parser-derived room are exposed.
            "transcript": [
                {
                    "run_id": move.run_id,
                    "n": move.n,
                    "room": move.room,
                    "command": move.command,
                    "text": move.text,
                    "score": move.score,
                    "score_delta": move.score_delta,
                    "died": move.died,
                    "expected": move.expected,
                    "surprise": move.surprise,
                }
                for move in moves
            ],
            "active_memories": [
                {
                    "memory_id": memory.memory_id,
                    "revision_id": memory.revision_id,
                    "kind": memory.kind,
                    "subjects": memory.subjects,
                    "content": memory.content,
                    "status": memory.status,
                }
                for memory in memories
            ],
            "memory_selection": {
                "strategy": "newest_first",
                "active_count": len(parent.memory_refs),
                "included_count": len(memories),
                "omitted_memory_ids": list(omitted_memory_ids),
            },
            "active_rules": [rule.model_dump(mode="json") for rule in rules],
            "allowed_parent": parent.version_id,
        }
        instructions = """You are the between-game Reflector for a Zork-playing harness.
Use only the supplied human-visible transcript and existing advisory memory. Propose a small,
evidence-cited change set that could help a later fresh game. Memories may describe maps, routes,
hypotheses, failures, procedures, or strategy, but must never claim current-game inventory or
location as current in a future game. Do not emit SAVE, RESTORE, RESTART, walkthrough knowledge,
game internals, valid-action lists, object trees, RAM, or world-state hashes.

Return exactly one JSON object with keys memory_ops, rule_diffs, and summary. memory_ops use:
add {op,key,kind,subjects,content,status,evidence:[{run_id,n}],rationale};
revise {op,memory_id,expected_revision_id,kind,subjects,content,status,evidence,rationale};
retire {op,memory_id,expected_revision_id,rationale,evidence}. Revisions are complete replacements.
For every add/revise: content MUST be a JSON object (for example {"text":"The trap killed me"});
status MUST be exactly "hypothesis", "supported", or "contradicted"; subjects MUST be an array of
strings. Every evidence item MUST contain one run_id string and one integer move n that appears in
the transcript--never a range, string, or summary. A valid add looks exactly like
{"op":"add","key":"trap","kind":"failure","subjects":["trap"],
"content":{"text":"Entering the trap was fatal"},"status":"supported",
"evidence":[{"run_id":"the exact visible run id","n":7}],"rationale":"Move 7 ended badly."}.
Moves carry the Player's own prediction ("expected") and whether the outcome surprised it
("surprise"): a surprise marks where its picture of the world was wrong, so prefer memories and
rules that explain deaths and surprises. Rule diffs are separate objects; new rules must cite
public evidence and are born soft. Empty arrays
are valid. Use add {op,key,text,when,verdict,evidence}, revise
{op,rule_id,key,text,when,verdict,evidence}, or retire {op,rule_id}. Never invent an evidence
reference that is absent from the supplied transcript. For add/revise, when MUST be a JSON object
using only these fields: command (regex), room, room_is_dark (true/false), carrying and
not_carrying (lists of items); for example
{"command":"(kill|attack) troll.*","not_carrying":["sword"]}.
verdict MUST be exactly "warn" or "block"; do not write natural-language strings for when
or use "soft" as the verdict. Prefer an empty rule_diffs array when no precise
machine-checkable condition follows directly from the evidence.
"""
        return f"{instructions}\nEVIDENCE_PACKET\n{json.dumps(packet, sort_keys=True)}"

    @staticmethod
    def _validate_transcript(run: RunRecord, moves: Sequence[MoveRecord]) -> None:
        if len(moves) != run.moves:
            raise ReflectionError(
                f"run records {run.moves} moves but transcript contains {len(moves)}"
            )
        expected = list(range(1, run.moves + 1))
        actual = [move.n for move in moves]
        if actual != expected or any(move.run_id != run.run_id for move in moves):
            raise ReflectionError("transcript must contain exactly the source run's ordered moves")


def propose(
    run_id: str,
    *,
    repository: ReflectionRepository,
    model: ReflectionModel,
    limits: ReflectorLimits | None = None,
) -> ReflectionProposal:
    """Contract-shaped convenience entry point with explicit dependency injection."""

    return Reflector(repository, model, limits=limits).propose(run_id)


def _validate_memory_targets(proposal: ReflectionProposal, parent: HarnessVersionRecord) -> None:
    active = {reference.memory_id: reference.revision_id for reference in parent.memory_refs}
    for operation in proposal.memory_ops:
        if isinstance(operation, AddMemoryOperation):
            continue
        revision_id = active.get(operation.memory_id)
        if revision_id is None:
            raise ReflectionError(f"memory is not active in parent: {operation.memory_id}")
        if revision_id != operation.expected_revision_id:
            raise ReflectionError(f"stale expected revision for memory {operation.memory_id}")


def _select_moves(moves: Sequence[MoveRecord], limit: int) -> list[MoveRecord]:
    """Keep outcome-relevant moves, then fill the budget from the end of the run.

    Priority: deaths, score changes, surprises (the Player's prediction failed), and the first
    entry into each room, so mid-game discoveries survive the cut in a long game.
    """

    if len(moves) <= limit:
        return list(moves)
    first_entries: set[int] = set()
    seen_rooms: set[str] = set()
    for move in sorted(moves, key=lambda m: m.n):
        if move.room and move.room not in seen_rooms:
            seen_rooms.add(move.room)
            first_entries.add(move.n)
    priority = {
        move.n
        for move in moves
        if move.died or move.score_delta != 0 or move.surprise or move.n in first_entries
    }
    selected = [move for move in moves if move.n in priority]
    if len(selected) > limit:
        selected = selected[-limit:]
    selected_numbers = {move.n for move in selected}
    for move in reversed(moves):
        if len(selected) == limit:
            break
        if move.n not in selected_numbers:
            selected.append(move)
            selected_numbers.add(move.n)
    return sorted(selected, key=lambda move: move.n)


def _response_payload(content: str | Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    if isinstance(content, Mapping):
        return dict(content)
    text = content.strip()
    match = _JSON_FENCE.fullmatch(text)
    if match:
        text = match.group(1)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReflectionError(f"model did not return valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ReflectionError("model response must be a JSON object")
    return value


def _serialized_response(content: str | Mapping[str, JsonValue]) -> bytes:
    if isinstance(content, str):
        return content.encode("utf-8")
    return json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _rule_diffs(value: JsonValue) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        raise ReflectionError("rule_diffs must be a list of objects")
    result: list[dict[str, JsonValue]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ReflectionError("rule_diffs must be a list of objects")
        result.append(item)
    return result
