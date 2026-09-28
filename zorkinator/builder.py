"""Context builder: a fresh prompt each move from the working KBs. No chat history.

The prefix (paper prompt + harness protocol + notes from earlier games) is fixed for the whole
game so it can be cached; the tail is rebuilt every move.
"""

from __future__ import annotations

import json
import os
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Protocol

from .models import HarnessVersionRecord, MemoryRevision, RuleDoc
from .monitor import Status
from .prompts import INITIAL_PROMPTS, PromptName
from .verifier import validate_when
from .world import WorldModel

HARNESS_PROTOCOL = """\
[Harness] The game is already running; do not reply "ready". Each turn you get your notes \
and the game's latest output. Reply with the next command on the first line. You may add \
a second line "Goal: <one sentence>" saying what you are trying to do next; it is shown \
back to you until you change it. Add a line "Expect: <one sentence>" predicting what the game \
will say after this command. When you are shown what you expected last move, also add \
"Surprise: yes" or "Surprise: no": did the game's output match your prediction?
Notes marked "from earlier games" may be wrong; check them."""

MINIMAL_PROTOCOL = """\
[Harness] The game is already running; do not reply "ready". Each turn you get your notes, \
your recent moves, and the game's latest output. Reply with only the next command.
Notes marked "from earlier games" may be wrong; check them."""


@dataclass
class PromptParts:
    prefix: str
    tail: str

    def __str__(self) -> str:
        return f"{self.prefix}\n\n{self.tail}"


def build_prefix(world: WorldModel, version_context: str = "", prompt: PromptName = "basic") -> str:
    """Fixed for the game: changes only between games, when the version changes."""
    notes = [
        *(f"- objective: {o.text}" for o in world.objectives if o.status == "open"),
        *(f"- hypothesis: {h}" for h in world.hypotheses),
        *(f"- past game: {s}" for s in world.past_runs),
    ]
    earlier = "\n".join(notes) if notes else "none yet"
    memory_map = f"\n\n{world.memory_map}" if world.memory_map else ""
    exact_version = f"\n\n{version_context}" if version_context else ""
    protocol = MINIMAL_PROTOCOL if "protocol" in ABLATE else HARNESS_PROTOCOL
    return (
        f"{INITIAL_PROMPTS[prompt]}\n\n{protocol}\n\nNotes from earlier games:\n{earlier}"
        f"{memory_map}{exact_version}"
    )


