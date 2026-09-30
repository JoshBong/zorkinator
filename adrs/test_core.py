"""Unit tests for adrs/core.py mechanics, against the real Zork I engine (no model calls).

The scripted routes below exercise the mechanics only; none of this is shown to the model.
"""

from __future__ import annotations

import collections
import json
import random
import subprocess
import sys
import unittest
from dataclasses import replace
from typing import ClassVar

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


class X11(unittest.TestCase):
    """Experiments: restore, try, read the game's text; graft score gains into the best routes."""

    def test_attempt_reports_score_changes_from_a_restore(self) -> None:
        inv, res = core.attempt(list(NO_LAMP), ["open case", "put egg in case"], SEED)
        self.assertTrue(core.holds(inv, "egg"))
        self.assertEqual([t["delta"] for t in res], [0, 5])
        self.assertFalse(any(t["died"] for t in res))

    def test_graft_puts_a_gain_into_the_best_route(self) -> None:
        a: dict = {}
        core.update_archive(a, TO_CELLAR, SEED)
        self.assertEqual(core.best_spot(a)["score"], 40)
        n = core.graft(["open case", "put egg in case"], "Living Room", ["egg"], a, SEED)
        self.assertGreaterEqual(n, 1)
        self.assertEqual(core.best_spot(a)["score"], 45)

    def test_play_records_the_purpose_of_an_item(self) -> None:
        kn = core.Knowledge()
        kn.learn(play([*NO_LAMP, "open case", "put egg in case"]), SEED, ["egg"])
        self.assertEqual(kn.found.get("egg"), "put egg in case")

    def test_experiment_prompt_template_has_no_game_knowledge(self) -> None:
        low = core.EXPERIMENT.casefold() + core.NUDGE.casefold()
        for w in ("zork", "trophy", "treasure", "grue", "troll", "lamp", "sword", "case"):
            self.assertNotIn(w, low)

    def test_session_with_a_fake_model(self) -> None:
        played = play(TO_CELLAR)
        a: dict = {}
        core.update_archive(a, TO_CELLAR, SEED)
        kn = core.Knowledge()
        kn.learn(played, SEED, ["egg", "lamp"])
        egg = {
            "thing": "egg",
            "go_to": "Living Room",
            "carry": ["egg"],
            "try": ["open case", "put egg in case"],
            "expect": "?",
        }
        reply = {"experiments": [egg, {**egg, "go_to": "Nowhere"}, egg]}
        real = core.ask
        try:
            core.ask = lambda *x, **k: (reply, 0.0)
            ex = core.Experiments()
            state: dict = {"facts": [], "hyps": [], "goals": []}
            stats, _ = ex.session(kn, state, a, SEED, core.CONFIGS["x11"])
        finally:
            core.ask = real
        self.assertEqual(stats.get("run"), 1)
        self.assertEqual(stats.get("invalid"), 1)
        self.assertEqual(stats.get("repeat"), 1)
        self.assertEqual(stats.get("gains"), 1)
        self.assertGreaterEqual(stats.get("grafted", 0), 1)
        self.assertEqual(kn.found.get("egg"), "put egg in case")
        self.assertTrue(any("[score 15->20]" in f["text"] for f in state["facts"]))
        self.assertEqual(core.best_spot(a)["score"], 45)
        self.assertIn("egg", ex.prompt(kn, state, a, core.CONFIGS["x11"]))

    def test_x11_chain_runs_end_to_end(self) -> None:
        import shutil
        from dataclasses import replace
        from pathlib import Path

        cfg = replace(
            core.CONFIGS["x11"],
            player="explorer",
            games=5,
            move_cap=40,
            exp_after=0,
            scoring_every=3,
        )

        def fake(prompt: str, *a: object, **k: object) -> tuple[dict, float]:
            if "what things are for" in prompt:
                return {
                    "experiments": [
                        {"go_to": "West of House", "carry": [], "try": ["open mailbox"]}
                    ]
                }, 0.0
            return {"facts_add": []}, 0.0

        real = core.ask
        tag = "test-offline-x11"
        try:
            core.ask = fake
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), cfg.games)
        self.assertTrue(any(r["exp"].get("proposed") for r in rows))


class X11Review(unittest.TestCase):
    """Regressions for the x11 code review (2026-09-28)."""

    def _setup(self) -> tuple[dict, core.Knowledge]:
        a: dict = {}
        core.update_archive(a, TO_CELLAR, SEED)
        kn = core.Knowledge()
        kn.learn(play(TO_CELLAR), SEED, ["egg", "lamp"])
        return a, kn

    def _run(self, reply: object, a: dict, kn: core.Knowledge, ex: core.Experiments) -> dict:
        real = core.ask
        try:
            core.ask = lambda *x, **k: (reply, 0.0)
            state: dict = {"facts": [], "hyps": [], "goals": []}
            return ex.session(kn, state, a, SEED, core.CONFIGS["x11"])[0]
        finally:
            core.ask = real

    def test_malformed_replies_do_not_crash(self) -> None:
        a, kn = self._setup()
        for reply in (None, [], {"experiments": None}, {"experiments": {"a": 1}}):
            self.assertEqual(self._run(reply, a, kn, core.Experiments()), {})
        bad = {"experiments": [{"go_to": "Living Room", "carry": [], "try": "open egg"}]}
        self.assertEqual(self._run(bad, a, kn, core.Experiments()).get("invalid"), 1)

    def test_a_known_setup_command_does_not_block_a_new_sequence(self) -> None:
        a, kn = self._setup()
        ex = core.Experiments()
        # "move rug" was typed in the Living Room on the way down; the sequence is still new
        e = {"go_to": "Living Room", "carry": ["egg"], "try": ["move rug", "open case"]}
        self.assertEqual(self._run({"experiments": [e]}, a, kn, ex).get("run"), 1)
        # but the exact same sequence again is a repeat
        self.assertEqual(self._run({"experiments": [e]}, a, kn, ex).get("repeat"), 1)

    def test_unreachable_is_not_remembered_as_done(self) -> None:
        a, kn = self._setup()
        ex = core.Experiments()
        e = {"go_to": "Living Room", "carry": ["sword"], "try": ["wave sword"]}
        kn.takes["sword"] = "Nowhere Room"
        self.assertEqual(self._run({"experiments": [e]}, a, kn, ex).get("unreachable"), 1)
        self.assertEqual(ex.done, set())


class X11Smoke(unittest.TestCase):
    """Smoke run 2026-09-28: the model proposed `put egg in case` but left carry empty."""

    def test_things_named_in_the_commands_are_carried(self) -> None:
        a: dict = {}
        core.update_archive(a, TO_CELLAR, SEED)
        kn = core.Knowledge()
        kn.learn(play(TO_CELLAR), SEED, ["egg", "lamp"])
        e = {"go_to": "Living Room", "carry": [], "try": ["open case", "put egg in case"]}
        real = core.ask
        try:
            core.ask = lambda *x, **k: ({"experiments": [e]}, 0.0)
            state: dict = {"facts": [], "hyps": [], "goals": []}
            stats, _ = core.Experiments().session(kn, state, a, SEED, core.CONFIGS["x11"])
        finally:
            core.ask = real
        self.assertEqual(stats.get("gains"), 1)


