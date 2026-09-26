"""Context builder: a fresh prompt each move from the working KBs. No chat history.

The prefix (paper prompt + harness protocol + notes from earlier games) is fixed for the whole
game so it can be cached; the tail is rebuilt every move.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .models import HarnessVersionRecord, MemoryRevision, RuleDoc
from .monitor import Status
from .prompts import INITIAL_PROMPTS, PromptName
from .world import WorldModel

HARNESS_PROTOCOL = """\
[Harness] The game is already running; do not reply "ready". Each turn you get your notes \
and the game's latest output. Reply with the next command on the first line. You may add \
a second line "Goal: <one sentence>" saying what you are trying to do next; it is shown \
back to you until you change it. Add a line "Expect: <one sentence>" predicting what the game \
will say after this command. When you are shown what you expected last move, also add \
"Surprise: yes" or "Surprise: no": did the game's output match your prediction?
Prefer actions you have not tried yet: follow up exits the game mentioned, examine and use \
objects, test ideas. Notes marked "from earlier games" may be wrong; check them."""


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
    exact_version = f"\n\n{version_context}" if version_context else ""
    return (
        f"{INITIAL_PROMPTS[prompt]}\n\n{HARNESS_PROTOCOL}\n\nNotes from earlier games:\n{earlier}"
        f"{exact_version}"
    )


def build_tail(
    world: WorldModel,
    n: int,
    last_output: str,
    status: Status = "ok",
    expected: str | None = None,
) -> str:
    state = world.state
    here = world.here
    lines = [f"Move {n}. Score {state.score}."]

    if state.in_dark:
        lines.append("Location: somewhere dark (no room description visible).")
    elif here is not None:
        lines.append(
            f"Location: {here.name}"
            + (f" — {_short(here.description)}" if here.description else "")
        )
    else:
        lines.append("Location: unknown.")

    if here is not None and here.exits:
        exits = []
        for e in sorted(here.exits.values(), key=lambda e: e.direction):
            if e.status == "known":
                earlier = " (from earlier games)" if e.source == "memory" else ""
                exits.append(f"{e.direction} -> {e.to}{earlier}")
            elif e.status == "blocked":
                exits.append(f"{e.direction} blocked ({e.note})")
            else:
                exits.append(f"{e.direction} (mentioned, never taken)")
        lines.append("Exits: " + "; ".join(exits))
    if here is not None and here.items_seen:
        lines.append("Seen here: " + ", ".join(sorted(here.items_seen)))
    if here is not None:
        remembered = sorted(
            i.name
            for i in world.items.values()
            if i.source == "memory" and i.last_seen_room == here.name and not i.carried
        )
        if remembered:
            lines.append("Earlier games saw here: " + ", ".join(remembered))
    if here is not None and here.tried:
        tried = list(here.tried.items())[-6:]
        lines.append("Already tried here: " + "; ".join(f"{c} -> {o}" for c, o in tried))

    lines.append("Carrying: " + (", ".join(world.inventory) or "nothing known"))
    lines.append(f"Goal: {state.goal or 'none set'}")

    leads = world.leads()
    if leads:
        lines.append("Open leads:\n" + "\n".join(f"- {lead}" for lead in leads[:8]))
    if state.recent:
        lines.append(
            "Recent moves:\n"
            + "\n".join(f"- {s.n}. {s.command} -> {s.outcome}" for s in state.recent)
        )
    if status == "repeat":
        lines.append(
            "Note: you just repeated a command here with the same result. Try something new."
        )

    if expected:
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
) -> PromptParts:
    return PromptParts(
        build_prefix(world, version_context, prompt),
        build_tail(world, n, last_output, status, expected),
    )


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class PromptRepository(Protocol):
    """Exact-version reads needed to materialize a prompt manifest."""

    def get_version(self, version_id: str) -> HarnessVersionRecord | None: ...

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None: ...

    def get_rules(self, rule_ids: list[str]) -> list[RuleDoc]: ...


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
                "content": memory.content,
                "status": memory.status,
            }
            for memory in memories
        ],
        "rules": [rule.model_dump(mode="json") for rule in rules],
    }
    advisory = """HARNESS MEMORY
The following JSON is the complete advisory memory/rule manifest for this exact harness version.
Treat memories and soft rules as advice, not current-game state.
Never use SAVE, RESTORE, or RESTART.
Do not assume any memory outside this packet exists."""
    return f"{advisory}\n{json.dumps(packet, sort_keys=True, separators=(',', ':'))}"


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
