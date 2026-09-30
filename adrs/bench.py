"""Puzzle bench: run the harness from saved game states, one puzzle at a time.

Development tool, not an evaluation. Each checkpoint is the state a player would be in right before
a puzzle, produced by replaying a prefix of Jericho's gold walkthrough on seed 0 (the harness never
sees those commands, only the resulting state -- like loading a save). From there the harness plays
with a small move budget and a checker reads the outcome from the player's own moves.

python -m adrs.bench --config x14b --tag bench1 [--puzzles troll dam] [--attempts 3]
                     [--state runs/adrs/x14b/s0/state-g15.json]
python -m adrs.bench --selftest   # prefix + reference solution through every checker ($0)
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from adrs import core

SEED = 0


@dataclass(frozen=True)
class Puzzle:
    name: str
    prefix: int  # walkthrough commands replayed before handoff
    solution: int  # walkthrough index just past the reference solution (self-test only)
    budget: int  # model moves allowed
    solved: Callable[[list[dict], list[dict]], bool]  # (model moves, engine states) -> bool
    what: str
    steps: tuple[str, ...] = ()  # reference solution when the walkthrough doesn't solve it here
    pre: tuple[str, ...] = ()  # extra commands after the walkthrough prefix, before handoff
    frontier: str = ""  # handoff text (default: the explore text)


def _reached(room: str):
    return lambda moves, states: any(
        core.norm_room(s["room"]) == core.norm_room(room) for s in states
    )


def _holds(item: str):
    return lambda moves, states: any(core.holds(s["inv"], item) for s in states)


def _said(pattern: str):
    rx = re.compile(pattern, re.I)
    return lambda moves, states: any(rx.search(m.get("text") or "") for m in moves)


def _deposit(item: str):
    """A scoring put-into-the-case of the item, in the player's own moves."""
    return lambda moves, states: any(
        item in m["command"].casefold()
        and re.search(r" in (the )?(trophy )?case\b", m["command"].casefold())
        and (m.get("score_delta") or 0) > 0
        for m in moves
    )


def _survives(n: int):
    return lambda moves, states: len(moves) >= n and not any(m.get("died") for m in moves)


PUZZLES: dict[str, Puzzle] = {
    p.name: p
    for p in [
        Puzzle("troll", 26, 30, 40, _reached("East-West Passage"), "armed, in the Cellar"),
        Puzzle(
            "carry_home",
            88,
            88,
            60,
            _deposit("painting"),
            "Gallery, holding the painting",
            ("W", "N", "U", "put painting in case"),
        ),
        # x15's deposit-mode handoff: in the goal room holding a treasure, told what the chain
        # tells the player; does it type the deposit? (x14b: 0/35 non-egg deposit tries scored)
        Puzzle(
            "deposit",
            88,
            88,
            12,
            _deposit("painting"),
            "Living Room, holding the painting",
            ("put painting in case",),
            ("W", "N", "U"),
            core.deposit_text("painting"),
        ),
        Puzzle("loud_room", 94, 96, 30, _holds("bar"), "Loud Room"),
        Puzzle("dam", 157, 166, 80, _said(r"sluice gates open"), "Dam Lobby"),
        Puzzle(
            "hades",
            109,
            118,
            60,
            _reached("Land of the Dead"),
            "Entrance to Hades, with bell/book/candles",
        ),
        Puzzle("maze", 132, 142, 120, _reached("Cyclops Room"), "Troll Room, maze to the west"),
        Puzzle("cyclops", 142, 144, 30, _reached("Strange Passage"), "Cyclops Room"),
        Puzzle("rainbow", 240, 244, 30, _holds("pot"), "Aragain Falls, holding the sceptre"),
        Puzzle("thief", 260, 269, 60, _holds("chalice"), "Treasure Room, thief present"),
        Puzzle("coal_mine", 305, 348, 150, _holds("diamond"), "Shaft Room, with torch/screwdriver"),
        Puzzle("lamp", 25, 25, 150, _survives(150), "Cellar, lamp lit: manage the light"),
    ]
}


def walkthrough() -> list[str]:
    from zorkinator.adapter import GameAdapter

    with GameAdapter() as g:
        g.reset(SEED)
        return list(g._env.get_walkthrough())


def selftest() -> dict[str, bool]:
    """Prefix + the reference solution must satisfy every checker (engine only, $0)."""
    from zorkinator.adapter import GameAdapter
    from zorkinator.scribe import parse_stub

    wt = walkthrough()
    out = {}
    for p in PUZZLES.values():
        sol = list(p.steps) or wt[p.prefix : p.solution]
        moves, states, last = [], [], 0
        with GameAdapter() as g:
            room = parse_stub(g.reset(SEED)).room
            for c in [*wt[: p.prefix], *p.pre]:
                r = g.step(c)
                last = r["score"]
                room = parse_stub(r["text"]).room or room
            alive = True
            for c in sol:
                r = g.step(c)
                ps = parse_stub(r["text"])
                room = ps.room or room
                moves.append(
                    {
                        "command": c,
                        "text": r["text"],
                        "score": r["score"],
                        "score_delta": r["score"] - last,
                        "died": ps.died,
                    }
                )
                states.append({"room": room, "inv": core.inventory(g), "score": r["score"]})
                last = r["score"]
                if ps.died or r["done"]:
                    alive = False
                    break
        out[p.name] = bool(alive and (p.solved(moves, states) if sol else True))
    return out


