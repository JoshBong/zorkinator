"""x4: Go-Explore for an LLM player. Pre-registration: zorkinator-vault/x4-preregistration.md.

Archive of spots (cells) keyed by room + inventory state (item state included), built
mechanically by replaying each game with save/restore inventory checks. Each spot keeps its best
trajectory (Go-Explore rule: higher score, or same score and shorter), minimized by replay into a
return route. Exploration games: pick a spot (few visits + open hypotheses there, weighted toward
progress), return to it mechanically, experiment. Stall (no new spot and no score gain for PATIENCE
exploration games) -> backtrack to earlier spots on the best path. Every SCORING_EVERY-th game is a
scoring game: best route + all facts, play for points.

python adrs/goexplore.py --tag x4 --seeds 0 1 2 --games 16
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from adrs.speedrun import MODEL, Speedrun, reflect, replay

SCORE_W = float(os.getenv("ZK_SCORE_W", "2.0"))
KEY_MODE = os.getenv("ZK_CELL_KEY", "room_score")  # "room_score_inv" for the H3 ablation
PATIENCE = 3
SCORING_EVERY = 4
SHOW_LOCAL, SHOW_GLOBAL = 5, 3


# --- mechanical state tracing ----------------------------------------------------------------
def _inventory(game) -> frozenset[str]:
    """Items held, with item state ("brass lantern (providing light)"). Event messages the game
    appends to the inventory output (a thief walking in, a glowing sword) are not items."""
    env = game._env
    state = env.get_state()
    text = game.step("inventory")["text"]
    env.set_state(state)
    # Items are the indented lines under "You are carrying:"; the game's event messages (a troll's
    # axe swinging past) are not indented. Nested contents are indented further and kept too.
    items = set()
    for line in text.splitlines():
        if line.startswith("  ") and line.strip():
            items.add(" ".join(line.split()).casefold())
    return frozenset(items)


def trace(cmds: list[str], seed: int) -> list[dict]:
    """State after every command (room, inventory incl. item state, score); stops at death."""
    from zorkinator.adapter import GameAdapter
    from zorkinator.scribe import parse_stub

    out = []
    with GameAdapter() as game:
        room = parse_stub(game.reset(seed)).room
        for i, cmd in enumerate(cmds):
            try:
                r = game.step(cmd)
            except ValueError:
                continue
            p = parse_stub(r["text"])
            if p.died or r["done"]:
                break
            room = "(dark)" if p.dark else (p.room or room)
            out.append({"i": i, "room": room, "inv": _inventory(game), "score": r["score"]})
    return out


def key_of(room: str, inv: frozenset[str], score: int) -> str:
    """Coarse cells (Go-Explore: room + level; Madotto: room text + reward). A full-inventory key
    made 223 cells from 3 games; inventory instead decides which trajectory a cell keeps."""
    if KEY_MODE == "room_score_inv":
        return f"{room} @{score} | {'; '.join(sorted(inv)) or 'empty-handed'}"
    return f"{room} @{score}"


def minimize_to(traj: list[str], seed: int, cell: dict) -> list[str]:
    """Shortest sub-sequence (chunk deletion) that still ends in this spot with >= its score."""

    def holds(r: list[str]) -> bool:
        t = trace(r, seed)
        if not t:
            return False
        e = t[-1]
        return (
            e["room"] == cell["room"] and cell_inv(cell) <= e["inv"] and e["score"] >= cell["score"]
        )

    route = list(traj)
    if not holds(route):
        return route
    for size in (16, 8, 4, 2, 1):
        i = 0
        while i < len(route):
            cand = route[:i] + route[i + size :]
            if cand and holds(cand):
                route = cand
            else:
                i += size
    return route


def cell_inv(cell: dict) -> frozenset[str]:
    return frozenset(cell["inv"])


# --- archive -----------------------------------------------------------------------------------
def update_archive(archive: dict, cmds: list[str], seed: int) -> int:
    new = 0
    seen_this_game = set()
    for st in trace(cmds, seed):
        k = key_of(st["room"], st["inv"], st["score"])
        traj = cmds[: st["i"] + 1]
        cur = archive.get(k)
        if cur is None:
            archive[k] = {
                "key": k,
                "room": st["room"],
                "inv": sorted(st["inv"]),
                "score": st["score"],
                "traj": traj,
                "route": None,
                "seen": 0,
                "chosen": 0,
            }
            new += 1
        elif (st["score"], len(st["inv"]), -len(traj)) > (
            cur["score"],
            len(cur["inv"]),
            -len(cur["traj"]),
        ):
            # Go-Explore rule (higher score, or same and shorter) with held items as the
            # tiebreak before length: a shorter path that dropped the lamp must not win.
            cur.update(score=st["score"], inv=sorted(st["inv"]), traj=traj, route=None, chosen=0)
        if k not in seen_this_game:
            archive[k]["seen"] += 1
            seen_this_game.add(k)
    return new


def weight(cell: dict, hyps: list[dict], max_score: float) -> tuple[float, dict]:
    open_here = sum(
        h["status"] == "open" and h["where"].casefold() == cell["room"].casefold() for h in hyps
    )
    explore = (
        1 / math.sqrt(1 + cell["chosen"])
        + 1 / math.sqrt(1 + cell["seen"])
        + 0.5 * min(open_here, 4) / 4
    )
    progress = cell["score"] / max_score if max_score else 0.0
    return explore * (1 + SCORE_W * progress), {
        "explore": round(explore, 3),
        "progress": round(progress, 3),
        "open_here": open_here,
    }


def best_cell(archive: dict) -> dict:
    """Highest score, then most items held, then shortest. Without the items tiebreak a dark
    lampless spot beat the lit one at the same score because its route skipped the lamp."""
    return max(archive.values(), key=lambda c: (c["score"], len(c["inv"]), -len(c["traj"])))


def route_for(cell: dict, seed: int) -> list[str]:
    if cell["route"] is None:
        cell["route"] = minimize_to(cell["traj"], seed, cell)
    return cell["route"]


# --- chain -------------------------------------------------------------------------------------
def chain(job) -> list[dict]:
    tag, seed, games = job
    load_dotenv(".env")
    from zorkinator.harness import GameSpec, Trace, make_chat, play_game
    from zorkinator.runner import JsonlSink
    from zorkinator.world import WorldModel

    rng = random.Random(seed)
    out = Path("runs/adrs") / tag / f"s{seed}"
    out.mkdir(parents=True, exist_ok=True)
    state = {"facts": [], "hyps": []}
    archive: dict = {}
    rows = []
    stall, best_seen, backtrack_k = 0, -1, 0
    for g in range(games):
        scoring = (g + 1) % SCORING_EVERY == 0 and archive
        max_score = max((c["score"] for c in archive.values()), default=0)
        pick, why = None, {}
        if archive and scoring:
            pick = best_cell(archive)
            why = {"mode": "scoring"}
        elif archive:
            pool = list(archive.values())
            if stall >= PATIENCE:
                # Grue-style backtracking: earlier spots on the best path, one step further back
                # each stalled game.
                best = best_cell(archive)
                on_path = [
                    c
                    for c in pool
                    if c["key"] != best["key"] and best["traj"][: len(c["traj"])] == c["traj"]
                ]
                on_path.sort(key=lambda c: len(c["traj"]))
                if on_path:
                    backtrack_k = min(backtrack_k + 1, len(on_path))
                    pool = on_path[-backtrack_k:]
                    why["mode"] = f"backtrack-{backtrack_k}"
            ws = [weight(c, state["hyps"], max_score) for c in pool]
            pick = rng.choices(pool, weights=[w for w, _ in ws])[0]
            why = {**why, **ws[pool.index(pick)][1], "mode": why.get("mode", "explore")}
            pick["chosen"] += 1
        route = route_for(pick, seed) if pick else []
        ref = replay(route, seed)["steps"] if route else []

        facts = (
            "Facts from earlier games (may be wrong; check them):\n"
            + "\n".join(f"- [{f['where']}] {f['text']}" for f in state["facts"])
            if state["facts"]
            else ""
        )
        if scoring:
            shown = []
            frontier = (
                "This is a scoring game: use everything you know to get as many points as possible."
            )
        else:
            here = pick["room"].casefold() if pick else ""
            open_h = [h for h in state["hyps"] if h["status"] == "open"]
            local = sorted(
                [h for h in open_h if h["where"].casefold() == here],
                key=lambda h: (-h["value"], h["attempts"]),
            )[:SHOW_LOCAL]
            rest = sorted(
                [h for h in open_h if h not in local], key=lambda h: (-h["value"], h["attempts"])
            )[:SHOW_GLOBAL]
            shown = local + rest
            for h in shown:
                h["attempts"] += 1
            frontier = (
                "You are at a spot chosen for exploring. Experiment: test these open "
                "hypotheses (from earlier games), starting with the ones here:\n"
                + "\n".join(
                    f"- [{h['where']}] {h['name']}: {h['hypothesis']} Try: {h['test']}"
                    for h in shown
                )
                if shown
                else "You are at a spot chosen for exploring: explore what "
                "no earlier game has tried."
            )
        result, sr = None, None
        for _ in range(3):
            try:
                sr = Speedrun(make_chat("model", MODEL, seed), route, ref, frontier)
                result = play_game(
                    GameSpec(
                        seed,
                        500,
                        2.0,
                        world=WorldModel.empty("x4"),
                        stuck_after=10_000,
                        context=facts,
                        router=sr.hook,
                    ),
                    chat=sr,
                    sink=JsonlSink(out),
                    trace=Trace(out / f"g{g}.trace.md"),
                )
                break
            except Exception as exc:
                print(f"[{tag} s{seed} g{g}] retry: {exc!r}"[:200], flush=True)
        if result is None:
            continue
        r = result.record
        mf = out / f"{r.run_id}.moves.jsonl"
        with mf.open() as fh:
            ms = [json.loads(line) for line in fh]
        cmds = [m["command"] for m in ms if m["command"] != "I give up"]
        new_cells = update_archive(archive, cmds, seed)
        peak = max((m.get("score", 0) for m in ms), default=0)
        best_now = max(c["score"] for c in archive.values())
        if not scoring:
            if new_cells == 0 and best_now <= best_seen:
                stall += 1
            else:
                stall, backtrack_k = 0, 0
        best_seen = max(best_seen, best_now)
        reached = bool(route) and sr.i >= len(route) and "did not go" not in sr.handoff
        cost = r.cost_usd
        try:
            state, verdicts, c1 = reflect(state, shown, r, mf, sr.i)
            cost += c1
        except Exception as exc:
            print(f"[{tag} s{seed} g{g}] reflect failed: {exc!r}"[:300], flush=True)
            verdicts = []
        (out / f"state-g{g}.json").write_text(json.dumps(state, indent=1))
        (out / f"archive-g{g}.json").write_text(json.dumps(archive, indent=0))
        rooms = {c["room"] for c in archive.values()}
        row = {
            "seed": seed,
            "game": g,
            "kind": "scoring" if scoring else "explore",
            "spot": pick["room"] if pick else None,
            "spot_score": pick["score"] if pick else 0,
            "why": why,
            "route_len": len(route),
            "reached_spot": reached,
            "score": r.score,
            "peak": peak,
            "moves": r.moves,
            "new_cells": new_cells,
            "cells": len(archive),
            "rooms_known": len(rooms),
            "best_score": best_now,
            "facts": len(state["facts"]),
            "resolved": sum(h["status"] in {"confirmed", "refuted"} for h in state["hyps"]),
            "verdicts": [v["status"] for v in verdicts],
            "stall": stall,
            "cost": cost,
        }
        rows.append(row)
        print(
            f"[{tag} s{seed}] g{g} {row['kind']}: spot {row['spot']}@{row['spot_score']} "
            f"({why.get('mode')}) route {len(route)} reached={reached} -> score {r.score} "
            f"peak {peak} | +{new_cells} cells ({len(archive)}), rooms {len(rooms)}, best "
            f"{best_now}, facts {row['facts']}, resolved {row['resolved']} {row['verdicts']} "
            f"stall {stall}",
            flush=True,
        )
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--games", type=int, default=16)
    a = p.parse_args()
    with ProcessPoolExecutor(len(a.seeds)) as ex:
        rows = [r for rs in ex.map(chain, [(a.tag, s, a.games) for s in a.seeds]) for r in rs]
    Path("runs/adrs", a.tag, "summary.json").write_text(
        json.dumps(
            {
                "tag": a.tag,
                "score_w": SCORE_W,
                "key": KEY_MODE,
                "cost": round(sum(r["cost"] for r in rows), 2),
                "rows": rows,
            },
            indent=1,
        )
    )
    print(json.dumps({"tag": a.tag, "cost": round(sum(r["cost"] for r in rows), 2)}))


if __name__ == "__main__":
    main()
