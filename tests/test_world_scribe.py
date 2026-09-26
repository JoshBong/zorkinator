import unittest

from zorkinator import scribe
from zorkinator.world import Exit, WorldModel


class ScribeEdgeCaseTests(unittest.TestCase):
    def test_parse_stub_uses_the_last_room_paragraph_and_detects_death_and_darkness(self) -> None:
        text = (
            "Copyright (c) 1981\nRevision 88\n\nDark Cellar\nIt is pitch dark. You have died.\n\n"
        )

        parsed = scribe.parse_stub(text, score_delta=5)

        self.assertEqual(parsed.room, "Dark Cellar")
        self.assertEqual(parsed.description, "It is pitch dark. You have died.")
        self.assertEqual(parsed.score_delta, 5)
        self.assertTrue(parsed.dark)
        self.assertTrue(parsed.died)
        self.assertIsNone(scribe.parse_stub("You are carrying:\nkey").room)

    def test_inventory_listing_reconciles_items_and_examination_removes_a_lead(self) -> None:
        world = WorldModel.empty("scribe-inventory")
        opening = "Foyer\nA plain room.\n\n"
        scribe.update(world, 0, None, opening, scribe.parse_stub(opening))
        taken = "brass lantern: Taken.\nsmall key: Taken.\n"
        scribe.update(world, 1, "take lantern", taken, scribe.parse_stub(taken))

        obs = scribe.update(
            world,
            2,
            "inventory",
            "You are carrying:\nsmall key\n",
            scribe.parse_stub("You are carrying:\nsmall key\n"),
        )
        examined = scribe.update(
            world,
            3,
            "examine key",
            "It is a small brass key.\n",
            scribe.parse_stub("It is a small brass key.\n"),
        )

        self.assertEqual(world.inventory, ["small key"])
        self.assertFalse(world.items["brass lantern"].carried)
        self.assertTrue(world.items["small key"].examined)
        self.assertTrue(obs.new_interaction)
        self.assertTrue(examined.new_interaction)
        self.assertNotIn("small key (carried, never examined)", world.leads())
        carried = {
            (fact.subject, fact.value) for fact in world.drain_facts() if fact.attr == "carried"
        }
        self.assertIn(("brass lantern", False), carried)
        self.assertIn(("small key", True), carried)

    def test_failed_known_or_dark_movement_does_not_erase_known_map_edges(self) -> None:
        world = WorldModel.empty("scribe-map")
        opening = "Foyer\nA plain room.\n\n"
        scribe.update(world, 0, None, opening, scribe.parse_stub(opening))
        world.room("Foyer").exits["east"] = Exit("east", "Gallery", "known", 0)
        world.drain_facts()

        scribe.update(
            world,
            1,
            "east",
            "The door is stuck.\n",
            scribe.parse_stub("The door is stuck.\n"),
        )
        scribe.update(
            world,
            2,
            "north",
            "It is pitch black.\n",
            scribe.parse_stub("It is pitch black.\n"),
        )

        self.assertEqual(world.room("Foyer").exits["east"].to, "Gallery")
        self.assertNotIn("north", world.room("Foyer").exits)
        self.assertEqual(world.drain_facts(), [])


class WorldModelTests(unittest.TestCase):
    def test_memory_and_current_observations_produce_compact_summary_and_leads(self) -> None:
        world = WorldModel.empty("world-summary")
        world.remember_exit("Foyer", "north", "Gallery")
        world.remember_item("old map", "Foyer", "shows a route")
        world.remember_objective("Find the treasure")
        world.remember_hypothesis("the trapdoor opens with the key")
        world.remember_past_run("A previous game reached the cellar.")
        world.state.room = "Foyer"
        world.state.score = 7
        world.state.goal = "Find the treasure"
        world.room("Foyer").exits["east"] = Exit("east", status="mentioned")
        world.room("Foyer").items_seen.add("old map")
        world.item("coin").carried = True
        world.room("Gallery").exits["up"] = Exit("up", status="mentioned")
        world.fact("Foyer", "dark", False, 4)

        leads = world.leads()
        summary = world.summary()

        self.assertIn("exit east (mentioned, never taken)", leads)
        self.assertIn("old map (seen here, never examined)", leads)
        self.assertIn("coin (carried, never examined)", leads)
        self.assertIn("elsewhere: Gallery has an untaken exit", leads)
        self.assertIn("hypothesis: the trapdoor opens with the key", leads)
        self.assertEqual(summary["inventory"], ["coin"])
        self.assertEqual(summary["score"], 7)
        self.assertEqual(world.objectives[0].source, "memory")
        self.assertEqual(world.past_runs, ["A previous game reached the cellar."])
        self.assertEqual(world.drain_facts()[0].attr, "dark")
        self.assertEqual(world.drain_facts(), [])


if __name__ == "__main__":
    unittest.main()
