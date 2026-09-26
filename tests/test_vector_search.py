"""Live proof that recall_scored() does real semantic ranking via Atlas Vector Search
(Automated Embedding on memories.content.text — see docs/OUTER_LOOP_MEMORY.md).

Opt in via MONGODB_URI, like tests/test_db.py. The index builds asynchronously after
ensure_indexes(), so this polls for it to become queryable before querying.
"""

import os
import time
import unittest
import uuid
from datetime import UTC, datetime

from zorkinator import db
from zorkinator.models import EvidenceRef, HarnessVersionRecord, MemoryRef, MemoryRevision

INDEX_READY_TIMEOUT_S = 90


def _revision(
    revision_id: str, memory_id: str, text: str, op_key: str, *, experiment_id: str
) -> MemoryRevision:
    now = datetime.now(UTC)
    return MemoryRevision(
        revision_id=revision_id,
        experiment_id=experiment_id,
        memory_id=memory_id,
        supersedes_revision_id=None,
        kind="procedure",
        subjects=["test"],
        content={"text": text},
        status="supported",
        evidence=[EvidenceRef(run_id="test-run1", n=1)],
        rationale="test fixture",
        source_run_id="test-run1",
        proposal_id="test-proposal1",
        operation_key=op_key,
        born_version_id="v-fixture",
        created_at=now,
    )


@unittest.skipUnless(os.environ.get("MONGODB_URI"), "MONGODB_URI is not set")
class VectorSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = db.MongoOuterLoopStore(db.get_db())
        self.store.ensure_indexes()
        self.experiment_id = f"vectest-{uuid.uuid4().hex[:8]}"
        self.version_id = f"{self.experiment_id}-v1"
        self.addCleanup(
            lambda: db.get_db().memories.delete_many({"experiment_id": self.experiment_id})
        )
        self.addCleanup(
            lambda: db.get_db().harness_versions.delete_many({"experiment_id": self.experiment_id})
        )

        self.revisions = [
            _revision(
                f"{self.experiment_id}-lamp",
                "mem-lamp",
                "Never enter a dark room without a lit lamp; a grue will kill you instantly.",
                "add_0",
                experiment_id=self.experiment_id,
            ),
            _revision(
                f"{self.experiment_id}-troll",
                "mem-troll",
                "The troll in the troll room blocks the passage and must be fought with the sword.",
                "add_1",
                experiment_id=self.experiment_id,
            ),
            _revision(
                f"{self.experiment_id}-trophy",
                "mem-trophy",
                "Deposit collected treasures in the trophy case to score points.",
                "add_2",
                experiment_id=self.experiment_id,
            ),
        ]
        for revision in self.revisions:
            self.store.put_memory_revision(revision)

        version = HarnessVersionRecord(
            version_id=self.version_id,
            experiment_id=self.experiment_id,
            parent_id=None,
            source_run_id=None,
            proposal_id=None,
            memory_refs=[
                MemoryRef(memory_id=r.memory_id, revision_id=r.revision_id) for r in self.revisions
            ],
            rule_ids=[],
            context_policy={},
            scores=[],
            created_at=datetime.now(UTC),
        )
        self.store.publish_version(version)
        self._wait_for_index()

    def _wait_for_index(self) -> None:
        """Poll a real query rather than the index's own `queryable` flag: that flag can
        be true (the index structure exists) before Atlas has finished embedding these
        specific just-inserted documents, which happens asynchronously per document."""
        deadline = time.monotonic() + INDEX_READY_TIMEOUT_S
        expected = {r.memory_id for r in self.revisions}
        while time.monotonic() < deadline:
            found = {r.memory_id for r, _score in self.store.recall_scored(self.version_id, "room")}
            if found == expected:
                return
            time.sleep(3)
        self.fail(
            f"vector index did not embed all fixture documents within {INDEX_READY_TIMEOUT_S}s"
        )

    def test_semantic_query_ranks_by_meaning_not_keywords(self) -> None:
        # Shares zero words with the lamp memory's text; a keyword search would fail this.
        results = self.store.recall_scored(
            self.version_id, "how do I survive when it's pitch black underground", limit=3
        )
        self.assertTrue(results)
        top_memory_id, top_score = results[0][0].memory_id, results[0][1]
        self.assertEqual(top_memory_id, "mem-lamp")
        self.assertGreater(top_score, results[-1][1])

    def test_recall_unchanged_for_non_vector_callers(self) -> None:
        # recall() (no query) still does the plain manifest scan, untouched by this feature.
        results = self.store.recall(self.version_id)
        self.assertEqual({r.memory_id for r in results}, {"mem-lamp", "mem-troll", "mem-trophy"})


if __name__ == "__main__":
    unittest.main()
