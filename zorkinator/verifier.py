"""Verifier: fixed, mechanical rule checks. No model calls, and never editable by the Reflector.

Two jobs:
- ``check`` runs before every harness move. Hard rules block the command; soft rules never block
  and never re-prompt: a match is logged on the move (ZorkGPT's LLM critic was wrong in 68 of 77
  overrides, so unproven rules never block). ``applies`` tells the prompt which cautions hold in
  the current state, so the Player sees them before it chooses.
- A rule is a caution about one action, so ``when`` must name a command. "Do X here" or "this
  place is dangerous" is knowledge, not a gate: it belongs in memories (the Reflector converts
  such rules into location-scoped memories).
- ``Promoter.promote`` decides whether a proposed rule becomes hard, by replaying it against logged
  moves. It is the ``RulePromoter`` that ``versions.VersionManager`` takes.

A rule's ``when`` may only use the fields in ``WHEN_FIELDS``, all human-visible:

    command       required. regex, full match, case-insensitive, e.g. "(kill|attack) troll.*"
                  (``command_pattern`` is accepted as the same field); a pattern that matches
                  any command (".*", "\\w+") names no action and is rejected
    room          room name or list of names (case-insensitive)
    room_is_dark  true/false: the current room's text said it is pitch black
    carrying      items that must all be carried (substring match)
    not_carrying  items that must all be absent from the inventory
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .models import MoveRecord, RuleDoc, RunRecord

WHEN_FIELDS = frozenset(
    {"command", "command_pattern", "room", "room_is_dark", "carrying", "not_carrying"}
)
STATE_FIELDS = frozenset({"room", "room_is_dark", "carrying", "not_carrying"})
# A pattern that matches these names no particular action.
_ANY_COMMAND_PROBES = ("", "qqq zzz", "a")


@dataclass(frozen=True)
class State:
    """What a human player knows right before typing a command."""

    room: str | None = None
    inventory: frozenset[str] = frozenset()
    room_is_dark: bool = False


@dataclass(frozen=True)
class Verdict:
    ok: bool
    rule_id: str | None = None
    reason: str | None = None
    hard: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)


def validate_when(when: Mapping[str, Any]) -> list[str]:
    """Errors in a rule's conditions; empty means the rule can be checked mechanically."""
    errors = [f"unknown field {key!r}" for key in when if key not in WHEN_FIELDS]
    if not when:
        errors.append("empty when: the rule would match every move")
    if "command" in when and "command_pattern" in when:
        errors.append("use command or command_pattern, not both")
    pattern = when.get("command", when.get("command_pattern"))
    if pattern is None or not str(pattern).strip():
        errors.append("no command: a rule must name the action it cautions against")
    else:
        try:
            compiled = re.compile(str(pattern), flags=re.IGNORECASE)
        except re.error as exc:
            errors.append(f"bad command regex: {exc}")
        else:
            if any(compiled.fullmatch(probe) for probe in _ANY_COMMAND_PROBES):
                errors.append("command matches any command: name a specific action")
    if "room_is_dark" in when and not isinstance(when["room_is_dark"], bool):
        errors.append("room_is_dark must be true or false")
    for key in ("carrying", "not_carrying"):
        if key in when and not isinstance(when[key], list):
            errors.append(f"{key} must be a list of item names")
    return errors


def matches(rule: RuleDoc, command: str, state: State) -> bool:
    pattern = rule.when.get("command", rule.when.get("command_pattern"))
    if not applies(rule, state):
        return False
    return re.fullmatch(str(pattern), command.strip(), flags=re.IGNORECASE) is not None


def applies(rule: RuleDoc, state: State) -> bool:
    """The rule is valid and its state conditions (everything but the command) hold now."""
    when = rule.when
    if validate_when(when):
        return False
    if "room" in when:
        rooms = when["room"] if isinstance(when["room"], list) else [when["room"]]
        if (state.room or "").casefold() not in {str(r).casefold() for r in rooms}:
            return False
    if "room_is_dark" in when and state.room_is_dark != when["room_is_dark"]:
        return False
    if any(not _carrying(state, str(item)) for item in when.get("carrying", [])):
        return False
    return not any(_carrying(state, str(item)) for item in when.get("not_carrying", []))


def check(command: str, state: State, rules: Iterable[RuleDoc]) -> Verdict:
    """First matching hard block rule rejects the command; matching soft rules only warn."""
    warnings: list[str] = []
    for rule in rules:
        if not matches(rule, command, state):
            continue
        if rule.status == "hard" and rule.verdict == "block":
            return Verdict(False, rule.id, rule.text, hard=True, warnings=tuple(warnings))
        warnings.append(f"{rule.id}: {rule.text}")
    return Verdict(True, warnings=tuple(warnings))


