"""Harness mode: one game, prompt rebuilt from the working KBs each move (docs/INNER_LOOP.md).

The outer loop talks to the inner loop only through ``GameSpec`` (in: seed, caps, version,
preloaded knowledge) and ``GameResult`` (out: run record + final knowledge). Gathering
knowledge during the game needs nothing from the outer loop; it comes from game text.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TextIO

from . import builder, player, scribe, verifier
from .adapter import DEFAULT_STORY_FILE, GameAdapter
from .builder import PromptRepository
from .models import HarnessVersionRecord, MoveRecord, RuleDoc, RunRecord, WorldFactDoc
from .monitor import STUCK_AFTER, Monitor
from .progress import ConsoleProgress
from .prompts import PromptName
from .runner import (
    BASELINE_MODEL,
    MAX_SCORE,
    OPENAI_DEV_MODEL,
    PRICES,
    AnthropicChat,
    Chat,
    JsonlSink,
    Sink,
    Usage,
)
from .world import WorldModel

EndReason = Literal["death", "won", "game_over", "gave_up", "cap", "usd_cap", "stuck40"]
FactWriter = Callable[[WorldFactDoc], None]
DEFAULT_TEST_MODEL = OPENAI_DEV_MODEL  # smoke tests; the benchmark model is BASELINE_MODEL


MAX_REJECTIONS = 2  # verifier rejections per move before the move is skipped


@dataclass
class GameSpec:
    """What the outer loop hands the inner loop for one game."""

    seed: int
    move_cap: int = 500
    usd_cap: float = 15.0
    version_id: str | None = None  # recorded on the RunRecord; the Reflector requires it
    world: WorldModel | None = None  # preloaded knowledge; None = run zero (empty KBs)
    stuck_after: int = STUCK_AFTER
    story_file: str | Path = DEFAULT_STORY_FILE
    chain: str | None = None
    game_index: int | None = None
    version: HarnessVersionRecord | None = None  # exact manifest; needs play_game(repository=)
    prompt: PromptName = "basic"


@dataclass
class GameResult:
    """What the inner loop hands back: the logged run and everything it learned."""

    record: RunRecord
    world: WorldModel


class Trace:
    """Human-readable per-move log: exactly what the model saw and said."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file: TextIO = self.path.open("w")

    def prefix(self, text: str) -> None:
        self._file.write(f"# Cached prefix\n\n```\n{text}\n```\n\n")

    def move(self, n: int, tail: str, reply: str, output: str) -> None:
        self._file.write(
            f"## Move {n}\n\n### Prompt tail\n```\n{tail}\n```\n"
            f"### Reply\n```\n{reply}\n```\n### Game\n```\n{output.strip()}\n```\n\n"
        )
        self._file.flush()

    def end(self, record: RunRecord, world: WorldModel) -> None:
        self._file.write(
            f"# End: {record.end_reason}, score {record.score}, {record.moves} moves\n\n"
            f"```json\n{json.dumps(world.summary(), indent=2)}\n```\n"
        )
        self._file.close()


