"""ADRS outer loop, local (no Atlas): play -> reflect into a notebook -> play with it.

The notebook is a short list of notes {where, text}. ``where`` is an exact room name (shown only
in that room) or "anywhere" (shown every move in the prefix). The reflector sees only the game
transcript, i.e. what a human player would have seen.

python adrs/chain_local.py --tag c1 --seeds 0 1 2 --games 8
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from dotenv import load_dotenv

MODEL = "gpt-5.6-luna"
MAX_NOTES = 25
LEDGER = os.getenv("ZK_LEDGER", "") == "1"
SELECT = os.getenv("ZK_SELECT", "latest")
NB_ROUTE = os.getenv("ZK_NB_ROUTE", "room")
NB_MODE = os.getenv("ZK_NB_MODE", "rewrite")
REFLECT_EFFORT = os.getenv("ZK_REFLECT_EFFORT", "low")

REFLECT = """You are keeping a notebook of advice for someone playing a text adventure game.
They play the same game again from the start after every game; the notebook is all they carry
over. Below are the current notebook and the transcript of the game that just ended.

Rewrite the notebook (at most {max_notes} notes) so the next game scores more points.
- Only record what the game output in the transcript actually showed. No outside knowledge.
- Priorities: exactly how points were gained (where, what command); what caused death or the end
  of the game and how to avoid it; places or actions that looked promising but were never done.
- Keep notes that held up, correct notes the transcript contradicts, drop notes that did not help.
- For every death or setback, find the ROOT cause in the transcript, not just the last move: what
  was the player carrying or not carrying, what had they done or skipped earlier? A note like
  "do not go east" is only right if going east itself was the problem.
- A note is 1-2 sentences, concrete (room names, items, exact commands).
- "where" is an exact room name from the transcript if the note matters only there, else "anywhere".

Reply with JSON only: {{"notes": [{{"where": "...", "text": "..."}}]}}

CURRENT NOTEBOOK:
{notebook}

LAST GAME (score {score}, ended by {end} after {moves} moves):
{transcript}
"""


def transcript(moves_file: Path, limit_chars: int = 240) -> str:
    rows = [json.loads(line) for line in moves_file.open()]
    out, last = [], 0
    for m in rows:
        text = " ".join((m.get("text") or "").split())[:limit_chars]
        delta = f" [score {last}->{m['score']}]" if m.get("score", 0) != last else ""
        last = m.get("score", last)
        out.append(f"{m['n']}. [{m.get('room')}] > {m['command']}\n   {text}{delta}")
    return "\n".join(out)


EDIT = """You are keeping a notebook of advice for someone playing a text adventure game.
They play the same game again from the start after every game; the notebook is all they carry
over. Below are the current numbered notes and the transcript of the game that just ended.

Edit the notebook so the next game scores more points. Only record what the game output in the
transcript actually showed; no outside knowledge.
- ADD notes for: exactly how points were gained (where, what command); the ROOT cause of every
  death or setback (what was carried or skipped earlier, not just the last move); places, objects
  or actions that looked promising but were never done.
- UPDATE a note the transcript refines or corrects.
- DELETE a note only if this transcript shows it is wrong; give the reason. Do not delete a note
  just because this game did not use it: notes from earlier games are still true.
- A note is 1-2 sentences, concrete (room names, items, exact commands). "where" is an exact room
  name if the note matters only there, else "anywhere".

Reply with JSON only:
{{"add": [{{"where": "...", "text": "..."}}], "update": [{{"id": 3, "text": "..."}}],
  "delete": [{{"id": 5, "reason": "..."}}]}}

CURRENT NOTES:
{notebook}

