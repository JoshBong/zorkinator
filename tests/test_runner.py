import tempfile
import unittest
from pathlib import Path

from anthropic.types import MessageParam

from zorkinator.memory import ChatArchive
from zorkinator.prompts import BASIC
from zorkinator.runner import ChatReply, JsonlSink, Usage, extract_command, play

STORY_FILE = Path(__file__).resolve().parent.parent / "games" / "zork1.z5"


class ScriptedChat:
    model = "claude-opus-4-5-20251101"

    def __init__(self, replies: list[str]) -> None:
        self.replies = replies
        self.calls: list[list[MessageParam]] = []

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply:
        self.calls.append(list(messages))
        text = self.replies.pop(0)
        return ChatReply(text, Usage(input=100, output=5), [{"role": "assistant", "content": text}])


class ExtractCommandTest(unittest.TestCase):
    def test_first_line_without_decoration(self) -> None:
        self.assertEqual(extract_command("`open mailbox`\nbecause"), "open mailbox")
        self.assertEqual(extract_command("\n> north"), "north")
        self.assertEqual(extract_command("   "), "")


@unittest.skipUnless(STORY_FILE.is_file(), "Zork story file is not installed")
class PaperModeTest(unittest.TestCase):
    def test_paper_protocol_blocked_command_and_give_up(self) -> None:
        chat = ScriptedChat(["ready", "open mailbox", "restart", "I give up"])
        with tempfile.TemporaryDirectory() as out:
            record = play("paper", 0, 10, chat=chat, sink=JsonlSink(out), story_file=STORY_FILE)
            moves = (Path(out) / f"{record.run_id}.moves.jsonl").read_text().splitlines()

        self.assertEqual(chat.calls[0], [{"role": "user", "content": BASIC}])
        self.assertIn("West of House", str(chat.calls[1][-1]["content"]))
        self.assertIn("Not allowed", str(chat.calls[3][-1]["content"]))
        self.assertEqual(record.end_reason, "gave_up")
        self.assertEqual(record.moves, 3)
        self.assertEqual(len(moves), 3)
        self.assertEqual(record.tokens_in, 400)

    def test_chat_archive_saves_and_searches(self) -> None:
        chat = ScriptedChat(["ready", "open mailbox", "I give up"])
        with tempfile.TemporaryDirectory() as out:
            archive = ChatArchive(Path(out) / "chats")
            record = play(
                "paper",
                0,
                5,
                chat=chat,
                sink=JsonlSink(out),
                story_file=STORY_FILE,
                archive=archive,
            )
            saved = (Path(out) / "chats" / f"{record.run_id}.txt").read_text()
            self.assertIn("Assistant: open mailbox", saved)
            self.assertIn("leaflet", archive.search("mailbox leaflet"))
            self.assertIn(record.run_id, archive.recent(1))

    def test_move_cap(self) -> None:
        chat = ScriptedChat(["ready"] + ["look"] * 3)
        with tempfile.TemporaryDirectory() as out:
            record = play("paper", 0, 3, chat=chat, sink=JsonlSink(out), story_file=STORY_FILE)
        self.assertEqual((record.end_reason, record.moves), ("cap", 3))


if __name__ == "__main__":
    unittest.main()