class X11Stopped(unittest.TestCase):
    """x11 run stopped 2026-09-28: 10/15 seed-2 experiments unreachable. Causes: fixtures in the
    carry list (`take case`), and compile_plan trying only the top-scoring spot in go_to."""

    DEPOSITED = (*NO_LAMP, "open case", "put egg in case")  # Living Room @20, egg in the case

    def test_compile_plan_uses_a_spot_that_already_holds_the_item(self) -> None:
        a: dict = {}
        core.update_archive(a, list(NO_LAMP), SEED)
        core.update_archive(a, list(self.DEPOSITED), SEED)
        kn = core.Knowledge()
        kn.learn(play(list(self.DEPOSITED)), SEED, ["egg"])
        plan = {"get": [{"item": "egg", "at": "Up a Tree"}], "go_to": "Living Room"}
        route = core.compile_plan(plan, a, kn, SEED)
        self.assertIsNotNone(route)
        self.assertTrue(core.holds(core.trace(route, SEED)[-1]["inv"], "egg"))

    def _session(self, e: dict, kn: core.Knowledge, a: dict, ex: core.Experiments) -> dict:
        real = core.ask
        try:
            core.ask = lambda *x, **k: ({"experiments": [e]}, 0.0)
            state: dict = {"facts": [], "hyps": [], "goals": []}
            return ex.session(kn, state, a, SEED, core.CONFIGS["x11"])[0]
        finally:
            core.ask = real

    def test_fixtures_are_not_carried(self) -> None:
        a: dict = {}
        core.update_archive(a, TO_CELLAR, SEED)
        kn = core.Knowledge()
        kn.learn(play(TO_CELLAR), SEED, ["egg", "case"])
        kn.seen_at["case"] = "Living Room"
        e = {"go_to": "Living Room", "carry": ["case", "egg"], "try": ["put egg in case"]}
        stats = self._session(
            {**e, "try": ["open case", "put egg in case"]}, kn, a, core.Experiments()
        )
        self.assertEqual(stats.get("gains"), 1)

    def test_looking_does_not_make_a_sequence_new(self) -> None:
        a: dict = {}
        core.update_archive(a, TO_CELLAR, SEED)
        kn = core.Knowledge()
        kn.learn(play(TO_CELLAR), SEED, ["egg"])
        ex = core.Experiments()
        e = {"go_to": "Living Room", "carry": [], "try": ["move rug", "open trap door"]}
        self.assertEqual(self._session(e, kn, a, ex).get("repeat"), 1)  # both done on the way
        e2 = {"go_to": "Living Room", "carry": [], "try": ["move rug", "look"]}
        self.assertEqual(self._session(e2, kn, a, ex).get("repeat"), 1)
        self.assertTrue(any(x.get("status") == "repeat" for x in ex.log))


class X12(unittest.TestCase):
    """Calm handoff (x10 seed 2 was handed a flooding room 1-2 moves from drowning in games 6-10)
    and the manual passages."""

    def test_the_x10_flood_route_is_walked_back_to_a_calm_point(self) -> None:
        import json
        from pathlib import Path

        fx = json.loads((Path(__file__).parent / "fixtures/doomed_route_s2.json").read_text())
        r, seed = fx["route"], fx["seed"]
        self.assertEqual(core.trace(r, seed)[-1]["room"], "Maintenance Room")
        self.assertLess(len(core.trace([*r, *["wait"] * 5], seed)), len(r) + 5)  # idle drowns
        calm = core.calm_prefix(r, seed, 5)
        self.assertLess(len(calm), len(r))
        self.assertGreaterEqual(len(calm), len(r) - 12)
        self.assertEqual(len(core.trace([*calm, *["wait"] * 5], seed)), len(calm) + 5)

    def test_a_calm_route_is_unchanged_and_a_guard_is_approached_not_entered(self) -> None:
        self.assertEqual(core.calm_prefix(TO_CELLAR, SEED, 5), TO_CELLAR)
        self.assertEqual(core.calm_prefix([*TO_CELLAR, "north"], SEED, 5), TO_CELLAR)
        self.assertEqual(core.calm_prefix([*TO_CELLAR, "north"], SEED, 0), [*TO_CELLAR, "north"])

    def test_manual_reaches_the_prompts_only_when_on(self) -> None:
        self.assertIn("putting treasure into the trophy case", core.MANUAL)
        on, off = core.CONFIGS["x12"], core.CONFIGS["x9"]
        self.assertTrue(on.manual and on.doom_waits == 5)
        self.assertFalse(off.manual or off.doom_waits)
        self.assertIn("{manual}", core.REFLECT)
        self.assertIn("{manual}", core.POSTMORTEM)

    def test_x12_chain_runs_end_to_end(self) -> None:
        import shutil
        from dataclasses import replace
        from pathlib import Path

        cfg = replace(
            core.CONFIGS["x12"],
            player="explorer",
            games=5,
            move_cap=40,
            scoring_every=3,
            wall_deaths=1,
            wall_stalls=1,
        )
        seen: list[str] = []

        def fake(prompt: str, *a: object, **k: object) -> tuple[dict, float]:
            seen.append(prompt)
            return {"facts_add": [], "plans": []}, 0.0

        real = core.ask
        tag = "test-offline-x12"
        try:
            core.ask = fake
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), cfg.games)
        self.assertTrue(any("trophy case" in p for p in seen))  # reflector saw the manual
        self.assertIn("handoff_death", rows[-1])

    def test_the_ablated_views_stay_off(self) -> None:
        """x11 ran without ZK_ABLATE, so leads/state/goal views came back (Open leads: 353 lines
        in one x11 trace vs 0 in x10). Ablation is now config and applied by chain()."""
        import shutil
        from dataclasses import replace
        from pathlib import Path

        cfg = replace(core.CONFIGS["x12"], player="explorer", games=1, move_cap=15)
        tag = "test-offline-ablate"
        real = core.ask
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            core.chain((cfg, tag, SEED))
            text = (Path("runs/adrs") / tag / f"s{SEED}" / "g0.trace.md").read_text()
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        for view in ("Open leads:", "Carrying:"):  # "Goal:" also appears in player replies
            self.assertNotIn(view, text)


