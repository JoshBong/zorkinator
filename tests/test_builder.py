from __future__ import annotations

import unittest
from datetime import UTC, datetime

from zorkinator.builder import build_prompt
from zorkinator.models import EvidenceRef, HarnessVersionRecord, MemoryRef, MemoryRevision, RuleDoc

NOW = datetime(2026, 9, 26, 17, 0, tzinfo=UTC)


def memory(memory_id: str, revision_id: str, text: str) -> MemoryRevision:
    return MemoryRevision(
        revision_id=revision_id,
        experiment_id="experiment-a",
        memory_id=memory_id,
        supersedes_revision_id=None,
        kind="lesson",
        subjects=["test"],
        content={"text": text},
        status="supported",
        evidence=[EvidenceRef(run_id="run-0", n=1)],
        rationale="Observed in game one.",
        source_run_id="run-0",
        proposal_id="proposal-0",
        operation_key=f"add:{memory_id}",
        born_version_id="version-child",
        created_at=NOW,
    )


class FakePromptRepository:
    def __init__(self) -> None:
        self.memories = {
            ("version-child", "memory-one"): memory(
                "memory-one", "revision-one", "committed game-one memory"
            ),
            ("version-foreign", "memory-foreign"): memory(
                "memory-foreign", "revision-foreign", "must never leak"
            ),
        }

    def get_version(self, version_id: str) -> HarnessVersionRecord | None:
        return None

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None:
        return self.memories.get((version_id, memory_id))

    def get_rules(self, rule_ids: list[str]) -> list[RuleDoc]:
        return []


class PromptBuilderTests(unittest.TestCase):
    def test_uses_only_exact_passed_manifest(self) -> None:
        version = HarnessVersionRecord(
            version_id="version-child",
            experiment_id="experiment-a",
            parent_id="version-root",
            source_run_id="run-0",
            proposal_id="proposal-0",
            memory_refs=[MemoryRef(memory_id="memory-one", revision_id="revision-one")],
            rule_ids=[],
            context_policy={},
            scores=[],
            created_at=NOW,
        )

        prompt = build_prompt("run-1", 1, version, repository=FakePromptRepository())

        self.assertIn("committed game-one memory", prompt)
        self.assertIn('"version_id":"version-child"', prompt)
        self.assertNotIn("must never leak", prompt)
        self.assertNotIn("memory-foreign", prompt)


if __name__ == "__main__":
    unittest.main()
