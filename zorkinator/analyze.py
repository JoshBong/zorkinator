"""Results tables from the run logs: scores by condition and by game in a chain, endings, deaths,
surprise rate, and a permutation test between two conditions.

    python -m zorkinator.analyze                         # every condition in runs/runs.jsonl
    python -m zorkinator.analyze --compare long harness  # plus a test between two conditions

A condition is the chain label (``long``, ``chats``, ``nomem``, ...), or the mode when a run has
no chain.
"""

from __future__ import annotations

import argparse
import random
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

from .models import MoveRecord, RunRecord

DEATH_CAUSES = ("troll", "thief", "grue", "water", "cyclops", "bat")


def load(out: str | Path) -> tuple[list[RunRecord], dict[str, list[MoveRecord]]]:
    directory = Path(out)
    runs = [RunRecord.model_validate_json(line) for line in _lines(directory / "runs.jsonl")]
    moves = {
        run.run_id: [
            MoveRecord.model_validate_json(line)
            for line in _lines(directory / f"{run.run_id}.moves.jsonl")
        ]
        for run in runs
    }
    return runs, moves


def condition(run: RunRecord) -> str:
    if run.chain:
        return run.chain.split("-chain")[0]
    return run.mode


def death_cause(moves: list[MoveRecord]) -> str:
    last = moves[-1].text.casefold() if moves else ""
    return next((cause for cause in DEATH_CAUSES if cause in last), "other")


def surprise_rate(moves: list[MoveRecord]) -> float | None:
    judged = [m.surprise for m in moves if m.surprise is not None]
    return sum(judged) / len(judged) if judged else None


def permutation_p(a: list[int], b: list[int], trials: int = 20000, seed: int = 0) -> float:
    """One-sided p that mean(b) - mean(a) is at least the observed gap by chance."""
    observed = st.mean(b) - st.mean(a)
    pooled = a + b
    rng = random.Random(seed)
    hits = 0
    for _ in range(trials):
        rng.shuffle(pooled)
        hits += st.mean(pooled[len(a) :]) - st.mean(pooled[: len(a)]) >= observed
    return hits / trials


def report(runs: list[RunRecord], moves: dict[str, list[MoveRecord]]) -> str:
    groups: dict[str, list[RunRecord]] = defaultdict(list)
    for run in runs:
        groups[condition(run)].append(run)

    out = [
        "| Condition | Games | Mean | Median | SD | Max | Cost |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, rs in sorted(groups.items()):
        scores = [r.score for r in rs]
        sd = st.stdev(scores) if len(scores) > 1 else 0.0
        cost = sum(r.cost_usd for r in rs)
        out.append(
            f"| {name} | {len(rs)} | {st.mean(scores):.1f} | {st.median(scores):g} | {sd:.1f} "
            f"| {max(scores)} | ${cost:.2f} |"
        )

    for name, rs in sorted(groups.items()):
        out += ["", f"### {name}"]
        by_game: dict[int, list[RunRecord]] = defaultdict(list)
        for r in rs:
            if r.game_index is not None:
                by_game[r.game_index].append(r)
        if by_game:
            out.append("| Game | " + " | ".join(str(g + 1) for g in sorted(by_game)) + " |")
            out.append("|---|" + "---|" * len(by_game))
            means = [f"{st.mean(r.score for r in by_game[g]):.1f}" for g in sorted(by_game)]
            out.append("| Mean score | " + " | ".join(means) + " |")
            rates = [
                _mean_or_dash([surprise_rate(moves[r.run_id]) for r in by_game[g]])
                for g in sorted(by_game)
            ]
            if any(rate != "-" for rate in rates):
                out.append("| Surprise rate | " + " | ".join(rates) + " |")
        endings = Counter(r.end_reason for r in rs)
        deaths = Counter(death_cause(moves[r.run_id]) for r in rs if r.end_reason == "death")
        out.append(f"Endings: {dict(endings.most_common())}")
        if deaths:
            out.append(f"Deaths: {dict(deaths.most_common())}")
    return "\n".join(out)


def compare(runs: list[RunRecord], base: str, other: str) -> str:
    a = [r.score for r in runs if condition(r) == base]
    b = [r.score for r in runs if condition(r) == other]
    if not a or not b:
        return f"Can't compare: {base} has {len(a)} games, {other} has {len(b)}."
    return (
        f"{other} vs {base}: {st.mean(b):.1f} vs {st.mean(a):.1f} "
        f"(gap {st.mean(b) - st.mean(a):+.1f}, one-sided permutation p={permutation_p(a, b):.3f}, "
        f"n={len(b)} vs {len(a)})"
    )


def _lines(path: Path) -> list[str]:
    return (
        [line for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
    )


def _mean_or_dash(values: list[float | None]) -> str:
    known = [v for v in values if v is not None]
    return f"{st.mean(known):.0%}" if known else "-"


def main() -> int:
    parser = argparse.ArgumentParser(prog="zorkinator.analyze")
    parser.add_argument("--out", default="runs", help="directory with runs.jsonl")
    parser.add_argument("--compare", nargs=2, metavar=("BASE", "OTHER"))
    args = parser.parse_args()
    runs, moves = load(args.out)
    print(report(runs, moves))
    if args.compare:
        print()
        print(compare(runs, *args.compare))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
