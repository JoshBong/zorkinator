"""Version memories hydrate the working KBs, and play confirms or contradicts them."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime

from pydantic import JsonValue

from zorkinator import builder, scribe
from zorkinator.models import EvidenceRef, HarnessVersionRecord, MemoryRevision
from zorkinator.world import WorldModel

NOW = datetime(2026, 9, 26, 17, 0, tzinfo=UTC)
WEST = "West of House\nYou are standing in an open field west of a white house.\n\n"
NORTH = "North of House\nYou are facing the north side of a white house.\n\n"


def memory(
    memory_id: str,
    kind: str,
    content: dict[str, JsonValue],
    status: str = "supported",
) -> MemoryRevision:
    return MemoryRevision.model_validate(
        {
            "revision_id": f"{memory_id}-r1",
            "experiment_id": "exp",
            "memory_id": memory_id,
            "supersedes_revision_id": None,
            "kind": kind,
            "subjects": ["test"],
            "content": content,
            "status": status,
            "evidence": [EvidenceRef(run_id="run-0", n=1)],
            "rationale": "seen in game one",
            "source_run_id": "run-0",
            "proposal_id": "p0",
            "operation_key": f"add:{memory_id}",
            "born_version_id": "v1",
            "created_at": NOW,
        }
    )


def loaded_world(*memories: MemoryRevision) -> tuple[WorldModel, set[str]]:
    world = WorldModel.empty("run-1")
    return world, world.load(memories)


class HydrationTests(unittest.TestCase):
    def test_structured_memories_fill_the_working_kbs(self) -> None:
        world, loaded = loaded_world(
            memory(
                "m-room",
                "room",
                {
                    "room": "West of House",
                    "exits": {"n": "North of House", "west": None},
                    "blocked": {"east": "The door is boarded"},
                    "items": ["a small mailbox"],
                },
            ),
            memory(
                "m-edge",
                "map_edge",
                {"from": "North of House", "direction": "e", "to": "Behind House"},
            ),
            memory(
                "m-item",
                "item",
                {"item": "the lantern", "room": "Living Room", "text": "lights dark rooms"},
            ),
            memory("m-goal", "objective", {"text": "Get into the house"}),
            memory("m-hyp", "hypothesis", {"text": "the window opens"}),
            memory("m-free", "failure", {"text": "the troll killed me unarmed"}),
            memory(
                "m-wrong", "map_edge", {"from": "A", "direction": "n", "to": "B"}, "contradicted"
            ),
        )

        self.assertEqual(loaded, {"m-room", "m-edge", "m-item", "m-goal", "m-hyp"})
        west = world.rooms["West of House"].exits
        self.assertEqual((west["north"].to, west["north"].status), ("North of House", "known"))
        self.assertEqual(west["west"].status, "mentioned")
        self.assertEqual(
            (west["east"].status, west["east"].note), ("blocked", "The door is boarded")
        )
        self.assertEqual(west["north"].memory_id, "m-room")
        self.assertEqual(world.items["small mailbox"].last_seen_room, "West of House")
        self.assertEqual(world.items["lantern"].memory_id, "m-item")
        self.assertEqual(world.objectives[0].text, "Get into the house")
        self.assertEqual(world.hypotheses, ["the window opens"])
        self.assertNotIn("A", world.rooms)
        self.assertIn(
            "- West of House: east blocked (The door is boarded); north -> North of House",
            world.memory_map,
        )
        self.assertIn("- lantern: lights dark rooms", world.memory_map)

    def test_prefix_shows_the_map_and_the_packet_omits_loaded_memories(self) -> None:
        room = memory(
            "m-room", "room", {"room": "West of House", "exits": {"north": "North of House"}}
        )
        free = memory("m-free", "failure", {"text": "the troll killed me unarmed"})
        world, loaded = loaded_world(room, free)
        version = HarnessVersionRecord.model_validate(
            {
                "version_id": "v1",
                "experiment_id": "exp",
                "parent_id": "v0",
                "source_run_id": "run-0",
                "proposal_id": "p0",
                "memory_refs": [
                    {"memory_id": "m-room", "revision_id": "m-room-r1"},
                    {"memory_id": "m-free", "revision_id": "m-free-r1"},
                ],
                "rule_ids": [],
                "context_policy": {},
                "scores": [],
                "created_at": NOW,
            }
        )

        context = builder.render_version_context("run-1", version, [room, free], [], loaded=loaded)
        prefix = builder.build_prefix(world, context)

        packet = json.loads(context.splitlines()[-1])
        self.assertEqual([m["memory_id"] for m in packet["memories"]], ["m-free"])
        self.assertEqual(packet["memories_in_notes"], 1)
        self.assertIn("north -> North of House", prefix)
        self.assertIn("the troll killed me unarmed", prefix)


class FeedbackTests(unittest.TestCase):
    def test_walking_a_remembered_exit_confirms_it(self) -> None:
        world, _ = loaded_world(
            memory(
                "m-room", "room", {"room": "West of House", "exits": {"north": "North of House"}}
            )
        )
        scribe.update(world, 0, None, WEST, scribe.parse_stub(WEST))
        world.drain_facts()
        scribe.update(world, 1, "north", NORTH, scribe.parse_stub(NORTH))

        self.assertEqual(
            world.memory_feedback["m-room"]["confirmed"],
            [{"n": 1, "claim": "West of House north", "detail": "led to North of House"}],
        )
        facts = {(f.subject, f.attr) for f in world.drain_facts()}
        self.assertIn(("m-room", "memory_confirmed:West of House north"), facts)
        self.assertEqual(world.summary()["memory_feedback"], world.memory_feedback)

    def test_a_remembered_exit_that_is_blocked_now_is_contradicted_and_replaced(self) -> None:
        world, _ = loaded_world(
            memory(
                "m-edge",
                "map_edge",
                {"from": "West of House", "direction": "north", "to": "North of House"},
            )
        )
        scribe.update(world, 0, None, WEST, scribe.parse_stub(WEST))
        blocked = "You can't go that way.\n\n"
        scribe.update(world, 1, "north", blocked, scribe.parse_stub(blocked))

        exit_ = world.rooms["West of House"].exits["north"]
        self.assertEqual((exit_.status, exit_.source), ("blocked", "this_game"))
        [entry] = world.memory_feedback["m-edge"]["contradicted"]
        self.assertEqual(entry["detail"], "blocked: You can't go that way.")

    def test_a_remembered_item_found_elsewhere_is_contradicted_once(self) -> None:
        world, _ = loaded_world(
            memory("m-item", "item", {"item": "leaflet", "room": "Living Room"})
        )
        text = WEST + "There is a leaflet here.\n\n"
        scribe.update(world, 0, None, text, scribe.parse_stub(text))
        scribe.update(world, 1, "take leaflet", "Taken.\n\n", scribe.parse_stub("Taken.\n\n"))

        self.assertEqual(
            world.memory_feedback["m-item"],
            {
                "contradicted": [
                    {"n": 0, "claim": "leaflet in Living Room", "detail": "found in West of House"}
                ]
            },
        )
        self.assertEqual(world.items["leaflet"].source, "this_game")


if __name__ == "__main__":
    unittest.main()
