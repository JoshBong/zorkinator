"""OpenAI-backed ``Chat`` for harness runs (same interface as runner.AnthropicChat).

Note: the team benchmark model is claude-haiku-4-5 (DECISIONS.md). Runs on another vendor are
not comparable to the Haiku paper-mode baseline unless the baseline is rerun on that model too.
"""

from __future__ import annotations

from typing import Any

from anthropic.types import MessageParam
from openai import OpenAI

from .memory import ChatArchive
from .runner import ChatReply, Usage

# USD per million tokens: (input, cached input, output). OpenAI list prices; verify before
# relying on the $ cap for a new model. Unlisted models are refused so the cap always works.
OPENAI_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
}
MAX_OUTPUT_TOKENS = 1024


class OpenAIChat:
    """Chat Completions call. No thinking where the model allows it (gpt-5*: minimal effort)."""

    def __init__(self, model: str, client: OpenAI | None = None) -> None:
        if model not in OPENAI_PRICES:
            raise ValueError(f"No price for {model!r}; add it to OPENAI_PRICES so the $ cap works.")
        self.model = model
        self._client = client or OpenAI(max_retries=8)

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply:
        kwargs: dict[str, Any] = {}
        if self.model.startswith("gpt-5"):
            kwargs["reasoning_effort"] = "minimal"
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": m["role"], "content": _text(m["content"])} for m in messages],  # type: ignore[misc]
            max_completion_tokens=MAX_OUTPUT_TOKENS,
            **kwargs,
        )
        text = response.choices[0].message.content or ""
        u = response.usage
        cached = (
            (u.prompt_tokens_details.cached_tokens or 0) if u and u.prompt_tokens_details else 0
        )
        usage = Usage(
            input=(u.prompt_tokens - cached) if u else 0,
            output=u.completion_tokens if u else 0,
            cache_read=cached,
        )
        return ChatReply(text, usage, [{"role": "assistant", "content": text or "(no reply)"}])

    def cost(self, usage: Usage) -> float:
        price_in, price_cached, price_out = OPENAI_PRICES[self.model]
        return (
            usage.input * price_in + usage.cache_read * price_cached + usage.output * price_out
        ) / 1_000_000


def _text(content: object) -> str:
    """Flatten Anthropic-style content blocks to one string. The fixed prefix stays first, so
    OpenAI's automatic prefix caching can apply once the prefix passes its size threshold."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n\n".join(
            str(b["text"]) for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""
