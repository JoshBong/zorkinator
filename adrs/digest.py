"""Per-game digest of an ADRS chain from its move logs (no model calls, no engine).

For each game: mode, route contents (drops/puts/presses replayed by the harness), where the harness
handed over, the model's moves after handoff (first visits for the chain, exact repeats, scoring
takes, deposit tries), death cause and move. Reads runs/adrs/<tag>/s<seed>/{rows.jsonl,
*.moves.jsonl, g<N>.trace.md}. Grades pre-registrations from traces, not the score line.

python -m adrs.digest x15 --seeds 0            # one line per game + chain totals
python -m adrs.digest x15 --seeds 0 --game 3   # full move list of one game after handoff
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from adrs.core import movement

DEATH = {
    "grue": r"grue",
    "troll": r"troll",
    "thief": r"thief|stiletto",
    "drown": r"drown|water",
    "fall": r"fall|plummet|cliff|chasm",
    "explode": r"explo|blast",
    "bat": r"bat ",
}
_HANDOFF = re.compile(r"The harness replayed your known route (?:\((\d+) moves\)|for (\d+) moves)")


def _handoff(trace_md: Path) -> int | None:
    if not trace_md.exists():
        return None
    m = _HANDOFF.search(trace_md.read_text())
    return int(m.group(1) or m.group(2)) if m else None


def death_cause(moves: list[dict], k: int) -> str:
    text = " ".join((m.get("text") or "") for m in moves[max(0, k - 1) : k + 1]).casefold()
    for name, rx in DEATH.items():
        if re.search(rx, text):
            return name
    return "other:" + " ".join(text.split())[-90:]


def load(tag: str, seed: int) -> tuple[list[dict], list[list[dict]]]:
    d = Path("runs/adrs") / tag / f"s{seed}"
    rows = [json.loads(x) for x in (d / "rows.jsonl").read_text().splitlines() if x.strip()]
    logs = []
    for f in sorted(d.glob("*.moves.jsonl")):
        logs.append([json.loads(x) for x in f.read_text().splitlines() if x.strip()])
    return rows, logs


def digest_game(g: int, row: dict, moves: list[dict], tag: str, seed: int, seen: set) -> dict:
    d = Path("runs/adrs") / tag / f"s{seed}"
    h = _handoff(d / f"g{g}.trace.md")
    if h is None:
        h = row.get("route_len", 0)
    route, model = moves[:h], moves[h:]
    route_cmds = [m["command"].casefold() for m in route]
    route_acts = [
        c
        for c in route_cmds
        if c.startswith(("drop", "put", "push", "press", "turn", "open", "take", "get", "kill"))
    ]
    first_visits, rooms_after = [], []
    for m in model:
        r = m.get("room") or ""
        if r and r not in seen:
            first_visits.append(r)
        if r:
            seen.add(r)
            rooms_after.append(r)
    for m in route:
        if m.get("room"):
            seen.add(m["room"])
    nonmove = [(m.get("room"), m["command"].casefold().strip()) for m in model]
    nonmove = [x for x in nonmove if not movement(x[1])]
    counts = Counter(nonmove)
    repeats = sum(v - 1 for v in counts.values())
    takes = [
        (m["n"], m.get("room"), m["command"])
        for m in model
        if m["command"].casefold().startswith(("take", "get", "pick up", "grab"))
        and (m.get("score_delta") or 0) > 0
    ]
    dep_tries = [
        (m["n"], m["command"], m.get("score_delta") or 0)
        for m in model
        if re.search(r" in(to)? (the )?(trophy )?case\b", m["command"].casefold())
    ]
    death = next((i for i, m in enumerate(moves) if m.get("died")), None)
    dead = None
    if death is not None:
        dead = {"n": moves[death]["n"], "by": "route" if death < h else "model"}
        dead["cause"] = death_cause(moves, death)
        dead["last"] = [m["command"] for m in moves[max(0, death - 3) : death + 1]]
    return {
        "g": g,
        "mode": row["why"].get("mode"),
        "why": {k: v for k, v in row["why"].items() if k != "mode"},
        "spot": row.get("spot"),
        "route_len": h,
        "route_acts": route_acts,
        "model_moves": len(model),
        "first_visits": first_visits,
        "repeats": repeats,
        "nonmove": len(nonmove),
        "scoring_takes": takes,
        "deposit_tries": dep_tries,
        "death": dead,
        "score": row["score"],
        "peak": row["peak"],
        "in_case": row.get("in_case"),
        "treasures": row.get("treasures"),
        "cost": row["cost"],
    }


def digest(tag: str, seed: int) -> list[dict]:
    rows, logs = load(tag, seed)
    seen: set = set()
    out = []
    for g, row in enumerate(rows):
        if g >= len(logs):
            break
        out.append(digest_game(g, row, logs[g], tag, seed, seen))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--seeds", type=int, nargs="+", default=[0])
    ap.add_argument("--game", type=int, default=None)
    a = ap.parse_args()
    for seed in a.seeds:
        _rows, logs = load(a.tag, seed)
        if a.game is not None:
            h = _handoff(Path("runs/adrs") / a.tag / f"s{seed}" / f"g{a.game}.trace.md") or 0
            for m in logs[a.game]:
                tagm = "R" if m["n"] <= h else "M"
                txt = " ".join((m.get("text") or "").split())[:110]
                print(
                    f"{tagm} {m['n']:3d} [{m.get('room')}] {m['command']!r:40s} "
                    f"{'+' + str(m['score_delta']) if m.get('score_delta') else ''} {txt}"
                )
            continue
        ds = digest(a.tag, seed)
        tot_cost = sum(d["cost"] for d in ds)
        print(f"== {a.tag} s{seed}: {len(ds)} games, ${tot_cost:.2f}")
        for d in ds:
            dead = d["death"]
            dtxt = (
                f"DIED@{dead['n']} {dead['by']} {dead['cause']} last={dead['last']}"
                if dead
                else "alive"
            )
            print(
                f"g{d['g']:2d} {d['mode']:8s} {str(d['why'])[:60]:60s} "
                f"spot={str(d['spot'])[:22]:22s} route={d['route_len']:3d} "
                f"acts={d['route_acts']} | model={d['model_moves']:3d} "
                f"first={d['first_visits']} rep={d['repeats']}/{d['nonmove']} "
                f"takes={d['scoring_takes']} dep={d['deposit_tries']} | {dtxt} | "
                f"score {d['score']}/{d['peak']} in_case={d['in_case']}"
            )
        deaths = Counter(d["death"]["cause"].split(":")[0] for d in ds if d["death"])
        print(
            f"   deaths: {dict(deaths)}; treasures={ds[-1]['treasures']} "
            f"in_case={ds[-1]['in_case']}"
        )


if __name__ == "__main__":
    main()
