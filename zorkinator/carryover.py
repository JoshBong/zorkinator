"""Fixed-code carry-over: what a finished game observed becomes next game's map memory.

The Reflector (an LLM) sees a bounded slice of the transcript, so it cannot be trusted to copy
a whole map. This module replays the run's public moves through the inner loop's own scribe,
starting from the parent version's memories, and derives:

- one ``room`` memory per room this game touched (exits, blocked exits, items first seen
  there, and notable actions tried there), revised only when this game changed it;
- per-memory feedback: which loaded memories play confirmed or contradicted, with the move;
- status upgrades: a ``hypothesis`` memory that play confirmed (and never contradicted);
- per-rule feedback: how often each active rule matched a command, and whether it blocked or
  the warned command killed, so the Reflector can retire noisy cautions and cite deaths.

Input is human-visible only: commands, game text, score deltas. No LLM call, no Atlas access.
See docs/DECISIONS.md "Outer loop consumes map facts and memory feedback".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue

from . import scribe
from .models import (
    AddMemoryOperation,
    EvidenceRef,
    MemoryOperation,
    MemoryRevision,
    MoveRecord,
    ReviseMemoryOperation,
)
from .world import Room, WorldModel, direction_of

CODE_KINDS = frozenset({"room"})  # memory kinds only this module writes
MAX_TRIED = 8  # actions kept per room; notable ones (points, death) first
MAX_EVIDENCE = 5
TEXT_CHARS = 200
# Commands that say nothing about a room.
META_COMMANDS = frozenset(
    {"look", "l", "inventory", "i", "score", "wait", "z", "diagnose", "verbose", "brief", "again"}
)
RATIONALE = "Observed in play; map carried over by fixed code from the public transcript."


@dataclass(frozen=True)
class CarryOver:
    ops: list[MemoryOperation]  # room memories: always applied
    upgrades: list[ReviseMemoryOperation]  # hypothesis -> supported, unless the Reflector acts
    feedback: list[dict[str, JsonValue]]  # what play showed about each loaded memory
    rule_feedback: list[dict[str, JsonValue]] = field(default_factory=list)
    # Memory ids only this module may change; the Reflector must not target them.
    owned: frozenset[str] = field(default_factory=frozenset)
    rooms: int = 0  # rooms in the carried map after this game


def derive(run_id: str, moves: Sequence[MoveRecord], parent: Sequence[MemoryRevision]) -> CarryOver:
    world = WorldModel.empty(run_id)
    world.load(parent)
    rooms_before = {name for name, room in world.rooms.items() if room.source == "memory"}

    touched: dict[str, list[int]] = {}
    first_seen: dict[str, str] = {}  # item -> room where this game first met it
    notable: dict[tuple[str, str], str] = {}  # (room, command) -> "[died]" / "[+10 points]"
    notable_moves: dict[str, list[int]] = {}
    ordered = sorted(moves, key=lambda m: m.n)
    if ordered:
        world.state.room = ordered[0].room
    for move in ordered:
        # The inner loop never scribes a give-up, a hard-rule block, or a refused command.
        if move.command == "I give up" or move.text.startswith("["):
            continue
        before = world.state.room
        scribe.update(world, move.n, move.command, move.text, scribe.parse_stub(move.text))
        world.state.score = move.score
        for name in {before, world.state.room} - {None}:
            assert name is not None
            touched.setdefault(name, []).append(move.n)
        for item in world.items.values():
            if item.source == "this_game" and item.last_seen_room and item.name not in first_seen:
                first_seen[item.name] = item.last_seen_room
        if before and (move.died or move.score_delta):
            tag = "[died]" if move.died else f"[{move.score_delta:+d} points]"
            notable[(before, move.command.casefold().strip())] = tag
            notable_moves.setdefault(before, []).append(move.n)

    memories = {_room_name(m): m for m in parent if m.kind in CODE_KINDS and _room_name(m)}
    ops: list[MemoryOperation] = []
    for name, ns in touched.items():
        room = world.rooms.get(name)
        if room is None:
            continue
        old = memories.get(name)
        content = _room_content(room, old, first_seen, notable)
        if old is not None and old.content == content:
            continue
        cited = sorted({*notable_moves.get(name, []), ns[0], ns[-1]})[:MAX_EVIDENCE]
        evidence = [EvidenceRef(run_id=run_id, n=n) for n in cited]
        if old is None:
            ops.append(
                AddMemoryOperation(
                    op="add",
                    key=f"code:room:{name}",
                    kind="room",
                    subjects=[name],
                    locations=[name],
                    content=content,
                    status="supported",
                    evidence=evidence,
                    rationale=RATIONALE,
                )
            )
        else:
            ops.append(
                ReviseMemoryOperation(
                    op="revise",
                    memory_id=old.memory_id,
                    expected_revision_id=old.revision_id,
                    kind="room",
                    subjects=[name],
                    locations=[name],
                    content=content,
                    status="supported",
                    evidence=evidence,
                    rationale=RATIONALE,
                )
            )

    feedback: list[dict[str, JsonValue]] = [
        {
            "memory_id": mid,
            "verdict": verdict,
            "n": check["n"],
            "claim": check["claim"],
            "detail": check["detail"],
        }
        for mid, verdicts in world.memory_feedback.items()
        for verdict, checks in verdicts.items()
        for check in checks
    ]
    upgrades = [
        ReviseMemoryOperation(
            op="revise",
            memory_id=m.memory_id,
            expected_revision_id=m.revision_id,
            kind=m.kind,
            subjects=m.subjects,
            locations=m.locations,
            content=m.content,
            status="supported",
            evidence=[
                EvidenceRef(run_id=run_id, n=check["n"])
                for check in world.memory_feedback[m.memory_id]["confirmed"][:MAX_EVIDENCE]
            ],
            rationale="Confirmed in play by a later game.",
        )
        for m in parent
        if m.kind not in CODE_KINDS
        and m.status == "hypothesis"
        and world.memory_feedback.get(m.memory_id, {}).get("confirmed")
        and not world.memory_feedback[m.memory_id].get("contradicted")
    ]
    return CarryOver(
        ops=ops,
        upgrades=upgrades,
        feedback=feedback,
        rule_feedback=_rule_feedback(ordered),
        owned=frozenset(m.memory_id for m in memories.values()),
        rooms=len(rooms_before | set(touched)),
    )


def _room_content(
    room: Room,
    old: MemoryRevision | None,
    first_seen: dict[str, str],
    notable: dict[tuple[str, str], str],
) -> dict[str, JsonValue]:
    """The room as the next game should know it: memory, overridden by this game."""
    exits: dict[str, JsonValue] = {}
    blocked: dict[str, JsonValue] = {}
    # room.exits already holds the loaded memory with this game's observations on top.
    for e in sorted(room.exits.values(), key=lambda e: e.direction):
        if e.status == "blocked":
            blocked[e.direction] = e.note or "blocked"
        else:
            exits[e.direction] = e.to

    old_items = old.content.get("items") if old is not None else None
    items = {
        str(i).casefold()
        for i in (old_items if isinstance(old_items, list) else [])
        if isinstance(i, str) and first_seen.get(i.casefold(), room.name) == room.name
    }
    items |= {name for name, where in first_seen.items() if where == room.name}

    old_tried = old.content.get("tried") if old is not None else None
    tried: dict[str, str] = {
        str(c): str(o) for c, o in (old_tried.items() if isinstance(old_tried, dict) else [])
    }
    for command, outcome in room.tried.items():
        tag = notable.get((room.name, command))
        # Moves are the exits' job, unless one scored or killed.
        if tag or (direction_of(command) is None and command not in META_COMMANDS):
            tried.pop(command, None)  # re-insert: most recent last
            tried[command] = f"{outcome} {tag}" if tag else outcome
    # Keep what scored or killed, then the most recent others.
    marked = [c for c, o in tried.items() if o.endswith(("[died]", "points]"))][-MAX_TRIED:]
    others = [c for c in tried if c not in marked]
    chosen = set(marked) | set(others[len(others) - (MAX_TRIED - len(marked)) :])
    trimmed: dict[str, JsonValue] = {c: o for c, o in tried.items() if c in chosen}

    text = " ".join(room.description.split())[:TEXT_CHARS]
    content: dict[str, JsonValue] = {
        "room": room.name,
        "exits": exits,
        "blocked": blocked,
        "items": [item for item in sorted(items)],
        "tried": trimmed,
        "dark": room.dark,
        "text": text or room.name,
    }
    return content


def _rule_feedback(moves: Sequence[MoveRecord]) -> list[dict[str, JsonValue]]:
    """Per rule: moves where it warned (soft) or blocked (hard), and warned moves that killed."""
    fired: dict[str, dict[str, list[int]]] = {}
    for move in moves:
        for rule_id in move.warnings:
            fired.setdefault(rule_id, {}).setdefault("warned", []).append(move.n)
            if move.died:
                fired[rule_id].setdefault("died", []).append(move.n)
        for rejection in move.rejections:
            if rejection.get("rule_id"):
                fired.setdefault(rejection["rule_id"], {}).setdefault("blocked", []).append(move.n)
    return [
        {
            "rule_id": rule_id,
            "warned": len(hits.get("warned", [])),
            "blocked": len(hits.get("blocked", [])),
            "died_after_warning": [n for n in hits.get("died", [])],
            "moves": [n for n in sorted({*hits.get("died", []), *hits.get("warned", [])[:3]})],
        }
        for rule_id, hits in sorted(fired.items())
    ]


def _room_name(memory: MemoryRevision) -> str | None:
    name = memory.content.get("room")
    return name if isinstance(name, str) and name else None