def replay_states(moves: Sequence[MoveRecord]) -> list[State]:
    """State before each move, rebuilt from the logged game text only.

    Uses the move's own ``room`` when the harness logged it; otherwise the last room title seen.
    """
    states: list[State] = []
    room: str | None = None
    dark = False
    inventory: set[str] = set()
    for move in sorted(moves, key=lambda m: m.n):
        states.append(State(move.room or room, frozenset(inventory), dark))
        text = move.text
        title = _room_title(text)
        if title:
            room = title
            dark = False
        if "pitch black" in text.casefold():
            dark = True
        _update_inventory(inventory, move.command, text)
    return states


class Promoter:
    """Soft -> hard, mechanically. Both must hold:

    1. A cited evidence move ended in death, and the rule matches it (it would have blocked it).
    2. The rule matches no survived move that was followed by points later in the same game.
       Killing the troll scores nothing but opens the way on, so "the move scored" is too weak.
    """

    def __init__(
        self,
        moves_for: Callable[[str], Sequence[MoveRecord]],
        history: Callable[[], Sequence[RunRecord]] | None = None,
    ) -> None:
        """``history`` adds more logged games to the false-positive check than the cited ones."""
        self._moves_for = moves_for
        self._history = history

    def promote(self, rule: RuleDoc, runs: Sequence[RunRecord]) -> Literal["hard", "soft"]:
        if rule.verdict != "block" or validate_when(rule.when):
            return "soft"
        replays = {run.run_id: self._replay(run.run_id) for run in runs}
        for ref in rule.evidence:
            run_id, _ = _parse_ref(ref)
            if run_id not in replays:
                replays[run_id] = self._replay(run_id)
        prevented_death = any(
            (hit := replays.get(run_id, {}).get(n)) is not None
            and hit[0].died
            and matches(rule, hit[0].command, hit[1])
            for run_id, n in map(_parse_ref, rule.evidence)
        )
        if not prevented_death:
            return "soft"
        checked = {run.run_id for run in [*runs, *(self._history() if self._history else [])]}
        for run_id in checked - replays.keys():
            replays[run_id] = self._replay(run_id)
        blocks_progress = any(_led_to_progress(rule, replays[run_id]) for run_id in checked)
        return "soft" if blocks_progress else "hard"

    def _replay(self, run_id: str) -> dict[int, tuple[MoveRecord, State]]:
        moves = sorted(self._moves_for(run_id), key=lambda m: m.n)
        return {m.n: (m, s) for m, s in zip(moves, replay_states(moves), strict=True)}


def _led_to_progress(rule: RuleDoc, replay: dict[int, tuple[MoveRecord, State]]) -> bool:
    """True if the rule matches a move the player survived and the game scored at or after it."""
    ordered = [replay[n] for n in sorted(replay)]
    for i, (move, state) in enumerate(ordered):
        if move.died or not matches(rule, move.command, state):
            continue
        if any(later.score_delta > 0 for later, _ in ordered[i:]):
            return True
    return False


def _carrying(state: State, item: str) -> bool:
    needle = item.casefold()
    return any(needle in held for held in state.inventory)


def _parse_ref(ref: str) -> tuple[str, int]:
    run_id, _, n = ref.rpartition(":")
    return run_id, int(n) if n.isdigit() else -1


def _room_title(text: str) -> str | None:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    looks_like_title = (
        0 < len(first) <= 40
        and first[0].isupper()
        and first[-1] not in ".!?:,"
        and not any(ch.isdigit() for ch in first)
        and len(first.split()) <= 6
        and not first.startswith(("You ", "It ", "There "))
    )
    return first if looks_like_title else None


def _update_inventory(inventory: set[str], command: str, text: str) -> None:
    lowered = text.casefold()
    words = command.casefold().split()
    obj = " ".join(words[1:]) if len(words) > 1 else ""
    for line in lowered.splitlines():  # "sword: Taken." from "take all"
        name, _, outcome = line.partition(":")
        if outcome.strip() == "taken.":
            inventory.add(name.strip())
        elif outcome.strip() == "dropped.":
            inventory.discard(name.strip())
    if words and words[0] in {"take", "get"} and obj and lowered.strip().startswith("taken"):
        inventory.add(obj)
    if words and words[0] == "drop" and obj and lowered.strip().startswith("dropped"):
        inventory.discard(obj)