def build_tail(
    world: WorldModel,
    n: int,
    last_output: str,
    status: Status = "ok",
    expected: str | None = None,
    cautions: Sequence[str] = (),
    warned: Sequence[str] = (),
) -> str:
    """``cautions``: learned rules whose conditions hold here (checked before the Player
    chooses). ``warned``: cautions the previous command matched; it still ran."""
    state = world.state
    here = None if state.in_dark else world.here
    lines = [f"Move {n}. Score {state.score}."]

    if state.in_dark:
        lines.append("Location: somewhere dark (no room description visible).")
    elif here is not None:
        lines.append(
            f"Location: {here.name}"
            # The full description: it is the puzzle text (the rug, the sword, the lantern).
            + (f" — {_short(here.description, ROOM_TEXT_CHARS)}" if here.description else "")
        )
    else:
        lines.append("Location: unknown.")

    if here is not None and here.exits and "exits" not in ABLATE:
        exits = []
        for e in sorted(here.exits.values(), key=lambda e: e.direction):
            if e.status == "known":
                earlier = " (from earlier games)" if e.source == "memory" else ""
                exits.append(f"{e.direction} -> {e.to}{earlier}")
            elif e.status == "blocked":
                exits.append(f"{e.direction} blocked ({e.note})")
            else:
                exits.append(f"{e.direction} (mentioned, never taken)")
        lines.append("Exits seen: " + "; ".join(exits))
    if here is not None and not state.in_dark and "untried" not in ABLATE:
        used = {e.direction for e in here.exits.values()} | {c.casefold() for c in here.tried}
        untried = [d for d in COMPASS if d not in used]
        if untried:
            lines.append(
                "Room text often leaves exits out. Directions never tried here: "
                + ", ".join(untried)
            )
    if "state" not in ABLATE and here is not None and here.items_seen:
        lines.append("Seen here: " + ", ".join(sorted(here.items_seen)))
    if here is not None:
        remembered = sorted(
            i.name
            for i in world.items.values()
            if i.source == "memory" and i.last_seen_room == here.name and not i.carried
        )
        if remembered:
            lines.append("Earlier games saw here: " + ", ".join(remembered))
    if here is not None and here.remembered:
        earlier_tries = [(c, o) for c, o in here.remembered.items() if c not in here.tried]
        if earlier_tries:
            lines.append(
                "Earlier games tried here: " + "; ".join(f"{c} -> {o}" for c, o in earlier_tries)
            )
    if world.routed:
        lines.append(
            "Notebook notes that matter now (from earlier games, may be wrong):\n"
            + "\n".join(f"- {r}" for r in world.routed)
        )
    notes = [] if state.in_dark else world.notes_here()
    if notes:
        lines.append(
            "Notes for this room (from earlier games, may be wrong):\n"
            + "\n".join(f"- {note}" for note in notes)
        )
    if "state" not in ABLATE and here is not None and here.tried:
        tried = list(here.tried.items())[-6:]
        lines.append("Already tried here: " + "; ".join(f"{c} -> {o}" for c, o in tried))

    if "state" not in ABLATE:
        lines.append("Carrying: " + (", ".join(world.inventory) or "nothing known"))
    if "protocol" not in ABLATE:
        lines.append(f"Goal: {state.goal or 'none set'}")

    leads = [] if "leads" in ABLATE else world.leads()
    if state.in_dark:
        leads = []
    if leads:
        lines.append("Open leads:\n" + "\n".join(f"- {lead}" for lead in leads[:8]))
    if state.recent and "rawhist" in ABLATE:
        lines.append(
            "Recent moves (full game output):\n"
            + "\n".join(
                f"> {s.command}\n{s.text or s.outcome}" for s in list(state.recent)[-RAW_MOVES:]
            )
        )
    elif state.recent:
        lines.append(
            "Recent moves:\n"
            + "\n".join(f"- {s.n}. {s.command} -> {s.outcome}" for s in state.recent)
        )
    if status in {"repeat", "idle"}:
        why = (
            "you just repeated a command here with the same result"
            if status == "repeat"
            else "no new room, item, or score for a while"
        )
        lines.append(
            f"Note: {why}. Pick one of the open leads above and pursue it (walk the map you "
            "know to get there), or try an action nobody has tried in this room."
        )

    if cautions:
        lines.append(
            "Cautions (learned from earlier games):\n" + "\n".join(f"- {c}" for c in cautions)
        )
    if warned:
        lines.append("Your last command matched a caution: " + "; ".join(warned))
    if expected and "protocol" not in ABLATE:
        lines.append(f"Last move you expected: {expected}")
    lines.append(f"Game output:\n{last_output.strip() or '(no output)'}")
    return "\n".join(lines)


def build_prompt(
    world: WorldModel,
    n: int,
    last_output: str,
    status: Status = "ok",
    *,
    version_context: str = "",
    prompt: PromptName = "basic",
    expected: str | None = None,
    cautions: Sequence[str] = (),
    warned: Sequence[str] = (),
) -> PromptParts:
    return PromptParts(
        build_prefix(world, version_context, prompt),
        build_tail(world, n, last_output, status, expected, cautions, warned),
    )


