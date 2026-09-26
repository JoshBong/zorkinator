import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from anthropic.types import MessageParam

from zorkinator.driver import ChainCursor
from zorkinator.memory import ChatArchive
from zorkinator.models import HarnessVersionRecord, MemoryRevision, MoveRecord, RuleDoc, RunRecord
from zorkinator.prompts import BASIC
from zorkinator.runner import (
    ChatReply,
    JsonlSink,
    Usage,
    extract_command,
    play,
    play_harness_chain,
)

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


class MemorySink:
    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.moves: list[MoveRecord] = []

    def move(self, record: MoveRecord) -> None:
        self.moves.append(record)

    def run(self, record: RunRecord) -> None:
        self.runs[record.run_id] = record


class HarnessRepository:
    def __init__(self, versions: list[HarnessVersionRecord]) -> None:
        self.versions = {version.version_id: version for version in versions}

    def get_version(self, version_id: str) -> HarnessVersionRecord | None:
        return self.versions.get(version_id)

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None:
        return None

    def get_rules(self, rule_ids: list[str]) -> list[RuleDoc]:
        return []


class TwoGameDriver:
    chain = "smoke-chain"

    def __init__(self, sink: MemorySink, versions: list[HarnessVersionRecord]) -> None:
        self.sink = sink
        self.versions = versions

    def run(self, games: int, play_game: object) -> ChainCursor:
        assert callable(play_game)
        for game_index, version in enumerate(self.versions[:games]):
            record = play_game(version.version_id, game_index)
            assert self.sink.runs.get(record.run_id) == record
            assert record.chain == self.chain
            assert record.game_index == game_index
            assert record.version_id == version.version_id
        return ChainCursor(version_id=self.versions[games - 1].version_id, game_index=games)


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

    def test_harness_chain_persists_driver_cursor_fields_before_return(self) -> None:
        now = datetime.now(UTC)
        versions = [
            HarnessVersionRecord(
                version_id=version_id,
                experiment_id="experiment-smoke",
                parent_id=None if index == 0 else "version-root",
                source_run_id=None if index == 0 else "run-0",
                proposal_id=None if index == 0 else "proposal-0",
                memory_refs=[],
                rule_ids=[],
                context_policy={},
                scores=[],
                created_at=now,
            )
            for index, version_id in enumerate(("version-root", "version-child"))
        ]
        sink = MemorySink()
        driver = TwoGameDriver(sink, versions)
        chat = ScriptedChat(["ready", "I give up", "ready", "I give up"])

        cursor = play_harness_chain(
            2,
            driver=driver,  # type: ignore[arg-type]
            repository=HarnessRepository(versions),
            chat=chat,
            sink=sink,
            seed=0,
            move_cap=20,
            story_file=STORY_FILE,
        )

        records = sorted(sink.runs.values(), key=lambda record: record.game_index or 0)
        self.assertEqual(cursor.game_index, 2)
        self.assertEqual([record.chain for record in records], [driver.chain, driver.chain])
        self.assertEqual([record.game_index for record in records], [0, 1])
        self.assertEqual(
            [record.version_id for record in records], ["version-root", "version-child"]
        )
        self.assertEqual(chat.calls[1][-2], {"role": "assistant", "content": "ready"})
        self.assertIn("West of House", str(chat.calls[1][-1]["content"]))


if __name__ == "__main__":
    unittest.main()
