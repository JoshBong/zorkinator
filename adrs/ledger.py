"""Verified ledger: facts the score signal proved, extracted mechanically from past transcripts
(no model writes it). Score gains -> an ordered route; deaths -> what preceded them."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path


def build(moves_files: list[Path]) -> str:
    gains: dict[tuple[str, str], list] = defaultdict(list)
    deaths: dict[str, list] = defaultdict(list)
    endings: dict[tuple, int] = defaultdict(int)
    warned: dict[str, list] = defaultdict(list)  # game text right before a death -> commands
    for f in moves_files:
        rows = [json.loads(line) for line in f.open()]
        last = 0
        for i, m in enumerate(rows):
            score = m.get("score", last)
            before = [r["command"] for r in rows[max(0, i - 3) : i]]
            if score > last:
                gains[(m.get("room") or "?", m["command"].casefold())].append(
                    (m["n"], score - last, before)
                )
            if m.get("died") or "you have died" in (m.get("text") or "").casefold():
                deaths[m.get("room") or "?"].append([*before, m["command"]])
                prev_text = " ".join((rows[i - 1].get("text") or "").split()) if i else ""
                warning = prev_text[:140]
                warned[warning].append(m["command"])
            last = score
        if rows:
            end = rows[-1]
            how = (
                "died"
                if "you have died" in (end.get("text") or "").casefold()
                else "gave up"
                if end["command"] == "I give up"
                else "ran out of moves"
            )
            endings[(how, end.get("room") or "?", end.get("score", 0))] += 1
    games = len(moves_files)
    lines = []
    if gains:
        lines.append(
            f"Verified point gains from {games} earlier games, in the order they usually happen:"
        )
        for (room, cmd), hits in sorted(
            gains.items(), key=lambda kv: statistics.median(h[0] for h in kv[1])
        ):
            pts = max(h[1] for h in hits)
            prev = hits[-1][2]
            lines.append(
                f"- +{pts} in {room}: `{cmd}` (worked in {len(hits)} games; "
                f"just before: {', '.join(prev) or '-'})"
            )
    if deaths:
        lines.append("Verified deaths (room: the moves that led to it):")
        for room, seqs in sorted(deaths.items(), key=lambda kv: -len(kv[1])):
            lines.append(f"- {room}, {len(seqs)}x, e.g. {' -> '.join(seqs[-1])}")
    if endings:
        lines.append(
            "How earlier games ended (the known route runs out here; look for new ways past it):"
        )
        for (how, room, sc), c in sorted(endings.items(), key=lambda kv: -kv[1])[:6]:
            lines.append(f"- {c}x {how} in {room} at score {sc}")
    if warned:
        lines.append("What the game said on the move right before each death:")
        for text, cmds in sorted(warned.items(), key=lambda kv: -len(kv[1]))[:6]:
            if text:
                lines.append(
                    f'- {len(cmds)}x after "{text}" (then: {", ".join(sorted(set(cmds))[:5])})'
                )
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    print(build([Path(p) for p in sys.argv[1:]]))
