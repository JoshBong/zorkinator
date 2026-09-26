"""Offline stand-in for the model: plays from the harness prompt's own blocks, no API calls.

It exists to exercise the inner loop end to end (map, items, leads, monitor, trace) without a key.
It reads only the prompt text the real model would see, so it is also a check that the prompt
carries enough to act on. It is not a baseline and its runs should never be reported as scores.
"""

from __future__ import annotations

import random
import re

from anthropic.types import MessageParam

from .memory import ChatArchive
from .runner import ChatReply, Usage

COMPASS = ["north", "south", "east", "west", "up", "down", "northeast", "southwest"]


class ExplorerChat:
    model = "offline-explorer"

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply:
        content = messages[-1]["content"]
        tail = content[-1]["text"] if isinstance(content, list) else str(content)
        command, goal = self._choose(str(tail))
        text = f"{command}\nGoal: {goal}"
        return ChatReply(text, Usage(), [{"role": "assistant", "content": text}])

    def _choose(self, tail: str) -> tuple[str, str]:
        tried = _field(tail, "Already tried here")
        exits = _field(tail, "Exits")
        seen = _field(tail, "Seen here")

        untaken = re.findall(r"(\w+) \(mentioned, never taken\)", exits)
        if untaken:
            direction = self._rng.choice(untaken)
            return direction, f"follow the {direction} exit the game mentioned"
        for item in (s.strip() for s in seen.split(",") if s.strip()):
            noun = item.split()[-1]  # Zork's parser wants the head noun: "take egg"
            for verb in ("examine", "take"):
                command = f"{verb} {noun}"
                if command not in tried:
                    return command, f"find out what the {item} is for"
        known = re.findall(r"(\w+) -> ", exits)
        options = [d for d in COMPASS if d not in tried] or known or COMPASS
        direction = self._rng.choice(options)
        return direction, "explore somewhere new"


def _field(tail: str, label: str) -> str:
    match = re.search(rf"^{label}: (.*)$", tail, re.MULTILINE)
    return match.group(1) if match else ""
