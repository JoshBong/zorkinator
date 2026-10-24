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
from itertools import pairwise
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


EXPLORE = """

EXPLORATION TASK: besides the edits above, the next game should also push into less-known
territory. Below are rooms that earlier games reached only rarely, with what the game showed there.
Pick ONE of them (or an unexplored direction or object seen there) and ADD 1-3 notes that tell the
player how to get there safely using the known route, and what to try there that no game has tried.
Keep the notes about the scoring route intact.

RARELY REACHED ROOMS:
{frontier}
"""


def frontier(moves_files: list[str], limit: int = 8) -> str:
    """Rooms reached in few of the archive's games, with their latest room text."""
    reached: dict[str, int] = {}
    text: dict[str, str] = {}
    for f in moves_files:
        with Path(f).open() as fh:
            rows = [json.loads(line) for line in fh]
        for r in {m.get("room") for m in rows if m.get("room")}:
            reached[r] = reached.get(r, 0) + 1
        # A move's text is the output of a command issued in m["room"]; the room it led to is
        # the next move's room, so that text is the arrival description.
        for m, nxt in pairwise(rows):
            if nxt.get("room") and nxt.get("room") != m.get("room") and m.get("text"):
                text.setdefault(nxt["room"], " ".join(m["text"].split())[:220])
    rare = sorted(reached.items(), key=lambda kv: kv[1])[:limit]
    return "\n".join(
        f"- {r} (reached in {c} of {len(moves_files)} games): {text.get(r, '')}" for r, c in rare
    )


def child(job):
    load_dotenv(".env")
    from zorkinator.models import RunRecord

    parent, game, extra = job
    record = RunRecord.model_validate_json(json.dumps(game["record"]))
    notes, cost = reflect(parent, record, Path(game["moves_file"]), extra)
    return notes, cost


def _rooms(moves_file: str) -> set[str]:
    with Path(moves_file).open() as fh:
        return {json.loads(line).get("room") for line in fh} - {None}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--gens", type=int, default=5)
    p.add_argument("--k", type=int, default=3)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--init", default="", help="starting notebook JSON (default: empty)")
    p.add_argument("--explore", type=int, default=0, help="explorer children per generation")
    p.add_argument("--novelty", type=float, default=0.0, help="fitness points per new room/game")
    a = p.parse_args()
    root = Path("runs/adrs") / a.tag
    root.mkdir(parents=True, exist_ok=True)
    archive = []  # {id, gen, parent, notes, games, mean, peak}
    spent = 0.0

    known: set[str] = set()  # rooms reached by any game of an earlier generation

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
            rooms = [_rooms(g["moves_file"]) for g in e["games"]]
            # Novelty is relative to earlier generations; the root has none to compare against.
            fresh = sc and known
            e["new_rooms"] = (
                round(statistics.mean(len(r - known) for r in rooms), 2) if fresh else 0
            )
            e["fitness"] = round(e["mean"] + a.novelty * e["new_rooms"], 2)
            spent += sum(g.get("cost", 0) for g in e["games"])
            archive.append(e)
            print(
                f"gen {e['gen']} {e['id']} (parent {e['parent']}): "
                f"mean {e['mean']} peak {e['peak']} new_rooms {e['new_rooms']} "
                f"fit {e['fitness']} scores {sc} notes {len(e['notes'])}",
                flush=True,
            )

    with ProcessPoolExecutor(a.workers) as pool:
        init = json.loads(Path(a.init).read_text()) if a.init else []
        evaluate(pool, [{"id": "g0-root", "gen": 0, "parent": None, "notes": init}])
        for gen in range(1, a.gens + 1):
            for e in archive:
                for g in e["games"]:
                    known.update(_rooms(g["moves_file"]))
            parent = max(archive, key=lambda e: (e["fitness"], e["peak"]))
            games = parent["games"][: a.k]
            all_files = [g["moves_file"] for e in archive for g in e["games"]]
            extra = EXPLORE.format(frontier=frontier(all_files))
            jobs = [
                (parent["notes"], g, extra if i >= a.k - a.explore else "")
                for i, g in enumerate(games)
            ]
            kids = list(pool.map(child, jobs))
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
    best = max(archive, key=lambda e: (e["fitness"], e["peak"]))
    (root / "best.json").write_text(json.dumps(best["notes"], indent=1))
    print(
        json.dumps(
            {"tag": a.tag, "best": best["id"], "best_mean": best["mean"], "spent": round(spent, 2)}
        )
    )


if __name__ == "__main__":
    main()
