import unittest
from datetime import UTC, datetime
from typing import Any

from zorkinator.models import MoveRecord, RuleDoc, RunRecord
from zorkinator.verifier import Promoter, State, check, replay_states, validate_when

NOW = datetime(2026, 9, 26, tzinfo=UTC)


def move(
    run_id: str, n: int, command: str, text: str, delta: int = 0, died: bool = False
) -> MoveRecord:
    return MoveRecord(
        run_id=run_id,
        n=n,
        room=None,
        command=command,
        proposals=[command],
        rejections=[],
        text=text,
        score=0,
        score_delta=delta,
        died=died,
        latency_ms=0,
        ts=NOW,
    )


def run(run_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        version_id="v1",
        mode="harness",
        prompt="basic",
        model="claude-haiku-4-5",
        seed=0,
        move_cap=500,
        score=0,
        moves=3,
        died=True,
        death_move=3,
        end_reason="death",
        tokens_in=0,
        tokens_out=0,
        tokens_cache_write=0,
        tokens_cache_read=0,
        cost_usd=0.0,
        started_at=NOW,
        ended_at=NOW,
    )


def rule(when: dict[str, Any], evidence: list[str], status: str = "soft") -> RuleDoc:
    return RuleDoc(
        id="r1",
        text="Don't fight the troll without the sword",
        when=when,
        verdict="block",
        status="hard" if status == "hard" else "soft",
        evidence=evidence,
        born_version="v1",
    )


TROLL = {"command": "(kill|attack) troll.*", "not_carrying": ["sword"]}

DEATH_RUN = [
    move("a", 1, "north", "Troll Room\nA nasty-looking troll blocks the way."),
    move("a", 2, "take lamp", "You can't see that here."),
    move("a", 3, "attack troll with axe", "The troll's axe removes your head.", died=True),
]
WIN_RUN = [
    move("b", 1, "take sword", "Taken."),
    move("b", 2, "north", "Troll Room\nA nasty-looking troll blocks the way."),
    move("b", 3, "kill troll with sword", "The troll is dead.", delta=10),
]


class CheckTest(unittest.TestCase):
    def test_hard_blocks_soft_warns(self) -> None:
        state = State(room="Troll Room")
        hard = check("attack troll with axe", state, [rule(TROLL, [], "hard")])
        soft = check("attack troll with axe", state, [rule(TROLL, [])])
        armed = check(
            "kill troll", State(inventory=frozenset({"sword"})), [rule(TROLL, [], "hard")]
        )
        self.assertFalse(hard.ok)
        self.assertTrue(soft.ok)
        self.assertEqual(len(soft.warnings), 1)
        self.assertTrue(armed.ok)

    def test_validate_when(self) -> None:
        self.assertEqual(validate_when(TROLL), [])
        self.assertTrue(validate_when({"location_id": 12}))
        self.assertTrue(validate_when({}))


class ReplayTest(unittest.TestCase):
    def test_state_tracks_room_dark_and_inventory(self) -> None:
        states = replay_states(
            [
                move("c", 1, "take sword", "Taken."),
                move(
                    "c",
                    2,
                    "down",
                    "Cellar\nIt is pitch black. You are likely to be eaten by a grue.",
                ),
                move("c", 3, "drop sword", "Dropped."),
                move("c", 4, "look", "It is pitch black."),
            ]
        )
        self.assertIn("sword", states[1].inventory)
        self.assertEqual((states[2].room, states[2].room_is_dark), ("Cellar", True))
        self.assertNotIn("sword", states[3].inventory)


class PromoteTest(unittest.TestCase):
    def promoter(self, logs: dict[str, list[MoveRecord]]) -> Promoter:
        return Promoter(lambda run_id: logs[run_id])

    def test_death_backed_rule_is_promoted(self) -> None:
        p = self.promoter({"a": DEATH_RUN, "b": WIN_RUN})
        self.assertEqual(p.promote(rule(TROLL, ["a:3"]), [run("a"), run("b")]), "hard")

    def test_rule_that_would_block_scoring_stays_soft(self) -> None:
        p = self.promoter({"a": DEATH_RUN, "b": WIN_RUN})
        too_broad = {"command": "(kill|attack) troll.*"}
        self.assertEqual(p.promote(rule(too_broad, ["a:3"]), [run("a"), run("b")]), "soft")

    def test_no_death_evidence_stays_soft(self) -> None:
        p = self.promoter({"a": DEATH_RUN})
        self.assertEqual(p.promote(rule(TROLL, ["a:2"]), [run("a")]), "soft")


if __name__ == "__main__":
    unittest.main()


class HarnessProtocolTest(unittest.TestCase):
    def test_command_pattern_alias(self) -> None:
        alias = rule({"command_pattern": "(kill|attack) troll.*"}, [], "hard")
        self.assertFalse(check("attack troll", State(), [alias]).ok)

    def test_expect_and_surprise_lines(self) -> None:
        from zorkinator.player import _tag, parse_surprise
        from zorkinator.runner import extract_command

        reply = "Surprise: no\nopen door\nGoal: get inside\nExpect: the door opens"
        self.assertEqual(extract_command(reply), "open door")
        self.assertEqual(_tag(reply, "expect:"), "the door opens")
        self.assertFalse(parse_surprise(reply))
