import os
import unittest
import uuid
from datetime import UTC, datetime

from zorkinator import db
from zorkinator.models import MoveRecord, RuleDoc, RunRecord
from zorkinator.view import mapping


def _run_record(run_id: str, **overrides: object) -> RunRecord:
    now = datetime.now(UTC)
    fields: dict[str, object] = dict(
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
        chain=None,
        game_index=None,
    )
    fields.update(overrides)
    return RunRecord.model_validate(fields)


class PureMappingTest(unittest.TestCase):
    """Mapping helpers that don't need Atlas."""

    def test_group_key_uses_chain_when_present(self) -> None:
        self.assertEqual(mapping._group_key(_run_record("run1", chain="chainA")), "chainA")
        self.assertEqual(mapping._group_key(_run_record("run1")), "run1")

    def test_life_of_defaults_to_one(self) -> None:
        self.assertEqual(mapping._life_of(_run_record("run1")), 1)
        self.assertEqual(mapping._life_of(_run_record("run1", game_index=3)), 3)

    def test_condition_of(self) -> None:
        self.assertEqual(mapping._condition_of("harness"), "harness")
        self.assertEqual(mapping._condition_of("paper"), "baseline")

    def test_config_for_run_defaults_by_mode_without_a_version(self) -> None:
        harness_config = mapping._config_for_run(_run_record("r", mode="harness"))
        self.assertTrue(harness_config.reflection)
        paper_config = mapping._config_for_run(_run_record("r", mode="paper"))
        self.assertFalse(paper_config.rules)

    def test_move_out_maps_last_rejection_to_guardrail(self) -> None:
        move = MoveRecord(
            run_id="r",
            n=2,
            room="Cellar",
            command="north",
            proposals=["drop lamp", "north"],
            rejections=[{"cmd": "drop lamp", "rule_id": "r1", "reason": "grue risk"}],
            text="Cold and dark.",
            score=5,
            score_delta=5,
            died=False,
            latency_ms=10,
            ts=datetime.now(UTC),
        )
        out = mapping.move_out(move)
        assert out.guardrail is not None
        self.assertEqual(out.guardrail.rule_id, "r1")
        self.assertEqual(out.guardrail.original_command, "drop lamp")
        self.assertEqual(out.guardrail.replacement_command, "north")

    def test_move_out_no_guardrail_without_rejections(self) -> None:
        move = MoveRecord(
            run_id="r",
            n=1,
            room="West of House",
            command="look",
            proposals=["look"],
            rejections=[],
            text="An open field.",
            score=0,
            score_delta=0,
            died=False,
            latency_ms=10,
            ts=datetime.now(UTC),
        )
        self.assertIsNone(mapping.move_out(move).guardrail)


class RuleMappingTest(unittest.TestCase):
    def _rule(self, **overrides: object) -> RuleDoc:
        fields: dict[str, object] = dict(
            id="r1",
            text="the rug hides a trapdoor",
            when={"room": "Living Room"},
            verdict="block",
            status="soft",
            evidence=["run7:212"],
            born_version="v1",
        )
        fields.update(overrides)
        return RuleDoc.model_validate(fields)

    def test_warn_verdict_maps_to_memory_type(self) -> None:
        self.assertEqual(mapping._rule_type_of(self._rule(verdict="warn")), "memory")

    def test_hard_block_maps_to_guardrail_type(self) -> None:
        rule = self._rule(verdict="block", status="hard")
        self.assertEqual(mapping._rule_type_of(rule), "guardrail")

    def test_soft_block_maps_to_rule_type(self) -> None:
        self.assertEqual(mapping._rule_type_of(self._rule(verdict="block", status="soft")), "rule")

    def test_rule_out_splits_evidence_into_run_id_and_move(self) -> None:
        out = mapping.rule_out(self._rule(status="hard", evidence=["run7:212"]))
        self.assertEqual(out.learned_from.run_id, "run7")
        self.assertEqual(out.learned_from.move, 212)
        self.assertEqual(out.status, "active")

    def test_rule_out_handles_missing_evidence(self) -> None:
        out = mapping.rule_out(self._rule(evidence=[]))
        self.assertEqual(out.learned_from.run_id, "")
        self.assertEqual(out.learned_from.move, 0)


@unittest.skipUnless(os.environ.get("MONGODB_URI"), "MONGODB_URI is not set")
class AtlasMappingIntegrationTest(unittest.TestCase):
    """Round-trip the FastAPI mapping layer against a real seeded run."""

    def setUp(self) -> None:
        self.chain = f"test-chain-{uuid.uuid4().hex[:8]}"
        self.run_id = f"{self.chain}-g1"
        sink = db.MongoSink()
        sink.run(_run_record(self.run_id, chain=self.chain, game_index=1, score=15, moves=2))
        sink.move(
            MoveRecord(
                run_id=self.run_id,
                n=1,
                room="West of House",
                command="look",
                proposals=["look"],
                rejections=[],
                text="An open field.",
                score=0,
                score_delta=0,
                died=False,
                latency_ms=10,
                ts=datetime.now(UTC),
            )
        )
        self.addCleanup(lambda: db.get_db().runs.delete_one({"_id": self.run_id}))
        self.addCleanup(lambda: db.get_db().moves.delete_many({"run_id": self.run_id}))

    def test_list_runs_groups_by_chain(self) -> None:
        runs = {r.run_id: r for r in mapping.list_runs()}
        self.assertIn(self.chain, runs)
        self.assertEqual(runs[self.chain].lives_count, 1)
        self.assertEqual(runs[self.chain].condition, "harness")

    def test_list_lives_and_moves(self) -> None:
        lives = mapping.list_lives(self.chain)
        self.assertEqual([life_.life for life_ in lives], [1])
        moves = mapping.list_moves(self.chain, 1)
        self.assertEqual([m.move for m in moves], [1])


if __name__ == "__main__":
    unittest.main()
