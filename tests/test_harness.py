import tempfile
import unittest
from pathlib import Path

from anthropic.types import MessageParam

from zorkinator import builder, scribe
from zorkinator.harness import play_harness
from zorkinator.memory import ChatArchive
from zorkinator.models import WorldFactDoc
from zorkinator.prompts import BASIC
from zorkinator.runner import ChatReply, JsonlSink, Usage
from zorkinator.world import WorldModel, direction_of

STORY_FILE = Path(__file__).resolve().parent.parent / "games" / "zork1.z5"


class ScriptedChat:
    model = "claude-haiku-4-5"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[list[MessageParam]] = []

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply:
        self.calls.append(list(messages))
        text = self.replies.pop(0)
        return ChatReply(text, Usage(input=100, output=5), [{"role": "assistant", "content": text}])


class ScribeTest(unittest.TestCase):
    def test_room_change_fills_exit_and_view_facts(self) -> None:
        world = WorldModel.empty("r1")
        opening = (
            "Copyright (c) 1981\nRevision 88 / Serial number 840726\n\n"
            "West of House\nYou are west of a house.\n\n"
        )
        scribe.update(world, 0, None, opening, scribe.parse_stub(opening))
        self.assertEqual(world.state.room, "West of House")

        text = "North of House\nTo the north a narrow path winds through the trees.\n\n"
        obs = scribe.update(world, 1, "n", text, scribe.parse_stub(text))

        self.assertTrue(obs.moved)
        self.assertEqual(world.rooms["West of House"].exits["north"].to, "North of House")
        self.assertEqual(world.rooms["North of House"].exits["north"].status, "mentioned")
        facts = {(f.subject, f.attr): f.value for f in world.drain_facts()}
        self.assertEqual(facts[("West of House", "exit_north")], "North of House")
        self.assertIs(facts[("North of House", "dark")], False)

    def test_blocked_move_and_inventory(self) -> None:
        world = WorldModel.empty("r1")
        opening = "West of House\nA field.\n\n"
        scribe.update(world, 0, None, opening, scribe.parse_stub(opening))
        text = "The door is boarded and you can't remove the boards.\n\n"
        scribe.update(world, 1, "east", text, scribe.parse_stub(text))
        self.assertEqual(world.rooms["West of House"].exits["east"].status, "blocked")

        scribe.update(world, 2, "take the leaflet", "Taken.\n\n", scribe.parse_stub("Taken.\n\n"))
        self.assertEqual(world.inventory, ["leaflet"])
        dropped = "Dropped.\n\n"
        scribe.update(world, 3, "drop leaflet", dropped, scribe.parse_stub(dropped))
        self.assertEqual(world.inventory, [])
        self.assertIn("leaflet", world.rooms["West of House"].items_seen)

    def test_direction_of(self) -> None:
        self.assertEqual(direction_of("N"), "north")
        self.assertEqual(direction_of("go up"), "up")
        self.assertIsNone(direction_of("open mailbox"))


@unittest.skipUnless(STORY_FILE.is_file(), "Zork story file is not installed")
class HarnessGameTest(unittest.TestCase):
    def test_scripted_game_builds_map_and_logs(self) -> None:
        chat = ScriptedChat(
            [
                "open mailbox\nGoal: see what the mailbox holds",
                "take leaflet",
                "north",
                "north",
                "I give up",
            ]
        )
        facts: list[WorldFactDoc] = []
        with tempfile.TemporaryDirectory() as out:
            record = play_harness(7, 20, chat=chat, sink=JsonlSink(out), facts=facts.append)
            moves = (Path(out) / f"{record.run_id}.moves.jsonl").read_text().splitlines()

        self.assertEqual((record.mode, record.end_reason, record.moves), ("harness", "gave_up", 5))
        self.assertEqual(len(moves), 5)
        edges = {(f.subject, f.attr, f.value) for f in facts}
        self.assertIn(("West of House", "exit_north", "North of House"), edges)
        self.assertIn(("North of House", "exit_north", "Forest Path"), edges)
        self.assertIn(("leaflet", "carried", True), edges)

        # Each move gets a fresh single-message prompt: cached paper prefix + live state tail.
        last = chat.calls[-1]
        self.assertEqual(len(last), 1)
        blocks = last[0]["content"]
        assert isinstance(blocks, list)
        texts = [b["text"] for b in blocks if b["type"] == "text"]
        self.assertTrue(texts[0].startswith(BASIC))
        tail = texts[1]
        self.assertIn("Location: Forest Path", tail)
        self.assertIn("Carrying: leaflet", tail)
        self.assertIn("Goal: see what the mailbox holds", tail)

    def test_prefix_is_stable_across_moves(self) -> None:
        world = WorldModel.empty("r1")
        first = builder.build_prompt(world, 1, "West of House\n")
        world.state.goal = "explore"
        second = builder.build_prompt(world, 2, "North of House\n")
        self.assertEqual(first.prefix, second.prefix)


if __name__ == "__main__":
    unittest.main()
