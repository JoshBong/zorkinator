import os
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from bson import ObjectId
from pymongo.errors import OperationFailure

from zorkinator import db
from zorkinator.models import MoveRecord, RuleDoc, RunRecord, WorldFactDoc


class MongoMappingTest(unittest.TestCase):
    """Pure mapping-helper tests; no Atlas connection needed."""

    def test_to_mongo_renames_id(self) -> None:
        rule = RuleDoc(
            id="r12",
            text="never drop the lamp before entering a dark room",
            when={"action": "drop (lamp|lantern)"},
            verdict="block",
            status="soft",
            born_version="v1",
        )
        doc = db._to_mongo(rule)
        self.assertEqual(doc["_id"], "r12")
        self.assertNotIn("id", doc)

    def test_from_mongo_round_trips(self) -> None:
        rule = RuleDoc(
            id="r12",
            text="never drop the lamp before entering a dark room",
            when={"action": "drop (lamp|lantern)"},
            verdict="block",
            status="soft",
            born_version="v1",
        )
        raw = db._to_mongo(rule)
        restored = db._from_mongo(RuleDoc, raw)
        self.assertEqual(restored, rule)

    def test_from_mongo_drops_objectid_for_models_without_an_id_field(self) -> None:
        raw = {
            "_id": ObjectId(),
            "run_id": "run1",
            "subject": "lamp",
            "attr": "lit",
            "value": True,
            "move": 5,
        }
        fact = db._from_mongo(WorldFactDoc, raw)
        self.assertEqual(fact.subject, "lamp")

    def test_without_id_strips_natural_key_id(self) -> None:
        raw = {"_id": "run1", "run_id": "run1", "score": 0}
        self.assertEqual(db._without_id(raw), {"run_id": "run1", "score": 0})


class MongoMockTest(unittest.TestCase):
    def setUp(self) -> None:
        db.close()

    def tearDown(self) -> None:
        db.close()

    def test_client_close_indexes_sink_and_simple_queries(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(RuntimeError, "MONGODB_URI"),
        ):
            db.get_client()
        client = MagicMock()
        database = MagicMock()
        client.__getitem__.return_value = database
        with (
            patch.dict(os.environ, {"MONGODB_URI": "mongodb://example"}),
            patch.object(db, "MongoClient", return_value=client),
        ):
            self.assertIs(db.get_db(), database)
            db.ensure_indexes()
            db.close()
        client.close.assert_called_once()

        with patch.object(db, "get_db", return_value=database):
            sink = db.MongoSink()
            sink.move(_move_record("run", 1))
            sink.run(_run_record("run"))
            database.runs.find_one.return_value = None
            self.assertIsNone(db.get_run("none"))
            database.runs.find.return_value.sort.return_value = []
            database.moves.find.return_value.sort.return_value = []
            self.assertEqual(db.get_runs("paper"), [])
            self.assertEqual(db.get_moves("run"), [])

    def test_fact_rule_and_store_error_paths(self) -> None:
        database = MagicMock()
        fact = WorldFactDoc(run_id="run", subject="lamp", attr="lit", value=True, move=2)
        with patch.object(db, "get_db", return_value=database):
            database.world_facts.find_one.return_value = {"move": 3}
            db.upsert_world_fact(fact)
            database.world_facts.update_one.assert_not_called()
            database.world_facts.find_one.return_value = None
            db.upsert_world_fact(fact)
            database.world_facts.find.return_value = []
            self.assertEqual(db.get_world_facts("run", "lamp"), [])
            rule = RuleDoc(
                id="r", text="t", when={}, verdict="warn", status="soft", born_version="v"
            )
            self.assertEqual(db.insert_rule(rule), "r")
            database.rules.find.return_value = []
            self.assertEqual(db.get_rules("soft"), [])
            db.record_rule_fired("r")
            db.promote_rule("r", "v2")

        with self.assertRaises(ValueError):
            db.MongoOuterLoopStore(database, max_recall_limit=0)
        store = db.MongoOuterLoopStore(database)
        with self.assertRaises(RuntimeError):
            store.publish_bundle(MagicMock(), [], [], [])
        with (
            patch.dict(os.environ, {"MONGODB_URI": ""}),
            self.assertRaisesRegex(RuntimeError, "MONGODB_URI"),
        ):
            db.MongoOuterLoopStore.from_env()

    def test_vector_index_swallows_only_duplicate_failures(self) -> None:
        database = MagicMock()
        store = db.MongoOuterLoopStore(database)
        database.memories.create_search_index.side_effect = OperationFailure("already exists")
        store._ensure_vector_index()
        database.memories.create_search_index.side_effect = OperationFailure("bad")
        with self.assertRaises(OperationFailure):
            store._ensure_vector_index()


