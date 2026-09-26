from __future__ import annotations

import io
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

from anthropic.types import MessageParam
from openai import OpenAI

from zorkinator import adapter
from zorkinator.builder import (
    HarnessPromptBuilder,
    PromptParts,
    build_prefix,
    build_tail,
    build_version_context,
)
from zorkinator.memory import ChatArchive, _text_of
from zorkinator.models import (
    AddMemoryOperation,
    EvidenceRef,
    HarnessVersionRecord,
    MemoryRef,
    MemoryRevision,
    ReflectionProposal,
    RunRecord,
)
from zorkinator.openai_chat import OPENAI_PRICES, OpenAIChat, _openai_input
from zorkinator.progress import ConsoleProgress
from zorkinator.scribe import Observation
from zorkinator.world import WorldModel

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def version() -> HarnessVersionRecord:
    return HarnessVersionRecord(
        version_id="v1",
        experiment_id="exp",
        parent_id=None,
        source_run_id=None,
        proposal_id=None,
        memory_refs=[],
        rule_ids=[],
        context_policy={},
        scores=[],
        created_at=NOW,
    )


def record(moves: int = 0) -> RunRecord:
    return RunRecord(
        run_id="run",
        version_id="v1",
        mode="harness",
        prompt="p",
        model="m",
        seed=1,
        move_cap=2,
        score=3,
        moves=moves,
        died=False,
        death_move=None,
        end_reason="cap",
        tokens_in=0,
        tokens_out=0,
        tokens_cache_write=0,
        tokens_cache_read=0,
        cost_usd=0.0,
        started_at=NOW,
        ended_at=NOW,
    )


