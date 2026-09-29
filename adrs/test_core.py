"""Unit tests for adrs/core.py mechanics, against the real Zork I engine (no model calls).

The scripted routes below exercise the mechanics only; none of this is shown to the model.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from adrs import core

SEED = 0
TO_CELLAR = [
    "north",
    "north",
    "climb tree",
    "take egg",
    "down",
    "south",
    "east",
    "open window",
    "enter window",
    "west",
    "take lamp",
    "move rug",
    "open trap door",
    "turn on lamp",
    "down",
]


def play(cmds: list[str], seed: int = SEED) -> list[tuple[str, dict]]:
    """(command, move dict) pairs like the harness logs them."""
    from zorkinator.adapter import GameAdapter
    from zorkinator.scribe import parse_stub

    out, last = [], 0
    with GameAdapter() as g:
        room = parse_stub(g.reset(seed)).room
        for n, c in enumerate(cmds, 1):
            r = g.step(c)
            m = {
                "n": n,
                "command": c,
                "text": r["text"],
                "score": r["score"],
                "score_delta": r["score"] - last,
                "room": room,
            }
            last = r["score"]
            room = parse_stub(r["text"]).room or room
            out.append((c, m))
    return out


class Engine(unittest.TestCase):
    def test_inventory_is_only_indented_items(self):
        from zorkinator.adapter import GameAdapter

        with GameAdapter() as g:
            g.reset(SEED)
            for c in ["north", "north", "climb tree", "take egg"]:
                g.step(c)
            self.assertEqual(core.inventory(g), frozenset({"a jewel-encrusted egg"}))

    def test_trace_reaches_cellar_with_lit_lamp(self):
        end = core.trace(TO_CELLAR, SEED)[-1]
        self.assertEqual(end["room"], "Cellar")
        self.assertEqual(end["score"], 40)
        self.assertIn("a brass lantern (providing light)", end["inv"])
        self.assertFalse(any("troll" in x for x in end["inv"]))


WITH_LAMP = (*TO_CELLAR[:10], "take lamp")  # Living Room @15 holding egg + lamp
NO_LAMP = tuple(TO_CELLAR[:10])  # Living Room @15 holding egg only (shorter)


class Archive(unittest.TestCase):
    def test_same_spot_keeps_more_items_even_if_longer(self):
        for order in ((WITH_LAMP, NO_LAMP), (NO_LAMP, WITH_LAMP)):
            a: dict = {}
            for traj in order:
                core.update_archive(a, list(traj), SEED)
            spot = a["Living Room @15"]
            self.assertTrue(core.holds(spot["inv"], "lantern"), order)

    def test_best_spot_prefers_items_on_equal_score(self):
        a = {
            "x": {"key": "x", "score": 40, "inv": ["a lamp"], "traj": ["a"] * 30},
            "y": {"key": "y", "score": 40, "inv": [], "traj": ["a"] * 5},
        }
        self.assertEqual(core.best_spot(a)["key"], "x")

    def test_minimize_keeps_the_lamp(self):
        noisy = [*TO_CELLAR[:10], "east", "west", "look", *TO_CELLAR[10:]]
        a: dict = {}
        core.update_archive(a, noisy, SEED)
        route = core.route_for(a["Cellar @40"], SEED)
        self.assertLess(len(route), len(noisy))
        self.assertIn("take lamp", route)
        self.assertIn("turn on lamp", route)
        end = core.trace(route, SEED)[-1]
        self.assertEqual((end["room"], end["score"]), ("Cellar", 40))


class WallsTest(unittest.TestCase):
    def test_passed_needs_a_new_exit_or_a_later_gain(self):
        w = core.Walls(core.CONFIGS["x9"])
        through = play([*TO_CELLAR, "south"])  # Cellar -> East of Chasm
        self.assertTrue(w.passed("cellar", through, SEED))
        self.assertFalse(w.passed("cellar", through, SEED))  # same exit again is not new
        never = play(TO_CELLAR[:10])  # never reaches the cellar
        self.assertFalse(core.Walls(core.CONFIGS["x9"]).passed("cellar", never, SEED))

    def test_passed_counts_a_gain_after_the_wall(self):
        w = core.Walls(core.CONFIGS["x9"])
        w.exits_seen["cellar"] = {"east of chasm"}  # the exit is not new...
        gain = play([*TO_CELLAR, "south", "east", "take painting"])  # ...but +4 after the wall
        self.assertTrue(w.passed("cellar", gain, SEED))

    def test_compile_plan_arrives_at_the_troll_holding_the_sword(self):
        to_troll = [*TO_CELLAR, "north"]
        played = play(to_troll)
        a: dict = {}
        core.update_archive(a, to_troll, SEED)
        kn = core.Knowledge()
        kn.learn(played, SEED, ["sword", "troll"])
        names = kn.names(a)
        plan = {
            "get": [{"item": "sword", "at": names["living room"]}],
            "go_to": names["troll room"],
        }
        route = core.compile_plan(plan, a, kn, SEED)
        self.assertIsNotNone(route)
        end = core.trace(route, SEED)[-1]
        self.assertEqual(core.norm_room(end["room"]), "troll room")
        self.assertTrue(core.holds(end["inv"], "sword"))

    def test_compile_plan_rejects_an_item_that_cannot_be_taken(self):
        to_troll = [*TO_CELLAR, "north"]
        a: dict = {}
        core.update_archive(a, to_troll, SEED)
        kn = core.Knowledge()
        kn.learn(play(to_troll), SEED, ["troll"])
        names = kn.names(a)
        plan = {"get": [{"item": "axe", "at": names["troll room"]}], "go_to": names["troll room"]}
        self.assertIsNone(core.compile_plan(plan, a, kn, SEED))

    def test_knowledge_records_where_things_were_seen(self):
        kn = core.Knowledge()
        kn.learn(play(TO_CELLAR), SEED, ["elvish sword", "trophy case"])
        self.assertEqual(core.norm_room(kn.seen_at.get("sword")), "living room")
        self.assertIn("sword", kn.room_text["living room"])


class Checks(unittest.TestCase):
    def test_quote_check_ignores_score_markers_but_not_made_up_text(self):
        moves = [{"n": 6, "command": "take egg", "text": "Taken.\n\n"}]
        self.assertTrue(core.verified([{"move": 6, "quote": "Taken. [score 0->5]"}], moves))
        self.assertFalse(core.verified([{"move": 6, "quote": "treasure scored"}], moves))
        self.assertFalse(core.verified([], moves))

    def test_rules_need_two_distinct_moves(self):
        moves = [
            {"n": 1, "command": "x", "text": "pitch black"},
            {"n": 2, "command": "y", "text": "pitch black"},
        ]
        one = [{"move": 1, "quote": "pitch black"}, {"move": 1, "quote": "pitch black"}]
        two = [{"move": 1, "quote": "pitch black"}, {"move": 2, "quote": "pitch black"}]
        self.assertFalse(core.verified(one, moves, min_distinct=2))
        self.assertTrue(core.verified(two, moves, min_distinct=2))

    def test_core_does_not_import_the_experiment_files(self):
        code = (
            "import sys, adrs.core; bad=[m for m in sys.modules if m.startswith('adrs.') "
            "and m != 'adrs.core']; print(bad)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()
        self.assertEqual(out, "[]")


class ReviewFindings(unittest.TestCase):
    """Regression tests for the 2026-09-28 code review of adrs/core.py."""

    def test_died_holding_uses_this_games_state(self):
        played = [("attack troll with sword", {"text": "... **** You have died **** ..."})]
        armed = [{"i": 0, "room": "The Troll Room", "inv": frozenset({"a sword"}), "score": 40}]
        bare = [{"i": 0, "room": "The Troll Room", "inv": frozenset({"a lamp"}), "score": 40}]
        self.assertTrue(core.died_holding("troll room", played, armed, ["sword"]))
        self.assertFalse(core.died_holding("troll room", played, bare, ["sword"]))

    def test_passed_ignores_exits_already_walked(self):
        w = core.Walls(core.CONFIGS["x9"])
        through = play([*TO_CELLAR, "south"])  # Cellar -> East of Chasm, already on the map
        self.assertFalse(w.passed("cellar", through, SEED, {"East of Chasm"}))

    def test_passed_ignores_gains_made_by_the_replayed_route(self):
        w = core.Walls(core.CONFIGS["x9"])
        w.exits_seen["cellar"] = {"east of chasm"}
        cmds = [*TO_CELLAR, "south", "east", "take painting"]
        # the whole thing was the harness's route (handoff after the last command)
        self.assertFalse(w.passed("cellar", play(cmds), SEED, handoff=len(cmds)))

    def test_minimize_never_keeps_a_route_that_dies(self):
        a: dict = {}
        core.update_archive(a, TO_CELLAR, SEED)
        route = core.route_for(a["Cellar @40"], SEED)
        self.assertEqual(len(core.trace(route, SEED)), len(route))

    def test_reflect_survives_a_malformed_reply(self):
        real = core.ask
        try:
            for bad in ([1, 2], {"facts_add": [{"text": "no where key"}]}, None):
                core.ask = lambda *a, _b=bad, **k: (_b, 0.0)
                state = {"facts": [], "hyps": [], "goals": []}
                rec = type("R", (), {"score": 0, "end_reason": "cap", "moves": 1})()
                new, _stats, _ = core.reflect(state, [], rec, [], 0, core.CONFIGS["x9"])
                self.assertEqual(new["facts"], [])
        finally:
            core.ask = real


if __name__ == "__main__":
    unittest.main()


class Wiring(unittest.TestCase):
    """The 2026-09-28 x10 crash: a module-level function dropped Walls.postmortem into its body."""

    def test_classes_have_their_methods(self) -> None:
        for cls, names in (
            (core.Walls, ("find", "passed", "postmortem")),
            (core.Knowledge, ("learn", "names")),
            (core.Speedrun, ("hook", "complete", "cost")),
        ):
            for n in names:
                self.assertTrue(callable(getattr(cls, n, None)), f"{cls.__name__}.{n}")

    def test_postmortem_with_a_fake_model_gives_a_compilable_plan(self) -> None:
        to_troll = [*TO_CELLAR, "north"]
        played = play(to_troll)
        a: dict = {}
        core.update_archive(a, to_troll, SEED)
        kn = core.Knowledge()
        kn.learn(played, SEED, ["sword", "troll"])
        reply = {
            "plans": [
                {
                    "need": "a weapon",
                    "get": [{"item": "sword", "at": "Living Room"}],
                    "go_to": "The Troll Room",
                    "then": "attack the troll with the sword",
                }
            ]
        }
        real = core.ask
        try:
            core.ask = lambda *x, **k: (reply, 0.0)
            walls = core.Walls(core.CONFIGS["x9"])
            plans, _ = walls.postmortem(
                "troll room", kn, {"facts": [], "hyps": [], "goals": []}, a, core.CONFIGS["x9"]
            )
        finally:
            core.ask = real
        self.assertEqual([p["get"][0]["item"] for p in plans], ["sword"])
        self.assertIsNotNone(core.compile_plan(plans[0], a, kn, SEED))


class OfflineChain(unittest.TestCase):
    """The whole chain loop, offline: the repo's explorer player + a fake model for reflection
    and post-mortems, thresholds low enough that walls and plan games happen."""

    def test_chain_runs_end_to_end(self) -> None:
        import shutil
        from dataclasses import replace
        from pathlib import Path

        cfg = replace(
            core.CONFIGS["x9"],
            player="explorer",
            games=6,
            move_cap=40,
            wall_deaths=1,
            wall_stalls=1,
            plateau_games=1,
            scoring_every=3,
        )

        def fake(prompt: str, *a: object, **k: object) -> tuple[dict, float]:
            if "keeps failing" in prompt:
                return {
                    "plans": [{"need": "x", "get": [], "go_to": "West of House", "then": "look"}]
                }, 0.0
            return {
                "facts_add": [],
                "new_things": [
                    {
                        "name": "mailbox",
                        "kind": "object",
                        "where": "West of House",
                        "hypothesis": "h",
                        "test": "open it",
                    }
                ],
            }, 0.0

        real = core.ask
        tag = "test-offline-chain"
        try:
            core.ask = fake
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), cfg.games)
        self.assertIn("scoring", {r["kind"] for r in rows})
