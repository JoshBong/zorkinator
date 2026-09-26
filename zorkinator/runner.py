"""Game loop. Paper mode is the bare loop from arXiv 2602.15867: the baseline."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import anthropic
from anthropic.types import MessageParam

from .adapter import DEFAULT_STORY_FILE, GameAdapter
from .memory import TOOLS, ChatArchive
from .models import MoveRecord, RunRecord
from .prompts import INITIAL_PROMPTS, PromptName

BASELINE_MODEL = "claude-opus-4-5-20251101"
MAX_SCORE = 350
MAX_TOOL_ROUNDS = 3

# (input, output) USD per million tokens. Cache writes bill at 1.25x input, reads at 0.1x.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-5-20251101": (5.0, 25.0),
    "claude-sonnet-4-5-20250929": (3.0, 15.0),
    "claude-sonnet-5": (2.0, 10.0),
}

EndReason = Literal["death", "won", "game_over", "gave_up", "cap", "usd_cap"]


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0

    def add(self, other: Usage) -> None:
        self.input += other.input
        self.output += other.output
        self.cache_write += other.cache_write
        self.cache_read += other.cache_read

    def cost(self, model: str) -> float:
        price_in, price_out = PRICES[model]
        return (
            self.input * price_in
            + self.cache_write * price_in * 1.25
            + self.cache_read * price_in * 0.1
            + self.output * price_out
        ) / 1_000_000


@dataclass
class ChatReply:
    text: str
    usage: Usage
    # Messages to append to history: tool rounds (if any) plus the final assistant turn.
    transcript: list[MessageParam] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)


class Chat(Protocol):
    model: str

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply: ...


class AnthropicChat:
    """Plain Messages API call. No thinking and default sampling, as in the paper."""

    def __init__(self, model: str, client: anthropic.Anthropic | None = None) -> None:
        if model not in PRICES:
            raise ValueError(f"No price for {model!r}; add it to PRICES so the $ cap works.")
        self.model = model
        self._client = client or anthropic.Anthropic()

    def complete(
        self, messages: list[MessageParam], archive: ChatArchive | None = None
    ) -> ChatReply:
        """One model turn. With an archive, past-chat tool rounds happen inside the turn."""
        usage = Usage()
        transcript: list[MessageParam] = []
        tool_calls: list[str] = []
        for round_ in range(MAX_TOOL_ROUNDS + 1):
            last = round_ == MAX_TOOL_ROUNDS
            response = self._create(messages + transcript, archive, allow_tools=not last)
            u = response.usage
            usage.add(
                Usage(
                    input=u.input_tokens,
                    output=u.output_tokens,
                    cache_write=u.cache_creation_input_tokens or 0,
                    cache_read=u.cache_read_input_tokens or 0,
                )
            )
            text = "".join(block.text for block in response.content if block.type == "text")
            uses = [block for block in response.content if block.type == "tool_use"]
            if archive is None or not uses:
                transcript.append({"role": "assistant", "content": text or "(no reply)"})
                return ChatReply(text, usage, transcript, tool_calls)

            assistant_blocks: list[Any] = []
            if text:
                assistant_blocks.append({"type": "text", "text": text})
            results: list[Any] = []
            for use in uses:
                tool_input = dict(use.input) if isinstance(use.input, dict) else {}
                tool_calls.append(f"{use.name}({json.dumps(tool_input)})")
                assistant_blocks.append(
                    {"type": "tool_use", "id": use.id, "name": use.name, "input": tool_input}
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": use.id,
                        "content": archive.run_tool(use.name, tool_input),
                    }
                )
            transcript.append({"role": "assistant", "content": assistant_blocks})
            transcript.append({"role": "user", "content": results})

        # Tool budget spent with no command: an empty turn, which the runner treats as blocked.
        transcript.append({"role": "assistant", "content": "(no reply)"})
        return ChatReply("", usage, transcript, tool_calls)

    def _create(
        self, messages: list[MessageParam], archive: ChatArchive | None, allow_tools: bool = True
    ) -> anthropic.types.Message:
        # Full history is resent every move; caching the growing prefix keeps a
        # 500-move game at roughly a tenth of the uncached input cost.
        if archive is None:
            return self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=messages,
                cache_control={"type": "ephemeral"},
            )
        return self._client.messages.create(
            model=self.model,
            max_tokens=1024,
            messages=messages,
            tools=TOOLS,
            # After MAX_TOOL_ROUNDS lookups the model must answer with a command.
            tool_choice={"type": "auto"} if allow_tools else {"type": "none"},
            cache_control={"type": "ephemeral"},
        )


class Sink(Protocol):
    def move(self, record: MoveRecord) -> None: ...

    def run(self, record: RunRecord) -> None: ...


class JsonlSink:
    """Local log until db.py lands: <dir>/<run_id>.moves.jsonl and <dir>/runs.jsonl."""

    def __init__(self, directory: str | Path = "runs") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def move(self, record: MoveRecord) -> None:
        self._append(f"{record.run_id}.moves.jsonl", record.model_dump(mode="json"))

    def run(self, record: RunRecord) -> None:
        self._append("runs.jsonl", record.model_dump(mode="json"))

    def _append(self, name: str, row: dict[str, object]) -> None:
        with (self.directory / name).open("a") as f:
            f.write(json.dumps(row) + "\n")


def extract_command(reply: str) -> str:
    """First non-empty line, minus quotes, backticks, and a leading prompt marker."""
    for line in reply.splitlines():
        command = line.strip().strip("`\"'“”").removeprefix(">").strip()
        if command:
            return command
    return ""


def gave_up(reply: str) -> bool:
    return "i give up" in reply.casefold()


def play(
    mode: Literal["paper", "harness"],
    seed: int,
    move_cap: int,
    *,
    chat: Chat,
    sink: Sink,
    prompt: PromptName = "basic",
    usd_cap: float = 15.0,
    story_file: str | Path = DEFAULT_STORY_FILE,
    archive: ChatArchive | None = None,
) -> RunRecord:
    """Play one game and log every move. ``move_cap`` counts commands issued, as in the paper.

    With ``archive``, the model can look up earlier games' chats (the paper's app setting), and this
    game's chat is added to the archive when it ends.
    """
    if mode != "paper":
        raise NotImplementedError("Harness mode is not built yet.")

    started_at = datetime.now(UTC)
    tag = "-chats" if archive else ""
    run_id = f"{mode}-{prompt}{tag}-{chat.model}-s{seed}-{started_at:%Y%m%dT%H%M%S%f}"
    usage = Usage()

    # Paper protocol: initial prompt, model replies "ready", then game output each turn.
    messages: list[MessageParam] = [{"role": "user", "content": INITIAL_PROMPTS[prompt]}]
    ack = chat.complete(messages, archive)
    usage.add(ack.usage)
    messages.extend(ack.transcript)

    score = 0
    moves = 0
    died = False
    death_move: int | None = None
    end_reason: EndReason = "cap"

    with GameAdapter(story_file) as game:
        messages.append({"role": "user", "content": game.reset(seed)})

        for n in range(1, move_cap + 1):
            if usage.cost(chat.model) >= usd_cap:
                end_reason = "usd_cap"
                break

            t0 = time.monotonic()
            reply = chat.complete(messages, archive)
            latency_ms = int((time.monotonic() - t0) * 1000)
            usage.add(reply.usage)
            messages.extend(reply.transcript)
            moves = n
            proposals = [*reply.tool_calls, reply.text]

            if gave_up(reply.text):
                give_up = _move(run_id, n, "I give up", proposals, "", score, 0, False, latency_ms)
                sink.move(give_up)
                end_reason = "gave_up"
                break

            command = extract_command(reply.text)
            try:
                result = game.step(command)
            except ValueError as exc:
                # Blocked (save/restore/restart/empty): the model sees why; the game doesn't move.
                text = f"[Not allowed: {exc}]"
                sink.move(_move(run_id, n, command, proposals, text, score, 0, False, latency_ms))
                messages.append({"role": "user", "content": text})
                continue

            text = result["text"]
            delta = result["score"] - score
            score = result["score"]
            died = result["done"] and "you have died" in text.casefold()
            sink.move(_move(run_id, n, command, proposals, text, score, delta, died, latency_ms))
            messages.append({"role": "user", "content": text or "(no output)"})

            if result["done"]:
                if died:
                    end_reason, death_move = "death", n
                else:
                    end_reason = "won" if score >= MAX_SCORE else "game_over"
                break

    record = RunRecord(
        run_id=run_id,
        version_id=None,
        mode=mode,
        prompt=prompt,
        model=chat.model,
        seed=seed,
        move_cap=move_cap,
        score=score,
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
    )
    sink.run(record)
    if archive is not None:
        archive.save(run_id, messages)
    return record


def _move(
    run_id: str,
    n: int,
    command: str,
    proposals: list[str],
    text: str,
    score: int,
    delta: int,
    died: bool,
    latency_ms: int,
) -> MoveRecord:
    return MoveRecord(
        run_id=run_id,
        n=n,
        room=None,  # filled by the parser once it exists
        command=command,
        proposals=proposals,
        rejections=[],
        text=text,
        score=score,
        score_delta=delta,
        died=died,
        latency_ms=latency_ms,
        ts=datetime.now(UTC),
    )


def _play_one(job: tuple[int, int, str, str, float, str, str | None]) -> dict[str, object]:
    seed, move_cap, prompt, model, usd_cap, out, chats = job
    record = play(
        "paper",
        seed,
        move_cap,
        chat=AnthropicChat(model),
        sink=JsonlSink(out),
        prompt="advanced" if prompt == "advanced" else "basic",
        usd_cap=usd_cap,
        archive=ChatArchive(chats) if chats else None,
    )
    return record.model_dump(mode="json")


def play_batch(
    runs: int,
    parallel: int,
    *,
    seed: int,
    move_cap: int,
    prompt: str,
    model: str,
    usd_cap: float,
    total_usd_cap: float,
    out: str,
    chats: str | None,
) -> list[dict[str, object]]:
    """Run games in waves of ``parallel``. With ``chats``, each wave can see every earlier wave's
    chats (games within a wave only see chats that finished before they looked)."""
    from concurrent.futures import ProcessPoolExecutor

    done: list[dict[str, object]] = []
    spent = 0.0
    with ProcessPoolExecutor(max_workers=parallel) as pool:
        while len(done) < runs:
            if spent >= total_usd_cap:
                print(f"Stopping: total ${spent:.2f} reached the ${total_usd_cap:.2f} cap.")
                break
            wave = min(parallel, runs - len(done))
            jobs = [(seed, move_cap, prompt, model, usd_cap, out, chats)] * wave
            for row in pool.map(_play_one, jobs):
                done.append(row)
                spent += float(str(row["cost_usd"]))
                print(
                    f"[{len(done)}/{runs}] score={row['score']} moves={row['moves']} "
                    f"end={row['end_reason']} ${float(str(row['cost_usd'])):.2f} "
                    f"(total ${spent:.2f})",
                    flush=True,
                )
    return done
