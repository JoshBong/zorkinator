import unittest
from typing import cast

from anthropic.types import MessageParam, TextBlockParam

from zorkinator.builder import PromptParts
from zorkinator.memory import ChatArchive
from zorkinator.monitor import Monitor
from zorkinator.player import parse_goal, parse_surprise, propose
from zorkinator.runner import ChatReply, Usage
from zorkinator.scribe import Observation
from zorkinator.world import Step, WorldModel


class RecordingChat:
    model = "gpt-5.6-luna"

    def __init__(self, reply: ChatReply) -> None:
        self.reply = reply
        self.messages: list[list[MessageParam]] = []

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply:
        self.messages.append(list(messages))
        return self.reply


class PlayerTests(unittest.TestCase):
    def test_propose_parses_protocol_fields_and_caches_the_prefix(self) -> None:
        chat = RecordingChat(
            ChatReply(
                text="open the door\nGoal: enter the house\nExpect: the door opens\nSurprise: yes",
                usage=Usage(input=3, output=4),
            )
        )

        proposal = propose(chat, PromptParts(prefix="fixed instructions", tail="state"))

        self.assertEqual(proposal.command, "open the door")
        self.assertEqual(proposal.goal, "enter the house")
        self.assertEqual(proposal.expect, "the door opens")
        self.assertTrue(proposal.surprise)
        self.assertEqual(proposal.usage.output, 4)
        messages = chat.messages[0]
        blocks = cast(list[TextBlockParam], messages[0]["content"])
        self.assertEqual(blocks[0]["cache_control"], {"type": "ephemeral"})
        self.assertEqual(blocks[1]["text"], "state")

    def test_propose_appends_verifier_feedback_only_to_dynamic_tail(self) -> None:
        chat = RecordingChat(ChatReply(text="wait", usage=Usage()))

        proposal = propose(
            chat,
            PromptParts(prefix="fixed instructions", tail="state"),
            feedback="A hard rule blocks that command.",
        )

        self.assertEqual(proposal.command, "wait")
        messages = chat.messages[0]
        blocks = cast(list[TextBlockParam], messages[0]["content"])
        self.assertEqual(blocks[0]["text"], "fixed instructions")
        self.assertEqual(blocks[1]["text"], "state\n\nRejected: A hard rule blocks that command.")

    def test_optional_response_tags_accept_protocol_case_and_reject_unknown_surprise(self) -> None:
        self.assertEqual(parse_goal("GOAL: Explore the cellar"), "Explore the cellar")
        self.assertTrue(parse_surprise("Surprise: YES, unexpected"))
        self.assertFalse(parse_surprise("surprise: no"))
        self.assertIsNone(parse_surprise("Surprise: maybe"))
        self.assertIsNone(parse_goal("Goal:"))

    def test_propose_preserves_give_up_without_optional_protocol_fields(self) -> None:
        chat = RecordingChat(ChatReply(text="I give up", usage=Usage()))

        proposal = propose(chat, PromptParts(prefix="fixed instructions", tail="state"))

        self.assertEqual(proposal.command, "I give up")
        self.assertTrue(proposal.gave_up)
        self.assertIsNone(proposal.goal)
        self.assertIsNone(proposal.expect)
        self.assertIsNone(proposal.surprise)


class MonitorTests(unittest.TestCase):
    def test_repeat_warning_precedes_stall_and_stall_wins_at_the_threshold(self) -> None:
        world = WorldModel.empty("run-monitor")
        monitor = Monitor(stuck_after=3, repeat_limit=1)

        self.assertEqual(monitor.update(world, 1, Observation(new_rooms=["West of House"])), "ok")
        self.assertEqual(monitor.idle_moves(1), 0)

        world.state.recent.append(Step(2, "West of House", "look", "Nothing happens."))
        self.assertEqual(monitor.update(world, 2, Observation()), "ok")

        world.state.recent.append(Step(3, "West of House", "LOOK", "Nothing happens."))
        self.assertEqual(monitor.update(world, 3, Observation()), "repeat")

        world.state.recent.append(Step(4, "West of House", "look", "Nothing happens."))
        self.assertEqual(monitor.update(world, 4, Observation()), "stuck")
        self.assertEqual(monitor.idle_moves(4), 3)

    def test_progress_resets_the_idle_counter_even_without_a_recent_step(self) -> None:
        monitor = Monitor(stuck_after=1)

        self.assertEqual(
            monitor.update(WorldModel.empty("run-progress"), 5, Observation(score_delta=1)), "ok"
        )
        self.assertEqual(monitor.idle_moves(5), 0)

    def test_empty_history_is_not_a_repeat_and_idle_moves_never_go_negative(self) -> None:
        monitor = Monitor(stuck_after=3, repeat_limit=0)
        monitor.last_progress = 5

        self.assertEqual(monitor.update(WorldModel.empty("run-empty"), 4, Observation()), "ok")
        self.assertEqual(monitor.idle_moves(4), 0)


if __name__ == "__main__":
    unittest.main()
