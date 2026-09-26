from __future__ import annotations

import unittest
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import JsonValue

from zorkinator.models import (
    AddMemoryOperation,
    EvidenceRef,
    HarnessVersionRecord,
    MemoryEvent,
    MemoryRef,
    MemoryRevision,
    ReflectionProposal,
    RetireMemoryOperation,
    ReviseMemoryOperation,
    RuleDoc,
    RunRecord,
)
from zorkinator.versions import CommitError, VersionManager

NOW = datetime(2026, 9, 26, 16, 0, tzinfo=UTC)


def run(run_id: str = "run_1", version_id: str = "v1") -> RunRecord:
    return RunRecord(
        run_id=run_id,
        version_id=version_id,
        mode="harness",
        prompt="basic",
        model="test-model",
        seed=1,
        move_cap=20,
        score=5,
        moves=3,
        died=True,
        death_move=3,
        end_reason="death",
        tokens_in=1,
        tokens_out=1,
        tokens_cache_write=0,
        tokens_cache_read=0,
        cost_usd=0.01,
        started_at=NOW,
        ended_at=NOW,
    )


def version(
    version_id: str = "v1",
    *,
    parent_id: str | None = None,
    source_run_id: str | None = None,
    proposal_id: str | None = None,
    refs: list[MemoryRef] | None = None,
    rules: list[str] | None = None,
) -> HarnessVersionRecord:
    return HarnessVersionRecord(
        version_id=version_id,
        experiment_id="exp_1",
        parent_id=parent_id,
        source_run_id=source_run_id,
        proposal_id=proposal_id,
        memory_refs=refs or [],
        rule_ids=rules or [],
        context_policy={"recent_moves": 5},
        scores=[],
        created_at=NOW,
    )


def memory() -> MemoryRevision:
    return MemoryRevision(
        revision_id="rev_old",
        experiment_id="exp_1",
        memory_id="mem_old",
        supersedes_revision_id=None,
        kind="fact",
        subjects=["room"],
        content={"text": "old"},
        status="supported",
        evidence=[EvidenceRef(run_id="run_0", n=1)],
        rationale="old",
        source_run_id="run_0",
        proposal_id="proposal_0",
        operation_key="add_0",
        born_version_id="v1",
        created_at=NOW,
    )


class FakeStore:
    def __init__(self) -> None:
        self.versions = {
            "v1": version(refs=[MemoryRef(memory_id="mem_old", revision_id="rev_old")])
        }
        self.revisions = {"rev_old": memory()}
        self.events: dict[str, MemoryEvent] = {}
        self.publish_calls = 0
        self.fail_publish = False
        self.rule_store: FakeRules | None = None

    def get_version(self, version_id: str) -> HarnessVersionRecord | None:
        return self.versions.get(version_id)

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None:
        manifest = self.versions.get(version_id)
        if manifest is None:
            return None
        ref = next((item for item in manifest.memory_refs if item.memory_id == memory_id), None)
        return None if ref is None else self.revisions.get(ref.revision_id)

    def put_memory_revision(self, revision: MemoryRevision) -> None:
        old = self.revisions.get(revision.revision_id)
        if old is not None and old != revision:
            raise ValueError("immutable revision mismatch")
        self.revisions[revision.revision_id] = revision

    def put_memory_event(self, event: MemoryEvent) -> None:
        old = self.events.get(event.event_id)
        if old is not None and old != event:
            raise ValueError("immutable event mismatch")
        self.events[event.event_id] = event

    def get_memory_event(self, event_id: str) -> MemoryEvent | None:
        return self.events.get(event_id)

    def publish_version(self, child: HarnessVersionRecord) -> None:
        self.publish_calls += 1
        if self.fail_publish:
            raise RuntimeError("publish failed")
        old = self.versions.get(child.version_id)
        if old is not None and old != child:
            raise ValueError("immutable version mismatch")
        self.versions[child.version_id] = child

    def publish_bundle(
        self,
        child: HarnessVersionRecord,
        revisions: Sequence[MemoryRevision],
        rules: Sequence[RuleDoc],
        events: Sequence[MemoryEvent],
    ) -> None:
        self.publish_calls += 1
        if self.fail_publish:
            raise RuntimeError("publish failed")
        if self.rule_store is None:
            raise RuntimeError("rule store is not configured")
        for revision in revisions:
            self.put_memory_revision(revision)
        for rule in rules:
            self.rule_store.put_rule(rule)
        old = self.versions.get(child.version_id)
        if old is not None and old != child:
            raise ValueError("immutable version mismatch")
        self.versions[child.version_id] = child
        for event in events:
            self.put_memory_event(event)


