"""ADRS inner-loop eval: N cold harness games (no memory), fixed seeds, parallel <=3.

python adrs/eval_cold.py --tag v1 --seeds 0 1 2 --reps 2
Writes runs/adrs/<tag>/summary.json. Termination matches the bare loop unless --stuck is set.
"""

from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dotenv import load_dotenv


def one(job: tuple[str, int, int, int, int]) -> dict[str, object]:
    tag, seed, rep, moves, stuck = job
    load_dotenv(".env")
    from zorkinator.harness import GameSpec, Trace, make_chat, play_game
    from zorkinator.runner import JsonlSink

    out = Path("runs/adrs") / tag
    chat = make_chat("model", "gpt-5.6-luna", seed)
    trace = Trace(out / f"s{seed}-r{rep}.trace.md")
    for _ in range(3):
        try:
            result = play_game(
                GameSpec(seed, moves, 2.0, stuck_after=stuck),
                chat=chat,
                sink=JsonlSink(out),
                facts=None,
                trace=trace,
            )
            r = result.record
            return {
                "seed": seed,
                "rep": rep,
                "score": r.score,
                "moves": r.moves,
                "end": r.end_reason,
                "cost": r.cost_usd,
            }
        except Exception as exc:  # rate limit / content filter: retry the game
            err = repr(exc)[:200]
            chat = make_chat("model", "gpt-5.6-luna", seed)
            trace = Trace(out / f"s{seed}-r{rep}.trace.md")
    return {"seed": seed, "rep": rep, "score": None, "error": err}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--reps", type=int, default=2)
    p.add_argument("--moves", type=int, default=500)
    p.add_argument("--stuck", type=int, default=10_000)
    p.add_argument("--workers", type=int, default=3)
    a = p.parse_args()
    jobs = [(a.tag, s, r, a.moves, a.stuck) for s in a.seeds for r in range(a.reps)]
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(one, jobs))
    scores = [r["score"] for r in res if r.get("score") is not None]
    summary = {
        "tag": a.tag,
        "n": len(scores),
        "mean": round(statistics.mean(scores), 1) if scores else None,
        "sd": round(statistics.stdev(scores), 1) if len(scores) > 1 else None,
        "scores": scores,
        "cost": round(sum(r.get("cost") or 0 for r in res), 3),
        "games": res,
    }
    Path("runs/adrs", a.tag, "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in ("tag", "n", "mean", "sd", "scores", "cost")}))


if __name__ == "__main__":
    main()