class X13a(unittest.TestCase):
    """Exact-repeat block: 49% of x12's non-movement actions repeated a command already answered
    identically in the same room with the same things held."""

    class W:
        def __init__(self, room: str, inv: list[str]) -> None:
            from types import SimpleNamespace

            self.state = SimpleNamespace(room=room, score=0)
            self.inventory = inv
            self.routed: list[str] = []

    class Chat:
        model = "fake"

    def _sr(self, route: list[str] | None = None) -> core.Speedrun:
        return core.Speedrun(self.Chat(), route or [], [], "", repeat_block=2)

    def test_third_identical_try_is_blocked(self) -> None:
        sr, w = self._sr(), self.W("Living Room", ["lantern"])
        for _ in range(2):
            self.assertIsNone(sr.intercept("put painting in case", w))
            sr.hook(w, 1, "You don't have the painting.")
        msg = sr.intercept("put  Painting in case", w)
        self.assertIsNotNone(msg)
        self.assertIn("You don't have the painting", msg or "")
        self.assertEqual(sr.blocked, 1)
        sr.hook(w, 2, msg or "")  # the block message itself is not recorded as a reply
        self.assertEqual(len(sr.replies[("Living Room", "put painting in case", ("lantern",))]), 2)

    def test_changed_state_or_varied_replies_or_free_commands_pass(self) -> None:
        sr = self._sr()
        w = self.W("Troll Room", ["sword"])
        for reply in ("The troll parries.", "You hit the troll."):
            sr.intercept("attack troll with sword", w)
            sr.hook(w, 1, reply)
        self.assertIsNone(sr.intercept("attack troll with sword", w))  # replies differ: random
        w2 = self.W("Living Room", [])
        for _ in range(3):
            self.assertIsNone(sr.intercept("look", w2))
            sr.hook(w2, 1, "Living Room ...")
        for _ in range(2):
            sr.intercept("take sword", w2)
            sr.hook(w2, 1, "You already have that!")
        self.assertIsNone(sr.intercept("take sword", self.W("Living Room", ["sword"])))

    def test_a_move_that_works_is_never_counted_as_a_repeat(self) -> None:
        sr, w = self._sr(), self.W("East of Chasm", ["lantern"])
        for _ in range(3):
            self.assertIsNone(sr.intercept("north", w))
            w.state.room = "Cellar"
            sr.hook(w, 1, "Cellar You are in a dark and damp cellar.")
            w.state.room = "East of Chasm"
        self.assertIsNone(sr.intercept("north", w))

    def test_moves_between_rooms_with_the_same_name_are_never_blocked(self) -> None:
        sr, w = self._sr(), self.W("Forest", [])
        for _ in range(4):
            self.assertIsNone(sr.intercept("north", w))
            sr.hook(w, 1, "Forest")  # a different room with the same name
        for c in ("climb tree", "go north", "enter window"):
            for _ in range(3):
                self.assertIsNone(sr.intercept(c, w))
                sr.hook(w, 1, "Same text")

    def test_nothing_is_blocked_during_route_replay_or_when_off(self) -> None:
        sr, w = self._sr(route=["north", "north"]), self.W("Forest", [])
        self.assertTrue(sr.active)
        self.assertIsNone(sr.intercept("north", w))
        off = core.Speedrun(self.Chat(), [], [], "", repeat_block=0)
        for _ in range(4):
            self.assertIsNone(off.intercept("jump", w))
            off.hook(w, 1, "Very good.")

    def test_x13a_chain_runs_end_to_end(self) -> None:
        import shutil
        from dataclasses import replace
        from pathlib import Path

        cfg = replace(core.CONFIGS["x13a"], player="explorer", games=3, move_cap=60)
        real = core.ask
        tag = "test-offline-x13a"
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), cfg.games)
        self.assertIn("blocked", rows[-1])

    def test_the_block_fires_in_a_real_game(self) -> None:
        """Wiring: GameSpec.intercept -> Speedrun.intercept, replies via the router hook."""
        import shutil
        from dataclasses import replace
        from pathlib import Path

        import zorkinator.harness as harness
        from zorkinator.seed_run import ScriptedChat

        cfg = replace(core.CONFIGS["x13a"], games=1, move_cap=8)
        real_ask, real_chat = core.ask, harness.make_chat
        tag = "test-offline-x13a-live"
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            harness.make_chat = lambda *a, **k: ScriptedChat(["xyzzy"] * 8)
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask, harness.make_chat = real_ask, real_chat
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertGreaterEqual(
            rows[0]["blocked"], 5
        )  # xyzzy (fixed reply) 3..8 never reach the game


class X13b(unittest.TestCase):
    """Danger signals: learned from own deaths (quote-checked), pinned when they recur in play."""

    MOVES: ClassVar[list[dict]] = [
        {"n": 1, "command": "turn on lamp", "text": "The brass lantern is now on.", "room": "A"},
        {
            "n": 2,
            "command": "wait",
            "text": "Time passes. Your lamp appears a bit dimmer.",
            "room": "A",
        },
        {"n": 3, "command": "north", "text": "It is pitch black.", "room": "A"},
        {
            "n": 4,
            "command": "north",
            "text": "Oh, no! You have been eaten by a grue.",
            "room": "B",
            "died": True,
        },
    ]

    def _learn(self, reply: object) -> tuple[dict, int]:
        state: dict = {"facts": [], "hyps": [], "goals": []}
        real = core.ask
        try:
            core.ask = lambda *x, **k: (reply, 0.0)
            n, _ = core.learn_signals(state, self.MOVES, core.CONFIGS["x13b"])
        finally:
            core.ask = real
        return state, n

    def test_only_quoted_earlier_warnings_are_kept(self) -> None:
        state, n = self._learn(
            {
                "signals": [
                    {"signal": "Your lamp appears a bit dimmer", "move": 2, "then": "grue, dark"},
                    {"signal": "the lamp will explode soon", "move": 2, "then": "made up"},
                    {"signal": "You have been eaten by a grue", "move": 4, "then": "not earlier"},
                ]
            }
        )
        self.assertEqual(n, 1)
        self.assertEqual(state["signals"][0]["signal"], "Your lamp appears a bit dimmer")
        self.assertEqual(self._learn(None)[1], 0)
        self.assertEqual(self._learn({"signals": "x"})[1], 0)

    def test_a_recurring_signal_is_pinned_once_after_handoff(self) -> None:
        from types import SimpleNamespace

        class Chat:
            model = "fake"

        sr = core.Speedrun(Chat(), [], [], "explore")
        sr.signals = [{"signal": "lamp appears a bit dimmer", "then": "eaten in the dark"}]
        w = SimpleNamespace(state=SimpleNamespace(room="A", score=0), routed=[])
        sr.hook(w, 7, "Time passes. Your lamp appears a bit dimmer.")
        sr.hook(w, 8, "Your lamp appears a bit dimmer.")
        self.assertEqual(len(sr.pinned), 1)
        self.assertTrue(any("eaten in the dark" in x for x in w.routed))

    def test_x13b_chain_keeps_signals_across_games(self) -> None:
        import shutil
        from dataclasses import replace
        from pathlib import Path

        cfg = replace(core.CONFIGS["x13b"], player="explorer", games=2, move_cap=40)
        real = core.ask
        tag = "test-offline-x13b"
        try:
            core.ask = lambda *x, **k: ({"facts_add": [], "signals": []}, 0.0)
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), 2)


