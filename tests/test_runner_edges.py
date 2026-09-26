from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import MagicMock, patch

import anthropic

from zorkinator import runner
from zorkinator.runner import AnthropicChat, ChatReply, Usage


class FakeSink:
    def __init__(self) -> None:
        self.moves: list[Any] = []
        self.runs: list[Any] = []

    def move(self, record: Any) -> None:
        self.moves.append(record)

    def run(self, record: Any) -> None:
        self.runs.append(record)


class FakeGame:
    result: ClassVar[dict[str, Any]] = {"text": "done", "score": 0, "moves": 1, "done": True}
    error: ClassVar[ValueError | None] = None

    def __init__(self, _story: object) -> None:
        pass

    def __enter__(self) -> FakeGame:
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def reset(self, _seed: int) -> str:
        return "opening"

    def step(self, _command: str) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return self.result


class ScriptedChat:
    model = "claude-haiku-4-5"

    def __init__(self, replies: list[str], usage: Usage | None = None) -> None:
        self.replies = replies
        self.usage = usage or Usage()

    def complete(self, _messages: list[Any], archive: object = None) -> ChatReply:
        return ChatReply(self.replies.pop(0), self.usage, [{"role": "assistant", "content": "x"}])


def response(content: list[Any]) -> Any:
    return SimpleNamespace(
        content=content,
        usage=SimpleNamespace(
            input_tokens=2,
            output_tokens=3,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        ),
    )


class AnthropicChatTests(unittest.TestCase):
    def test_transport_validation_plain_and_tool_rounds(self) -> None:
        with self.assertRaises(ValueError):
            AnthropicChat("unknown", client=cast(anthropic.Anthropic, MagicMock()))
        client = MagicMock()
        client.messages.create.return_value = response([SimpleNamespace(type="text", text="look")])
        chat = AnthropicChat("claude-haiku-4-5", client=cast(anthropic.Anthropic, client))
        plain = chat.complete([{"role": "user", "content": "p"}])
        self.assertEqual(plain.text, "look")
        self.assertEqual(
            client.messages.create.call_args.kwargs["cache_control"], {"type": "ephemeral"}
        )

        archive = MagicMock()
        tool = SimpleNamespace(type="tool_use", id="id", name="recent_chats", input={"n": 1})
        client.messages.create.side_effect = [
            response([tool]),
            response([SimpleNamespace(type="text", text="north")]),
        ]
        reply = chat.complete([{"role": "user", "content": "p"}], archive)
        self.assertEqual(reply.text, "north")
        self.assertEqual(reply.tool_calls, ['recent_chats({"n": 1})'])
        self.assertEqual(client.messages.create.call_args.kwargs["tool_choice"], {"type": "auto"})

    def test_tool_budget_returns_empty_reply(self) -> None:
        client = MagicMock()
        tool = SimpleNamespace(type="tool_use", id="id", name="recent_chats", input=None)
        client.messages.create.side_effect = [
            response([tool]) for _ in range(runner.MAX_TOOL_ROUNDS)
        ] + [response([tool])]
        archive = MagicMock()
        reply = AnthropicChat(
            "claude-haiku-4-5", client=cast(anthropic.Anthropic, client)
        ).complete([{"role": "user", "content": "p"}], archive)
        self.assertEqual(reply.text, "")
        self.assertEqual(len(reply.tool_calls), runner.MAX_TOOL_ROUNDS + 1)
        self.assertEqual(client.messages.create.call_args.kwargs["tool_choice"], {"type": "none"})


class PlayEdgeTests(unittest.TestCase):
    def test_configuration_and_terminal_paths(self) -> None:
        sink = FakeSink()
        chat = ScriptedChat(["ack"])
        with self.assertRaisesRegex(ValueError, "harness play requires"):
            runner.play("harness", 1, 1, chat=chat, sink=sink)
        with self.assertRaisesRegex(ValueError, "paper mode cannot"):
            runner.play("paper", 1, 1, chat=chat, sink=sink, version=cast(Any, object()))

        with patch.object(runner, "GameAdapter", FakeGame):
            record = runner.play(
                "paper",
                1,
                1,
                chat=ScriptedChat(["ack"], Usage(input=10_000_000)),
                sink=sink,
                usd_cap=0.1,
            )
        self.assertEqual((record.end_reason, record.moves), ("usd_cap", 0))

    def test_game_over_death_and_invalid_command(self) -> None:
        sink = FakeSink()
        with patch.object(runner, "GameAdapter", FakeGame):
            FakeGame.result = {"text": "You have died.", "score": 1, "moves": 1, "done": True}
            death = runner.play("paper", 1, 1, chat=ScriptedChat(["ack", "north"]), sink=sink)
            FakeGame.result = {"text": "The end.", "score": 350, "moves": 1, "done": True}
            won = runner.play("paper", 1, 1, chat=ScriptedChat(["ack", "north"]), sink=sink)
            FakeGame.error = ValueError("blocked")
            blocked = runner.play("paper", 1, 1, chat=ScriptedChat(["ack", "north"]), sink=sink)
            FakeGame.error = None
        self.assertEqual(death.end_reason, "death")
        self.assertEqual(won.end_reason, "won")
        self.assertEqual(blocked.end_reason, "cap")
        self.assertIn("Not allowed", sink.moves[-1].text)
