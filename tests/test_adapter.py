import unittest
from pathlib import Path

from zorkinator.adapter import GameAdapter

STORY_FILE = Path(__file__).resolve().parent.parent / "games" / "zork1.z5"


@unittest.skipUnless(STORY_FILE.is_file(), "Zork story file is not installed")
class GameAdapterTest(unittest.TestCase):
    def test_reset_and_step(self) -> None:
        with GameAdapter(STORY_FILE) as game:
            opening = game.reset(seed=7)
            result = game.step("look")

        self.assertIn("West of House", opening)
        self.assertIn("West of House", result["text"])
        self.assertEqual(result["moves"], 1)
        self.assertFalse(result["done"])

    def test_forbidden_commands_are_blocked(self) -> None:
        with GameAdapter(STORY_FILE) as game:
            game.reset(seed=7)
            for command in ("SAVE", " restore ", "Restart"):
                with self.subTest(command=command), self.assertRaises(ValueError):
                    game.step(command)


if __name__ == "__main__":
    unittest.main()
