from __future__ import annotations

import unittest
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import MagicMock

from bson import BSON
from pydantic import ValidationError
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from zorkinator.db import MongoOuterLoopStore
from zorkinator.models import (
    EvidenceRef,
    HarnessVersionRecord,
    MemoryRef,
    MemoryRevision,
    ReflectionProposal,
    RetireMemoryOperation,
)

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def revision(
    revision_id: str = "memrev_1_1", *, text: str = "A path leads north."
) -> MemoryRevision:
    return MemoryRevision(
        revision_id=revision_id,
        experiment_id="exp_1",
        memory_id="mem_1",
        supersedes_revision_id=None,
        kind="map_edge",
        subjects=["room_a", "room_b"],
        content={"text": text},
        status="supported",
        evidence=[EvidenceRef(run_id="run_1", n=3)],
        rationale="Preserve an observed path.",
        source_run_id="run_1",
        proposal_id="proposal_1",
        operation_key="add_0",
        born_version_id="v2",
        created_at=NOW,
    )


def version(revision_id: str = "memrev_1_1") -> HarnessVersionRecord:
    return HarnessVersionRecord(
        version_id="v2",
        experiment_id="exp_1",
        parent_id="v1",
        source_run_id="run_1",
        proposal_id="proposal_1",
        memory_refs=[MemoryRef(memory_id="mem_1", revision_id=revision_id)],
        rule_ids=[],
        context_policy={},
        scores=[],
        created_at=NOW,
    )


def mongo_document(model: Any, id_field: str) -> dict[str, Any]:
    data: dict[str, Any] = model.model_dump(mode="python")
    data["_id"] = data.pop(id_field)
    return data


class MongoOuterLoopStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = MagicMock()
        self.store = MongoOuterLoopStore(
            cast(Database[dict[str, Any]], self.database), max_recall_limit=10
        )

    def test_read_resolves_only_revision_in_requested_manifest(self) -> None:
        active_revision = revision()
        self.database.harness_versions.find_one.return_value = mongo_document(
            version(), "version_id"
        )
        self.database.memories.find_one.return_value = mongo_document(
            active_revision, "revision_id"
        )

        result = self.store.read("v2", "mem_1")

        self.assertEqual(result, active_revision)
        self.database.memories.find_one.assert_called_once_with(
            {"_id": "memrev_1_1", "experiment_id": "exp_1"}
        )

    def test_recall_filters_the_bounded_active_manifest(self) -> None:
        active_revision = revision(text="The NORTH exit reaches room_b.")
        self.database.harness_versions.find_one.return_value = mongo_document(
            version(), "version_id"
        )
        self.database.memories.find.return_value = [mongo_document(active_revision, "revision_id")]

        result = self.store.recall(
            "v2", query="north", subjects=["room_a"], kinds=["map_edge"], limit=1
        )

        self.assertEqual(result, [active_revision])
        self.database.memories.find.assert_called_once_with(
            {"_id": {"$in": ["memrev_1_1"]}, "experiment_id": "exp_1"}
        )

    def test_recall_returns_empty_without_querying_for_empty_manifest(self) -> None:
        empty_version = version().model_copy(update={"memory_refs": []})
        self.database.harness_versions.find_one.return_value = mongo_document(
            empty_version, "version_id"
        )

        self.assertEqual(self.store.recall("v2"), [])
        self.database.memories.find.assert_not_called()

    def test_recall_rejects_unknown_version(self) -> None:
        self.database.harness_versions.find_one.return_value = None

        with self.assertRaisesRegex(LookupError, "unknown harness version"):
            self.store.recall("missing")

        self.database.memories.find.assert_not_called()

    def test_recall_preserves_manifest_order_and_applies_limit(self) -> None:
        first = revision()
        second = revision("memrev_2_1", text="A useful procedure.").model_copy(
            update={"memory_id": "mem_2", "kind": "procedure", "subjects": ["puzzle_a"]}
        )
        ordered_version = version().model_copy(
            update={
                "memory_refs": [
                    MemoryRef(memory_id="mem_1", revision_id="memrev_1_1"),
                    MemoryRef(memory_id="mem_2", revision_id="memrev_2_1"),
                ]
            }
        )
        self.database.harness_versions.find_one.return_value = mongo_document(
            ordered_version, "version_id"
        )
        # Mongo does not promise the same order as the manifest.
        self.database.memories.find.return_value = [
            mongo_document(second, "revision_id"),
            mongo_document(first, "revision_id"),
        ]

        self.assertEqual(self.store.recall("v2", limit=1), [second])

    def test_immutable_retry_accepts_bson_canonical_datetime(self) -> None:
        document = mongo_document(revision(), "revision_id")
        collection = self.database.__getitem__.return_value
        collection.insert_one.side_effect = DuplicateKeyError("duplicate")
        collection.find_one.return_value = BSON(BSON.encode(document)).decode()

        self.store.put_memory_revision(revision())

        collection.find_one.assert_called_once_with({"_id": "memrev_1_1"}, session=None)

    def test_recall_excludes_nonmatching_filters_and_inactive_revision(self) -> None:
        active = revision(text="The north exit reaches room_b.")
        inactive = revision("memrev_1_0", text="Stale south exit.")
        self.database.harness_versions.find_one.return_value = mongo_document(
            version(), "version_id"
        )
        # Include a historical revision to ensure the manifest remains authoritative,
        # even if a backing-store test double returns more than the requested ids.
        self.database.memories.find.return_value = [
            mongo_document(inactive, "revision_id"),
            mongo_document(active, "revision_id"),
        ]

        self.assertEqual(
            self.store.recall("v2", query="south", subjects=["room_a"], kinds=["map_edge"]),
            [],
        )
        self.assertEqual(
            self.store.recall("v2", query="north", subjects=["other_room"]),
            [],
        )
        self.assertEqual(self.store.recall("v2", query="north", kinds=["procedure"]), [])

    def test_recall_enforces_serialized_byte_budget(self) -> None:
        constrained_store = MongoOuterLoopStore(
            cast(Database[dict[str, Any]], self.database), max_recall_bytes=1
        )
        self.database.harness_versions.find_one.return_value = mongo_document(
            version(), "version_id"
        )
        self.database.memories.find.return_value = [mongo_document(revision(), "revision_id")]

        self.assertEqual(constrained_store.recall("v2"), [])

    def test_recall_rejects_missing_or_mismatched_manifest_revision(self) -> None:
        self.database.harness_versions.find_one.return_value = mongo_document(
            version(), "version_id"
        )
        self.database.memories.find.return_value = []

        with self.assertRaisesRegex(ValueError, "references missing revision"):
            self.store.recall("v2")

        wrong_memory = revision().model_copy(update={"memory_id": "mem_other"})
        self.database.memories.find.return_value = [mongo_document(wrong_memory, "revision_id")]
        with self.assertRaisesRegex(ValueError, "does not match its manifest memory_id"):
            self.store.recall("v2")

    def test_recall_rejects_limits_outside_configured_bounds(self) -> None:
        for invalid_limit in (0, 11):
            with (
                self.subTest(limit=invalid_limit),
                self.assertRaisesRegex(ValueError, "between 1 and 10"),
            ):
                self.store.recall("v2", limit=invalid_limit)

    def test_proposal_rejects_duplicate_operation_for_one_memory(self) -> None:
        operation = RetireMemoryOperation(
            op="retire",
            memory_id="mem_1",
            expected_revision_id="memrev_1_1",
            rationale="Duplicate.",
            evidence=[],
        )
        with self.assertRaisesRegex(ValidationError, "duplicate memory operation"):
            ReflectionProposal(
                proposal_id="proposal_2",
                run_id="run_2",
                parent_id="v2",
                memory_ops=[operation, operation],
                rule_diffs=[],
                summary="No duplicate targets are allowed.",
            )

    def test_manifest_rejects_duplicate_logical_memory(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unique memory_id"):
            HarnessVersionRecord(
                version_id="v2",
                experiment_id="exp_1",
                parent_id="v1",
                source_run_id="run_1",
                proposal_id="proposal_1",
                memory_refs=[
                    MemoryRef(memory_id="mem_1", revision_id="memrev_1_1"),
                    MemoryRef(memory_id="mem_1", revision_id="memrev_1_2"),
                ],
                rule_ids=[],
                context_policy={},
                scores=[],
                created_at=NOW,
            )

    def test_record_proposal_persists_rule_diffs_and_summary(self) -> None:
        proposal = ReflectionProposal(
            proposal_id="proposal_rule",
            run_id="run_1",
            parent_id="v1",
            memory_ops=[],
            rule_diffs=[
                {
                    "op": "add",
                    "key": "fatal",
                    "text": "Avoid the fatal action.",
                    "when": {"action": "fatal"},
                    "verdict": "block",
                    "evidence": [{"run_id": "run_1", "n": 3}],
                }
            ],
            summary="Preserve the proposed rule.",
        )

        self.store.record_proposal(
            proposal,
            experiment_id="exp_1",
            model="test-model",
            created_at=NOW,
        )

        document = self.database.__getitem__.return_value.insert_one.call_args.args[0]
        self.assertEqual(document["proposal"]["rule_diffs"], proposal.rule_diffs)
        self.assertEqual(document["proposal"]["summary"], proposal.summary)


if __name__ == "__main__":
    unittest.main()
