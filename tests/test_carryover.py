"""Fixed-code carry-over closes the loop: game 1's play becomes game 2's working memory."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from tests.test_reflector import FakeModel, FakeRepository, run
from zorkinator import builder, carryover
from zorkinator.models import (
    AddMemoryOperation,
    MemoryOperation,
    MemoryRevision,
    MoveRecord,
    ReviseMemoryOperation,
)
from zorkinator.reflector import Reflector
from zorkinator.world import WorldModel

NOW = datetime(2026, 9, 26, 17, 0, tzinfo=UTC)
NORTH = "North of House\nYou are facing the north side of a white house.\n\n"
BEHIND = "Behind House\nYou are behind the white house. A window is slightly ajar.\n\n"
KITCHEN = "Kitchen\nYou are in the kitchen.\nOn the table is an elongated brown sack.\n\n"


def mv(
    run_id: str, n: int, room: str, command: str, text: str, delta: int = 0, died: bool = False
) -> MoveRecord:
    return MoveRecord(
        run_id=run_id,
        n=n,
        room=room,
        command=command,
        proposals=[command],
        rejections=[],
        text=text,
        score=delta,
        score_delta=delta,
        died=died,
        latency_ms=1,
        ts=NOW,
    )


def game_one(run_id: str = "run_1") -> list[MoveRecord]:
    return [
        mv(
            run_id,
            1,
            "West of House",
            "open mailbox",
            "Opening the small mailbox reveals a leaflet.",
        ),
        mv(run_id, 2, "West of House", "north", NORTH),
        mv(run_id, 3, "North of House", "east", BEHIND),
        mv(run_id, 4, "Behind House", "open window", "With great effort, you open the window."),
        mv(run_id, 5, "Behind House", "west", KITCHEN, delta=10),
        mv(run_id, 6, "Kitchen", "look", KITCHEN),
        mv(run_id, 7, "Kitchen", "eat sack", "You have died.", died=True),
    ]


def commit(ops: list[MemoryOperation], parent: list[MemoryRevision]) -> list[MemoryRevision]:
    """What versions.commit does to memory, minus persistence."""
    active = {m.memory_id: m for m in parent}
    for i, op in enumerate(ops):
        assert not isinstance(op, AddMemoryOperation | ReviseMemoryOperation) or op.evidence
        if isinstance(op, AddMemoryOperation):
            memory_id = f"mem-{op.key}"
        elif isinstance(op, ReviseMemoryOperation):
            memory_id = op.memory_id
        else:
            active.pop(op.memory_id)
            continue
        active[memory_id] = MemoryRevision.model_validate(
            {
                "revision_id": f"{memory_id}@{i}",
                "experiment_id": "exp",
                "memory_id": memory_id,
                "supersedes_revision_id": None,
                "kind": op.kind,
                "subjects": op.subjects,
                "content": op.content,
                "status": op.status,
                "evidence": op.evidence,
                "rationale": op.rationale,
                "source_run_id": "run_1",
                "proposal_id": "p",
                "operation_key": f"k{i}",
                "born_version_id": "v2",
                "created_at": NOW,
            }
        )
    return list(active.values())


class DeriveTests(unittest.TestCase):
    def test_game_one_becomes_room_memories_with_exits_items_and_notable_actions(self) -> None:
        carry = carryover.derive("run_1", game_one(), [])

        rooms = {op.content["room"]: op for op in carry.ops if isinstance(op, AddMemoryOperation)}
        self.assertEqual(set(rooms), {"West of House", "North of House", "Behind House", "Kitchen"})
        west, behind, kitchen = rooms["West of House"], rooms["Behind House"], rooms["Kitchen"]
        self.assertEqual(west.content["exits"], {"north": "North of House"})
        self.assertEqual(west.locations, ["West of House"])  # Elliott's recall filter
        self.assertEqual(west.content["items"], ["leaflet"])
        self.assertEqual(
            west.content["tried"], {"open mailbox": "Opening the small mailbox reveals a leaflet."}
        )
        self.assertEqual(
            behind.content["tried"],
            {
                "open window": "With great effort, you open the window.",
                "west": "Kitchen [+10 points]",
            },
        )
        self.assertEqual(kitchen.content["tried"], {"eat sack": "You have died. [died]"})
        self.assertEqual(kitchen.content["items"], ["elongated brown sack"])
        self.assertIn(7, [ref.n for ref in kitchen.evidence])
        self.assertEqual((carry.feedback, carry.upgrades, carry.rooms), ([], [], 4))

    def test_next_game_sees_the_map_and_actions_and_confirms_them(self) -> None:
        memories = commit(carryover.derive("run_1", game_one(), []).ops, [])

        world = WorldModel.empty("run_2")
        loaded = world.load(memories)
        world.state.room = "Kitchen"
        tail = builder.build_tail(world, 1, KITCHEN)
        self.assertEqual(len(loaded), 4)
        self.assertIn("Earlier games tried here: eat sack -> You have died. [died]", tail)
        self.assertIn("West of House: north -> North of House", world.memory_map)
        self.assertIn("west -> Kitchen [+10 points]", world.memory_map)

        replay = [mv("run_2", 1, "West of House", "north", NORTH)]
        carry = carryover.derive("run_2", replay, memories)

        self.assertEqual(carry.ops, [])  # nothing new: no revision churn
        [check] = carry.feedback
        self.assertEqual((check["verdict"], check["n"]), ("confirmed", 1))
        self.assertEqual(carry.owned, {m.memory_id for m in memories})

    def test_a_contradicted_exit_is_revised_from_the_newer_game(self) -> None:
        memories = commit(carryover.derive("run_1", game_one(), []).ops, [])
        blocked = "The door is locked."
        carry = carryover.derive(
            "run_2", [mv("run_2", 1, "West of House", "north", blocked)], memories
        )

        [op] = carry.ops
        assert isinstance(op, ReviseMemoryOperation)
        self.assertEqual(op.content["exits"], {})
        self.assertEqual(op.content["blocked"], {"north": blocked})
        self.assertEqual(carry.feedback[0]["verdict"], "contradicted")

    def test_confirmed_hypothesis_is_upgraded(self) -> None:
        [hint] = commit(
            [
                AddMemoryOperation.model_validate(
                    {
                        "op": "add",
                        "key": "lamp",
                        "kind": "item",
                        "subjects": ["lamp"],
                        "content": {"item": "leaflet", "room": "West of House"},
                        "status": "hypothesis",
                        "evidence": [{"run_id": "run_1", "n": 1}],
                        "rationale": "seen",
                    }
                )
            ],
            [],
        )
        text = "Opening the small mailbox reveals a leaflet."
        carry = carryover.derive(
            "run_2", [mv("run_2", 1, "West of House", "open mailbox", text)], [hint]
        )

        [upgrade] = carry.upgrades
        self.assertEqual((upgrade.memory_id, upgrade.status), (hint.memory_id, "supported"))


class ReflectorCarryOverTests(unittest.TestCase):
    def test_proposal_merges_code_ops_and_drops_model_map_ops(self) -> None:
        moves = game_one("run_2")
        model = FakeModel(
            {
                "memory_ops": [
                    {
                        "op": "add",
                        "key": "kitchen",
                        "kind": "room",
                        "subjects": ["Kitchen"],
                        "content": {"text": "a model-written map entry"},
                        "status": "supported",
                        "evidence": [{"run_id": "run_2", "n": 7}],
                        "rationale": "model map",
                    },
                    {
                        "op": "add",
                        "key": "sack",
                        "kind": "failure",
                        "subjects": ["sack"],
                        "content": {"text": "Eating the sack killed me"},
                        "status": "supported",
                        "evidence": [{"run_id": "run_2", "n": 7}],
                        "rationale": "death at move 7",
                    },
                ],
                "rule_diffs": [],
                "summary": "Learned about the sack.",
            }
        )
        reflector = Reflector(
            FakeRepository(run(moves=len(moves)), moves),
            model,
            id_factory=lambda: "proposal_1",
            manifest=lambda version: [],
        )

        proposal = reflector.propose("run_2")

        kinds = [op.kind for op in proposal.memory_ops if isinstance(op, AddMemoryOperation)]
        self.assertEqual(kinds, ["failure", "room", "room", "room", "room"])
        self.assertIn("4 room updates", proposal.summary)
        self.assertIn("1 model map ops dropped", proposal.summary)
        packet = json.loads(model.prompts[0].split("EVIDENCE_PACKET\n", 1)[1])
        self.assertEqual(packet["map"]["rooms_known"], 4)
        self.assertEqual(packet["memory_feedback"], [])


if __name__ == "__main__":
    unittest.main()
