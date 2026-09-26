from __future__ import annotations

import json
import unittest
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

import anthropic
from openai import OpenAI

from zorkinator.models import (
    HarnessVersionRecord,
    MemoryRef,
    MemoryRevision,
    MoveRecord,
    ReflectionProposal,
    RuleDoc,
    RunRecord,
)
from zorkinator.reflector import (
    AnthropicReflectionModel,
    OpenAIReflectionModel,
    ReflectionError,
    ReflectionModelResponse,
    Reflector,
    ReflectorLimits,
)

NOW = datetime(2026, 9, 26, 16, 0, tzinfo=UTC)


def run(*, moves: int = 2, model: str = "test-model") -> RunRecord:
    return RunRecord(
        run_id="run_2",
        version_id="v1",
        mode="harness",
        prompt="basic",
        model=model,
        seed=7,
        move_cap=20,
        score=5,
        moves=moves,
        died=True,
        death_move=moves,
        end_reason="death",
        tokens_in=1,
        tokens_out=1,
        tokens_cache_write=0,
        tokens_cache_read=0,
        cost_usd=0.01,
        started_at=NOW,
        ended_at=NOW,
    )


def move(n: int, *, delta: int = 0, died: bool = False) -> MoveRecord:
    return MoveRecord(
        run_id="run_2",
        n=n,
        room="Public Room",
        command=f"command {n}",
        proposals=["PRIVATE PROPOSAL MUST NOT LEAK"],
        rejections=[{"cmd": "x", "reason": "PRIVATE REJECTION MUST NOT LEAK"}],
        text=f"visible output {n}",
        score=delta,
        score_delta=delta,
        died=died,
        latency_ms=1,
        ts=NOW,
    )


def root() -> HarnessVersionRecord:
    return HarnessVersionRecord(
        version_id="v1",
        experiment_id="exp_1",
        parent_id=None,
        source_run_id=None,
        proposal_id=None,
        memory_refs=[],
        rule_ids=[],
        context_policy={},
        scores=[],
        created_at=NOW,
    )


class FakeRepository:
    def __init__(self, source: RunRecord, moves: list[MoveRecord]) -> None:
        self.source = source
        self.moves = moves
        self.recorded: list[tuple[ReflectionProposal, dict[str, Any]]] = []

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.source if run_id == self.source.run_id else None

    def get_moves(self, run_id: str) -> list[MoveRecord]:
        return list(self.moves)

    def get_version(self, version_id: str) -> HarnessVersionRecord | None:
        return root() if version_id == "v1" else None

    def recall(self, version_id: str, *, limit: int = 10) -> Sequence[MemoryRevision]:
        return []

    def get_rules(self, rule_ids: Sequence[str]) -> Sequence[RuleDoc]:
        return []

    def record_proposal(self, proposal: ReflectionProposal, **kwargs: Any) -> object:
        self.recorded.append((proposal, kwargs))
        return object()


class FakeModel:
    def __init__(self, content: str | dict[str, Any], *, model: str = "test-model") -> None:
        self.model = model
        self.content = content
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> ReflectionModelResponse:
        self.prompts.append(prompt)
        return ReflectionModelResponse(self.content, {"tokens": 12})


