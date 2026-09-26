"""Progress monitor: notices repetition and stalls. Never blocks a command."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from .scribe import Observation
from .world import WorldModel

STUCK_AFTER = 60  # moves with no new room, item, or score before the game ends ("stuck40")
REPEAT_LIMIT = 2  # same command, same room, same outcome more than this -> warning

Status = Literal["ok", "repeat", "idle", "stuck"]
IDLE_AFTER = 15  # moves with no new room, item, or score: switch to exploring the frontier


class Monitor:
    def __init__(
        self,
        stuck_after: int = STUCK_AFTER,
        repeat_limit: int = REPEAT_LIMIT,
        idle_after: int = IDLE_AFTER,
    ) -> None:
        self.stuck_after = stuck_after
        self.idle_after = idle_after
        self.repeat_limit = repeat_limit
        self.last_progress = 0
        self._repeats: Counter[tuple[str | None, str, str]] = Counter()
        self.status: Status = "ok"

    def update(self, world: WorldModel, n: int, obs: Observation) -> Status:
        if obs.progressed:
            self.last_progress = n
        step = world.state.recent[-1] if world.state.recent else None
        repeated = False
        if step is not None:
            key = (step.room, step.command.casefold(), step.outcome)
            self._repeats[key] += 1
            repeated = self._repeats[key] > self.repeat_limit
        if n - self.last_progress >= self.stuck_after:
            self.status = "stuck"
        elif repeated:
            self.status = "repeat"
        elif n - self.last_progress >= self.idle_after:
            self.status = "idle"
        else:
            self.status = "ok"
        return self.status

    def idle_moves(self, n: int) -> int:
        """Moves since the last new room, item, interaction, or score gain."""
        return max(0, n - self.last_progress)
