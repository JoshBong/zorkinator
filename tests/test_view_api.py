from __future__ import annotations

import importlib
import unittest
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from zorkinator.models import MoveRecord, RunRecord, WorldFactDoc
from zorkinator.view import mapping
from zorkinator.view.schemas import (
    HarnessConfig,
    LifeOut,
    MoveOut,
    ReflectionOut,
    RunMapOut,
    RunOut,
)

app: Any = importlib.import_module("zorkinator.view.app")
mapping_db: Any = mapping

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def move(*, died: bool = False, rejected: bool = False) -> MoveRecord:
    return MoveRecord(
        run_id="run",
        n=2,
        room="Room",
        command="north",
        proposals=["north"],
        rejections=[{"cmd": "south", "rule_id": "rule"}] if rejected else [],
        text="text",
        score=1,
        score_delta=1,
        died=died,
        latency_ms=1,
        ts=NOW,
    )


def run(*, game_index: int | None = 2) -> RunRecord:
    return RunRecord(
        run_id="run",
        version_id=None,
        mode="harness",
        prompt="p",
        model="m",
        seed=1,
        move_cap=2,
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
        started_at=NOW,
        ended_at=NOW,
        game_index=game_index,
    )


class ViewApiTests(unittest.TestCase):
    def test_mapping_endpoint_delegates(self) -> None:
        expected_run = RunOut(
            run_id="run",
            condition="harness",
            model="m",
            config=HarnessConfig(reflection=True, rules=True, memory=True, guardrails=True),
            status="done",
            lives_count=1,
            created_at=NOW.isoformat(),
        )
        expected_life = LifeOut(life=1, score=0, moves=0, death_cause="cap", rules_learned=[])
        expected_move = MoveOut(
            move=1,
            observation="text",
            command="look",
            room="Room",
            score=0,
            retrieved_rules=[],
            guardrail=None,
            repeated_action=False,
        )
        expected_map = RunMapOut(rooms=[], edges=[])
        with (
            patch.object(app.mapping, "list_runs", return_value=[expected_run]),
            patch.object(app.mapping, "list_lives", return_value=[expected_life]),
            patch.object(app.mapping, "list_moves", return_value=[expected_move]),
            patch.object(app.mapping, "list_rules", return_value=[]),
            patch.object(app.mapping, "get_map", return_value=expected_map),
            patch.object(app.mapping, "get_eval_summary", return_value={}),
            patch.object(app.mapping, "list_game_evaluations", return_value=[]),
        ):
            self.assertEqual(app.list_runs(), [expected_run])
            self.assertEqual(app.list_lives("run"), [expected_life])
            self.assertEqual(app.list_moves("run", 1), [expected_move])
            self.assertEqual(app.list_rules(), [])
            self.assertEqual(app.get_map("run"), expected_map)
            self.assertEqual(app.get_eval_summary(), {})
            self.assertEqual(app.list_game_evaluations("chain", "query"), [])

    def test_reflection_endpoint_handles_found_and_missing(self) -> None:
        reflection = ReflectionOut(cause="c", effect="e", lesson_text="l", rule_id="r")
        with patch.object(app.mapping, "get_reflection", return_value=reflection):
            self.assertEqual(app.get_reflection("run", 1), reflection)
        with (
            patch.object(app.mapping, "get_reflection", return_value=None),
            self.assertRaises(HTTPException) as raised,
        ):
            app.get_reflection("run", 4)
        self.assertEqual(raised.exception.status_code, 404)

    def test_sse_watch_stream_and_poll(self) -> None:
        record = move(died=True, rejected=True)
        stream = MagicMock()
        stream.__enter__.return_value = [{"fullDocument": {**record.model_dump(), "_id": "x"}}]
        database = MagicMock()
        database.moves.watch.return_value = stream
        with (
            patch.object(app.db, "get_db", return_value=database),
            patch.object(
                app.mapping, "move_out", return_value=MagicMock(model_dump=lambda: {"move": 2})
            ),
        ):
            events = list(app._watch_moves("run", 3))
        self.assertEqual(len(events), 3)
        self.assertIn("event: death", events[1])
        self.assertIn("event: guardrail_block", events[2])
        self.assertEqual(app._sse("x", {"a": 1}), 'event: x\ndata: {"a": 1}\n\n')

        with (
            patch.object(app.db, "get_run", return_value=run()),
            patch.object(app, "_watch_moves", return_value=iter(())),
        ):
            response = app.stream_run("run")
        self.assertEqual(response.media_type, "text/event-stream")
        with (
            patch.object(app.db, "get_run", return_value=None),
            patch.object(app, "_watch_moves", return_value=iter(())),
        ):
            self.assertEqual(app.stream_run("missing").media_type, "text/event-stream")

        with (
            patch.object(app.db, "get_moves", return_value=[record]),
            patch.object(
                app.mapping, "move_out", return_value=MagicMock(model_dump=lambda: {"move": 2})
            ),
        ):
            self.assertEqual(app.poll_run("run"), [{"type": "move", "data": {"move": 2}}])


