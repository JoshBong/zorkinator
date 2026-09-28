"""ADRS loop over notebooks: generate -> evaluate (multi-game) -> select.

Each generation the best notebook so far (by mean score over its eval games) spawns K children,
one reflection per transcript of the parent's eval games. Every child is evaluated on the dev
seeds. Archive = every notebook with its scores; nothing is discarded.

python adrs/evolve.py --tag e1 --gens 5 --k 3 --seeds 0 1 2
"""

from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

from adrs.chain_local import build_world, reflect


def play(job):
    tag, nid, notebook, seed = job
    load_dotenv(".env")
    from zorkinator.harness import GameSpec, Trace, make_chat, play_game
    from zorkinator.runner import JsonlSink

    out = Path("runs/adrs") / tag / nid
    out.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        try:
            world, ctx = build_world(notebook)
            r = play_game(
                GameSpec(seed, 500, 2.0, world=world, stuck_after=10_000, context=ctx),
                chat=make_chat("model", "gpt-5.6-luna", seed),
                sink=JsonlSink(out),
                trace=Trace(out / f"s{seed}.trace.md"),
            ).record
            moves = out / f"{r.run_id}.moves.jsonl"
            peak = max(json.loads(line).get("score", 0) for line in moves.open())
            return {
                "seed": seed,
                "score": r.score,
                "peak": peak,
                "end": r.end_reason,
                "cost": r.cost_usd,
                "moves_file": str(moves),
                "record": r.model_dump(mode="json"),
            }
        except Exception as exc:
            err = repr(exc)[:200]
    return {"seed": seed, "score": None, "error": err}


def child(job):
    load_dotenv(".env")
    from zorkinator.models import RunRecord

    parent, game = job
    record = RunRecord.model_validate_json(json.dumps(game["record"]))
    notes, cost = reflect(parent, record, Path(game["moves_file"]))
    return notes, cost


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--gens", type=int, default=5)
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--workers", type=int, default=6)
    a = p.parse_args()
    root = Path("runs/adrs") / a.tag
    root.mkdir(parents=True, exist_ok=True)
    archive = []  # {id, gen, parent, notes, games, mean, peak}
    spent = 0.0

    def evaluate(pool, entries):
        nonlocal spent
        jobs = [(a.tag, e["id"], e["notes"], s) for e in entries for s in a.seeds]
        res = list(pool.map(play, jobs))
        for e in entries:
            e["games"] = [
                r
                for r, j in zip(res, jobs, strict=True)
                if j[1] == e["id"] and r.get("score") is not None
            ]
            sc = [g["score"] for g in e["games"]]
            e["mean"] = round(statistics.mean(sc), 1) if sc else -1
            e["peak"] = round(statistics.mean(g["peak"] for g in e["games"]), 1) if sc else -1
            spent += sum(g.get("cost", 0) for g in e["games"])
            archive.append(e)
            print(
                f"gen {e['gen']} {e['id']} (parent {e['parent']}): "
                f"mean {e['mean']} peak {e['peak']} "
                f"scores {sc} notes {len(e['notes'])}",
                flush=True,
            )

    with ProcessPoolExecutor(a.workers) as pool:
        evaluate(pool, [{"id": "g0-root", "gen": 0, "parent": None, "notes": []}])
        for gen in range(1, a.gens + 1):
            parent = max(archive, key=lambda e: (e["mean"], e["peak"]))
            games = parent["games"][: a.k]
            kids = list(pool.map(child, [(parent["notes"], g) for g in games]))
            spent += sum(c for _, c in kids)
            entries = [
                {"id": f"g{gen}-c{i}", "gen": gen, "parent": parent["id"], "notes": notes}
                for i, (notes, _) in enumerate(kids)
            ]
            evaluate(pool, entries)
            (root / "archive.json").write_text(
                json.dumps(
                    [
                        {k: v for k, v in e.items() if k != "games"}
                        | {"scores": [g["score"] for g in e["games"]]}
                        for e in archive
                    ],
                    indent=1,
                )
            )
            print(
                f"== gen {gen} done, best so far {max(e['mean'] for e in archive)}, "
                f"spent ${spent:.2f}",
                flush=True,
            )
    best = max(archive, key=lambda e: (e["mean"], e["peak"]))
    (root / "best.json").write_text(json.dumps(best["notes"], indent=1))
    print(
        json.dumps(
            {"tag": a.tag, "best": best["id"], "best_mean": best["mean"], "spent": round(spent, 2)}
        )
    )


if __name__ == "__main__":
    main()
