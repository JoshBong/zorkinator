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


def load_atlas() -> tuple[list[RunRecord], dict[str, list[MoveRecord]]]:
    """Same shape as ``load``, read from Atlas (where harness chains are logged)."""
    from dotenv import load_dotenv

    from . import db

    load_dotenv()
    runs = db.get_runs()
    return runs, {run.run_id: db.get_moves(run.run_id) for run in runs}


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


def plot_learning_curve(
    runs: list[RunRecord],
    moves: dict[str, list[MoveRecord]],
    path: str | Path,
    *,
    baseline: str = "chats",
    baseline_chain: str = "long",
    harness: str = "harness",
) -> Path:
    """Score by game number: baseline chains as a band + mean, the long baseline chain, and the
    harness chain with its surprise rate. Regenerate as harness games land."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def by_game(name: str) -> dict[int, list[RunRecord]]:
        out: dict[int, list[RunRecord]] = defaultdict(list)
        for r in runs:
            if condition(r) == name and r.game_index is not None:
                out[r.game_index].append(r)
        return out

    fig, ax = plt.subplots(figsize=(10, 5.2), dpi=150)
    band = by_game(baseline)
    if band:
        xs = sorted(band)
        lo = [min(r.score for r in band[g]) for g in xs]
        hi = [max(r.score for r in band[g]) for g in xs]
        mean = [st.mean(r.score for r in band[g]) for g in xs]
        ax.fill_between(
            [x + 1 for x in xs],
            lo,
            hi,
            color="#9AA5B1",
            alpha=0.25,
            label="baseline, 10 chains (min-max)",
        )
        ax.plot([x + 1 for x in xs], mean, color="#5B6B7B", lw=2, label="baseline, 10-chain mean")
    long_chain = by_game(baseline_chain)
    if long_chain:
        xs = sorted(long_chain)
        ax.plot(
            [x + 1 for x in xs],
            [long_chain[g][0].score for g in xs],
            color="#2B3A4A",
            lw=1.6,
            marker="o",
            ms=3.5,
            label="baseline, one 25-game chain",
        )
    harness_games = by_game(harness)
    if harness_games:
        xs = sorted(harness_games)
        ax.plot(
            [x + 1 for x in xs],
            [harness_games[g][0].score for g in xs],
            color="#C27C1E",
            lw=2.4,
            marker="o",
            ms=5,
            label="harness chain",
        )
        rates = [surprise_rate(moves[harness_games[g][0].run_id]) for g in xs]
        if any(r is not None for r in rates):
            ax2 = ax.twinx()
            ax2.plot(
                [x + 1 for x in xs],
                [100 * (r or 0) for r in rates],
                color="#C27C1E",
                lw=1.4,
                ls="--",
                label="harness surprise rate",
            )
            ax2.set_ylabel("surprise rate (% of predictions wrong)", color="#C27C1E")
            ax2.set_ylim(0, 100)
            ax2.legend(loc="lower right", frameon=False)
    ax.set_xlabel("game number in chain")
    ax.set_ylabel("score (of 350)")
    ax.set_ylim(0, max(80, ax.get_ylim()[1]))
    ax.set_title("Same model (Claude Haiku 4.5), same seed: bare loop vs harness")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left", frameon=False)
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    return out


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
    parser.add_argument("--plot", metavar="PNG", help="write the learning-curve chart here")
    parser.add_argument("--atlas", action="store_true", help="read runs from Atlas, not --out")
    parser.add_argument("--harness", default="harness", help="harness condition/chain label")
    args = parser.parse_args()
    runs, moves = load_atlas() if args.atlas else load(args.out)
    print(report(runs, moves))
    if args.plot:
        print("wrote", plot_learning_curve(runs, moves, args.plot, harness=args.harness))
    if args.compare:
        print()
        print(compare(runs, *args.compare))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
