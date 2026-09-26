"""Command-line entry points for local game interaction."""

from __future__ import annotations

import argparse

from .adapter import GameAdapter


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


def main() -> int:
    parser = argparse.ArgumentParser(prog="zorkinator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    manual_parser = subparsers.add_parser("manual", help="play Zork in this terminal")
    manual_parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    if args.command == "manual":
        return manual(args.seed)
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
