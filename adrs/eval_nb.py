"""Utilization eval: play N games from a FIXED notebook (no reflection) under a delivery mode.

--route room  : room notes only in that room, anywhere-notes in the prefix (chain_local default)
--route all   : every note in the prefix, every move
--route none  : cold control (notebook ignored)
"""

from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dotenv import load_dotenv


def world_for(notebook, route):
    from adrs.chain_local import build_world
    from zorkinator.world import WorldModel

    if route in ("none", "router"):
        return WorldModel.empty("nb"), ""
    if route == "room":
        return build_world(notebook)
    ctx = "Notebook from earlier games (may be wrong; check it):\n" + "\n".join(
        f"- [{n['where']}] {n['text']}" for n in notebook
    )
    return WorldModel.empty("nb"), ctx


def one(job):
    tag, nb, route, seed, rep = job
    load_dotenv(".env")
    from zorkinator.harness import GameSpec, Trace, make_chat, play_game
    from zorkinator.runner import JsonlSink

    notebook = json.loads(Path(nb).read_text()) if nb else []
    out = Path("runs/adrs") / tag
    err = ""
    for _ in range(3):
        try:
            world, ctx = world_for(notebook, route)
            router = None
            if route == "router":
                from adrs.router import Router

                router = Router(notebook)
            r = play_game(
                GameSpec(
                    seed, 500, 2.0, world=world, stuck_after=10_000, context=ctx, router=router
                ),
                chat=make_chat("model", "gpt-5.6-luna", seed),
                sink=JsonlSink(out),
                trace=Trace(out / f"s{seed}-r{rep}.trace.md"),
            ).record
            return {
                "seed": seed,
                "rep": rep,
                "score": r.score,
                "moves": r.moves,
                "end": r.end_reason,
                "cost": r.cost_usd + (router.cost if router else 0),
                "router_calls": router.calls if router else 0,
            }
        except Exception as exc:
            err = repr(exc)[:300]
    return {"seed": seed, "rep": rep, "score": None, "error": err}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--nb", default="")
    p.add_argument("--route", choices=["room", "all", "none", "router"], default="room")
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--reps", type=int, default=4)
    p.add_argument("--workers", type=int, default=4)
    a = p.parse_args()
    jobs = [(a.tag, a.nb, a.route, s, r) for s in a.seeds for r in range(a.reps)]
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(one, jobs))
    sc = [r["score"] for r in res if r.get("score") is not None]
    s = {
        "tag": a.tag,
        "route": a.route,
        "nb": a.nb,
        "n": len(sc),
        "mean": round(statistics.mean(sc), 1),
        "sd": round(statistics.stdev(sc), 1),
        "scores": sc,
        "cost": round(sum(r.get("cost") or 0 for r in res), 3),
        "games": res,
    }
    Path("runs/adrs", a.tag, "summary.json").write_text(json.dumps(s, indent=2))
    print(json.dumps({k: s[k] for k in ("tag", "route", "n", "mean", "sd", "scores", "cost")}))


if __name__ == "__main__":
    main()
