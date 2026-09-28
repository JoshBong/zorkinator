"""Scribe: turns each game response into working-KB updates and world_facts evidence.

Code only for now. The first-visit LLM extraction (exits/items a room mentions) comes later;
until then mentioned exits come from direction words in the room description and items from
Zork's stock "There is a ... here." sentences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .world import Exit, Item, Step, WorldModel, direction_of

OUTCOME_CHARS = 80
_DIRECTION_WORDS = re.compile(
    r"\b(north|south|east|west|northeast|northwest|southeast|southwest|up|down)\b"
)
_TAKEN = re.compile(r"^(?:(?P<item>[^:\n]+): )?(?:Taken\.|\(Taken\))$", re.MULTILINE)
_DROPPED = re.compile(r"^(?:(?P<item>[^:\n]+): )?Dropped\.$", re.MULTILINE)
_ARTICLE = re.compile(r"^(?:a|an|the|some)\s+", re.IGNORECASE)
# Zork's stock sentences for objects in a room (a stand-in for the first-visit LLM extraction).
_ITEMS_IN_TEXT = [
    re.compile(r"\bThere is an? (?!no\b)([^.,]+?) here\b"),
    re.compile(r"\b(?:On|In|Beside|Under) [^.]*? (?:is|are) (?:an?|some) ([^.,]+)"),
    re.compile(r"^An? ([^.,]+?) (?:is|are|lies) (?:on the ground|here)\b", re.MULTILINE),
    re.compile(r"\breveals an? ([^.,]+)"),
]


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
        # A new command is not progress: "examine X" for 300 moves would never look stuck.
        return bool(self.new_rooms or self.new_items or self.score_delta > 0)


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
        room = world.room(parsed.room)
        is_new = room.visits == 0  # rooms known only from earlier games count as new here
        room.visits += 1
        room.source = "this_game"
        if parsed.description and not room.description:
            room.description = parsed.description
        if is_new:
            obs.new_rooms.append(room.name)
            world.fact(room.name, "dark", parsed.dark, n)
            for word in sorted(set(_DIRECTION_WORDS.findall(parsed.description.casefold()))):
                room.exits.setdefault(word, Exit(word, None, "mentioned", n))
                world.fact(room.name, f"lead_{word}", "mentioned", n)
    direction = direction_of(command) if command else None
    # A movement command answered with a room title moved us, even into a same-named room
    # (Zork has several distinct rooms called "Forest").
    if parsed.room is not None and (parsed.room != prev or direction is not None):
        obs.moved = True
        state.prev_room, state.room = prev, parsed.room

    if direction is not None and prev is not None:
        exits = world.room(prev).exits
        old = exits.get(direction)
        remembered = old if old is not None and old.source == "memory" else None
        claim = f"{prev} {direction}"
        if obs.moved and state.room is not None:
            exits[direction] = Exit(direction, state.room, "known", n)
            world.fact(prev, f"exit_{direction}", state.room, n)
            if remembered is not None:
                held = remembered.status == "mentioned" or remembered.to == state.room
                world.check_memory(
                    remembered.memory_id, claim, n, held=held, detail=f"led to {state.room}"
                )
        elif not obs.moved and not parsed.dark:
            # A remembered exit that fails here is replaced by what this game saw.
            if remembered is not None or old is None or old.status != "known":
                exits[direction] = Exit(direction, None, "blocked", n, outcome)
                world.fact(prev, f"blocked_{direction}", outcome, n)
            if remembered is not None:
                held = remembered.status == "blocked"
                world.check_memory(
                    remembered.memory_id, claim, n, held=held, detail=f"blocked: {outcome}"
                )

    # Items the text mentions where we are now.
    if state.room is not None:
        for pattern in _ITEMS_IN_TEXT:
            for match in pattern.finditer(text):
                _seen(world, _item_name(match.group(1)), state.room, n, obs)

    # Items and inventory.
    if command is not None:
        target = _resolve(world, _object_of(command))
        for match in _TAKEN.finditer(text):
            name = _resolve(world, _item_name(match["item"])) if match["item"] else target
            if name:
                _carry(world, name, True, n, obs)
        for match in _DROPPED.finditer(text):
            name = _resolve(world, _item_name(match["item"])) if match["item"] else target
            if name:
                _carry(world, name, False, n, obs)
        if text.startswith("You are carrying:"):
            carried = {
                _resolve(world, _item_name(line)) or "" for line in text.splitlines()[1:]
            } - {""}
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
        state.recent.append(
            Step(n=n, room=prev, command=command, outcome=outcome, text=text.strip())
        )
    return obs


def _resolve(world: WorldModel, name: str | None) -> str | None:
    """Map a short name the player typed ("mailbox") to a known item ("small mailbox")."""
    if not name or name in world.items:
        return name
    matches = [known for known in world.items if known.endswith(" " + name)]
    return matches[0] if len(matches) == 1 else name


def _noun_phrase(name: str) -> str:
    """'large egg encrusted with precious jewels' -> 'large egg'."""
    words = re.split(r"\s+(?:with|that|which|apparently)\b", name)[0].split()
    for i, word in enumerate(words[1:], start=1):
        if word.endswith("ed"):
            return " ".join(words[:i])
    return " ".join(words)


def _seen(world: WorldModel, name: str, room: str, n: int, obs: Observation) -> None:
    name = _noun_phrase(name)
    if not name or len(name.split()) > 4:
        return
    is_new = name not in world.items
    item = world.item(name)
    if item.carried:
        return
    _first_sighting(world, item, room, n)
    item.last_seen_room, item.source = room, "this_game"
    world.room(room).items_seen.add(name)
    if is_new:
        obs.new_items.append(name)
        world.fact(name, "seen_in", room, n)


def _carry(world: WorldModel, name: str, carried: bool, n: int, obs: Observation) -> None:
    is_new = name not in world.items
    item = world.item(name)
    if world.state.room:
        _first_sighting(world, item, world.state.room, n)
    item.carried, item.source = carried, "this_game"
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


def _first_sighting(world: WorldModel, item: Item, room: str, n: int) -> None:
    """The first time this game meets a remembered item, check where memory said it was."""
    if item.source != "memory" or item.last_seen_room is None:
        return
    world.check_memory(
        item.memory_id,
        f"{item.name} in {item.last_seen_room}",
        n,
        held=item.last_seen_room == room,
        detail=f"found in {room}",
    )


__all__ = ["Observation", "Parsed", "parse_stub", "update"]
