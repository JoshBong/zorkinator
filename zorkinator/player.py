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
    expect: str | None = None
    surprise: bool | None = None


def parse_goal(reply: str) -> str | None:
    return _tag(reply, "goal:")


def parse_surprise(reply: str) -> bool | None:
    value = (_tag(reply, "surprise:") or "").casefold()
    if value.startswith("yes"):
        return True
    if value.startswith("no"):
        return False
    return None


def _tag(reply: str, tag: str) -> str | None:
    for line in reply.splitlines():
        stripped = line.strip()
        if stripped.casefold().startswith(tag):
            return stripped[len(tag) :].strip() or None
    return None


def propose(chat: Chat, prompt: PromptParts, feedback: str | None = None) -> Proposal:
    """``feedback`` is a verifier message (a rejection or a warning) to retry with."""
    tail = prompt.tail if feedback is None else f"{prompt.tail}\n\n{feedback}"
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
        expect=_tag(reply.text, "expect:"),
        surprise=parse_surprise(reply.text),
    )