class FakeEvidence:
    def __init__(self) -> None:
        self.runs = {"run_1": run(), "run_0": run("run_0", "v0")}
        self.moves = {("run_1", 3), ("run_0", 1)}

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    def has_move(self, run_id: str, n: int) -> bool:
        return (run_id, n) in self.moves


class FakeRules:
    def __init__(self) -> None:
        self.rules: dict[str, RuleDoc] = {}

    def get_rule(self, rule_id: str) -> RuleDoc | None:
        return self.rules.get(rule_id)

    def put_rule(self, rule: RuleDoc) -> None:
        old = self.rules.get(rule.id)
        if old is not None and old != rule:
            raise ValueError("immutable rule mismatch")
        self.rules[rule.id] = rule


class FakePromoter:
    def __init__(self, result: Literal["hard", "soft"] = "soft") -> None:
        self.result = result
        self.calls: list[tuple[RuleDoc, list[RunRecord]]] = []

    def promote(self, rule: RuleDoc, runs: Sequence[RunRecord]) -> Literal["hard", "soft"]:
        self.calls.append((rule, list(runs)))
        return self.result


def add_proposal(
    *, evidence_n: int = 3, rule_diffs: list[dict[str, JsonValue]] | None = None
) -> ReflectionProposal:
    return ReflectionProposal(
        proposal_id="proposal_1",
        run_id="run_1",
        parent_id="v1",
        memory_ops=[
            AddMemoryOperation(
                op="add",
                key="new_fact",
                kind="fact",
                subjects=["room"],
                locations=["West of House"],
                content={"text": "new"},
                status="supported",
                evidence=[EvidenceRef(run_id="run_1", n=evidence_n)],
                rationale="learned",
            )
        ],
        rule_diffs=[] if rule_diffs is None else rule_diffs,
        summary="Learned something.",
    )


class VersionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeStore()
        self.evidence = FakeEvidence()
        self.rules = FakeRules()
        self.store.rule_store = self.rules
        self.promoter = FakePromoter()
        self.manager = VersionManager(self.store, self.evidence, self.rules, self.promoter)

    def test_add_publishes_complete_child_and_retry_is_idempotent(self) -> None:
        proposal = add_proposal()

        child_id = self.manager.commit("v1", proposal, "run_1")
        retried_id = self.manager.commit("v1", proposal, "run_1")

        self.assertEqual(retried_id, child_id)
        child = self.store.versions[child_id]
        self.assertEqual(child.parent_id, "v1")
        self.assertEqual(len(child.memory_refs), 2)
        self.assertEqual(child.context_policy, {"recent_moves": 5})
        self.assertEqual(
            self.store.revisions[child.memory_refs[1].revision_id].locations, ["West of House"]
        )
        self.assertEqual(child.scores, [])
        self.assertEqual(self.store.publish_calls, 1)
        self.assertIn("proposal_1:validated", self.store.events)
        self.assertIn("proposal_1:committed", self.store.events)

    def test_add_key_cannot_collide_with_revision_operation_namespace(self) -> None:
        proposal = add_proposal()
        proposal = proposal.model_copy(
            update={
                "memory_ops": [
                    proposal.memory_ops[0].model_copy(update={"key": "revise:1"}),
                    ReviseMemoryOperation(
                        op="revise",
                        memory_id="mem_old",
                        expected_revision_id="rev_old",
                        kind="fact",
                        subjects=["room"],
                        content={"text": "corrected"},
                        status="supported",
                        evidence=[EvidenceRef(run_id="run_1", n=3)],
                        rationale="correct it",
                    ),
                ]
            }
        )

        self.manager.commit("v1", proposal, "run_1")

        operation_keys = {revision.operation_key for revision in self.store.revisions.values()}
        self.assertIn("add:revise:1", operation_keys)
        self.assertIn("revise:1", operation_keys)

    def test_revise_replaces_exact_revision_and_retire_removes_only_child_ref(self) -> None:
        revise = ReflectionProposal(
            proposal_id="proposal_revise",
            run_id="run_1",
            parent_id="v1",
            memory_ops=[
                ReviseMemoryOperation(
                    op="revise",
                    memory_id="mem_old",
                    expected_revision_id="rev_old",
                    kind="fact",
                    subjects=["room"],
                    content={"text": "corrected"},
                    status="supported",
                    evidence=[EvidenceRef(run_id="run_1", n=3)],
                    rationale="correct it",
                )
            ],
            rule_diffs=[],
            summary="Corrected.",
        )
        revised_id = self.manager.commit("v1", revise, "run_1")
        revised = self.store.versions[revised_id]
        self.assertNotEqual(revised.memory_refs[0].revision_id, "rev_old")
        self.assertEqual(self.store.versions["v1"].memory_refs[0].revision_id, "rev_old")

        self.evidence.runs["run_2"] = run("run_2", revised_id)
        self.evidence.moves.add(("run_2", 3))
        retire = ReflectionProposal(
            proposal_id="proposal_retire",
            run_id="run_2",
            parent_id=revised_id,
            memory_ops=[
                RetireMemoryOperation(
                    op="retire",
                    memory_id="mem_old",
                    expected_revision_id=revised.memory_refs[0].revision_id,
                    rationale="obsolete",
                    evidence=[],
                )
            ],
            rule_diffs=[],
            summary="Retired.",
        )
        retired_id = self.manager.commit(revised_id, retire, "run_2")

        self.assertEqual(self.store.versions[retired_id].memory_refs, [])
        self.assertEqual(len(self.store.versions[revised_id].memory_refs), 1)

    def test_rule_is_born_soft_and_only_promoter_can_make_it_hard(self) -> None:
        self.promoter.result = "hard"
        proposal = add_proposal(
            rule_diffs=[
                {
                    "op": "add",
                    "key": "fatal_action",
                    "text": "Avoid the fatal action.",
                    "when": {"action": "fatal"},
                    "verdict": "block",
                    "evidence": [{"run_id": "run_1", "n": 3}],
                }
            ]
        )

        child_id = self.manager.commit("v1", proposal, "run_1")

        child = self.store.versions[child_id]
        learned_rule = self.rules.rules[child.rule_ids[0]]
        self.assertEqual(self.promoter.calls[0][0].status, "soft")
        self.assertEqual(learned_rule.status, "hard")
        self.assertEqual(learned_rule.promoted_version, child_id)

    def test_invalid_evidence_rejects_whole_batch_before_staging(self) -> None:
        with self.assertRaisesRegex(CommitError, "unknown evidence move"):
            self.manager.commit("v1", add_proposal(evidence_n=99), "run_1")

        self.assertEqual(set(self.store.revisions), {"rev_old"})
        self.assertEqual(set(self.store.versions), {"v1"})
        self.assertIn("proposal_1:rejected", self.store.events)

    def test_cross_branch_evidence_and_stale_revision_are_rejected(self) -> None:
        self.evidence.runs["sibling_run"] = run("sibling_run", "sibling")
        self.evidence.moves.add(("sibling_run", 1))
        proposal = add_proposal()
        proposal = proposal.model_copy(
            update={
                "memory_ops": [
                    proposal.memory_ops[0].model_copy(
                        update={"evidence": [EvidenceRef(run_id="sibling_run", n=1)]}
                    )
                ]
            }
        )
        with self.assertRaisesRegex(CommitError, "outside parent ancestry"):
            self.manager.commit("v1", proposal, "run_1")

        stale = ReflectionProposal(
            proposal_id="proposal_stale",
            run_id="run_1",
            parent_id="v1",
            memory_ops=[
                RetireMemoryOperation(
                    op="retire",
                    memory_id="mem_old",
                    expected_revision_id="wrong",
                    rationale="stale",
                    evidence=[],
                )
            ],
            rule_diffs=[],
            summary="Stale.",
        )
        with self.assertRaisesRegex(CommitError, "stale expected revision"):
            self.manager.commit("v1", stale, "run_1")

    def test_publication_failure_leaves_parent_visible_and_retry_recovers(self) -> None:
        self.store.fail_publish = True
        proposal = add_proposal()
        with self.assertRaisesRegex(RuntimeError, "publish failed"):
            self.manager.commit("v1", proposal, "run_1")

        self.assertEqual(set(self.store.versions), {"v1"})
        self.assertEqual(set(self.rules.rules), set())
        self.assertEqual(len(self.store.versions["v1"].memory_refs), 1)
        self.assertIn("proposal_1:failed", self.store.events)

        self.store.fail_publish = False
        child_id = self.manager.commit("v1", proposal, "run_1")
        self.assertIn(child_id, self.store.versions)

    def test_retry_rejects_reused_proposal_id_with_different_content(self) -> None:
        proposal = add_proposal()
        self.manager.commit("v1", proposal, "run_1")
        changed = proposal.model_copy(update={"summary": "Different content."})

        with self.assertRaisesRegex(CommitError, "different content"):
            self.manager.commit("v1", changed, "run_1")


if __name__ == "__main__":
    unittest.main()