class ViewMappingMockTests(unittest.TestCase):
    def test_list_helpers_and_reflection_paths(self) -> None:
        chained = run(game_index=1).model_copy(
            update={"chain": "chain", "version_id": "v1", "score": 7, "moves": 3}
        )
        with (
            patch.object(mapping_db.db, "get_runs", return_value=[chained]),
            patch.object(mapping_db.db, "get_db", return_value=MagicMock()),
            patch.object(mapping_db.db, "MongoOuterLoopStore") as store_type,
        ):
            store_type.return_value.get_version.return_value = MagicMock(
                context_policy={"rules": False}
            )
            self.assertEqual(mapping.list_runs()[0].config.rules, False)
            self.assertEqual(mapping.list_lives("chain")[0].score, 7)
            with patch.object(mapping_db.db, "get_moves", return_value=[move()]):
                self.assertEqual(mapping.list_moves("chain", 1)[0].move, 2)
        with patch.object(mapping_db.db, "get_runs", return_value=[]):
            self.assertEqual(mapping.list_moves("none", 1), [])
            self.assertIsNone(mapping.get_reflection("none", 1))

        database = MagicMock()
        database.memory_events.find_one.side_effect = [
            None,
            {
                "operations": [{"memory_id": "memory"}],
                "proposal_id": "proposal",
                "phase": "committed",
                "reason": None,
            },
        ]
        with (
            patch.object(mapping_db.db, "get_runs", return_value=[chained]),
            patch.object(mapping_db.db, "get_db", return_value=database),
        ):
            self.assertIsNone(mapping.get_reflection("chain", 1))
            reflection = mapping.get_reflection("chain", 1)
        assert reflection is not None
        self.assertEqual(reflection.rule_id, "memory")
        self.assertIn("cap", reflection.effect)

    def test_rule_map_summary_and_evaluations(self) -> None:
        chained = run(game_index=1).model_copy(update={"chain": "chain", "score": 8, "moves": 4})
        facts = [
            WorldFactDoc(run_id="run", subject="Room", attr="dark", value=True, move=1),
            WorldFactDoc(run_id="run", subject="Room", attr="exit_north", value="North", move=2),
        ]
        with (
            patch.object(mapping_db.db, "get_runs", return_value=[chained]),
            patch.object(mapping_db.db, "get_world_facts", return_value=facts),
        ):
            mapped = mapping.get_map("chain")
        self.assertTrue(mapped.rooms[0].dark)
        self.assertEqual(mapped.edges[0].direction, "north")

        paper = chained.model_copy(update={"mode": "paper", "run_id": "paper", "score": 2})
        database = MagicMock()
        database.moves.count_documents.return_value = 3
        with (
            patch.object(mapping_db.db, "get_runs", side_effect=[[paper], [chained]]),
            patch.object(mapping_db.db, "get_db", return_value=database),
        ):
            summary = mapping.get_eval_summary()
        self.assertEqual(len(summary.conditions), 2)
        self.assertEqual(summary.conditions[0].guardrail_blocks, 3)

        expected = MagicMock()
        with (
            patch.object(mapping_db.db, "get_db", return_value=database),
            patch.object(mapping_db.db, "MongoOuterLoopStore") as store_type,
        ):
            store_type.return_value.get_game_evaluations.return_value = [expected]
            self.assertEqual(
                mapping.list_game_evaluations(chain="c", retrieval_query="q"), [expected]
            )