LAST GAME (score {score}, ended by {end} after {moves} moves):
{transcript}
"""
MAX_EDIT_NOTES = 40


def apply_edits(notebook: list[dict], ops: dict) -> list[dict]:
    notes = {i: dict(n) for i, n in enumerate(notebook)}
    for u in ops.get("update", []):
        if int(u["id"]) in notes:
            notes[int(u["id"])]["text"] = str(u["text"])
    for d in ops.get("delete", []):
        notes.pop(int(d["id"]), None)
    out = list(notes.values()) + [
        {"where": str(a["where"]), "text": str(a["text"])} for a in ops.get("add", [])
    ]
    return out[-MAX_EDIT_NOTES:]


def reflect(notebook: list[dict], record, moves_file: Path) -> tuple[list[dict], float]:
    from openai import OpenAI

    from zorkinator.openai_chat import OPENAI_PRICES

    edit = NB_MODE == "edit"
    numbered = [{"id": i, **n} for i, n in enumerate(notebook)]
    prompt = (EDIT if edit else REFLECT).format(
        max_notes=MAX_NOTES,
        notebook=json.dumps(numbered if edit else notebook, indent=1) if notebook else "(empty)",
        score=record.score,
        end=record.end_reason,
        moves=record.moves,
        transcript=transcript(moves_file),
    )
    client = OpenAI(max_retries=8)
    for _ in range(3):
        r = client.responses.create(
            model=MODEL,
            input=prompt,
            max_output_tokens=6000,
            reasoning={"effort": REFLECT_EFFORT},
            store=False,
        )
        pi, _, po = OPENAI_PRICES[MODEL]
        cost = (r.usage.input_tokens * pi + r.usage.output_tokens * po) / 1e6
        txt = r.output_text.strip().removeprefix("```json").removesuffix("```").strip()
        try:
            parsed = json.loads(txt)
            if edit:
                return apply_edits(notebook, parsed), cost
            notes = parsed["notes"]
            return [{"where": str(n["where"]), "text": str(n["text"])} for n in notes][
                :MAX_NOTES
            ], cost
        except Exception:
            continue
    return notebook, cost


def build_world(notebook: list[dict]):
    from zorkinator.world import WorldModel

    world = WorldModel.empty("chain")
    if NB_ROUTE == "all":
        ctx = "Notebook from earlier games (may be wrong; check it):\n" + "\n".join(
            f"- [{n['where']}] {n['text']}" for n in notebook
        )
        return world, ctx if notebook else ""
    anywhere = []
    for note in notebook:
        if note["where"].strip().casefold() in {"anywhere", "", "global"}:
            anywhere.append(note["text"])
        else:
            world.remember_note(note["where"], note["text"])
    context = (
        (
            "Notebook from earlier games (may be wrong; check it):\n"
            + "\n".join(f"- {t}" for t in anywhere)
        )
        if anywhere
        else ""
    )
    return world, context


def chain(job) -> list[dict]:
    tag, seed, games, moves = job
    load_dotenv(".env")
    from zorkinator.harness import GameSpec, Trace, make_chat, play_game
    from zorkinator.runner import JsonlSink

    out = Path("runs/adrs") / tag / f"s{seed}"
    out.mkdir(parents=True, exist_ok=True)
    notebook: list[dict] = []
    best: tuple[float, list[dict]] | None = None  # (score, notebook that produced it)
    rows = []
    for g in range(games):
        world, context = build_world(notebook)
        if LEDGER:
            from adrs.ledger import build as build_ledger

            ledger = build_ledger(sorted(out.glob("*.moves.jsonl")))
            context = "\n\n".join(x for x in (ledger, context) if x)
        result = None
        for _ in range(3):
            try:
                result = play_game(
                    GameSpec(seed, moves, 2.0, world=world, stuck_after=10_000, context=context),
                    chat=make_chat("model", MODEL, seed),
                    sink=JsonlSink(out),
                    trace=Trace(out / f"g{g}.trace.md"),
                )
                break
            except Exception as exc:
                print(f"[{tag} s{seed} g{g}] retry: {exc!r}"[:200], flush=True)
                world, _ = build_world(notebook)
        if result is None:
            print(f"[{tag} s{seed}] game {g}: FAILED", flush=True)
            continue
        r = result.record
        if SELECT == "best":
            if best is None or r.score >= best[0]:
                best = (r.score, notebook)
            notebook = best[1]  # reflect from the best-scoring notebook so far
        try:
            notebook, rcost = reflect(notebook, r, out / f"{r.run_id}.moves.jsonl")
        except Exception as exc:
            print(f"[{tag} s{seed}] reflect failed: {exc!r}"[:300], flush=True)
            rcost = 0.0
        (out / f"nb-g{g}.json").write_text(json.dumps(notebook, indent=1))
        rows.append(
            {
                "seed": seed,
                "game": g,
                "score": r.score,
                "moves": r.moves,
                "end": r.end_reason,
                "play_cost": r.cost_usd,
                "reflect_cost": rcost,
            }
        )
        print(f"[{tag} s{seed}] game {g}: {r.score} ({r.end_reason}, {r.moves} moves)", flush=True)
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--tag", required=True)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--games", type=int, default=8)
    p.add_argument("--moves", type=int, default=500)
    a = p.parse_args()
    with ProcessPoolExecutor(len(a.seeds)) as ex:
        rows = [
            r
            for rows in ex.map(chain, [(a.tag, s, a.games, a.moves) for s in a.seeds])
            for r in rows
        ]
    by_game = {g: [r["score"] for r in rows if r["game"] == g] for g in range(a.games)}
    half = a.games // 2
    early = [r["score"] for r in rows if r["game"] < half]
    late = [r["score"] for r in rows if r["game"] >= half]
    summary = {
        "tag": a.tag,
        "per_game_mean": {g: round(statistics.mean(v), 1) for g, v in by_game.items()},
        "early_mean": round(statistics.mean(early), 1),
        "late_mean": round(statistics.mean(late), 1),
        "cost": round(sum(r["play_cost"] + r["reflect_cost"] for r in rows), 3),
        "rows": rows,
    }
    Path("runs/adrs", a.tag, "summary.json").write_text(json.dumps(summary, indent=2))
    print(
        json.dumps(
            {k: summary[k] for k in ("tag", "per_game_mean", "early_mean", "late_mean", "cost")}
        )
    )


if __name__ == "__main__":
    main()