def _run_record(run_id: str) -> RunRecord:
    now = datetime.now(UTC)
    return RunRecord(
        run_id=run_id,
        version_id=None,
        mode="harness",
        prompt="basic",
        model="test-model",
        seed=1,
        move_cap=10,
        score=0,
        moves=0,
        died=False,
        death_move=None,
        end_reason="cap",
        tokens_in=0,
        tokens_out=0,
        tokens_cache_write=0,
        tokens_cache_read=0,
        cost_usd=0.0,
        started_at=now,
        ended_at=now,
    )


def _move_record(run_id: str, n: int) -> MoveRecord:
    return MoveRecord(
        run_id=run_id,
        n=n,
        room=None,
        command="look",
        proposals=["look"],
        rejections=[],
        text="You are here.",
        score=0,
        score_delta=0,
        died=False,
        latency_ms=100,
        ts=datetime.now(UTC),
    )


@unittest.skipUnless(os.environ.get("MONGODB_URI"), "MONGODB_URI is not set")
class AtlasIntegrationTest(unittest.TestCase):
    """Round-trip tests against a real Atlas sandbox; opt in via MONGODB_URI."""

    def setUp(self) -> None:
        db.ensure_indexes()
        self.run_id = f"test-run-{uuid.uuid4().hex[:8]}"
        self.sink = db.MongoSink()
        self.sink.run(_run_record(self.run_id))
        self.addCleanup(lambda: db.get_db().runs.delete_one({"_id": self.run_id}))
        self.addCleanup(lambda: db.get_db().moves.delete_many({"run_id": self.run_id}))

    def test_sink_run_and_get_run(self) -> None:
        run = db.get_run(self.run_id)
        assert run is not None
        self.assertEqual(run.mode, "harness")
        self.assertFalse(run.died)

    def test_sink_run_upserts_on_replay(self) -> None:
        finished = _run_record(self.run_id).model_copy(update={"score": 42, "died": True})
        self.sink.run(finished)
        run = db.get_run(self.run_id)
        assert run is not None
        self.assertEqual(run.score, 42)
        self.assertTrue(run.died)

    def test_sink_move_and_get_moves(self) -> None:
        self.sink.move(_move_record(self.run_id, 1))
        self.sink.move(_move_record(self.run_id, 2))
        moves = db.get_moves(self.run_id)
        self.assertEqual([m.n for m in moves], [1, 2])

    def test_world_fact_newest_move_wins(self) -> None:
        db.upsert_world_fact(
            WorldFactDoc(run_id=self.run_id, subject="lamp", attr="lit", value=True, move=5)
        )
        db.upsert_world_fact(
            WorldFactDoc(run_id=self.run_id, subject="lamp", attr="lit", value=False, move=2)
        )
        self.addCleanup(lambda: db.get_db().world_facts.delete_many({"run_id": self.run_id}))
        facts = db.get_world_facts(self.run_id, subject="lamp")
        self.assertEqual(len(facts), 1)
        self.assertTrue(facts[0].value)
        self.assertEqual(facts[0].move, 5)


if __name__ == "__main__":
    unittest.main()
