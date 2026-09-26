"""Command-line entry points for local game interaction."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Literal

from dotenv import load_dotenv

from . import db
from .adapter import GameAdapter
from .driver import BetweenGameDriver, MongoRunRepository, ReflectorProposalCreator
from .models import RuleDoc, RunRecord
from .reflector import AnthropicReflectionModel
from .runner import (
    BASELINE_MODEL,
    AnthropicChat,
    JsonlSink,
    play,
    play_chains,
    play_harness_chain,
)
from .versions import VersionManager


class _SoftOnlyPromoter:
    """Safe integration default until the fixed replay verifier is wired."""

    def promote(self, rule: RuleDoc, runs: Sequence[RunRecord]) -> Literal["hard", "soft"]:
        return "soft"


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


def harness(
    chain: str,
    games: int,
    seed: int,
    moves: int,
    prompt: str,
    model: str,
    usd_cap: float,
) -> int:
    """Run or resume one Atlas-backed sequential harness chain."""
    load_dotenv()
    db.ensure_indexes()
    store = db.MongoOuterLoopStore.from_env()
    try:
        store.ensure_indexes()
        run_repository = MongoRunRepository()
        proposals = ReflectorProposalCreator(store, AnthropicReflectionModel(model))
        versions = VersionManager(store, store, store, _SoftOnlyPromoter())
        driver = BetweenGameDriver(chain, store, run_repository, proposals, versions)
        cursor = play_harness_chain(
            games,
            driver=driver,
            repository=store,
            chat=AnthropicChat(model),
            sink=db.MongoSink(),
            seed=seed,
            move_cap=moves,
            prompt="advanced" if prompt == "advanced" else "basic",
            usd_cap=usd_cap,
            facts=db.upsert_world_fact,
        )
        print(
            f"Harness chain {chain!r}: completed {cursor.game_index}/{games} games; "
            f"next version {cursor.version_id}"
        )
    finally:
        store.close()
        db.close()
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
    baseline_parser.add_argument("--chains", type=int, default=1, help="parallel chains")
    baseline_parser.add_argument("--games", type=int, default=1, help="games per chain, in order")
    baseline_parser.add_argument(
        "--chats", default=None, help="past-chat archive dir; omit for no memory between games"
    )
    baseline_parser.add_argument("--label", default=None, help="chain name prefix in run records")
    baseline_parser.add_argument("--total-usd-cap", type=float, default=100.0)
    harness_parser = subparsers.add_parser("harness", help="Atlas-backed sequential harness chain")
    harness_parser.add_argument(
        "--chain", required=True, help="stable chain name (resumes by name)"
    )
    harness_parser.add_argument("--games", type=int, default=2, help="total games in the chain")
    harness_parser.add_argument("--seed", type=int, default=0)
    harness_parser.add_argument("--moves", type=int, default=20)
    harness_parser.add_argument("--prompt", choices=["basic", "advanced"], default="basic")
    harness_parser.add_argument("--model", default=BASELINE_MODEL)
    harness_parser.add_argument("--usd-cap", type=float, default=5.0)
    args = parser.parse_args()

    if args.command == "manual":
        return manual(args.seed)
    if args.command == "baseline":
        if args.chains == 1 and args.games == 1 and not args.chats:
            return baseline(args.seed, args.moves, args.prompt, args.model, args.usd_cap, args.out)
        load_dotenv()
        play_chains(
            args.chains,
            args.games,
            seed=args.seed,
            move_cap=args.moves,
            prompt=args.prompt,
            model=args.model,
            usd_cap=args.usd_cap,
            total_usd_cap=args.total_usd_cap,
            out=args.out,
            chats=args.chats,
            label=args.label or ("chats" if args.chats else "nomem"),
        )
        return 0
    if args.command == "harness":
        return harness(
            args.chain,
            args.games,
            args.seed,
            args.moves,
            args.prompt,
            args.model,
            args.usd_cap,
        )
    parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
