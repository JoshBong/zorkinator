"""Command-line entry points for local game interaction."""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from .adapter import GameAdapter
from .runner import BASELINE_MODEL, AnthropicChat, JsonlSink, play, play_batch


def manual(seed: int) -> int:
    """Run a human-controlled Zork session in the terminal."""
    with GameAdapter() as game:
        print(game.reset(seed))
        while True:
            try:
                command = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0

            if command.casefold() in {"quit", "exit"}:
                return 0

            try:
                result = game.step(command)
            except ValueError as exc:
                print(f"Blocked: {exc}")
                continue

            print(result["text"])
            print(f"[score={result['score']} moves={result['moves']}]")
            if result["done"]:
                return 0


def baseline(seed: int, moves: int, prompt: str, model: str, usd_cap: float, out: str) -> int:
    """Run the paper's bare loop once and print the run summary."""
    load_dotenv()
    record = play(
        "paper",
        seed,
        moves,
        chat=AnthropicChat(model),
        sink=JsonlSink(out),
        prompt="advanced" if prompt == "advanced" else "basic",
        usd_cap=usd_cap,
    )
    print(record.model_dump_json(indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="zorkinator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    manual_parser = subparsers.add_parser("manual", help="play Zork in this terminal")
    manual_parser.add_argument("--seed", type=int, default=0)
    baseline_parser = subparsers.add_parser("baseline", help="paper-mode run (arXiv 2602.15867)")
    baseline_parser.add_argument("--seed", type=int, default=0)
    baseline_parser.add_argument("--moves", type=int, default=500, help="move cap (paper: 500)")
    baseline_parser.add_argument("--prompt", choices=["basic", "advanced"], default="basic")
    baseline_parser.add_argument("--model", default=BASELINE_MODEL)
    baseline_parser.add_argument("--usd-cap", type=float, default=15.0)
    baseline_parser.add_argument("--out", default="runs")
    baseline_parser.add_argument("--runs", type=int, default=1, help="number of games")
    baseline_parser.add_argument("--parallel", type=int, default=1, help="games per wave")
    baseline_parser.add_argument(
        "--chats", default=None, help="past-chat archive dir; omit for no memory between games"
    )
    baseline_parser.add_argument("--total-usd-cap", type=float, default=100.0)
    args = parser.parse_args()

    if args.command == "manual":
        return manual(args.seed)
    if args.command == "baseline":
        if args.runs == 1 and not args.chats:
            return baseline(args.seed, args.moves, args.prompt, args.model, args.usd_cap, args.out)
        load_dotenv()
        play_batch(
            args.runs,
            args.parallel,
            seed=args.seed,
            move_cap=args.moves,
            prompt=args.prompt,
            model=args.model,
            usd_cap=args.usd_cap,
            total_usd_cap=args.total_usd_cap,
            out=args.out,
            chats=args.chats,
        )
        return 0
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