class ReflectorTests(unittest.TestCase):
    def test_anthropic_transport_normalizes_usage_without_sampling_overrides(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.content = [MagicMock(type="text", text='{"summary":"ok"}')]
        response.usage.input_tokens = 11
        response.usage.output_tokens = 7
        response.usage.cache_creation_input_tokens = None
        response.usage.cache_read_input_tokens = 3
        client.messages.create.return_value = response
        model = AnthropicReflectionModel(
            "test-model", client=cast(anthropic.Anthropic, client), max_tokens=321
        )

        result = model.generate("reflect this")

        self.assertEqual(result.content, '{"summary":"ok"}')
        self.assertEqual(
            result.usage,
            {
                "input_tokens": 11,
                "output_tokens": 7,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 3,
            },
        )
        kwargs = client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "test-model")
        self.assertEqual(kwargs["max_tokens"], 321)
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "reflect this"}])
        self.assertNotIn("output_config", kwargs)
        self.assertNotIn("temperature", kwargs)

    def test_anthropic_transport_rejects_empty_content(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.content = []
        client.messages.create.return_value = response
        model = AnthropicReflectionModel("test-model", client=cast(anthropic.Anthropic, client))

        with self.assertRaisesRegex(ReflectionError, "no text content"):
            model.generate("reflect this")

    def test_openai_transport_normalizes_usage_without_sampling_overrides(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.output_text = '{"summary":"ok"}'
        response.usage.input_tokens = 13
        response.usage.output_tokens = 5
        response.usage.input_tokens_details.cache_write_tokens = 2
        response.usage.input_tokens_details.cached_tokens = 7
        client.responses.create.return_value = response
        model = OpenAIReflectionModel(
            "gpt-5.6-luna", client=cast(OpenAI, client), max_output_tokens=654
        )

        result = model.generate("reflect this")

        self.assertEqual(result.content, '{"summary":"ok"}')
        self.assertEqual(
            result.usage,
            {
                "input_tokens": 13,
                "output_tokens": 5,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 7,
            },
        )
        kwargs = client.responses.create.call_args.kwargs
        self.assertEqual(kwargs["model"], "gpt-5.6-luna")
        self.assertEqual(kwargs["input"], "reflect this")
        self.assertEqual(kwargs["max_output_tokens"], 654)
        self.assertEqual(kwargs["reasoning"], {"effort": "none"})
        self.assertFalse(kwargs["store"])
        self.assertNotIn("temperature", kwargs)

    def test_openai_transport_rejects_empty_content(self) -> None:
        client = MagicMock()
        response = MagicMock()
        response.output_text = ""
        client.responses.create.return_value = response
        model = OpenAIReflectionModel("gpt-5.6-luna", client=cast(OpenAI, client))

        with self.assertRaisesRegex(ReflectionError, "no text content"):
            model.generate("reflect this")

    def test_proposes_from_public_packet_and_records_before_return(self) -> None:
        repository = FakeRepository(run(), [move(1), move(2, died=True)])
        model = FakeModel(
            {
                "memory_ops": [
                    {
                        "op": "add",
                        "key": "death_lesson",
                        "kind": "failure",
                        "subjects": ["Public Room"],
                        "content": {"text": "command 2 was fatal"},
                        "status": "supported",
                        "evidence": [{"run_id": "run_2", "n": 2}],
                        "rationale": "Avoid a repeated death.",
                    }
                ],
                "rule_diffs": [],
                "summary": "Remember the fatal action.",
            }
        )
        reflector = Reflector(
            repository,
            model,
            id_factory=lambda: "proposal_2",
            clock=lambda: NOW,
        )

        proposal = reflector.propose("run_2")

        self.assertEqual(proposal.proposal_id, "proposal_2")
        self.assertEqual(repository.recorded[0][0], proposal)
        self.assertEqual(repository.recorded[0][1]["experiment_id"], "exp_1")
        self.assertEqual(repository.recorded[0][1]["usage"], {"tokens": 12})
        prompt = model.prompts[0]
        self.assertIn("visible output 2", prompt)
        self.assertNotIn("PRIVATE PROPOSAL", prompt)
        self.assertNotIn("PRIVATE REJECTION", prompt)

    def test_accepts_fenced_json_and_keeps_outcome_relevant_move_in_bound(self) -> None:
        moves = [move(n, delta=1 if n == 2 else 0, died=n == 5) for n in range(1, 6)]
        repository = FakeRepository(run(moves=5), moves)
        model = FakeModel(
            '```json\n{"memory_ops": [], "rule_diffs": [], "summary": "No change."}\n```'
        )
        reflector = Reflector(
            repository,
            model,
            limits=ReflectorLimits(max_moves=2),
            id_factory=lambda: "proposal_2",
        )

        reflector.propose("run_2")

        packet = json.loads(model.prompts[0].split("EVIDENCE_PACKET\n", 1)[1])
        self.assertEqual([item["n"] for item in packet["transcript"]], [2, 5])

    def test_packet_discloses_memories_omitted_from_reflection(self) -> None:
        repository = FakeRepository(run(), [move(1), move(2, died=True)])
        parent = root().model_copy(
            update={
                "memory_refs": [
                    MemoryRef(memory_id="mem_old", revision_id="rev_old"),
                    MemoryRef(memory_id="mem_new", revision_id="rev_new"),
                ]
            }
        )
        repository.get_version = lambda version_id: parent  # type: ignore[method-assign]
        model = FakeModel({"memory_ops": [], "rule_diffs": [], "summary": "No change."})

        Reflector(repository, model).propose("run_2")

        packet = json.loads(model.prompts[0].split("EVIDENCE_PACKET\n", 1)[1])
        self.assertEqual(packet["memory_selection"]["active_count"], 2)
        self.assertEqual(packet["memory_selection"]["omitted_memory_ids"], ["mem_old", "mem_new"])

    def test_rejects_evidence_not_shown_to_model(self) -> None:
        repository = FakeRepository(run(), [move(1), move(2, died=True)])
        model = FakeModel(
            {
                "memory_ops": [
                    {
                        "op": "add",
                        "key": "invented",
                        "kind": "fact",
                        "subjects": [],
                        "content": {"text": "invented"},
                        "status": "hypothesis",
                        "evidence": [{"run_id": "run_2", "n": 99}],
                        "rationale": "bad",
                    }
                ],
                "rule_diffs": [],
                "summary": "Bad evidence.",
            }
        )

        with self.assertRaisesRegex(ReflectionError, "was not in the prompt"):
            Reflector(repository, model).propose("run_2")
        self.assertEqual(repository.recorded, [])

    def test_rejects_rule_evidence_not_shown_to_model(self) -> None:
        repository = FakeRepository(run(), [move(1), move(2, died=True)])
        model = FakeModel(
            {
                "memory_ops": [],
                "rule_diffs": [
                    {
                        "op": "add",
                        "key": "invented",
                        "text": "Invented rule.",
                        "when": {"action": "x"},
                        "evidence": [{"run_id": "run_2", "n": 99}],
                    }
                ],
                "summary": "Bad evidence.",
            }
        )

        with self.assertRaisesRegex(ReflectionError, "rule evidence.*was not in the prompt"):
            Reflector(repository, model).propose("run_2")
        self.assertEqual(repository.recorded, [])

    def test_rejects_malformed_rule_before_persisting_proposal(self) -> None:
        repository = FakeRepository(run(), [move(1), move(2, died=True)])
        model = FakeModel(
            {
                "memory_ops": [],
                "rule_diffs": [
                    {
                        "op": "add",
                        "key": "malformed",
                        "text": "Avoid this action.",
                        "when": "command 2",
                        "verdict": "block",
                        "evidence": [{"run_id": "run_2", "n": 2}],
                    }
                ],
                "summary": "Malformed rule.",
            }
        )

        with self.assertRaisesRegex(ReflectionError, "rule when must be an object"):
            Reflector(repository, model).propose("run_2")

        self.assertEqual(repository.recorded, [])

    def test_rejects_stale_memory_target_before_persisting_proposal(self) -> None:
        repository = FakeRepository(run(), [move(1), move(2, died=True)])
        parent = root().model_copy(
            update={"memory_refs": [MemoryRef(memory_id="mem_1", revision_id="rev_current")]}
        )
        repository.get_version = lambda version_id: parent  # type: ignore[method-assign]
        model = FakeModel(
            {
                "memory_ops": [
                    {
                        "op": "retire",
                        "memory_id": "mem_1",
                        "expected_revision_id": "rev_stale",
                        "rationale": "No longer useful.",
                        "evidence": [],
                    }
                ],
                "rule_diffs": [],
                "summary": "Retire stale memory.",
            }
        )

        with self.assertRaisesRegex(ReflectionError, "stale expected revision"):
            Reflector(repository, model).propose("run_2")

        self.assertEqual(repository.recorded, [])

    def test_rejects_model_mismatch_and_incomplete_transcript(self) -> None:
        with self.assertRaisesRegex(ReflectionError, "differs from run model"):
            Reflector(
                FakeRepository(run(), [move(1), move(2)]), FakeModel({}, model="other")
            ).propose("run_2")

        with self.assertRaisesRegex(ReflectionError, "transcript contains"):
            Reflector(FakeRepository(run(), [move(1)]), FakeModel({})).propose("run_2")


if __name__ == "__main__":
    unittest.main()