class X14D1(unittest.TestCase):
    """D1: trajectory ranking by dominance (never item count) and frontier-weighted spot choice."""

    CLEAN: ClassVar[list[str]] = [
        "north",
        "north",
        "climb tree",
        "take egg",
        "down",
    ]  # Forest Path @5
    BROKEN: ClassVar[list[str]] = [
        "open mailbox",
        "take leaflet",
        "north",
        "north",
        "climb tree",
        "take egg",
        "drop egg",  # breaks it; a broken canary appears
        "down",
        "take canary",
    ]

    def test_better_never_rewards_item_count(self) -> None:
        self.assertTrue(core.better((5, {"egg"}, 5), (5, {"leaflet", "canary"}, 9)))  # shorter
        self.assertFalse(core.better((5, {"leaflet", "canary"}, 9), (5, {"egg"}, 5)))
        self.assertTrue(core.better((5, {"egg", "lamp"}, 9), (5, {"egg"}, 5)))  # superset wins
        self.assertFalse(core.better((5, {"egg"}, 5), (5, {"egg", "lamp"}, 9)))
        self.assertTrue(core.better((6, {"egg"}, 50), (5, {"egg", "lamp"}, 5)))  # score first

    def test_archive_keeps_the_route_that_does_not_break_the_egg(self) -> None:
        end = core.trace(self.BROKEN, SEED)[-1]
        self.assertEqual(end["room"], "Forest Path")
        self.assertTrue(any("canary" in x for x in end["inv"]))
        for order in ((self.CLEAN, self.BROKEN), (self.BROKEN, self.CLEAN)):
            a: dict = {}
            for r in order:
                core.update_archive(a, r, SEED)
            self.assertEqual(a["Forest Path @5"]["traj"], self.CLEAN, order)

    def test_untried_directions_are_learned_and_weighted(self) -> None:
        kn = core.Knowledge()
        kn.learn(play(["north", "go south", "e", "look"]), SEED, [])
        self.assertEqual(kn.tried["west of house"], {"north"})
        self.assertIn("south", kn.tried["north of house"])
        import random

        def spot(key, room):
            return {
                "key": key,
                "room": room,
                "inv": [],
                "score": 0,
                "traj": [],
                "seen": 1,
                "chosen": 0,
            }

        a = {"A": spot("A", "Closed"), "B": spot("B", "Open")}
        tried = {"closed": set(core._DIRS), "open": set()}
        cfg = core.CONFIGS["x14"]
        picks = collections.Counter(
            core.pick_spot(a, [], random.Random(i), {}, cfg, tried)[0]["key"] for i in range(300)
        )
        self.assertGreater(picks["B"], 170)  # ~63%: frontier bonus 1.0 on a base weight of ~1.7
        off = replace(cfg, frontier_w=0.0)
        picks0 = collections.Counter(
            core.pick_spot(a, [], random.Random(i), {}, off, tried)[0]["key"] for i in range(300)
        )
        self.assertLess(abs(picks0["A"] - picks0["B"]), 60)


class X14b(unittest.TestCase):
    """x14 run 1: dark spots chosen as explore starts ("I give up" on move 1); the sword plan was
    'unreachable' because the spliced route died on its last step."""

    def test_explore_never_starts_in_the_dark_when_a_lit_spot_exists(self) -> None:
        import random

        a = {
            "d": {
                "key": "d",
                "room": "(dark)",
                "inv": [],
                "score": 40,
                "traj": ["a"],
                "seen": 1,
                "chosen": 0,
            },
            "l": {
                "key": "l",
                "room": "Cellar",
                "inv": [],
                "score": 5,
                "traj": ["a"],
                "seen": 9,
                "chosen": 9,
            },
        }
        picks = {
            core.pick_spot(a, [], random.Random(i), {}, core.CONFIGS["x14b"])[0]["key"]
            for i in range(50)
        }
        self.assertEqual(picks, {"l"})

    def test_plan_to_a_wall_hands_over_at_the_calm_point_holding_the_item(self) -> None:
        to_troll = [*TO_CELLAR, "north"]
        a: dict = {}
        core.update_archive(a, to_troll, SEED)
        kn = core.Knowledge()
        kn.learn(play(to_troll), SEED, ["sword", "troll"])
        plan = {"get": [{"item": "sword", "at": "Living Room"}], "go_to": "The Troll Room"}
        route = core.compile_plan(plan, a, kn, SEED, waits=5)
        self.assertIsNotNone(route)
        end = core.trace(route, SEED)[-1]
        self.assertTrue(core.holds(end["inv"], "sword"))
        self.assertEqual(end["room"], "Cellar")  # one step short of the troll, on purpose
        self.assertEqual(len(core.trace([*route, *["wait"] * 5], SEED)), len(route) + 5)


class X15(unittest.TestCase):
    """The round trip: treasures (scoring takes) are carried to the manual's container by a
    replay-solved return leg; the chimney needs a light load (x14b: 122 refusals, 23 climbs)."""

    TO_STUDIO: ClassVar[list[str]] = [*TO_CELLAR, "south", "east", "take painting", "north"]

    def test_goal_noun_is_read_from_the_manual(self) -> None:
        self.assertEqual(core.goal_noun(core.MANUAL), "trophy case")
        self.assertIsNone(core.goal_noun("nothing here"))

    def test_ledger_learns_treasures_deposits_and_the_goal_room(self) -> None:
        kn = core.Knowledge()
        kn.learn(play([*NO_LAMP, "open case", "put egg in case"]), SEED, ["egg", "case"])
        self.assertEqual(kn.treasures, {"egg": "Up a Tree"})
        self.assertEqual(kn.deposited, {"egg"})
        a: dict = {}
        core.update_archive(a, list(NO_LAMP), SEED)
        self.assertEqual(kn.goal_room(a, core.NOUN), "Living Room")
        kn2 = core.Knowledge()
        kn2.learn(play(list(NO_LAMP)), SEED, [])
        self.assertEqual(kn2.goal_room(a, core.NOUN), "Living Room")  # from the room's own text

    def test_return_leg_drops_the_egg_to_climb_the_chimney(self) -> None:
        # the chain has walked the chimney once (light load) so the edge exists on its map
        walked = [
            *NO_LAMP,
            "take lamp",
            "turn on lamp",
            "east",
            "up",
            "down",
            "west",
            "move rug",
            "open trap door",
            "down",
            "south",
            "east",
            "north",
            "up",
            "west",
        ]
        kn = core.Knowledge()
        kn.learn(play(walked), SEED, ["painting", "egg", "lamp"])
        self.assertEqual(kn.graph.get("Studio", {}).get("up"), "Kitchen")
        a: dict = {}
        core.update_archive(a, self.TO_STUDIO, SEED)
        kn.learn(play(self.TO_STUDIO), SEED, ["painting"])
        spot = a["Studio @44"]
        self.assertTrue(core.holds(spot["inv"], "painting") and core.holds(spot["inv"], "egg"))
        leg = core.return_leg(spot, "painting", kn, SEED, "Living Room")
        self.assertIsNotNone(leg)
        end = core.trace(leg, SEED)[-1]
        self.assertEqual(end["room"], "Living Room")
        self.assertTrue(core.holds(end["inv"], "painting"))
        self.assertTrue(any(x.startswith("drop") for x in leg))
        self.assertFalse(any("lamp" in x or "lantern" in x for x in leg if x.startswith("drop")))

    def test_next_deposit_picks_an_undeposited_treasure_with_a_leg(self) -> None:
        walked = [
            *NO_LAMP,
            "take lamp",
            "turn on lamp",
            "east",
            "up",
            "down",
            "west",
            "move rug",
            "open trap door",
            "down",
            "south",
            "east",
            "north",
            "up",
            "west",
        ]
        kn = core.Knowledge()
        kn.learn(play(walked), SEED, ["painting", "egg", "lamp"])
        kn.learn(play(self.TO_STUDIO), SEED, ["painting"])
        a: dict = {}
        core.update_archive(a, self.TO_STUDIO, SEED)
        legs: dict = {}
        dep = core.next_deposit(a, kn, SEED, core.CONFIGS["x15"], legs)
        self.assertIsNotNone(dep)
        _spot, treasure, route = dep
        self.assertIn(treasure, ("egg", "painting"))
        self.assertEqual(core.trace(route, SEED)[-1]["room"], "Living Room")
        kn.deposited |= {"egg", "painting"}
        self.assertIsNone(core.next_deposit(a, kn, SEED, core.CONFIGS["x15"], legs))

    def test_x15_chain_runs_end_to_end(self) -> None:
        import shutil
        from pathlib import Path

        cfg = replace(core.CONFIGS["x15"], player="explorer", games=3, move_cap=40)
        real = core.ask
        tag = "test-offline-x15"
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), 3)
        self.assertIn("in_case", rows[-1])

    def test_ledger_is_phrasing_independent(self) -> None:
        for cmd in ("put egg into case", "insert egg in case", "put the egg in the trophy case"):
            kn = core.Knowledge()
            kn.learn(play([*NO_LAMP, "open case", cmd]), SEED, [])
            self.assertEqual(kn.deposited, {"egg"}, cmd)
            self.assertEqual(kn.deposit_room, "Living Room")
        kn = core.Knowledge()
        kn.learn(play([*NO_LAMP, "open case", "put leaflet in case"]), SEED, [])  # 0 points
        self.assertEqual(kn.deposited, set())


