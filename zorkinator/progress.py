"""Live console output for a harness game: one line per move, a running score, and what the
KBs learned. Score is the game's own status-line score (human-visible), out of 350."""

from __future__ import annotations

import sys
import time
from typing import TextIO

from .models import RunRecord
from .runner import MAX_SCORE
from .scribe import Observation
from .world import WorldModel

OUTCOME_WIDTH = 52
CHECKPOINT_EVERY = 10


class ConsoleProgress:
    """Prints as the game runs. ``verbose`` adds goals, KB changes and warnings under each move;
    every ``checkpoint_every`` moves a score checkpoint line is printed either way."""

    def __init__(
        self,
        verbose: bool = True,
        stream: TextIO = sys.stdout,
        checkpoint_every: int = CHECKPOINT_EVERY,
    ) -> None:
        self.verbose = verbose
        self.stream = stream
        self.checkpoint_every = checkpoint_every
        self._t0 = time.monotonic()
        self._goal: str | None = None
        self.score = 0
        self.best = 0
        self.last_gain_move = 0
        self.history: list[tuple[int, int, int]] = []  # (move, delta, total) for every change

    def start(self, run_id: str, model: str, seed: int, move_cap: int, usd_cap: float) -> None:
        self._t0 = time.monotonic()
        self._print(f"=== harness game {run_id}")
        self._print(f"    model {model} | seed {seed} | cap {move_cap} moves | ${usd_cap:.2f} cap")

    def opening(self, world: WorldModel) -> None:
        self.score = self.best = world.state.score
        remembered = sum(1 for r in world.rooms.values() if r.source == "memory")
        self._print(
            f"    start in {world.state.room or '?'} | KBs: {len(world.rooms)} rooms "
            f"({remembered} from earlier games), {len(world.items)} items, "
            f"{len(world.hypotheses)} hypotheses"
        )

    def move(
        self,
        n: int,
        move_cap: int,
        room: str | None,
        command: str,
        output: str,
        latency_ms: int,
        cost: float,
        world: WorldModel,
        obs: Observation | None = None,
        status: str = "ok",
    ) -> None:
        delta = world.state.score - self.score
        self.score = world.state.score
        if delta:
            self.history.append((n, delta, self.score))
            if delta > 0:
                self.last_gain_move = n
            self.best = max(self.best, self.score)

        first = next((ln.strip() for ln in output.splitlines() if ln.strip()), "(no output)")
        score = f"{self.score:>3}" + (f" ({delta:+d})" if delta else "      ")
        self._print(
            f"[{n:>3}/{move_cap}] {(room or '?')[:18]:<18} > {command[:24]:<24} | "
            f"{first[:OUTCOME_WIDTH]:<{OUTCOME_WIDTH}} | score {score} "
            f"{latency_ms / 1000:>4.1f}s ${cost:.3f}"
        )
        if self.verbose:
            goal = world.state.goal
            if goal and goal != self._goal:
                self._goal = goal
                self._note(f"goal: {goal}")
            if obs is not None:
                for name in obs.new_rooms:
                    self._note(f"+ room: {name}")
                for name in obs.new_items:
                    self._note(f"+ item: {name}")
            if delta:
                self._note(f"* score {delta:+d} -> {self.score}/{MAX_SCORE}")
            if status == "repeat":
                self._note("! repeated a command with the same result (warning added to prompt)")
        if self.checkpoint_every and n % self.checkpoint_every == 0:
            self.checkpoint(n, world, cost)

    def checkpoint(self, n: int, world: WorldModel, cost: float) -> None:
        since = n - self.last_gain_move
        gain = (
            f"last gain move {self.last_gain_move} ({since} ago)"
            if self.last_gain_move
            else (f"no points yet ({since} moves)")
        )
        self._print(
            f"--- move {n}: score {self.score}/{MAX_SCORE} ({100 * self.score / MAX_SCORE:.1f}%) | "
            f"best {self.best} | {gain} | {len(world.rooms)} rooms, {len(world.items)} items | "
            f"${cost:.3f}"
        )

    def event(self, message: str) -> None:
        self._note(message)

    def end(self, record: RunRecord, world: WorldModel) -> None:
        elapsed = time.monotonic() - self._t0
        per_move = elapsed / record.moves if record.moves else 0.0
        exits = sum(len(room.exits) for room in world.rooms.values())
        self._print(
            f"=== ended {record.end_reason} after {record.moves} moves | "
            f"score {record.score}/{MAX_SCORE} ({100 * record.score / MAX_SCORE:.1f}%) | "
            f"${record.cost_usd:.3f} | {elapsed:.0f}s ({per_move:.1f}s/move)"
        )
        self._print(f"    score timeline: {self.timeline()}")
        self._print(
            f"    KBs: {len(world.rooms)} rooms, {exits} exits, {len(world.items)} items, "
            f"carrying {', '.join(world.inventory) or 'nothing'}"
        )

    def timeline(self) -> str:
        """'0 -> 5 @11 -> 15 @40' (every score change with the move it happened on)."""
        if not self.history:
            return f"{self.score} (no change)"
        start = self.history[0][2] - self.history[0][1]
        return " -> ".join([str(start), *(f"{total} @{n}" for n, _, total in self.history)])

    def _note(self, message: str) -> None:
        self._print(f"{'':>10}{message}")

    def _print(self, line: str) -> None:
        print(line, file=self.stream, flush=True)
