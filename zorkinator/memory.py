"""Past-chat access, approximating the chatbot apps' memory used in arXiv 2602.15867.

The paper ran each game as a new chat in the consumer apps, where the model "does have access to
previous chats which the model may or may not use". Here that is two tools over an archive of
earlier games' transcripts (chat text only), shaped like the Claude app's past-chat tools.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from anthropic.types import MessageParam, ToolParam

MAX_RECENT = 3
RECENT_CHARS = 4000
SEARCH_HITS = 5
SNIPPET_CHARS = 400

TOOLS: list[ToolParam] = [
    {
        "name": "recent_chats",
        "description": (
            "Retrieve the user's most recent previous conversations with you, newest first. "
            "Returns the end of each conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"n": {"type": "integer", "minimum": 1, "maximum": MAX_RECENT}},
            "required": ["n"],
        },
    },
    {
        "name": "conversation_search",
        "description": (
            "Search the user's previous conversations with you by keyword. "
            "Returns matching snippets with the conversation they came from."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
]


class ChatArchive:
    """One plain-text transcript per finished game: <directory>/<run_id>.txt."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def save(self, run_id: str, messages: list[MessageParam]) -> Path:
        lines = [f"Conversation from {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", ""]
        for message in messages:
            text = _text_of(message["content"])
            if text:
                speaker = "User" if message["role"] == "user" else "Assistant"
                lines.append(f"{speaker}: {text}")
        path = self.directory / f"{run_id}.txt"
        path.write_text("\n".join(lines) + "\n")
        return path

    def chats(self) -> list[Path]:
        return sorted(self.directory.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)

    def recent(self, n: int) -> str:
        chats = self.chats()[: max(1, min(n, MAX_RECENT))]
        if not chats:
            return "No previous conversations."
        return "\n\n".join(
            f"--- {path.stem} ---\n...{path.read_text()[-RECENT_CHARS:]}" for path in chats
        )

    def search(self, query: str) -> str:
        words = [w for w in re.findall(r"\w+", query.casefold()) if len(w) > 2]
        if not words:
            return "No matches."
        scored: list[tuple[int, str]] = []
        for path in self.chats():
            text = path.read_text()
            lowered = text.casefold()
            counts = {w: lowered.count(w) for w in words if w in lowered}
            if not counts:
                continue
            # Center on the rarest matching word; common ones ("zork") say little.
            rarest = min(counts, key=lambda w: counts[w])
            i = lowered.rfind(rarest)
            start = max(0, i - SNIPPET_CHARS // 2)
            snippet = f"--- {path.stem} ---\n...{text[start : start + SNIPPET_CHARS]}..."
            scored.append((len(counts), snippet))
        scored.sort(key=lambda hit: hit[0], reverse=True)
        return "\n\n".join(s for _, s in scored[:SEARCH_HITS]) or "No matches."

    def run_tool(self, name: str, tool_input: dict[str, Any]) -> str:
        if name == "recent_chats":
            return self.recent(int(tool_input.get("n", 1)))
        if name == "conversation_search":
            return self.search(str(tool_input.get("query", "")))
        return f"Unknown tool {name!r}."


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b["text"] for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(parts)
    return ""