class X15b(unittest.TestCase):
    """Solver fixes found by replaying x14b s0 through the ledger before any x15 run: 0/3 treasures
    had a leg because (a) the walked map has no way back (the player never walks back) and (b) the
    chimney takes the treasure and the light only, which dropping 1-2 things never reaches."""

    def test_reverse_edges_are_hypothesised_only_where_nothing_was_walked(self) -> None:
        g = {"A": {"north": "B", "climb tree": "T"}, "B": {"south": "C"}, "D": {"east": "(dark)"}}
        full = core.with_reverse(g)
        self.assertEqual(full["B"]["south"], "C")  # walked edge kept
        self.assertEqual(full["C"], {"north": "B"})  # hypothesis
        self.assertNotIn("(dark)", full)
        self.assertNotIn("T", full)  # "climb tree" has no compass reverse

    def test_drop_sets_try_few_traces_first_and_keep_the_light(self) -> None:
        held = ["a sack", "a lamp (providing light)", "a sword", "a leaflet"]
        sets = core.drop_sets(held)
        self.assertEqual(sets[:4], [[x] for x in held])
        self.assertEqual(sets[4], ["a sack", "a sword", "a leaflet"])  # keep only the light
        self.assertIn(held, sets)  # everything
        self.assertEqual(len(sets), 4 + 4 + 1 + 6)
        self.assertEqual(core.drop_sets(["a", "b"]), [["a"], ["b"], ["a", "b"]])

    def test_return_leg_uses_a_reverse_edge_the_chain_never_walked(self) -> None:
        # the map knows Living Room -east-> Kitchen but not the way back (as if never walked);
        # the way back is a hypothesis the solver verifies by replay
        walked = [*NO_LAMP, "east"]
        kn = core.Knowledge()
        kn.learn(play(walked), SEED, ["egg"])
        del kn.graph["Kitchen"]["west"]
        a: dict = {}
        core.update_archive(a, walked, SEED)
        spot = a["Kitchen @15"]
        leg = core.return_leg(spot, "egg", kn, SEED, "Living Room")
        self.assertIsNotNone(leg)
        self.assertEqual(leg[-1], "west")
        self.assertEqual(core.trace(leg, SEED)[-1]["room"], "Living Room")

    def test_return_leg_keeps_only_the_treasure_and_the_light_for_the_chimney(self) -> None:
        heavy = [*TO_CELLAR[:10], "east", "take sack", "take bottle", "west", *TO_CELLAR[10:]]
        walked = [*TO_CELLAR, "south", "east", "north", "up", "west"]  # chimney walked, lightly
        kn = core.Knowledge()
        kn.learn(play(walked), SEED, ["painting", "egg", "lamp", "sack", "bottle"])
        loaded = [*heavy, "south", "east", "take painting", "north"]
        kn.learn(play(loaded), SEED, ["painting"])
        a: dict = {}
        core.update_archive(a, loaded, SEED)
        spot = a["Studio @44"]
        self.assertGreaterEqual(len(spot["inv"]), 5)
        leg = core.return_leg(spot, "painting", kn, SEED, "Living Room")
        self.assertIsNotNone(leg)
        end = core.trace(leg, SEED)[-1]
        self.assertEqual(end["room"], "Living Room")
        self.assertTrue(core.holds(end["inv"], "painting"))
        self.assertTrue(any(core.light_item(x) for x in end["inv"]))


class X16(unittest.TestCase):
    """The harness walks untried exits from archived spots after each game (movement only, $0)."""

    def test_expansion_finds_rooms_nobody_walked_and_adds_them_to_the_map(self) -> None:
        walked = list(NO_LAMP)  # north, north, tree, ..., Living Room: many exits never tried
        kn = core.Knowledge()
        kn.learn(play(walked), SEED, ["egg"])
        a: dict = {}
        core.update_archive(a, walked, SEED)
        rooms0 = {c["room"] for c in a.values()}
        done: set = set()
        found = core.expand_archive(a, kn, SEED, 40, done)
        rooms1 = {c["room"] for c in a.values()}
        self.assertTrue(rooms1 > rooms0, (rooms0, rooms1))
        self.assertTrue(all(f["to"] in rooms1 for f in found))
        for f in found:  # every found edge is on the map
            self.assertEqual(kn.graph[f["from"].split(" @")[0]][f["dir"]], f["to"])
        self.assertTrue(done)  # (room, dir) pairs remembered
        again = core.expand_archive(a, kn, SEED, 40, done)
        self.assertTrue(
            all((f["from"], f["dir"]) not in {(g["from"], g["dir"]) for g in found} for f in again)
        )

    def test_a_load_limited_exit_is_opened_by_dropping_to_the_light(self) -> None:
        heavy = [*TO_CELLAR[:10], "east", "take sack", "take bottle", "west", *TO_CELLAR[10:]]
        loaded = [*heavy, "south", "east", "take painting", "north"]  # Studio, 6+ things held
        kn = core.Knowledge()
        kn.learn(play(loaded), SEED, ["painting", "egg", "lamp", "sack", "bottle"])
        a: dict = {c: v for c, v in {}.items()}
        core.update_archive(a, loaded, SEED)
        spot = a["Studio @44"]
        self.assertNotIn("up", kn.tried.get("studio", set()))
        done: set = set()
        found = core.expand_archive({spot["key"]: spot}, kn, SEED, 30, done)
        up = [f for f in found if f["from"] == "Studio @44" and f["dir"] == "up"]
        self.assertEqual(len(up), 1, found)
        self.assertEqual(up[0]["to"], "Kitchen")
        self.assertGreater(up[0]["drops"], 0)
        self.assertEqual(kn.graph["Studio"]["up"], "Kitchen")

    def test_x16_chain_runs_end_to_end(self) -> None:
        import shutil
        from pathlib import Path

        cfg = replace(
            core.CONFIGS["x16"], player="explorer", games=3, move_cap=40, expand_budget=20
        )
        real = core.ask
        tag = "test-offline-x16"
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), 3)
        self.assertIn("expanded", rows[-1])

    def test_a_give_up_with_moves_left_is_pushed_back_when_configured(self) -> None:
        import tempfile
        from pathlib import Path

        from zorkinator.harness import GameSpec, play_game
        from zorkinator.runner import ChatReply, JsonlSink, Usage
        from zorkinator.world import WorldModel

        class Scripted:
            model = "scripted"

            def __init__(self, replies: list[str]) -> None:
                self.replies = replies

            def complete(self, messages, archive=None):
                text = self.replies.pop(0)
                return ChatReply(text, Usage(), [{"role": "assistant", "content": text}])

        for pushbacks, want in ((0, 2), (1, 3)):  # the final give-up is logged as a move
            chat = Scripted(["open mailbox", "I give up", "north", "I give up"])
            with tempfile.TemporaryDirectory() as out:
                r = play_game(
                    GameSpec(SEED, 10, 1.0, world=WorldModel.empty("t"), pushbacks=pushbacks),
                    chat=chat,
                    sink=JsonlSink(out),
                ).record
                moves = (Path(out) / f"{r.run_id}.moves.jsonl").read_text().splitlines()
            self.assertEqual(r.end_reason, "gave_up")
            self.assertEqual(len(moves), want, pushbacks)


