"""Working knowledge bases for one harness game: map, items, objectives, run state.

Everything starts empty (or from a published version's memories) and is filled in move by
move from human-visible game text only. See docs/INNER_LOOP.md.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from pydantic import JsonValue

from .models import MemoryRevision, WorldFactDoc

RECENT_MOVES = 20  # enough to notice a loop; 5 hid fifteen "east"s in a row

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


class MemoryCheck(TypedDict):
    """One move's test of a loaded memory: the move, the claim tested, what happened."""

    n: int
    claim: str
    detail: str


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
    memory_id: str | None = None  # the version memory this exit came from, if any


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
    remembered: dict[str, str] = field(default_factory=dict)  # earlier games: command -> outcome


@dataclass
class Item:
    name: str
    last_seen_room: str | None = None
    carried: bool = False
    examined: bool = False
    tried: dict[str, str] = field(default_factory=dict)  # command -> short outcome
    source: Source = "this_game"
    memory_id: str | None = None


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
        # location (casefolded) -> notes from earlier games that apply only there
        self.location_notes: dict[str, list[str]] = {}
        self.memory_map = ""  # earlier games' map, rendered once at load (cached prompt prefix)
        # memory_id -> {"confirmed" | "contradicted": [{n, claim, detail}]}: what this game's
        # play showed about each loaded memory. Evidence for the outer loop, never a verdict.
        self.memory_feedback: dict[str, dict[str, list[MemoryCheck]]] = {}
        self.state = RunState()
        self._pending: list[WorldFactDoc] = []

    @classmethod
    def empty(cls, run_id: str) -> WorldModel:
        """Game 1 of an experiment: no map, no items, no objectives."""
        return cls(run_id)

    # --- knowledge from earlier games (outer loop -> inner loop) -----------------------
    # Everything added here is source="memory": shown as "from earlier games", replaced as
    # soon as this game observes the same thing.

    def load(self, memories: Iterable[MemoryRevision]) -> set[str]:
        """Hydrate the working KBs from one exact version's memories.

        Returns the ids of the memories now held here; the rest stay advisory text in the
        prompt. Contradicted memories are not loaded as facts. Shapes: see ``_load`` below.
        """
        loaded = {m.memory_id for m in memories if m.status != "contradicted" and _load(self, m)}
        self.memory_map = self._render_memory_map()
        return loaded

    def remember_exit(
        self,
        room: str,
        direction: str,
        to: str | None,
        memory_id: str | None = None,
        *,
        blocked: str = "",
    ) -> None:
        """``to=None`` is an exit an earlier game saw mentioned; ``blocked`` gives its reason."""
        self.room(room).source = "memory"
        status: ExitStatus = "blocked" if blocked else "known" if to else "mentioned"
        if to:
            self.room(to).source = "memory"
        self.room(room).exits[direction] = Exit(
            direction, to, status, 0, blocked, source="memory", memory_id=memory_id
        )

    def remember_item(
        self, name: str, room: str | None, note: str = "", memory_id: str | None = None
    ) -> None:
        item = self.item(name)
        item.last_seen_room, item.source, item.memory_id = room, "memory", memory_id
        if note:
            item.tried["(earlier games)"] = note

    def remember_objective(self, text: str) -> None:
        self.objectives.append(Objective(text, source="memory"))

    def remember_hypothesis(self, text: str) -> None:
        self.hypotheses.append(text)

    def remember_past_run(self, summary: str) -> None:
        self.past_runs.append(summary)

    def remember_note(self, location: str, text: str) -> None:
        self.location_notes.setdefault(location.casefold(), []).append(text)

    def notes_here(self) -> list[str]:
        return self.location_notes.get((self.state.room or "").casefold(), [])

    def check_memory(
        self, memory_id: str | None, claim: str, n: int, *, held: bool, detail: str = ""
    ) -> None:
        """Record that move ``n`` confirmed (``held``) or contradicted a loaded memory."""
        if memory_id is None:
            return
        verdict = "confirmed" if held else "contradicted"
        entry: MemoryCheck = {"n": n, "claim": claim, "detail": detail}
        self.memory_feedback.setdefault(memory_id, {}).setdefault(verdict, []).append(entry)
        self.fact(memory_id, f"memory_{verdict}:{claim}", detail or claim, n)

    def _render_memory_map(self) -> str:
        lines: list[str] = []
        for room in self.rooms.values():
            if room.source != "memory":
                continue
            parts = []
            for e in sorted(room.exits.values(), key=lambda e: e.direction):
                if e.status == "known":
                    parts.append(f"{e.direction} -> {e.to}")
                elif e.status == "blocked":
                    parts.append(f"{e.direction} blocked ({e.note})")
                else:
                    parts.append(f"{e.direction} (mentioned)")
            seen = sorted(
                i.name
                for i in self.items.values()
                if i.source == "memory" and i.last_seen_room == room.name
            )
            if seen:
                parts.append("seen: " + ", ".join(seen))
            parts += [
                f"{command} -> {outcome}"
                for command, outcome in room.remembered.items()
                if outcome.endswith(("[died]", "points]"))
            ]
            if parts:
                lines.append(f"- {room.name}: " + "; ".join(parts))
        for item in self.items.values():
            note = item.tried.get("(earlier games)")
            if item.source == "memory" and note:
                lines.append(f"- {item.name}: {note}")
        if not lines:
            return ""
        return "Map and items from earlier games (may be wrong; check them):\n" + "\n".join(lines)

    # --- final state (inner loop -> outer loop) ------------------------------------------

    def summary(self) -> dict[str, object]:
        """Compact end-of-game knowledge for the Reflector and for inspection."""
        return {
            "run_id": self.run_id,
            "room": self.state.room,
            "score": self.state.score,
            "goal": self.state.goal,
            "inventory": self.inventory,
            "rooms": [
                {
                    "name": r.name,
                    "visits": r.visits,
                    "dark": r.dark,
                    "source": r.source,
                    "exits": {
                        e.direction: e.to if e.status == "known" else e.status
                        for e in r.exits.values()
                    },
                    "items_seen": sorted(r.items_seen),
                    "tried": len(r.tried),
                }
                for r in self.rooms.values()
            ],
            "items": [
                {
                    "name": i.name,
                    "last_seen_room": i.last_seen_room,
                    "carried": i.carried,
                    "source": i.source,
                }
                for i in self.items.values()
            ],
            "leads": self.leads(),
            "memory_feedback": self.memory_feedback,
        }

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


