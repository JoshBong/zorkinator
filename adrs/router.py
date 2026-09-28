"""Agentic note router: on each room change (and every EVERY moves) one cheap model call reads the
whole notebook plus the recent game text and picks the notes that matter now, plus one next step."""

from __future__ import annotations

import json

from openai import OpenAI

ROUTER = """You help a text-adventure player use their notebook from earlier games.
Pick the notes that matter for what the player should do in the next few moves, given where they
are and what just happened. Also write one short next step.
Reply with JSON only: {{"ids": [..at most {k} note ids..], "next": "..."}}

NOTEBOOK:
{notebook}

WHERE: {room}
RECENT GAME TEXT:
{recent}
"""


class Router:
    def __init__(
        self, notebook: list[dict], model: str = "gpt-5.6-luna", k: int = 8, every: int = 15
    ):
        self.notes = notebook
        self.model, self.k, self.every = model, k, every
        self.client = OpenAI(max_retries=8)
        self.last_room: str | None = "__start__"
        self.last_n = -10_000
        self.cost = 0.0
        self.calls = 0

    def __call__(self, world, n: int, last_output: str) -> None:
        room = None if world.state.in_dark else world.state.room
        if room == self.last_room and n - self.last_n < self.every:
            return
        self.last_room, self.last_n = room, n
        recent = "\n".join(
            f"> {s.command}\n{(s.text or s.outcome)[:300]}" for s in list(world.state.recent)[-6:]
        )
        prompt = ROUTER.format(
            k=self.k,
            room=room or "somewhere dark",
            recent=recent or last_output[:600],
            notebook="\n".join(
                f"{i}. [{x['where']}] {x['text']}" for i, x in enumerate(self.notes)
            ),
        )
        try:
            r = self.client.responses.create(
                model=self.model,
                input=prompt,
                max_output_tokens=400,
                reasoning={"effort": "none"},
                store=False,
            )
            self.cost += (r.usage.input_tokens * 0.2 + r.usage.output_tokens * 1.2) / 1e6
            self.calls += 1
            out = json.loads(
                r.output_text.strip().removeprefix("```json").removesuffix("```").strip()
            )
            picked = [
                self.notes[int(i)] for i in out.get("ids", []) if 0 <= int(i) < len(self.notes)
            ]
            world.routed = [f"[{x['where']}] {x['text']}" for x in picked[: self.k]]
            if out.get("next"):
                world.routed.append(f"Suggested next step: {out['next']}")
        except Exception:
            pass