class X17(unittest.TestCase):
    """Expansion retries a refused exit from richer cells; the give-up pushback is game text."""

    def test_a_refused_exit_is_retried_from_a_richer_cell_of_the_same_room(self) -> None:
        # x16 remembered (room, direction) after any refusal; with per_level the memory is per
        # cell, so a later cell of the same room retries it. (The minimizer keeps only room,
        # score and inventory, so a "door now open" state cannot be shown with a fixture; the
        # keying rule is tested on a plain edge instead.)
        kn = core.Knowledge()
        kn.learn(play(list(NO_LAMP)), SEED, ["egg"])
        a: dict = {}
        core.update_archive(a, list(NO_LAMP), SEED)
        kitchen = next(k for k in a if k.startswith("Kitchen @"))
        blocked = core.expand_archive(a, kn, SEED, 200, {("kitchen", "east")})
        self.assertNotIn(("east"), {f["dir"] for f in blocked if f["from"] == kitchen})
        retried = core.expand_archive(a, kn, SEED, 200, {("kitchen", "east")}, per_level=True)
        self.assertIn((kitchen, "east"), {(f["from"], f["dir"]) for f in retried})
        self.assertEqual(kn.graph["Kitchen"]["east"], "Behind House")
        # the model's own tries are never retried by the harness
        kn.tried["kitchen"].add("east")
        again = core.expand_archive(a, kn, SEED, 200, set(), per_level=True)
        self.assertNotIn("east", {f["dir"] for f in again if f["from"] == kitchen})

    def test_the_give_up_pushback_can_be_the_games_score_line(self) -> None:
        import tempfile
        from pathlib import Path

        from zorkinator.harness import GameSpec, play_game
        from zorkinator.runner import ChatReply, JsonlSink, Usage
        from zorkinator.world import WorldModel

        class Scripted:
            model = "scripted"

            def __init__(self, replies: list[str]) -> None:
                self.replies, self.seen = replies, []

            def complete(self, messages, archive=None):
                self.seen.append(str(messages[-1]["content"]))
                text = self.replies.pop(0)
                return ChatReply(text, Usage(), [{"role": "assistant", "content": text}])

        chat = Scripted(["open mailbox", "I give up", "north", "I give up"])
        with tempfile.TemporaryDirectory() as out:
            r = play_game(
                GameSpec(
                    SEED,
                    10,
                    1.0,
                    world=WorldModel.empty("t"),
                    pushbacks=1,
                    pushback_score=True,
                ),
                chat=chat,
                sink=JsonlSink(out),
            ).record
            moves = (Path(out) / f"{r.run_id}.moves.jsonl").read_text().splitlines()
        self.assertEqual(len(moves), 3)  # open mailbox, north, the final give-up
        self.assertTrue(any("Your score is" in m and "Don't give up" not in m for m in chat.seen))
        self.assertIn("in 1 move", "".join(chat.seen))  # peeked: no time passed for `score`


class X18(unittest.TestCase):
    def test_deposit_text_names_what_never_scored_from_the_ledger(self) -> None:
        held = [
            "a painting",
            "a sword",
            "a brass lantern (providing light)",
            "a jewel-encrusted egg",
        ]
        t = core.deposit_text("painting", held, {"painting", "egg"})
        self.assertIn("holding the painting", t)
        self.assertIn("Also scored when taken: a jewel-encrusted egg", t)
        self.assertIn("Taking these never scored: a sword, a brass lantern (providing light)", t)
        self.assertNotIn("sword", core.deposit_text("painting"))  # x15-x17 text unchanged
        self.assertNotIn(
            "never scored", core.deposit_text("painting", ["a painting"], {"painting"})
        )

    def test_expansion_skips_exits_already_on_the_map(self) -> None:
        kn = core.Knowledge()
        kn.learn(play(list(NO_LAMP)), SEED, ["egg"])
        a: dict = {}
        core.update_archive(a, list(NO_LAMP), SEED)
        kitchen = next(k for k in a if k.startswith("Kitchen @"))
        kn.graph.setdefault("Kitchen", {})["east"] = "Behind House"  # as if walked
        found = core.expand_archive(a, kn, SEED, 200, set(), per_level=True, known=False)
        self.assertNotIn((kitchen, "east"), {(f["from"], f["dir"]) for f in found})
        found = core.expand_archive(a, kn, SEED, 200, set(), per_level=True, known=True)
        self.assertIn((kitchen, "east"), {(f["from"], f["dir"]) for f in found})

    def test_x18_chain_runs_end_to_end(self) -> None:
        import shutil
        from pathlib import Path

        cfg = replace(
            core.CONFIGS["x18"], player="explorer", games=3, move_cap=40, expand_budget=20
        )
        real = core.ask
        tag = "test-offline-x18"
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), 3)


