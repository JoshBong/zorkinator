import io
import tempfile
import unittest
from pathlib import Path

from anthropic.types import MessageParam

from zorkinator import builder, scribe
from zorkinator.explorer import ExplorerChat
from zorkinator.harness import GameSpec, Trace, play_game, play_harness
from zorkinator.memory import ChatArchive
from zorkinator.models import WorldFactDoc
from zorkinator.progress import ConsoleProgress
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


class ItemTextTest(unittest.TestCase):
    def test_room_text_items_and_short_names(self) -> None:
        world = WorldModel.empty("r1")
        tree = (
            "Up a Tree\nBeside you on the branch is a small bird's nest.\n"
            "In the bird's nest is a large egg encrusted with precious jewels, apparently lost.\n\n"
        )
        scribe.update(world, 0, None, tree, scribe.parse_stub(tree))
        self.assertEqual(world.rooms["Up a Tree"].items_seen, {"large egg", "small bird's nest"})

        scribe.update(world, 1, "take egg", "Taken.\n\n", scribe.parse_stub("Taken.\n\n"))
        self.assertEqual(world.inventory, ["large egg"])

    def test_same_named_rooms_are_a_move_not_a_block(self) -> None:
        world = WorldModel.empty("r1")
        first = "Forest\nThis is a dimly lit forest.\n\n"
        scribe.update(world, 0, None, first, scribe.parse_stub(first))
        again = "Forest\nYou hear in the distance the chirping of a song bird.\n\n"
        obs = scribe.update(world, 1, "south", again, scribe.parse_stub(again))
        self.assertTrue(obs.moved)
        self.assertEqual(world.rooms["Forest"].exits["south"].status, "known")


@unittest.skipUnless(STORY_FILE.is_file(), "Zork story file is not installed")
class InnerLoopRunTest(unittest.TestCase):
    def test_offline_run_zero_exercises_every_kb(self) -> None:
        with tempfile.TemporaryDirectory() as out:
            trace = Trace(Path(out) / "run.trace.md")
            result = play_game(
                GameSpec(seed=0, move_cap=120),
                chat=ExplorerChat(seed=0),
                sink=JsonlSink(out),
                trace=trace,
            )
            trace_text = trace.path.read_text()

        world, record = result.world, result.record
        self.assertGreaterEqual(len(world.rooms), 5)
        self.assertTrue(world.inventory)
        self.assertGreater(record.score, 0)
        self.assertIn(record.end_reason, {"stuck40", "cap", "death"})
        self.assertEqual(record.cost_usd, 0.0)
        self.assertIn("## Move 1\n", trace_text)
        self.assertIn('"inventory"', trace_text)

    def test_console_progress_reports_each_move_and_kb_changes(self) -> None:
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as out:
            play_game(
                GameSpec(seed=0, move_cap=12),
                chat=ExplorerChat(seed=0),
                sink=JsonlSink(out),
                progress=ConsoleProgress(stream=stream),
            )
        lines = stream.getvalue().splitlines()
        self.assertTrue(lines[0].startswith("=== harness game"))
        self.assertEqual(sum(1 for line in lines if line.startswith("[")), 12)
        self.assertTrue(any("+ room:" in line for line in lines))
        self.assertTrue(any(line.startswith("--- move 10: score") for line in lines))
        ended = next(line for line in lines if line.startswith("=== ended"))
        self.assertIn("/350", ended)
        self.assertTrue(any("score timeline:" in line for line in lines))

    def test_score_timeline_records_every_change(self) -> None:
        progress = ConsoleProgress(stream=io.StringIO())
        world = WorldModel.empty("r1")
        for n, score in [(1, 0), (2, 5), (3, 5), (4, 15)]:
            world.state.score = score
            progress.move(n, 10, "Room", "cmd", "text", 0, 0.0, world)
        self.assertEqual(progress.timeline(), "0 -> 5 @2 -> 15 @4")
        self.assertEqual((progress.best, progress.last_gain_move), (15, 4))

    def test_run_one_preloaded_memory_reaches_the_prompt(self) -> None:
        world = WorldModel.empty("preload")
        world.remember_exit("West of House", "north", "North of House")
        world.remember_item("leaflet", "West of House", "reading it tells you about the game")
        world.remember_hypothesis("the small window behind the house may open")
        chat = ScriptedChat(["look", "north", "I give up"])
        with tempfile.TemporaryDirectory() as out:
            result = play_game(
                GameSpec(seed=0, move_cap=5, version_id="v1", world=world),
                chat=chat,
                sink=JsonlSink(out),
            )

        blocks = chat.calls[0][0]["content"]
        assert isinstance(blocks, list)
        first = [b["text"] for b in blocks if b["type"] == "text"]
        self.assertIn("hypothesis: the small window behind the house may open", first[1])
        self.assertIn("north -> North of House (from earlier games)", first[1])
        self.assertIn("Earlier games saw here: leaflet", first[1])
        # Walking the remembered exit confirms it in this game.
        exit_ = result.world.rooms["West of House"].exits["north"]
        self.assertEqual((exit_.source, exit_.to), ("this_game", "North of House"))
        self.assertEqual(result.record.version_id, "v1")


if __name__ == "__main__":
    unittest.main()