class AdapterUnitTests(unittest.TestCase):
    def test_adapter_errors_and_global_delegates(self) -> None:
        game = adapter.GameAdapter("/not/a/story")
        with self.assertRaises(FileNotFoundError):
            game.reset(1)
        with self.assertRaises(RuntimeError):
            game.step("look")

        fake = MagicMock()
        fake.step.return_value = ("text", 0, True, {"score": 4, "moves": 2})
        game._env = fake
        self.assertEqual(
            game.step(" LOOK "), {"text": "text", "score": 4, "moves": 2, "done": True}
        )
        with self.assertRaises(ValueError):
            game.step(" ")
        game.close()
        fake.close.assert_called_once()

        default = MagicMock()
        default.reset.return_value = "opening"
        default.step.return_value = {"text": "x", "score": 0, "moves": 1, "done": False}
        with patch.object(adapter, "_default_adapter", default):
            self.assertEqual(adapter.reset(3), "opening")
            self.assertEqual(adapter.step("look")["text"], "x")

    def test_adapter_rejects_non_text_reset_and_context_manager(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            story = Path(tmp) / "story.z5"
            story.touch()
            env = MagicMock()
            env.reset.return_value = (b"bad", {})
            with patch.object(adapter, "FrotzEnv", return_value=env):
                game = adapter.GameAdapter(story)
                with self.assertRaises(TypeError):
                    game.reset(2)
                self.assertIs(game.__enter__(), game)
                game.__exit__()


class BuilderUnitTests(unittest.TestCase):
    def test_prefix_tail_and_short_edge_cases(self) -> None:
        world = WorldModel("run")
        self.assertIn("none yet", build_prefix(world))
        self.assertIn("Location: unknown.", build_tail(world, 1, ""))
        self.assertEqual(str(PromptParts("a", "b")), "a\n\nb")
        self.assertEqual(build_prefix(world, "context", "basic").endswith("context"), True)

    def test_version_context_rejects_missing_memory_and_rule_manifest(self) -> None:
        class Repo:
            def get_version(self, _version_id: str) -> HarnessVersionRecord | None:
                return None

            def read(self, _version_id: str, _memory_id: str) -> MemoryRevision | None:
                return None

            def get_rules(self, _rule_ids: list[str]) -> list[Any]:
                return []

        with self.assertRaisesRegex(ValueError, "cannot resolve"):
            build_version_context(
                "run",
                version().model_copy(
                    update={"memory_refs": [MemoryRef(memory_id="m", revision_id="r")]}
                ),
                repository=Repo(),
            )

        class WrongRules(Repo):
            def get_rules(self, _rule_ids: list[str]) -> list[Any]:
                return []

        with self.assertRaisesRegex(ValueError, "rule manifest"):
            build_version_context(
                "run", version().model_copy(update={"rule_ids": ["r"]}), repository=WrongRules()
            )

    def test_bound_prompt_builder_delegates(self) -> None:
        class Repo:
            def get_version(self, _version_id: str) -> HarnessVersionRecord | None:
                return None

            def read(self, _version_id: str, _memory_id: str) -> MemoryRevision | None:
                return None

            def get_rules(self, _rule_ids: list[str]) -> list[Any]:
                return []

        self.assertIn(
            "Current move number: 2", HarnessPromptBuilder(Repo()).build_prompt("run", 2, version())
        )


class MemoryUnitTests(unittest.TestCase):
    def test_archive_save_recent_search_and_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = ChatArchive(tmp)
            archive.save("one", [{"role": "user", "content": "find the lantern"}])
            archive.save("two", [{"role": "assistant", "content": "the lantern is brass"}])
            self.assertIn("two", archive.recent(99))
            self.assertIn("lantern", archive.search("lantern"))
            self.assertEqual(archive.search("a an"), "No matches.")
            self.assertIn("two", archive.run_tool("recent_chats", {"n": 1}))
            self.assertIn("lantern", archive.run_tool("conversation_search", {"query": "lantern"}))
            self.assertIn("Unknown tool", archive.run_tool("other", {}))

    def test_text_extraction_accepts_only_text_blocks(self) -> None:
        self.assertEqual(_text_of("plain"), "plain")
        self.assertEqual(_text_of([{"type": "text", "text": "a"}, {"type": "image"}]), "a")
        self.assertEqual(_text_of(3), "")


class OpenAITransportUnitTests(unittest.TestCase):
    def test_transport_validation_completion_cost_and_input_errors(self) -> None:
        with self.assertRaises(ValueError):
            OpenAIChat("unpriced", client=cast(OpenAI, MagicMock()))
        with self.assertRaises(ValueError):
            OpenAIChat("gpt-5.6-luna", client=cast(OpenAI, MagicMock()), max_output_tokens=0)
        client = MagicMock()
        response = MagicMock()
        response.output_text = None
        response.usage = None
        client.responses.create.return_value = response
        chat = OpenAIChat("gpt-5.6-luna", client=cast(OpenAI, client))
        reply = chat.complete([{"role": "user", "content": "hi"}])
        self.assertEqual(reply.text, None)
        self.assertEqual(reply.transcript[0]["content"], "(no reply)")
        self.assertEqual(chat.cost(reply.usage), 0.0)
        with self.assertRaisesRegex(ValueError, "past-chat"):
            chat.complete([], ChatArchive(tempfile.mkdtemp()))
        self.assertEqual(
            _openai_input(
                cast(
                    list[MessageParam],
                    [{"role": "user", "content": [{"type": "text", "text": "a"}]}],
                )
            ),
            [{"role": "user", "content": "a"}],
        )
        with self.assertRaisesRegex(ValueError, "text message"):
            _openai_input(
                cast(list[MessageParam], [{"role": "user", "content": [{"type": "image"}]}])
            )
        self.assertIn("gpt-5.6-luna", OPENAI_PRICES)


class ProgressAndModelUnitTests(unittest.TestCase):
    def test_progress_verbose_checkpoint_and_end(self) -> None:
        stream = io.StringIO()
        world = WorldModel("run")
        world.state.room = "Room"
        world.state.goal = "win"
        world.state.score = 2
        progress = ConsoleProgress(stream=stream, checkpoint_every=1)
        progress.start("run", "model", 1, 2, 0.1)
        progress.opening(world)
        world.state.score = 3
        progress.move(1, 2, "Room", "look", "output", 50, 0.01, world, Observation(), "repeat")
        progress.event("event")
        progress.end(record(), world)
        self.assertIn("repeated", stream.getvalue())
        self.assertIn("3 @1", progress.timeline())

    def test_model_validators_reject_duplicate_operations_and_refs(self) -> None:
        operation = AddMemoryOperation(
            op="add",
            key="same",
            kind="fact",
            subjects=[],
            content={},
            status="hypothesis",
            evidence=[EvidenceRef(run_id="r", n=1)],
            rationale="because",
        )
        with self.assertRaisesRegex(ValueError, "duplicate add"):
            ReflectionProposal(
                proposal_id="p",
                run_id="r",
                parent_id="v",
                memory_ops=[operation, operation],
                rule_diffs=[],
                summary="s",
            )
        with self.assertRaisesRegex(ValueError, "unique memory"):
            HarnessVersionRecord(
                version_id="v1",
                experiment_id="exp",
                parent_id=None,
                source_run_id=None,
                proposal_id=None,
                memory_refs=[
                    MemoryRef(memory_id="m", revision_id="a"),
                    MemoryRef(memory_id="m", revision_id="b"),
                ],
                rule_ids=[],
                context_policy={},
                scores=[],
                created_at=NOW,
            )