class X19(unittest.TestCase):
    """Give-ups are not honored while the game runs; scoring starts hold plan-named things;
    the inventory peek never reads the game's event text as an item."""

    DEATH_PEEK = (
        "You are carrying:\n  A brass lantern (providing light)\nThe flat of the troll's axe "
        "hits you delicately on the head, knocking you out.\nConquering his fears, the troll "
        "puts you to death.\nIt appears that that last blow was too much for you. I'm afraid "
        "you are dead.\n \n   ****  You have died  **** \n\nNow, let's take a look here..."
    )

    def test_inventory_reads_only_the_block_under_the_header(self) -> None:
        # the exact peek text from x17 s0 g2 (move 43, unarmed in the guard room)
        self.assertEqual(
            core.parse_inventory(self.DEATH_PEEK), {"a brass lantern (providing light)"}
        )
        nested = (
            "You are carrying:\n  A glass bottle\n  The glass bottle contains:\n"
            "    A quantity of water\n  A lunch\n"
        )
        self.assertEqual(
            core.parse_inventory(nested),
            {"a glass bottle", "the glass bottle contains:", "a quantity of water", "a lunch"},
        )
        self.assertEqual(core.parse_inventory("You are empty-handed.\n"), frozenset())
        # engine check: the real peek agrees with the parser on a plain state
        from zorkinator.adapter import GameAdapter

        with GameAdapter() as g:
            g.reset(SEED)
            for c in NO_LAMP:
                g.step(c)
            self.assertTrue(core.holds(core.inventory(g), "egg"))

    def test_a_phantom_item_no_longer_dominates_the_goal_room_cell(self) -> None:
        # x17's archive after g2, reduced: the goal-room cell held the light only; the guard-room
        # cell "held" the light plus the death banner and was picked as the scoring start
        a = {
            "goal @50": {
                "key": "goal @50",
                "room": "goal",
                "score": 50,
                "inv": ["a light"],
                "traj": ["a"] * 36,
            },
            "guard @50": {
                "key": "guard @50",
                "room": "guard",
                "score": 50,
                "inv": ["a light", "**** you have died ****"],
                "traj": ["a"] * 44,
            },
        }
        self.assertEqual(core.best_spot(a)["key"], "guard @50")  # the old outcome, for the record
        a["guard @50"]["inv"] = sorted(core.parse_inventory(self.DEATH_PEEK))
        self.assertEqual(core.best_spot(a)["key"], "goal @50")

    def test_scoring_start_prefers_a_cell_holding_what_the_plans_named(self) -> None:
        armed = [*TO_CELLAR[:10], "take sword", *TO_CELLAR[10:]]  # Cellar @40 with the sword
        unarmed_deeper = [*TO_CELLAR, "south"]  # East of Chasm @40, no sword
        a: dict = {}
        core.update_archive(a, unarmed_deeper, SEED)
        top = core.best_spot(a)
        self.assertFalse(core.holds(top["inv"], "sword"))
        # no top cell holds the sword: the highest-score lit holder is the start instead
        core.update_archive(a, armed[:12], SEED)  # Living Room @15 holding the sword
        pick = core.best_spot(a, {"sword"})
        self.assertTrue(core.holds(pick["inv"], "sword"))
        self.assertEqual(pick["room"], "Living Room")
        self.assertEqual(core.best_spot(a)["score"], 40)  # without `needed`: unchanged
        # a top cell holding it wins over the deeper unarmed one
        core.update_archive(a, armed, SEED)
        pick = core.best_spot(a, {"sword"})
        self.assertEqual((pick["room"], pick["score"]), ("Cellar", 40))
        # a needed thing no cell holds is ignored
        self.assertEqual(core.best_spot(a, {"unicorn"})["key"], core.best_spot(a)["key"])

    def test_needed_items_come_from_the_chains_own_plans(self) -> None:
        w = core.Walls(core.CONFIGS["x19"])
        w.plans["troll room"].append(
            {
                "need": "a weapon",
                "get": [{"item": "sword", "at": "Living Room"}],
                "result": "unreachable",
            }
        )
        w.plans["__plateau__"].append({"need": "(no valid plan)", "get": [], "result": "no-plan"})
        self.assertEqual(core.needed_items(w), {"sword"})
        self.assertEqual(core.needed_items(core.Walls(core.CONFIGS["x19"])), frozenset())

    def test_the_prefix_can_omit_the_give_up_sentence(self) -> None:
        from zorkinator import builder
        from zorkinator.world import WorldModel

        w = WorldModel.empty("t")
        self.assertIn("I give up", builder.build_prefix(w))
        self.assertNotIn("give up", builder.build_prefix(w, giveup_sentence=False))
        self.assertIn("text adventure game Zork", builder.build_prefix(w, giveup_sentence=False))

    def test_a_give_up_is_a_no_op_while_the_game_runs(self) -> None:
        import tempfile
        from pathlib import Path

        from zorkinator.harness import GameSpec, play_game
        from zorkinator.runner import ChatReply, JsonlSink, Usage
        from zorkinator.world import WorldModel

        class Scripted:
            model = "scripted"

            def __init__(self, replies: list[str]) -> None:
                self.replies = replies

            def complete(self, messages, archive=None):
                text = self.replies.pop(0)
                return ChatReply(text, Usage(), [{"role": "assistant", "content": text}])

        chat = Scripted(["I give up", "north", "I give up", "I give up", "east", "I give up"] * 2)
        with tempfile.TemporaryDirectory() as out:
            r = play_game(
                GameSpec(SEED, 10, 1.0, world=WorldModel.empty("t"), giveup_honored=False),
                chat=chat,
                sink=JsonlSink(out),
            ).record
            moves = [
                json.loads(x)
                for x in (Path(out) / f"{r.run_id}.moves.jsonl").read_text().splitlines()
            ]
        self.assertEqual(r.end_reason, "cap")
        self.assertEqual(
            [m["command"] for m in moves][:6],
            ["I give up", "north", "I give up", "I give up", "east", "I give up"],
        )
        self.assertEqual(moves[4]["room"], "North of House")  # play went on after the give-ups
        # three in a row end the game
        chat = Scripted(["north", "I give up", "I give up", "I give up", "east"])
        with tempfile.TemporaryDirectory() as out:
            r = play_game(
                GameSpec(SEED, 10, 1.0, world=WorldModel.empty("t"), giveup_honored=False),
                chat=chat,
                sink=JsonlSink(out),
            ).record
        self.assertEqual((r.end_reason, r.moves), ("gave_up", 4))

    def test_x19_chain_runs_end_to_end(self) -> None:
        import shutil
        from pathlib import Path

        cfg = replace(
            core.CONFIGS["x19"], player="explorer", games=4, move_cap=40, expand_budget=20
        )
        real = core.ask
        tag = "test-offline-x19"
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), 4)
        self.assertIn("giveups", rows[-1])
        self.assertEqual(rows[3]["kind"], "scoring")