def attempt(p: Puzzle, cfg: core.Config, tag: str, state: dict, k: int) -> dict:
    """One harness attempt at a puzzle: replay the prefix as a route, hand over, play `budget`
    moves, grade from the player's moves. Mirrors one chain game (same Speedrun, same prompts)."""
    from dotenv import load_dotenv

    load_dotenv(".env")
    from zorkinator import builder
    from zorkinator.harness import GameSpec, Trace, make_chat, play_game
    from zorkinator.runner import JsonlSink
    from zorkinator.world import WorldModel

    builder.ABLATE = frozenset(cfg.ablate)
    out = Path("runs/bench") / tag / p.name
    out.mkdir(parents=True, exist_ok=True)
    route = [*walkthrough()[: p.prefix], *p.pre]
    if cfg.doom_waits:
        route = core.calm_prefix(route, SEED, cfg.doom_waits)
    ref = core.reference(route, SEED)
    parts = ([core.MANUAL] if cfg.manual else []) + (
        [
            "Facts from your earlier games (each seen in the game's own output):\n"
            + "\n".join(f"- [{f['where']}] {f['text']}" for f in state.get("facts", []))
        ]
        if state.get("facts")
        else []
    )
    frontier = p.frontier or (
        "You are at a spot chosen for exploring: try what no earlier game has tried."
    )
    sr = core.Speedrun(
        make_chat(cfg.player, core.MODEL, SEED), route, ref, frontier, cfg.repeat_block
    )
    sr.signals = list(state.get("signals", [])) if cfg.signals else []
    result = play_game(
        GameSpec(
            SEED,
            len(route) + p.budget,
            cfg.usd_cap,
            world=WorldModel.empty(tag),
            stuck_after=10_000,
            context="\n\n".join(parts),
            router=sr.hook,
            intercept=sr.intercept if cfg.repeat_block else None,
            pushbacks=cfg.giveup_pushbacks,
        ),
        chat=sr,
        sink=JsonlSink(out),
        trace=Trace(out / f"a{k}.trace.md"),
    )
    r = result.record
    with (out / f"{r.run_id}.moves.jsonl").open() as fh:
        moves = [json.loads(line) for line in fh]
    played = [m["command"] for m in moves if m["command"] != "I give up"]
    states = core.trace(played, SEED)
    model = moves[sr.i :]
    died = next((m["n"] for m in model if m.get("died")), None)
    row = {
        "puzzle": p.name,
        "attempt": k,
        "solved": bool(p.solved(model, states[sr.i :])),
        "model_moves": len(model),
        "died_at": died,
        "score": r.score,
        "cost": round(r.cost_usd, 4),
        "last": [m["command"] for m in model[-5:]],
    }
    with (Path("runs/bench") / tag / "results.jsonl").open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--config", default="x14b", choices=sorted(core.CONFIGS))
    ap.add_argument("--tag", default="bench")
    ap.add_argument("--puzzles", nargs="*", default=list(PUZZLES))
    ap.add_argument("--attempts", type=int, default=2)
    ap.add_argument("--state", help="a chain's state-gN.json to play with its notebook")
    ap.add_argument("--player", default=None, help='"explorer" for an offline dry run')
    a = ap.parse_args()
    if a.selftest:
        res = selftest()
        print(json.dumps(res, indent=1))
        raise SystemExit(0 if all(res.values()) else 1)
    cfg = core.CONFIGS[a.config]
    if a.player:
        cfg = replace(cfg, player=a.player)
    state = json.loads(Path(a.state).read_text()) if a.state else {"facts": [], "signals": []}
    (Path("runs/bench") / a.tag).mkdir(parents=True, exist_ok=True)
    (Path("runs/bench") / a.tag / "config.json").write_text(json.dumps(asdict(cfg), indent=1))
    for name in a.puzzles:
        p = PUZZLES[name]
        for k in range(a.attempts):
            row = attempt(p, cfg, a.tag, state, k)
            print(
                f"[{a.tag}] {p.name} a{k}: {'SOLVED' if row['solved'] else 'no'} in "
                f"{row['model_moves']} moves, died_at {row['died_at']}, score {row['score']}, "
                f"last {row['last']}",
                flush=True,
            )


if __name__ == "__main__":
    main()