def play_game(
    spec: GameSpec,
    *,
    chat: Chat,
    sink: Sink,
    repository: PromptRepository | None = None,
    facts: FactWriter | None = None,
    trace: Trace | None = None,
    progress: ConsoleProgress | None = None,
) -> GameResult:
    """Play one harness game, logging every move. ``facts`` receives world_facts upserts
    (e.g. ``db.upsert_world_fact``); without it they are dropped. ``repository`` is
    required when ``spec.version`` pins an exact version manifest."""
    started_at = datetime.now(UTC)
    run_id = f"harness-{chat.model}-s{spec.seed}-{started_at:%Y%m%dT%H%M%S%f}"
    version_id = spec.version_id
    if spec.version is not None:
        if version_id is not None and version_id != spec.version.version_id:
            raise ValueError("version_id does not match the exact version manifest")
        if repository is None:
            raise ValueError("an exact version manifest requires its repository")
        version_id = spec.version.version_id
        version_context = builder.build_version_context(run_id, spec.version, repository=repository)
        rules: list[RuleDoc] = repository.get_rules(list(spec.version.rule_ids))
    else:
        version_context = ""
        rules = []
    expected: str | None = None  # the Player's prediction for the previous move
    world = spec.world or WorldModel.empty(run_id)
    world.run_id = run_id
    monitor = Monitor(stuck_after=spec.stuck_after)
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

    with GameAdapter(spec.story_file) as game:
        output = game.reset(spec.seed)
        scribe.update(world, 0, None, output, scribe.parse_stub(output))
        flush()
        status = monitor.status
        if trace is not None:
            trace.prefix(builder.build_prefix(world, version_context, spec.prompt))
        if progress is not None:
            progress.start(run_id, chat.model, spec.seed, spec.move_cap, spec.usd_cap)
            progress.opening(world)

        for n in range(1, spec.move_cap + 1):
            if _cost(usage, chat) >= spec.usd_cap:
                end_reason = "usd_cap"
                break

            move_prompt = builder.build_prompt(
                world,
                n,
                output,
                status,
                version_context=version_context,
                prompt=spec.prompt,
                expected=expected,
            )
            t0 = time.monotonic()
            proposal = player.propose(chat, move_prompt)
            usage.add(proposal.usage)
            # Hard rules block and the Player retries with the reason (max 3 tries);
            # soft rules already reach the prompt through the version context.
            rejections: list[dict[str, str]] = []
            here = verifier.State(
                room=world.state.room,
                inventory=frozenset(item.casefold() for item in world.inventory),
                room_is_dark=world.state.in_dark,
            )
            while not proposal.gave_up and len(rejections) < MAX_REJECTIONS:
                verdict = verifier.check(proposal.command, here, rules)
                if verdict.ok:
                    break
                rejections.append(
                    {
                        "cmd": proposal.command,
                        "rule_id": verdict.rule_id or "",
                        "reason": verdict.reason or "",
                    }
                )
                proposal = player.propose(chat, move_prompt, feedback=verdict.reason)
                usage.add(proposal.usage)
            latency_ms = int((time.monotonic() - t0) * 1000)
            moves = n
            surprise = proposal.surprise if expected else None
            expected = proposal.expect
            if proposal.goal:
                world.state.goal = proposal.goal

            if proposal.gave_up:
                sink.move(
                    _move(
                        world,
                        n,
                        "I give up",
                        proposal.raw,
                        "",
                        0,
                        False,
                        latency_ms,
                        rejections=rejections,
                        surprise=surprise,
                    )
                )
                if trace is not None:
                    trace.move(n, move_prompt.tail, proposal.raw, "")
                if progress is not None:
                    progress.event("model gave up")
                end_reason = "gave_up"
                break

            room_before = world.state.room
            if not verifier.check(proposal.command, here, rules).ok:
                output = "[Blocked: a proven rule forbids this; try something else]"
                sink.move(
                    _move(
                        world,
                        n,
                        proposal.command,
                        proposal.raw,
                        output,
                        0,
                        False,
                        latency_ms,
                        rejections=rejections,
                        expected=expected,
                        surprise=surprise,
                    )
                )
                continue
            try:
                result = game.step(proposal.command)
            except ValueError as exc:
                output = f"[Not allowed: {exc}]"
                sink.move(
                    _move(
                        world,
                        n,
                        proposal.command,
                        proposal.raw,
                        output,
                        0,
                        False,
                        latency_ms,
                        rejections=rejections,
                        expected=expected,
                        surprise=surprise,
                    )
                )
                if trace is not None:
                    trace.move(n, move_prompt.tail, proposal.raw, output)
                if progress is not None:
                    cost = _cost(usage, chat)
                    progress.move(
                        n,
                        spec.move_cap,
                        room_before,
                        proposal.command,
                        output,
                        latency_ms,
                        cost,
                        world,
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
                    rejections=rejections,
                    expected=expected,
                    surprise=surprise,
                )
            )
            if trace is not None:
                trace.move(n, move_prompt.tail, proposal.raw, output)
            if progress is not None:
                progress.move(
                    n,
                    spec.move_cap,
                    room_before,
                    proposal.command,
                    output,
                    latency_ms,
                    _cost(usage, chat),
                    world,
                    obs,
                    status,
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
        prompt=spec.prompt,
        model=chat.model,
        seed=spec.seed,
        move_cap=spec.move_cap,
        score=world.state.score,
        moves=moves,
        died=died,
        death_move=death_move,
        end_reason=end_reason,
        tokens_in=usage.input,
        tokens_out=usage.output,
        tokens_cache_write=usage.cache_write,
        tokens_cache_read=usage.cache_read,
        cost_usd=_cost(usage, chat),
        started_at=started_at,
        ended_at=datetime.now(UTC),
        chain=spec.chain,
        game_index=spec.game_index,
    )
    sink.run(record)
    if trace is not None:
        trace.end(record, world)
    if progress is not None:
        progress.end(record, world)
    return GameResult(record, world)


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
    """runner.play-shaped wrapper: a run-zero game (empty KBs), returning only the record."""
    spec = GameSpec(
        seed=seed,
        move_cap=move_cap,
        usd_cap=usd_cap,
        version_id=version_id,
        stuck_after=stuck_after,
        story_file=story_file,
        chain=chain,
        game_index=game_index,
        version=version,
        prompt=prompt,
    )
    return play_game(spec, chat=chat, sink=sink, repository=repository, facts=facts).record


