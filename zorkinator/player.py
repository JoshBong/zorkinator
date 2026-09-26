"""Player: one model call per move -> one command, plus an optional running goal."""

from __future__ import annotations

from dataclasses import dataclass

from anthropic.types import MessageParam, TextBlockParam

from .builder import PromptParts
from .runner import Chat, Usage, extract_command, gave_up


@dataclass
class Proposal:
    command: str
    goal: str | None
    gave_up: bool
    raw: str
    usage: Usage


def parse_goal(reply: str) -> str | None:
    for line in reply.splitlines()[1:]:
        stripped = line.strip()
        if stripped.casefold().startswith("goal:"):
            return stripped[5:].strip() or None
    return None


def propose(chat: Chat, prompt: PromptParts, feedback: str | None = None) -> Proposal:
    """``feedback`` is a verifier rejection reason to retry with (verifier not built yet)."""
    tail = prompt.tail if feedback is None else f"{prompt.tail}\n\nRejected: {feedback}"
    prefix_block: TextBlockParam = {
        "type": "text",
        "text": prompt.prefix,
        "cache_control": {"type": "ephemeral"},
    }
    messages: list[MessageParam] = [
        {"role": "user", "content": [prefix_block, {"type": "text", "text": tail}]}
    ]
    reply = chat.complete(messages)
    return Proposal(
        command=extract_command(reply.text),
        goal=parse_goal(reply.text),
        gave_up=gave_up(reply.text),
        raw=reply.text,
        usage=reply.usage,
    )
