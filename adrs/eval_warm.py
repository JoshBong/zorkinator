"""ADRS utilization eval: games that START from a fixed prebuilt memory file (no Atlas, no
reflection). Measures whether the inner loop uses knowledge, separately from writing it.

python adrs/eval_warm.py --tag w1 --kb adrs/kb/s4-final.json --seeds 0 1 2 --reps 4
"""

from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dotenv import load_dotenv


def load_kb(path: str):
    from zorkinator.models import MemoryRevision

    raw = json.loads(Path(path).read_text())["memories"]
    keep = set(MemoryRevision.model_fields)
    out = []
    for m in raw:
        m = {k: v for k, v in m.items() if k in keep}
        if isinstance(m.get("created_at"), str):
            m["created_at"] = m["created_at"].replace(" ", "T") + (
                "" if "+" in m["created_at"] else "+00:00"
            )
        out.append(MemoryRevision.model_validate_json(json.dumps(m)))
    return out


def advisory(memories, loaded) -> str:
    rest = [
        {"kind": m.kind, "locations": m.locations, "content": m.content, "status": m.status}
        for m in memories
        if m.memory_id not in loaded
    ]
    if not rest:
        return ""
    return "Other notes from earlier games (may be wrong):\n" + json.dumps(
        rest, separators=(",", ":")
    )


def one(job):
    tag, kb, seed, rep, moves = job
    load_dotenv(".env")
    from zorkinator.harness import GameSpec, Trace, make_chat, play_game
    from zorkinator.runner import JsonlSink
    from zorkinator.world import WorldModel

    out = Path("runs/adrs") / tag
    err = ""
    for _ in range(3):
        try:
            memories = load_kb(kb) if kb else []
            world = WorldModel.empty("warm")
            loaded = world.load(memories) if memories else set()
            spec = GameSpec(
                seed,
                moves,
                2.0,
                world=world,
                stuck_after=10_000,
                context=advisory(memories, loaded),
            )
            r = play_game(
                spec,
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
                "cost": r.cost_usd,
            }
        except Exception as exc:
            err = repr(exc)[:300]
    return {"seed": seed, "rep": rep, "score": None, "error": err}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--kb", default="")
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--reps", type=int, default=4)
    p.add_argument("--moves", type=int, default=500)
    p.add_argument("--workers", type=int, default=5)
    a = p.parse_args()
    jobs = [(a.tag, a.kb, s, r, a.moves) for s in a.seeds for r in range(a.reps)]
    with ProcessPoolExecutor(a.workers) as ex:
        res = list(ex.map(one, jobs))
    sc = [r["score"] for r in res if r.get("score") is not None]
    summary = {
        "tag": a.tag,
        "kb": a.kb,
        "n": len(sc),
        "mean": round(statistics.mean(sc), 1) if sc else None,
        "sd": round(statistics.stdev(sc), 1) if len(sc) > 1 else None,
        "scores": sc,
        "cost": round(sum(r.get("cost") or 0 for r in res), 3),
        "games": res,
    }
    Path("runs/adrs", a.tag, "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: summary[k] for k in ("tag", "n", "mean", "sd", "scores", "cost")}))


if __name__ == "__main__":
    main()
