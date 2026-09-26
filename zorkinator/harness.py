"""Harness mode: one game, prompt rebuilt from the working KBs each move (docs/INNER_LOOP.md)."""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from . import builder, player, scribe
from .adapter import DEFAULT_STORY_FILE, GameAdapter
from .builder import PromptRepository
from .models import HarnessVersionRecord, MoveRecord, RunRecord, WorldFactDoc
from .monitor import STUCK_AFTER, Monitor
from .prompts import PromptName
from .runner import BASELINE_MODEL, MAX_SCORE, AnthropicChat, Chat, JsonlSink, Sink, Usage
from .world import WorldModel

EndReason = Literal["death", "won", "game_over", "gave_up", "cap", "usd_cap", "stuck40"]
FactWriter = Callable[[WorldFactDoc], None]


def play_harness(
    seed: int,
    move_cap: int,
    *,
    chat: Chat,
    sink: Sink,
    usd_cap: float = 15.0,
    story_file: str | Path = DEFAULT_STORY_FILE,
    version_id: str | None = None,
    version: HarnessVersionRecord | None = None,
    repository: PromptRepository | None = None,
    facts: FactWriter | None = None,
    stuck_after: int = STUCK_AFTER,
    chain: str | None = None,
    game_index: int | None = None,
    prompt: PromptName = "basic",
) -> RunRecord:
    """Play one harness game and log every move. ``facts`` receives world_facts upserts
    (e.g. ``db.upsert_world_fact``); without it they are dropped."""
    started_at = datetime.now(UTC)
    run_id = f"harness-{chat.model}-s{seed}-{started_at:%Y%m%dT%H%M%S%f}"
    if version is not None:
        if version_id is not None and version_id != version.version_id:
            raise ValueError("version_id does not match the exact version manifest")
        if repository is None:
            raise ValueError("an exact version manifest requires its repository")
        version_id = version.version_id
        version_context = builder.build_version_context(run_id, version, repository=repository)
    else:
        version_context = ""
    world = WorldModel.empty(run_id)  # TODO(Seb): WorldModel.load(run_id, version_id, store)
    monitor = Monitor(stuck_after=stuck_after)
    usage = Usage()
    moves = 0
    died = False
    death_move: int | None = None
    end_reason: EndReason = "cap"

    def flush() -> None:
        pending = world.drain_facts()
        if facts is not None:
            for fact in pending:
                facts(fact)

    with GameAdapter(story_file) as game:
        output = game.reset(seed)
        scribe.update(world, 0, None, output, scribe.parse_stub(output))
        flush()
        status = monitor.status

        for n in range(1, move_cap + 1):
            if usage.cost(chat.model) >= usd_cap:
                end_reason = "usd_cap"
                break

            move_prompt = builder.build_prompt(
                world,
                n,
                output,
                status,
                version_context=version_context,
                prompt=prompt,
            )
            t0 = time.monotonic()
            proposal = player.propose(chat, move_prompt)
            latency_ms = int((time.monotonic() - t0) * 1000)
            usage.add(proposal.usage)
            moves = n
            if proposal.goal:
                world.state.goal = proposal.goal

            if proposal.gave_up:
                sink.move(_move(world, n, "I give up", proposal.raw, "", 0, False, latency_ms))
                end_reason = "gave_up"
                break

            room_before = world.state.room
            try:
                result = game.step(proposal.command)
            except ValueError as exc:
                output = f"[Not allowed: {exc}]"
                sink.move(
                    _move(world, n, proposal.command, proposal.raw, output, 0, False, latency_ms)
                )
                continue

            output = result["text"]
            delta = result["score"] - world.state.score
            world.state.score, world.state.moves = result["score"], result["moves"]
            parsed = scribe.parse_stub(output, delta)
            obs = scribe.update(world, n, proposal.command, output, parsed)
            flush()
            status = monitor.update(world, n, obs)
            died = result["done"] and parsed.died
            sink.move(
                _move(
                    world,
                    n,
                    proposal.command,
                    proposal.raw,
                    output,
                    delta,
                    died,
                    latency_ms,
                    room_before,
                )
            )

            if result["done"]:
                if died:
                    end_reason, death_move = "death", n
                else:
                    end_reason = "won" if world.state.score >= MAX_SCORE else "game_over"
                break
            if status == "stuck":
                end_reason = "stuck40"
                break

    record = RunRecord(
        run_id=run_id,
        version_id=version_id,
        mode="harness",
        prompt=prompt,
        model=chat.model,
        seed=seed,
        move_cap=move_cap,
        score=world.state.score,
        moves=moves,
        died=died,
        death_move=death_move,
        end_reason=end_reason,
        tokens_in=usage.input,
        tokens_out=usage.output,
        tokens_cache_write=usage.cache_write,
        tokens_cache_read=usage.cache_read,
        cost_usd=float(usage.cost(chat.model)),
        started_at=started_at,
        ended_at=datetime.now(UTC),
        chain=chain,
        game_index=game_index,
    )
    sink.run(record)
    return record


def _move(
    world: WorldModel,
    n: int,
    command: str,
    raw: str,
    text: str,
    delta: int,
    died: bool,
    latency_ms: int,
    room: str | None = None,
) -> MoveRecord:
    return MoveRecord(
        run_id=world.run_id,
        n=n,
        room=room if room is not None else world.state.room,
        command=command,
        proposals=[raw],
        rejections=[],
        text=text,
        score=world.state.score,
        score_delta=delta,
        died=died,
        latency_ms=latency_ms,
        ts=datetime.now(UTC),
    )


def main() -> int:
    """One harness game from the terminal: python -m zorkinator.harness --seed 0 --moves 20"""
    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(prog="zorkinator.harness")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--moves", type=int, default=20, help="move cap (smoke test: 20; full: 500)"
    )
    parser.add_argument("--model", default=BASELINE_MODEL)
    parser.add_argument("--usd-cap", type=float, default=2.0)
    parser.add_argument("--out", default="runs")
    args = parser.parse_args()

    load_dotenv()
    facts: list[WorldFactDoc] = []
    record = play_harness(
        args.seed,
        args.moves,
        chat=AnthropicChat(args.model),
        sink=JsonlSink(args.out),
        usd_cap=args.usd_cap,
        facts=facts.append,
    )
    print(record.model_dump_json(indent=2))
    print(f"{len(facts)} world facts; moves in {args.out}/{record.run_id}.moves.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
