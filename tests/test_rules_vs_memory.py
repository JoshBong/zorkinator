"""Rules gate actions; knowledge and suggestions are located memories. No soft re-prompts."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.test_carryover import mv
from tests.test_harness import ScriptedChat
from tests.test_reflector import FakeModel, FakeRepository, run
from tests.test_world_memory import NOW, memory
from zorkinator import carryover
from zorkinator.harness import GameSpec, play_game
from zorkinator.models import AddMemoryOperation, HarnessVersionRecord, MemoryRevision, RuleDoc
from zorkinator.reflector import Reflector
from zorkinator.runner import JsonlSink
from zorkinator.verifier import State, applies, check, validate_when
from zorkinator.versions import CommitError, validate_rule_diffs

STORY_FILE = Path(__file__).resolve().parent.parent / "games" / "zork1.z5"
# Moving in the dark: the danger is a state, but the rule still names the action.
GRUE = {"command": "(go )?(north|south|east|west|up|down|[nsewud])", "room_is_dark": True}


def rule(rule_id: str, when: dict[str, Any], text: str, status: str = "soft") -> RuleDoc:
    return RuleDoc.model_validate(
        {
            "id": rule_id,
            "text": text,
            "when": when,
            "verdict": "block",
            "status": status,
            "born_version": "v0",
        }
    )


class RuleShapeTests(unittest.TestCase):
    def test_a_rule_must_name_a_specific_action(self) -> None:
        self.assertIn("no command", validate_when({"room": "West of House"})[0])
        self.assertIn("no command", validate_when({"room_is_dark": True, "carrying": []})[0])
        self.assertTrue(validate_when({"command": ".*", "room": "West of House"}))
        self.assertTrue(validate_when({"command": "\\w+"}))
        self.assertEqual(validate_when(GRUE), [])

    def test_room_only_rule_never_fires_and_state_rule_fires_only_for_its_action(self) -> None:
        mailbox = rule("mailbox", {"room": "West of House"}, "Attempt to open the mailbox.")
        grue = rule("grue", GRUE, "Moving in the dark got me eaten.", "hard")
        dark = State(room="Cellar", room_is_dark=True)

        self.assertEqual(check("open mailbox", State(room="West of House"), [mailbox]).warnings, ())
        self.assertFalse(check("north", dark, [grue]).ok)
        self.assertTrue(check("turn on lamp", dark, [grue]).ok)
        self.assertTrue(applies(grue, dark))
        self.assertFalse(applies(grue, State(room="Cellar")))

    def test_commit_rejects_a_rule_without_an_action(self) -> None:
        diff: dict[str, Any] = {
            "op": "add",
            "key": "mailbox",
            "text": "Open the mailbox.",
            "when": {"room": "West of House"},
            "verdict": "warn",
            "evidence": [{"run_id": "r", "n": 1}],
        }
        with self.assertRaisesRegex(CommitError, "no command"):
            validate_rule_diffs([diff], [], "p1")


class ReflectorTriageTests(unittest.TestCase):
    def test_advice_proposed_as_a_rule_becomes_a_located_memory(self) -> None:
        moves = [mv("run_2", 1, "West of House", "open mailbox", "Opening reveals a leaflet.")]
        moves += [mv("run_2", 2, "West of House", "north", "It is pitch black.", died=True)]
        model = FakeModel(
            {
                "memory_ops": [],
                "rule_diffs": [
                    {
                        "op": "add",
                        "key": "mailbox",
                        "text": "Attempt to open the mailbox here.",
                        "when": {"room": "West of House"},
                        "verdict": "warn",
                        "evidence": [{"run_id": "run_2", "n": 1}],
                    },
                    {
                        "op": "add",
                        "key": "grue",
                        "text": "Moving in the dark got me eaten.",
                        "when": GRUE,
                        "verdict": "block",
                        "evidence": [{"run_id": "run_2", "n": 2}],
                    },
                ],
                "summary": "Two lessons.",
            }
        )
        reflector = Reflector(
            FakeRepository(run(moves=2), moves), model, id_factory=lambda: "proposal_1"
        )

        proposal = reflector.propose("run_2")

        self.assertEqual([d["key"] for d in proposal.rule_diffs], ["grue"])
        [advice] = proposal.memory_ops
        assert isinstance(advice, AddMemoryOperation)
        self.assertEqual((advice.kind, advice.locations), ("caution", ["West of House"]))
        self.assertEqual(advice.content, {"text": "Attempt to open the mailbox here."})
        self.assertIn("1 rules without an action kept as memory", proposal.summary)
        self.assertIn("Positive advice is never a rule", model.prompts[0])

    def test_rule_feedback_counts_warnings_blocks_and_deaths(self) -> None:
        warned = mv("r", 1, "Cellar", "north", "You have died.", died=True)
        warned = warned.model_copy(update={"warnings": ["grue"]})
        blocked = mv("r", 2, "Cellar", "turn on lamp", "The lamp is on.")
        blocked = blocked.model_copy(
            update={"rejections": [{"cmd": "north", "rule_id": "grue", "reason": "dark"}]}
        )

        [feedback] = carryover.derive("r", [warned, blocked], []).rule_feedback

        self.assertEqual(
            feedback,
            {"rule_id": "grue", "warned": 1, "blocked": 1, "died_after_warning": [1], "moves": [1]},
        )


class Repository:
    def __init__(self, memories: list[MemoryRevision], rules: list[RuleDoc]) -> None:
        self.memories = {m.memory_id: m for m in memories}
        self.rules = rules

    def get_version(self, version_id: str) -> HarnessVersionRecord | None:
        return None

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None:
        return self.memories.get(memory_id)

    def get_rules(self, rule_ids: list[str]) -> list[RuleDoc]:
        return [r for r in self.rules if r.id in rule_ids]


class HarnessCautionTests(unittest.TestCase):
    def test_soft_rule_is_a_caution_not_a_second_call_and_notes_stay_in_their_room(self) -> None:
        note = memory("m-note", "advice", {"text": "The mailbox held a leaflet."})
        note = note.model_copy(update={"locations": ["West of House"]})
        caution = rule(
            "r-leaflet",
            {"command": "read leaflet", "room": "West of House"},
            "Reading the leaflet here gave nothing new.",
        )
        version = HarnessVersionRecord.model_validate(
            {
                "version_id": "v1",
                "experiment_id": "exp",
                "parent_id": "v0",
                "source_run_id": "run-0",
                "proposal_id": "p0",
                "memory_refs": [{"memory_id": "m-note", "revision_id": note.revision_id}],
                "rule_ids": ["r-leaflet"],
                "context_policy": {},
                "scores": [],
                "created_at": NOW,
            }
        )
        chat = ScriptedChat(["read leaflet", "north", "I give up"])
        with tempfile.TemporaryDirectory() as out:
            result = play_game(
                GameSpec(seed=0, move_cap=5, version=version, story_file=STORY_FILE),
                chat=chat,
                sink=JsonlSink(out),
                repository=Repository([note], [caution]),
            )
            lines = (Path(out) / f"{result.record.run_id}.moves.jsonl").read_text().splitlines()
            moves = [json.loads(line) for line in lines]

        tails = [call[0]["content"][1]["text"] for call in chat.calls]  # type: ignore[index]
        self.assertEqual(len(chat.calls), 3)  # one Player call per move: no re-prompt
        self.assertIn("Notes for this room (from earlier games, may be wrong):", tails[0])
        self.assertIn("The mailbox held a leaflet.", tails[0])
        self.assertIn("Reading the leaflet here gave nothing new. (caution:", tails[0])
        self.assertEqual(moves[0]["warnings"], ["r-leaflet"])
        self.assertIn("Your last command matched a caution: Reading the leaflet", tails[1])
        self.assertNotIn("The mailbox held a leaflet.", tails[2])  # North of House
        self.assertNotIn("Cautions (learned", tails[2])


if __name__ == "__main__":
    unittest.main()
