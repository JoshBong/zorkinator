"""OpenAI Responses API ``Chat`` adapter for development and harness smoke runs.

Note: the team benchmark model is claude-haiku-4-5 (DECISIONS.md). Runs on another vendor are
not comparable to the Haiku paper-mode baseline unless the baseline is rerun on that model too.
"""

from __future__ import annotations

from typing import cast

from anthropic.types import MessageParam
from openai import OpenAI
from openai.types.responses import ResponseInputParam

from .memory import ChatArchive
from .runner import OPENAI_DEV_MODEL, ChatReply, Usage

# USD per million tokens: (input, cached input, output). OpenAI list prices; verify before
# relying on the $ cap for a new model. Unlisted models are refused so the cap always works.
OPENAI_PRICES: dict[str, tuple[float, float, float]] = {
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-nano": (0.10, 0.025, 0.40),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
}
MAX_OUTPUT_TOKENS = 1024


class OpenAIChat:
    """Stateless Responses API transport with normalized usage and cost accounting."""

    def __init__(
        self,
        model: str = OPENAI_DEV_MODEL,
        client: OpenAI | None = None,
        *,
        max_output_tokens: int = MAX_OUTPUT_TOKENS,
    ) -> None:
        if model not in OPENAI_PRICES:
            raise ValueError(f"No price for {model!r}; add it to OPENAI_PRICES so the $ cap works.")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        self.model = model
        self._client = client or OpenAI(max_retries=8)
        self._max_output_tokens = max_output_tokens

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply:
        if archive is not None:
            raise ValueError("OpenAI development transport does not support past-chat archives")
        response = self._client.responses.create(
            model=self.model,
            input=_openai_input(messages),
            max_output_tokens=self._max_output_tokens,
            reasoning={"effort": "none"},
            store=False,
        )
        text = response.output_text
        usage = response.usage
        details = None if usage is None else usage.input_tokens_details
        normalized = Usage(
            input=0 if usage is None else usage.input_tokens,
            output=0 if usage is None else usage.output_tokens,
            cache_write=0 if details is None else details.cache_write_tokens,
            cache_read=0 if details is None else details.cached_tokens,
        )
        transcript: list[MessageParam] = [{"role": "assistant", "content": text or "(no reply)"}]
        return ChatReply(text, normalized, transcript)

    def cost(self, usage: Usage) -> float:
        price_in, price_cached, price_out = OPENAI_PRICES[self.model]
        return (
            usage.input * price_in
            + usage.cache_write * price_in * 1.25
            + usage.cache_read * price_cached
            + usage.output * price_out
        ) / 1_000_000


def _openai_input(messages: list[MessageParam]) -> ResponseInputParam:
    """Convert provider-neutral text messages while preserving fixed-prefix ordering."""
    converted: list[dict[str, str]] = []
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            text = content
        else:
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    value = block.get("text")
                    if isinstance(value, str):
                        parts.append(value)
                        continue
                raise ValueError("OpenAI development transport accepts text message blocks only")
            text = "\n\n".join(parts)
        converted.append({"role": message["role"], "content": text})
    return cast(ResponseInputParam, converted)
