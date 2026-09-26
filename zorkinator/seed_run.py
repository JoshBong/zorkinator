"""Append one scripted game to an existing harness chain, then reflect on it.

    python -m zorkinator.seed_run --chain haiku-short-01 --moves 60

The scripted player types the first ``--moves`` commands of the Zork I walkthrough that Jericho
ships, through the real harness loop: the scribe logs rooms and facts, the run is persisted as
the chain's next game, and the Reflector learns from it exactly as from any game. The run's model
is recorded as ``scripted-walkthrough`` so it is never mistaken for model play. Stop the chain's
own process first; two writers on one chain collide on the game index.

Then resume the chain normally (``python -m zorkinator harness --chain <chain> --games N``) and
compare the next real games' actions with the ones before the seed.
"""

from __future__ import annotations

import argparse

from anthropic.types import MessageParam
from dotenv import load_dotenv
from jericho import FrotzEnv

from . import builder, db, verifier
from .adapter import DEFAULT_STORY_FILE
from .cli import MAX_CHILD_OPERATIONS
from .driver import (
    BetweenGameDriver,
    MongoRunRepository,
    ReflectionBudget,
    ReflectorProposalCreator,
)
from .harness import play_harness
from .memory import ChatArchive
from .reflector import AnthropicReflectionModel
from .runner import ChatReply, Usage
from .versions import VersionLimits, VersionManager

SCRIPTED_MODEL = "scripted-walkthrough"


class ScriptedChat:
    """A ``Chat`` that plays a fixed command list; no model, no cost."""

    model = SCRIPTED_MODEL

    def __init__(self, commands: list[str]) -> None:
        self._commands = list(commands)
        self._i = 0

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply:
        command = self._commands[self._i] if self._i < len(self._commands) else "look"
        self._i += 1
        text = f"{command}\nGoal: follow the route\nExpect: (scripted)"
        return ChatReply(text, Usage(), [{"role": "assistant", "content": text}])


def seed(chain: str, moves: int, reflect_model: str, reflect_usd: float) -> str:
    load_dotenv()
    db.ensure_indexes()
    store = db.MongoOuterLoopStore.from_env()
    try:
        store.ensure_indexes()
        run_repository = MongoRunRepository()
        proposals = ReflectorProposalCreator(
            store,
            AnthropicReflectionModel(reflect_model),
            manifest=lambda version: builder.resolve_manifest(version, repository=store)[0],
        )
        promoter = verifier.Promoter(
            db.get_moves, history=lambda: run_repository.get_chain_runs(chain)
        )
        limits = VersionLimits(max_operations=MAX_CHILD_OPERATIONS, max_memory_bytes=512 * 1024)
        versions = VersionManager(store, store, store, promoter, limits=limits)
        budget = ReflectionBudget(max_calls=1_000, max_usd=reflect_usd)
        driver = BetweenGameDriver(chain, store, run_repository, proposals, versions, budget=budget)

        cursor = driver.recover()
        version = store.get_version(cursor.version_id)
        if version is None:
            raise LookupError(cursor.version_id)
        walkthrough = FrotzEnv(str(DEFAULT_STORY_FILE)).get_walkthrough()[:moves]
        record = play_harness(
            0,
            moves,
            chat=ScriptedChat(walkthrough),
            sink=db.MongoSink(),
            usd_cap=1.0,
            version=version,
            repository=store,
            facts=db.upsert_world_fact,
            chain=chain,
            game_index=cursor.game_index,
            prompt="basic",
        )
        print(
            f"[{chain} game {cursor.game_index + 1}] scripted: score={record.score} "
            f"moves={record.moves} end={record.end_reason}",
            flush=True,
        )
        child = driver.advance(record.run_id)
        print(f"reflected: {cursor.version_id} -> {child}")
        return child
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="zorkinator.seed_run")
    parser.add_argument("--chain", required=True)
    parser.add_argument("--moves", type=int, default=60)
    parser.add_argument("--model", default="claude-haiku-4-5", help="reflection model")
    parser.add_argument("--reflect-usd", type=float, default=5.0)
    args = parser.parse_args()
    seed(args.chain, args.moves, args.model, args.reflect_usd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
