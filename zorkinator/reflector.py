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

from . import carryover
from .models import (
    AddMemoryOperation,
    EvidenceRef,
    HarnessVersionRecord,
    MemoryOperation,
    MemoryRevision,
    MoveRecord,
    ReflectionProposal,
    RetireMemoryOperation,
    RuleDoc,
    RunRecord,
)
from .verifier import validate_when
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
        manifest: Callable[[HarnessVersionRecord], Sequence[MemoryRevision]] | None = None,
    ) -> None:
        """``manifest`` loads a version's complete memory set. With it, the fixed-code map
        carry-over (``carryover.derive``) joins every proposal; without it, the Reflector's
        own operations are the whole proposal."""
        self._manifest = manifest
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
        carry = (
            carryover.derive(run.run_id, moves, self._manifest(parent))
            if self._manifest is not None
            else None
        )
        memories = list(self._repository.recall(run.version_id, limit=self._limits.max_memories))
        if carry is not None:
            # Room memories are maintained by code; the model sees feedback, not the map.
            memories = [m for m in memories if m.memory_id not in carry.owned]
        selected_memory_ids = {memory.memory_id for memory in memories}
        omitted_memory_ids = [
            reference.memory_id
            for reference in parent.memory_refs
            if reference.memory_id not in selected_memory_ids
        ]
        rules = list(self._repository.get_rules(parent.rule_ids))
        prompt = self._build_prompt(
            run, parent, selected_moves, memories, omitted_memory_ids, rules, carry
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
        if carry is not None:
            visible_refs |= {
                (run.run_id, f["n"]) for f in carry.feedback if isinstance(f["n"], int)
            }
            visible_refs |= {
                (run.run_id, n)
                for f in carry.rule_feedback
                for n in (f["moves"] if isinstance(f["moves"], list) else [])
                if isinstance(n, int)
            }
        for operation in proposal.memory_ops:
            for evidence in operation.evidence:
                if (evidence.run_id, evidence.n) not in visible_refs:
                    raise ReflectionError(
                        f"memory evidence {evidence.run_id}:{evidence.n} was not in the prompt"
                    )
        if carry is not None:
            proposal = _merge_carryover(proposal, carry)
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
        proposal = _demote_ungated_rules(proposal)

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
        carry: carryover.CarryOver | None = None,
    ) -> str:
        packet: dict[str, object] = {
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
                    "warnings": move.warnings,
                }
                for move in moves
            ],
            "active_memories": [
                {
                    "memory_id": memory.memory_id,
                    "revision_id": memory.revision_id,
                    "kind": memory.kind,
                    "subjects": memory.subjects,
                    "locations": memory.locations,
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
        if carry is not None:
            packet["map"] = {
                "maintained_by": "fixed code",
                "rooms_known": carry.rooms,
                "room_updates_this_game": len(carry.ops),
            }
            packet["memory_feedback"] = carry.feedback
            packet["rule_feedback"] = carry.rule_feedback
        instructions = """You are the between-game Reflector for a Zork-playing harness.
Use only the supplied human-visible transcript and existing advisory memory. Propose a small,
evidence-cited change set that could help a later fresh game. Memories may describe maps, routes,
hypotheses, failures, procedures, or strategy, but must never claim current-game inventory or
location as current in a future game. Do not emit SAVE, RESTORE, RESTART, walkthrough knowledge,
game internals, valid-action lists, object trees, RAM, or world-state hashes.

Return exactly one JSON object with keys memory_ops, rule_diffs, and summary. memory_ops use:
add {op,key,kind,subjects,locations,content,status,evidence:[{run_id,n}],rationale};
revise {op,memory_id,expected_revision_id,kind,subjects,locations,content,status,evidence,
rationale};
retire {op,memory_id,expected_revision_id,rationale,evidence}. Revisions are complete replacements.
For every add/revise: locations MUST be an array of room or area names where the memory applies;
use [] when the memory is not location-specific. content MUST be a JSON object (for example
{"text":"The trap killed me"});
status MUST be exactly "hypothesis", "supported", or "contradicted"; subjects MUST be an array of
strings. Every evidence item MUST contain one run_id string and one integer move n that appears in
the transcript--never a range, string, or summary. A valid add looks exactly like
{"op":"add","key":"trap","kind":"failure","subjects":["trap"],
"content":{"text":"Entering the trap was fatal"},"status":"supported",
"evidence":[{"run_id":"the exact visible run id","n":7}],"rationale":"Move 7 ended badly."}.
Moves carry the Player's own prediction ("expected") and whether the outcome surprised it
("surprise"): a surprise marks where its picture of the world was wrong, so prefer memories and
rules that explain deaths and surprises. Empty arrays are valid.

Knowledge vs rules. Memories are knowledge and suggestions: what is where, what worked, what to try
("the mailbox at West of House holds a leaflet", "climbing the tree here reached a nest"). Set
locations to the rooms a memory is about; the next game shows it only in those rooms. Rules are
cautions about one risky action, checked mechanically before each command: "when" MUST include
"command", a regex naming that action, and state fields only narrow it. A danger tied to a state
still names the action that triggers it (moving while the room is dark: a movement-command regex
with "room_is_dark": true). Rule text says what went wrong ("Attacking the troll unarmed got me
killed"), never an instruction to do something. Positive advice is never a rule: a rule without
a command is stored as a location-scoped memory instead. Rules are born soft (a caution shown to
the player); only the fixed replay check can make one block. rule_feedback counts how often each
active rule matched a command this game and what followed: retire or narrow rules that fire often
with no harm, and cite a death a caution failed to prevent when proposing it as "block".
The map (kind "room": exits, items, actions tried per room) is carried over by fixed code; never
add, revise or retire "room" or "map_edge" memories. memory_feedback lists, per memory the game
loaded, the moves where play confirmed or contradicted it: revise or retire contradicted memories
(an item a thief moved is not a wrong memory). Write the shapes the next game loads into its
notes: kind "objective" or "hypothesis" or "run_summary" with content {"text": ...}; kind "item"
with content {"item": name, "text": what it does or needs} and locations [where it was found].
Use other kinds (failure, strategy, advice, ...) with {"text": ...} and locations for anything else.
Rule diffs use add {op,key,text,when,verdict,evidence}, revise
{op,rule_id,key,text,when,verdict,evidence}, or retire {op,rule_id}. Never invent an evidence
reference that is absent from the supplied transcript. For add/revise, when MUST be a JSON object
using only these fields: command (regex, required), room, room_is_dark (true/false), carrying
and not_carrying (lists of items); for example
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


def _demote_ungated_rules(proposal: ReflectionProposal) -> ReflectionProposal:
    """A rule that names no action is knowledge, not a gate: keep it as a located memory.

    Malformed diffs (no text/evidence, non-object when) are left for ``validate_rule_diffs``.
    """
    rules: list[dict[str, JsonValue]] = []
    demoted: list[MemoryOperation] = []
    for diff in proposal.rule_diffs:
        when, text, evidence = diff.get("when"), diff.get("text"), diff.get("evidence")
        if (
            diff.get("op") not in {"add", "revise"}
            or not isinstance(when, dict)
            or not validate_when(when)
            or not isinstance(text, str)
            or not text.strip()
            or not isinstance(evidence, list)
            or not evidence
        ):
            rules.append(diff)
            continue
        room = when.get("room")
        rooms = room if isinstance(room, list) else [room]
        locations = [r for r in rooms if isinstance(r, str) and r.strip()]
        demoted.append(
            AddMemoryOperation(
                op="add",
                key=f"rule-as-memory:{diff.get('key') or len(demoted)}",
                kind="caution",
                subjects=locations or ["caution"],
                locations=locations,
                content={"text": text.strip()},
                status="hypothesis",
                evidence=[EvidenceRef.model_validate(ref) for ref in evidence],
                rationale="Proposed as a rule but names no action; kept as located knowledge.",
            )
        )
    if not demoted:
        return proposal
    try:
        return ReflectionProposal(
            proposal_id=proposal.proposal_id,
            run_id=proposal.run_id,
            parent_id=proposal.parent_id,
            memory_ops=[*proposal.memory_ops, *demoted],
            rule_diffs=rules,
            summary=f"{proposal.summary} [{len(demoted)} rules without an action kept as memory]",
        )
    except ValidationError as exc:
        raise ReflectionError(f"invalid reflection response: {exc}") from exc


def _merge_carryover(
    proposal: ReflectionProposal, carry: carryover.CarryOver
) -> ReflectionProposal:
    """Model operations that touch code-owned memory are dropped; code operations join.

    Status upgrades apply only to memories the model left alone this time.
    """
    kept: list[MemoryOperation] = []
    dropped = 0
    for operation in proposal.memory_ops:
        code_kind = not isinstance(operation, RetireMemoryOperation) and (
            operation.kind in carryover.CODE_KINDS or operation.kind == "map_edge"
        )
        owned = not isinstance(operation, AddMemoryOperation) and operation.memory_id in carry.owned
        if code_kind or owned:
            dropped += 1
            continue
        kept.append(operation)
    targeted = {op.memory_id for op in kept if not isinstance(op, AddMemoryOperation)}
    upgrades = [op for op in carry.upgrades if op.memory_id not in targeted]
    summary = proposal.summary
    if carry.ops or upgrades or dropped:
        summary += (
            f" [map carry-over: {len(carry.ops)} room updates, {len(upgrades)} confirmed"
            f" hypotheses, {dropped} model map ops dropped]"
        )
    return ReflectionProposal(
        proposal_id=proposal.proposal_id,
        run_id=proposal.run_id,
        parent_id=proposal.parent_id,
        memory_ops=[*kept, *carry.ops, *upgrades],
        rule_diffs=proposal.rule_diffs,
        summary=summary,
    )


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