# --- memory shapes the inner loop can hydrate -------------------------------------------
# Memory kinds are open-ended (docs/OUTER_LOOP_MEMORY.md); these are the structured shapes the
# working KBs understand. Anything else stays advisory JSON in the prompt.
#   room     {room, exits: {dir: to | null}, blocked: {dir: reason}, items: [name], text}
#   map_edge {from, direction, to}
#   item     {item | name, room | location, text | note}   (or the room from ``locations``)
#   objective / hypothesis / run_summary {text}
#   any other kind with ``locations`` and {text}: a note shown only in those rooms

_ARTICLES = ("a ", "an ", "the ", "some ")


def _load(world: WorldModel, memory: MemoryRevision) -> bool:
    kind = memory.kind.casefold()
    content = memory.content
    text = _text(content.get("text"))
    mid = memory.memory_id

    if kind in {"objective", "goal"} and text:
        world.remember_objective(text)
        return True
    if kind == "hypothesis" and text:
        world.remember_hypothesis(text)
        return True
    if kind == "run_summary" and text:
        world.remember_past_run(text)
        return True

    room = _text(content.get("room"))
    exits = content.get("exits")
    if room and isinstance(exits, dict):
        raw_blocked = content.get("blocked")
        blocked = raw_blocked if isinstance(raw_blocked, dict) else {}
        for raw_dir, raw_to in exits.items():
            direction = direction_of(raw_dir)
            if direction is not None:
                world.remember_exit(room, direction, _text(raw_to) or None, mid)
        for raw_dir, reason in blocked.items():
            direction = direction_of(raw_dir)
            if direction is not None:
                world.remember_exit(room, direction, None, mid, blocked=_text(reason) or "blocked")
        world.room(room).source = "memory"
        tried = content.get("tried")
        for command, outcome in tried.items() if isinstance(tried, dict) else []:
            if _text(outcome):
                world.room(room).remembered[command.casefold()] = _text(outcome)
        items = content.get("items")
        for name in items if isinstance(items, list) else []:
            if _text(name):
                world.remember_item(_item_name(_text(name)), room, memory_id=mid)
        return True

    src, to = _text(content.get("from")), _text(content.get("to"))
    direction = direction_of(_text(content.get("direction") or content.get("dir")))
    if src and to and direction is not None:
        world.remember_exit(src, direction, to, mid)
        return True

    name = _text(content.get("item") or content.get("name"))
    if kind == "item" and name:
        where = _text(content.get("room") or content.get("location"))
        where = where or next((loc for loc in memory.locations), "")
        note = text or _text(content.get("note"))
        world.remember_item(_item_name(name), where or None, note, mid)
        return True

    # Located knowledge (advice, cautions, failures about a place) is shown only there.
    if memory.locations and text:
        for location in memory.locations:
            world.remember_note(location, text)
        return True
    return False


def _text(value: JsonValue) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _item_name(name: str) -> str:
    name = name.casefold()
    for article in _ARTICLES:
        if name.startswith(article):
            return name[len(article) :]
    return name