class X20(unittest.TestCase):
    """Expansion records the exits the game refused; explore games can start at one."""

    def test_expansion_records_refusals_with_the_games_reply(self) -> None:
        kn = core.Knowledge()
        kn.learn(play(list(NO_LAMP)), SEED, ["egg"])
        a: dict = {}
        core.update_archive(a, list(NO_LAMP), SEED)
        refusals: dict = {}
        core.expand_archive(a, kn, SEED, 200, set(), per_level=True, known=False, refusals=refusals)
        self.assertTrue(refusals)
        for (room, d), r in refusals.items():
            self.assertEqual(core.norm_room(r["room"]), room)
            self.assertEqual(r["dir"], d)
            self.assertIn(r["from"], a)
            self.assertTrue(r["reply"])
            self.assertNotIn(d, kn.graph.get(r["room"], {}))
        # the parser's generic phrase is found by counting, not by knowing it
        generic = core.generic_refusal(refusals)
        self.assertTrue(generic)
        self.assertGreaterEqual(
            sum(core._norm(r["reply"])[:60] == generic for r in refusals.values()), 3
        )
        # a refused exit the map has since passed is not offered again
        rng = random.Random(1)
        cfg = core.CONFIGS["x20"]
        first = core.pick_refusal(a, refusals, kn, rng, cfg)
        self.assertIsNotNone(first)
        spot, why = first
        self.assertEqual(why["mode"], "explore")
        self.assertEqual(
            spot["key"], refusals[(core.norm_room(why["refusal"][0]), why["refusal"][1])]["from"]
        )
        for (_room, d), r in list(refusals.items()):
            kn.graph.setdefault(r["room"], {})[d] = "Somewhere"
        self.assertIsNone(core.pick_refusal(a, refusals, kn, rng, cfg))

    def test_generic_refusal_needs_a_majority(self) -> None:
        r = {("a", "north"): {"reply": "no"}, ("b", "south"): {"reply": "locked"}}
        self.assertIsNone(core.generic_refusal(r))
        r = {(str(i), "north"): {"reply": "You can't go that way."} for i in range(4)}
        r[("z", "up")] = {"reply": "The door is locked."}
        self.assertEqual(core.generic_refusal(r), "you can't go that way.")

    def test_x20_chain_runs_end_to_end_and_logs_refusals(self) -> None:
        import shutil
        from pathlib import Path

        cfg = replace(
            core.CONFIGS["x20"],
            player="explorer",
            games=4,
            move_cap=40,
            expand_budget=30,
            refusal_share=1.0,
        )
        real = core.ask
        tag = "test-offline-x20"
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            rows = core.chain((cfg, tag, SEED))
            logged = (Path("runs/adrs") / tag / "s0" / "refused.jsonl").read_text().splitlines()
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), 4)
        self.assertGreater(rows[-1]["refusals"], 0)
        self.assertTrue(logged)
        self.assertTrue(any(r["why"].get("refusal") for r in rows if r["kind"] == "explore"))


class X20b(unittest.TestCase):
    def test_refusal_reply_is_its_first_sentence(self) -> None:
        self.assertEqual(
            core.first_sentence("You can't go that way. Someone carrying a large bag is here."),
            "You can't go that way.",
        )
        self.assertEqual(
            core.first_sentence("Are you out of your mind?"), "Are you out of your mind?"
        )
        self.assertEqual(core.first_sentence("  no  end "), "no end")

    def test_specific_refusals_are_offered_first(self) -> None:
        a = {
            f"r{i} @10": {
                "key": f"r{i} @10",
                "room": f"r{i}",
                "score": 10,
                "inv": [],
                "traj": ["a"],
            }
            for i in range(12)
        }
        refusals = {
            (f"r{i}", "north"): {
                "room": f"r{i}",
                "dir": "north",
                "from": f"r{i} @10",
                "reply": "You can't go that way.",
                "handoffs": 0,
            }
            for i in range(11)
        }
        refusals[("r11", "up")] = {
            "room": "r11",
            "dir": "up",
            "from": "r11 @10",
            "reply": "The hatch is bolted.",
            "handoffs": 0,
        }
        kn = core.Knowledge()
        cfg = replace(core.CONFIGS["x20b"], specific_first=1.0)
        picks = [
            core.pick_refusal(a, refusals, kn, random.Random(i), cfg)[1]["refusal"][2]
            for i in range(10)
        ]
        self.assertTrue(all(p == "The hatch is bolted." for p in picks))
        cfg = replace(core.CONFIGS["x20b"], specific_first=0.0)
        picks = [
            core.pick_refusal(a, refusals, kn, random.Random(i), cfg)[1]["refusal"][2]
            for i in range(10)
        ]
        self.assertGreater(sum(p != "The hatch is bolted." for p in picks), 5)


class X21(unittest.TestCase):
    def test_archive_stops_at_a_zero_point_deposit_that_removed_a_thing(self) -> None:
        cmds = [*NO_LAMP, "open case", "put egg in case", "take sword", "put sword in case", "east"]
        played = play(cmds)
        states = core.trace(cmds, SEED)
        cut = core.junk_deposit_index(played, states)
        self.assertEqual(cmds[cut], "put sword in case")
        self.assertEqual(played[cut][1]["score_delta"], 0)
        # a refused deposit removes nothing and is not a cut
        cmds2 = [*NO_LAMP, "put egg in case", "east"]
        self.assertIsNone(core.junk_deposit_index(play(cmds2), core.trace(cmds2, SEED)))
        a: dict = {}
        core.update_archive(a, cmds[:cut], SEED, [st for st in states if st["i"] < cut])
        self.assertNotIn("Kitchen @20", a)  # the walk after the junk deposit is not archived
        self.assertIn("Living Room @20", a)
        self.assertTrue(core.holds(a["Living Room @20"]["inv"], "sword"))

    def test_x21_chain_runs_end_to_end(self) -> None:
        import shutil
        from pathlib import Path

        cfg = replace(
            core.CONFIGS["x21"], player="explorer", games=4, move_cap=40, expand_budget=20
        )
        real = core.ask
        tag = "test-offline-x21"
        try:
            core.ask = lambda *x, **k: ({"facts_add": []}, 0.0)
            rows = core.chain((cfg, tag, SEED))
        finally:
            core.ask = real
            shutil.rmtree(Path("runs/adrs") / tag, ignore_errors=True)
        self.assertEqual(len(rows), 4)


class X22(unittest.TestCase):
    def test_expansion_retries_a_model_tried_exit_lighter(self) -> None:
        # the chain's route holds egg + lamp + others at the load-limited spot; the model tried
        # the direction loaded, so x16-x21 never retried it
        route = [*TO_CELLAR, "south", "east", "take painting", "north"]
        kn = core.Knowledge()
        kn.learn(play(route), SEED, ["egg", "painting"])
        a: dict = {}
        core.update_archive(a, route, SEED)
        studio = next(k for k in a if k.startswith("Studio @"))
        kn.tried["studio"].add("up")
        found = core.expand_archive(a, kn, SEED, 300, set(), per_level=True, known=False)
        self.assertNotIn((studio, "up"), {(f["from"], f["dir"]) for f in found})
        found = core.expand_archive(
            a, kn, SEED, 300, set(), per_level=True, known=False, model_tried=True
        )
        hit = [f for f in found if f["from"] == studio and f["dir"] == "up"]
        self.assertEqual(len(hit), 1)
        self.assertGreater(hit[0]["drops"], 0)
        self.assertEqual(kn.graph["Studio"]["up"], "Kitchen")


class X22b(unittest.TestCase):
    def test_scoring_start_is_never_dark_while_a_lit_top_cell_exists(self) -> None:
        a = {
            "(dark) @40": {
                "key": "(dark) @40",
                "room": "(dark)",
                "score": 40,
                "inv": ["a lamp"],
                "traj": ["a"] * 10,
            },
            "Cellar @40": {
                "key": "Cellar @40",
                "room": "Cellar",
                "score": 40,
                "inv": ["a lamp"],
                "traj": ["a"] * 30,
            },
            "Kitchen @15": {
                "key": "Kitchen @15",
                "room": "Kitchen",
                "score": 15,
                "inv": [],
                "traj": ["a"] * 5,
            },
        }
        self.assertEqual(core.best_spot(a)["key"], "Cellar @40")
        del a["Cellar @40"]
        self.assertEqual(core.best_spot(a)["key"], "Kitchen @15")  # a lower lit cell beats the dark
        del a["Kitchen @15"]
        self.assertEqual(core.best_spot(a)["key"], "(dark) @40")  # only when nothing lit exists