def _cost(usage: Usage, chat: Chat) -> float:
    """A chat with its own price table (OpenAIChat) prices itself; Claude models use
    runner.PRICES; offline players cost nothing."""
    own = getattr(chat, "cost", None)
    if callable(own):
        return float(own(usage))
    return float(usage.cost(chat.model)) if chat.model in PRICES else 0.0


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
    *,
    rejections: list[dict[str, str]] | None = None,
    expected: str | None = None,
    surprise: bool | None = None,
) -> MoveRecord:
    return MoveRecord(
        run_id=world.run_id,
        n=n,
        room=room if room is not None else world.state.room,
        command=command,
        proposals=[r["cmd"] for r in rejections or []] + [raw],
        rejections=rejections or [],
        text=text,
        score=world.state.score,
        score_delta=delta,
        died=died,
        latency_ms=latency_ms,
        ts=datetime.now(UTC),
        expected=expected,
        surprise=surprise,
    )


def make_chat(player: str, model: str, seed: int) -> Chat:
    """explorer -> offline; gpt-* -> OpenAI (OPENAI_API_KEY); claude-* -> Anthropic."""
    if player == "explorer":
        from .explorer import ExplorerChat

        return ExplorerChat(seed)
    if model.startswith(("gpt-", "o1", "o3", "o4")):
        from .openai_chat import OpenAIChat

        return OpenAIChat(model)
    return AnthropicChat(model)


def main() -> int:
    """One harness game from the terminal, with live progress.

    python -m zorkinator.harness --player explorer --moves 60     # offline, no API key
    python -m zorkinator.harness --moves 20                       # gpt-5.6-luna (OPENAI_API_KEY)
    python -m zorkinator.harness --model claude-haiku-4-5    # benchmark (ANTHROPIC_API_KEY)
    """
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(prog="zorkinator.harness")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--moves", type=int, default=20, help="move cap (smoke: 20; full: 500)")
    parser.add_argument("--player", choices=["model", "explorer"], default="model")
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_TEST_MODEL") or DEFAULT_TEST_MODEL,
        help=f"development: {DEFAULT_TEST_MODEL}; benchmark: {BASELINE_MODEL}",
    )
    parser.add_argument("--usd-cap", type=float, default=2.0)
    parser.add_argument("--out", default="runs")
    parser.add_argument("--quiet", action="store_true", help="one line per move, no KB notes")
    parser.add_argument("--no-trace", action="store_true", help="skip the per-move trace file")
    args = parser.parse_args()

    chat = make_chat(args.player, args.model, args.seed)
    spec = GameSpec(args.seed, args.moves, args.usd_cap)
    facts: list[WorldFactDoc] = []
    out = Path(args.out)
    trace = (
        None if args.no_trace else Trace(out / f"harness-{chat.model}-s{args.seed}-latest.trace.md")
    )
    result = play_game(
        spec,
        chat=chat,
        sink=JsonlSink(out),
        facts=facts.append,
        trace=trace,
        progress=ConsoleProgress(verbose=not args.quiet),
    )
    print(f"    {len(facts)} world_facts | moves: {out / (result.record.run_id + '.moves.jsonl')}")
    if trace is not None:
        print(f"    trace: {trace.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
