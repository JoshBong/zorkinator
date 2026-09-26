"""Context builder: a fresh prompt each move from the working KBs. No chat history.

The prefix (paper prompt + harness protocol + notes from earlier games) is fixed for the whole
game so it can be cached; the tail is rebuilt every move.
"""

from __future__ import annotations

from dataclasses import dataclass

from .monitor import Status
from .prompts import BASIC
from .world import WorldModel

HARNESS_PROTOCOL = """\
[Harness] The game is already running; do not reply "ready". Each turn you get your notes \
and the game's latest output. Reply with the next command on the first line. You may add \
a second line "Goal: <one sentence>" saying what you are trying to do next; it is shown \
back to you until you change it.
Prefer actions you have not tried yet: follow up exits the game mentioned, examine and use \
objects, test ideas. Notes marked "from earlier games" may be wrong; check them."""


@dataclass
class PromptParts:
    prefix: str
    tail: str

    def __str__(self) -> str:
        return f"{self.prefix}\n\n{self.tail}"


def build_prefix(world: WorldModel) -> str:
    """Fixed for the game: changes only between games, when the version changes."""
    notes = [
        *(f"- objective: {o.text}" for o in world.objectives if o.status == "open"),
        *(f"- hypothesis: {h}" for h in world.hypotheses),
        *(f"- past game: {s}" for s in world.past_runs),
    ]
    earlier = "\n".join(notes) if notes else "none yet"
    return f"{BASIC}\n\n{HARNESS_PROTOCOL}\n\nNotes from earlier games:\n{earlier}"


def build_tail(world: WorldModel, n: int, last_output: str, status: Status = "ok") -> str:
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
                exits.append(f"{e.direction} -> {e.to}")
            elif e.status == "blocked":
                exits.append(f"{e.direction} blocked ({e.note})")
            else:
                exits.append(f"{e.direction} (mentioned, never taken)")
        lines.append("Exits: " + "; ".join(exits))
    if here is not None and here.items_seen:
        lines.append("Seen here: " + ", ".join(sorted(here.items_seen)))
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

    lines.append(f"Game output:\n{last_output.strip() or '(no output)'}")
    return "\n".join(lines)


def build_prompt(world: WorldModel, n: int, last_output: str, status: Status = "ok") -> PromptParts:
    return PromptParts(build_prefix(world), build_tail(world, n, last_output, status))


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
