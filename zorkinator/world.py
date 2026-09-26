"""Working knowledge bases for one harness game: map, items, objectives, run state.

Everything starts empty (or from a published version's memories) and is filled in move by
move from human-visible game text only. See docs/INNER_LOOP.md.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from .models import WorldFactDoc

RECENT_MOVES = 5

DIRECTIONS: dict[str, str] = {
    "n": "north",
    "s": "south",
    "e": "east",
    "w": "west",
    "ne": "northeast",
    "nw": "northwest",
    "se": "southeast",
    "sw": "southwest",
    "u": "up",
    "d": "down",
}
DIRECTION_NAMES = frozenset({*DIRECTIONS.values(), "in", "out"})

ExitStatus = Literal["mentioned", "known", "blocked"]
Source = Literal["this_game", "memory"]


def direction_of(command: str) -> str | None:
    """The compass direction a movement command names ("n", "go north", "walk up"), else None."""
    words = command.casefold().split()
    if len(words) == 2 and words[0] in {"go", "walk", "run", "climb"}:
        words = words[1:]
    if len(words) != 1:
        return None
    word = DIRECTIONS.get(words[0], words[0])
    return word if word in DIRECTION_NAMES else None


@dataclass
class Exit:
    direction: str
    to: str | None = None
    status: ExitStatus = "mentioned"
    move: int = 0
    note: str = ""
    source: Source = "this_game"


@dataclass
class Room:
    name: str
    description: str = ""
    visits: int = 0
    dark: bool = False
    exits: dict[str, Exit] = field(default_factory=dict)
    items_seen: set[str] = field(default_factory=set)
    tried: dict[str, str] = field(default_factory=dict)  # command -> short outcome
    source: Source = "this_game"


@dataclass
class Item:
    name: str
    last_seen_room: str | None = None
    carried: bool = False
    examined: bool = False
    tried: dict[str, str] = field(default_factory=dict)  # command -> short outcome
    source: Source = "this_game"


@dataclass
class Objective:
    text: str
    status: Literal["open", "done", "dropped"] = "open"
    source: Source = "this_game"


@dataclass
class Step:
    n: int
    room: str | None
    command: str
    outcome: str


@dataclass
class RunState:
    room: str | None = None
    prev_room: str | None = None
    score: int = 0
    moves: int = 0
    goal: str | None = None
    in_dark: bool = False
    recent: deque[Step] = field(default_factory=lambda: deque(maxlen=RECENT_MOVES))


class WorldModel:
    """The inner loop's working KBs for one run, plus the world_facts evidence they produce."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.rooms: dict[str, Room] = {}
        self.items: dict[str, Item] = {}
        self.objectives: list[Objective] = []
        self.hypotheses: list[str] = []
        self.past_runs: list[str] = []  # condensed summaries of earlier games, from memory
        self.state = RunState()
        self._pending: list[WorldFactDoc] = []

    @classmethod
    def empty(cls, run_id: str) -> WorldModel:
        """Game 1 of an experiment: no map, no items, no objectives."""
        return cls(run_id)

    # TODO(Seb): load(run_id, version_id, store) -> hydrate rooms/items/objectives from
    # store.recall(version_id, kinds=[...]) with source="memory" once Elliott's memory kinds land.

    # --- knowledge updates -------------------------------------------------------------

    def room(self, name: str) -> Room:
        return self.rooms.setdefault(name, Room(name))

    def item(self, name: str) -> Item:
        return self.items.setdefault(name, Item(name))

    @property
    def here(self) -> Room | None:
        return self.rooms.get(self.state.room) if self.state.room else None

    @property
    def inventory(self) -> list[str]:
        return sorted(name for name, item in self.items.items() if item.carried)

    def fact(self, subject: str, attr: str, value: str | bool | int, n: int) -> None:
        """Queue one world_facts upsert. Shapes the view reads: exit_<dir> -> room, dark -> bool."""
        self._pending.append(
            WorldFactDoc(run_id=self.run_id, subject=subject, attr=attr, value=value, move=n)
        )

    def drain_facts(self) -> list[WorldFactDoc]:
        facts, self._pending = self._pending, []
        return facts

    # --- views for the prompt ----------------------------------------------------------

    def leads(self) -> list[str]:
        """Things the game has shown us but we haven't followed up. Text-derived only."""
        out: list[str] = []
        here = self.here
        if here is not None:
            out += [
                f"exit {e.direction} (mentioned, never taken)"
                for e in here.exits.values()
                if e.status == "mentioned"
            ]
            out += [
                f"{name} (seen here, never examined)"
                for name in sorted(here.items_seen)
                if not self.item(name).examined and not self.item(name).carried
            ]
        out += [
            f"{name} (carried, never examined)"
            for name in self.inventory
            if not self.items[name].examined
        ]
        out += [f"elsewhere: {room.name} has an untaken exit" for room in self._rooms_with_leads()]
        out += [f"hypothesis: {h}" for h in self.hypotheses]
        return out

    def _rooms_with_leads(self) -> list[Room]:
        return [
            room
            for room in self.rooms.values()
            if room.name != self.state.room
            and any(e.status == "mentioned" for e in room.exits.values())
        ][:3]
