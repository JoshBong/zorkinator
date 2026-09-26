"""Scribe: turns each game response into working-KB updates and world_facts evidence.

Code only for now. The first-visit LLM extraction (exits/items a room mentions) comes later;
until then mentioned exits come from direction words in the room description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .world import Exit, Step, WorldModel, direction_of

OUTCOME_CHARS = 80
_DIRECTION_WORDS = re.compile(
    r"\b(north|south|east|west|northeast|northwest|southeast|southwest|up|down)\b"
)
_TAKEN = re.compile(r"^(?:(?P<item>[^:\n]+): )?Taken\.$", re.MULTILINE)
_DROPPED = re.compile(r"^(?:(?P<item>[^:\n]+): )?Dropped\.$", re.MULTILINE)
_ARTICLE = re.compile(r"^(?:a|an|the|some)\s+", re.IGNORECASE)


@dataclass
class Parsed:
    """Stand-in for Josh's parser.parse(text, prev) -> {room, score_delta, died, new_items}."""

    room: str | None
    description: str
    score_delta: int
    died: bool
    dark: bool


@dataclass
class Observation:
    """What one move changed, for the monitor and the move log."""

    moved: bool = False
    new_rooms: list[str] = field(default_factory=list)
    new_items: list[str] = field(default_factory=list)
    new_interaction: bool = False
    score_delta: int = 0

    @property
    def progressed(self) -> bool:
        return bool(
            self.new_rooms or self.new_items or self.new_interaction or self.score_delta > 0
        )


def _is_room_title(line: str) -> bool:
    return (
        0 < len(line) <= 40
        and line[0].isupper()
        and line == line.strip()
        and line[-1] not in ".!?:,"
        and not any(ch.isdigit() or ch in ":/" for ch in line)
        and len(line.split()) <= 6
        and not line.startswith("You ")
    )


def parse_stub(text: str, score_delta: int = 0) -> Parsed:
    """Room title = first line of the last paragraph that starts with a title-like line."""
    room: str | None = None
    description = ""
    for paragraph in text.split("\n\n"):
        lines = paragraph.split("\n")
        if lines and _is_room_title(lines[0]):
            room, description = lines[0], "\n".join(lines[1:]).strip()
    lowered = text.casefold()
    return Parsed(
        room=room,
        description=description,
        score_delta=score_delta,
        died="you have died" in lowered,
        dark="pitch black" in lowered or "pitch dark" in lowered,
    )


def _outcome(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "(no output)")
    return first[:OUTCOME_CHARS]


def _item_name(raw: str) -> str:
    return _ARTICLE.sub("", raw.strip()).casefold()


def _object_of(command: str) -> str | None:
    """'take the brass lantern' -> 'brass lantern'; single-word commands have no object."""
    words = command.casefold().split()
    if len(words) < 2:
        return None
    rest = " ".join(words[1:])
    rest = re.split(r"\s+(?:with|in|on|into|to|from|at)\s+", rest)[0]
    return _item_name(rest) or None


def update(
    world: WorldModel, n: int, command: str | None, text: str, parsed: Parsed
) -> Observation:
    """Apply one game response to the working KBs. ``command`` is None for the opening text."""
    obs = Observation(score_delta=parsed.score_delta)
    state = world.state
    prev = state.room
    outcome = _outcome(text)

    # Position and map.
    state.in_dark = parsed.dark
    if parsed.room is not None:
        is_new = parsed.room not in world.rooms
        room = world.room(parsed.room)
        room.visits += 1
        if parsed.description and not room.description:
            room.description = parsed.description
        if is_new:
            obs.new_rooms.append(room.name)
            world.fact(room.name, "dark", parsed.dark, n)
            for word in sorted(set(_DIRECTION_WORDS.findall(parsed.description.casefold()))):
                room.exits.setdefault(word, Exit(word, None, "mentioned", n))
                world.fact(room.name, f"lead_{word}", "mentioned", n)
        if parsed.room != prev:
            obs.moved = True
            state.prev_room, state.room = prev, parsed.room

    direction = direction_of(command) if command else None
    if direction is not None and prev is not None:
        exits = world.room(prev).exits
        if obs.moved and state.room is not None:
            exits[direction] = Exit(direction, state.room, "known", n)
            world.fact(prev, f"exit_{direction}", state.room, n)
        elif not obs.moved and not parsed.dark:
            if direction not in exits or exits[direction].status != "known":
                exits[direction] = Exit(direction, None, "blocked", n, outcome)
                world.fact(prev, f"blocked_{direction}", outcome, n)

    # Items and inventory.
    if command is not None:
        target = _object_of(command)
        for match in _TAKEN.finditer(text):
            name = _item_name(match["item"]) if match["item"] else target
            if name:
                _carry(world, name, True, n, obs)
        for match in _DROPPED.finditer(text):
            name = _item_name(match["item"]) if match["item"] else target
            if name:
                _carry(world, name, False, n, obs)
        if text.startswith("You are carrying:"):
            carried = {_item_name(line) for line in text.splitlines()[1:] if line.strip()}
            for name in carried:
                _carry(world, name, True, n, obs)
            for name in set(world.inventory) - carried:
                _carry(world, name, False, n, obs)
        if (
            target
            and target in world.items
            and command.casefold().startswith(("examine", "x ", "read", "look at"))
        ):
            world.items[target].examined = True

    # What was tried where. A new (room, command) pair counts as a new interaction.
    if command is not None:
        where = world.room(prev) if prev else None
        key = command.casefold().strip()
        if where is not None and key not in where.tried and direction is None:
            obs.new_interaction = True
        if where is not None:
            where.tried[key] = outcome
        state.recent.append(Step(n=n, room=prev, command=command, outcome=outcome))
    return obs


def _carry(world: WorldModel, name: str, carried: bool, n: int, obs: Observation) -> None:
    is_new = name not in world.items
    item = world.item(name)
    item.carried = carried
    if world.state.room:
        item.last_seen_room = world.state.room
        seen = world.room(world.state.room).items_seen
        if carried:
            seen.discard(name)
        else:
            seen.add(name)
    if is_new:
        obs.new_items.append(name)
    world.fact(name, "carried", carried, n)


__all__ = ["Observation", "Parsed", "parse_stub", "update"]