ROOM_TEXT_CHARS = 700
RAW_MOVES = int(os.getenv("ZK_RAW_MOVES", "20"))
ABLATE = frozenset(filter(None, os.getenv("ZK_ABLATE", "").split(",")))
COMPASS = (
    "north",
    "south",
    "east",
    "west",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
    "up",
    "down",
)


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class PromptRepository(Protocol):
    """Exact-version reads needed to materialize a prompt manifest."""

    def get_version(self, version_id: str) -> HarnessVersionRecord | None: ...

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None: ...

    def get_rules(self, rule_ids: list[str]) -> list[RuleDoc]: ...


def resolve_manifest(
    version: HarnessVersionRecord, *, repository: PromptRepository
) -> tuple[list[MemoryRevision], list[RuleDoc]]:
    """The exact memory revisions and rules ``version`` references, in manifest order.

    Never queries a global/latest version and fails closed on a mismatched ref.
    """
    memories: list[MemoryRevision] = []
    for reference in version.memory_refs:
        memory = repository.read(version.version_id, reference.memory_id)
        if memory is None or memory.revision_id != reference.revision_id:
            raise ValueError(
                f"version {version.version_id!r} cannot resolve exact memory "
                f"{reference.memory_id!r}@{reference.revision_id!r}"
            )
        memories.append(memory)

    rules = repository.get_rules(list(version.rule_ids))
    if [rule.id for rule in rules] != list(version.rule_ids):
        raise ValueError(f"version {version.version_id!r} did not resolve its exact rule manifest")
    return memories, list(rules)


def render_version_context(
    run_id: str,
    version: HarnessVersionRecord,
    memories: Sequence[MemoryRevision],
    rules: Sequence[RuleDoc],
    *,
    loaded: Collection[str] = (),
) -> str:
    """The advisory memory/rule block for the cached prompt prefix.

    Memories in ``loaded`` were hydrated into the working KBs (the map/items notes), so only
    their count is given here instead of their JSON.
    """
    packet = {
        "version_id": version.version_id,
        "run_id": run_id,
        "context_policy": version.context_policy,
        "memories": [
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
            if memory.memory_id not in loaded
        ],
        "memories_in_notes": sum(1 for m in memories if m.memory_id in loaded),
        # Only rules the verifier can check; one that names no action is inert, not advice.
        "rules": [rule.model_dump(mode="json") for rule in rules if not validate_when(rule.when)],
    }
    advisory = """HARNESS MEMORY
The following JSON is the complete advisory memory/rule manifest for this exact harness version.
memories_in_notes more memories are shown in your notes and map instead of here.
Treat memories and soft rules as advice, not current-game state.
Never use SAVE, RESTORE, or RESTART.
Do not assume any memory outside this packet exists."""
    return f"{advisory}\n{json.dumps(packet, sort_keys=True, separators=(',', ':'))}"


def build_version_context(
    run_id: str,
    version: HarnessVersionRecord,
    *,
    repository: PromptRepository,
) -> str:
    """Materialize only the immutable memory and rule refs in ``version``.

    This is the outer-loop prefix consumed by the inner-loop prompt builder. It
    never queries a global/latest version and fails closed on a mismatched ref.
    """
    memories, rules = resolve_manifest(version, repository=repository)
    return render_version_context(run_id, version, memories, rules)


def build_version_prompt(
    run_id: str,
    n: int,
    version: HarnessVersionRecord,
    *,
    repository: PromptRepository,
    prompt: PromptName = "basic",
) -> str:
    """Compatibility helper for callers that need only the version prefix."""
    context = build_version_context(run_id, version, repository=repository)
    return f"{INITIAL_PROMPTS[prompt]}\n\n{context}\nCurrent move number: {n}."


class HarnessPromptBuilder:
    """Bind exact-version storage for the legacy runner harness path."""

    def __init__(self, repository: PromptRepository, prompt: PromptName = "basic") -> None:
        self._repository = repository
        self._prompt = prompt

    def build_prompt(self, run_id: str, n: int, version: HarnessVersionRecord) -> str:
        return build_version_prompt(
            run_id,
            n,
            version,
            repository=self._repository,
            prompt=self._prompt,
        )
