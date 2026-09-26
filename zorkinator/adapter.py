"""Jericho adapter exposing the game contract used by the harness."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from jericho import FrotzEnv

from .models import StepResult, StepResultPayload

DEFAULT_STORY_FILE = Path(__file__).resolve().parent.parent / "games" / "zork1.z5"
FORBIDDEN_COMMANDS = frozenset({"save", "restore", "restart"})


class GameAdapter:
    """Own one Jericho environment and expose only benchmark-safe operations."""

    def __init__(self, story_file: str | Path = DEFAULT_STORY_FILE) -> None:
        self.story_file = Path(story_file)
        self._env: FrotzEnv | None = None

    def reset(self, seed: int) -> str:
        """Start a fresh game with ``seed`` and return its visible opening text."""
        if not self.story_file.is_file():
            raise FileNotFoundError(
                f"Zork story file not found at {self.story_file}. See README.md setup."
            )
        self.close()
        self._env = FrotzEnv(str(self.story_file), seed=seed)
        text, _info = self._env.reset()
        if not isinstance(text, str):
            raise TypeError("Jericho returned non-text output from reset().")
        return text

    def step(self, cmd: str) -> StepResultPayload:
        """Apply one allowed command and return the public adapter result shape."""
        if self._env is None:
            raise RuntimeError("Call reset(seed) before step(cmd).")

        normalized = cmd.strip().casefold()
        if normalized in FORBIDDEN_COMMANDS:
            raise ValueError(f"Command {cmd.strip()!r} is forbidden by the benchmark.")
        if not normalized:
            raise ValueError("Command must not be empty.")

        text, _reward, done, info = self._env.step(cmd.strip())
        result = StepResult(
            text=text,
            score=int(info.get("score", 0)),
            moves=int(info.get("moves", 0)),
            done=done,
        )
        return cast(StepResultPayload, result.model_dump())

    def close(self) -> None:
        """Release the active Jericho environment, if any."""
        if self._env is not None:
            self._env.close()
            self._env = None

    def __enter__(self) -> GameAdapter:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


_default_adapter = GameAdapter()


def reset(seed: int) -> str:
    """Reset the default adapter, matching the project contract."""
    return _default_adapter.reset(seed)


def step(cmd: str) -> StepResultPayload:
    """Step the default adapter, matching the project contract."""
    return _default_adapter.step(cmd)
