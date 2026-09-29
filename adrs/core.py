"""ADRS harness core: one module, explicit Config, no cross-file monkeypatching.

Consolidates what survived x3-x9 (see zorkinator-vault/adrs-log.md):
- Go-Explore archive of spots (room @ score), best trajectory per spot (score, items, shorter),
  minimized into a return route that keeps what you hold; replay-verified, $0.
- Speedrun: the harness plays the route itself, checks each step, hands off on mismatch.
- Memory: every quote-checked fact shown each move (the design that won 3 retrieval tests),
  capped + deduplicated; evidence-only goal theory; one hypothesis per new thing.
- Failure-driven replanning: walls (repeated deaths/stalls), an attempt log, a common-sense
  post-mortem whose plans must differ from what failed, compiled into replay-verified routes that
  arrive at the wall with the needed items. Concrete walls before the plateau wall.
Reflection and post-mortem calls use Config.effort (default "none"); the player is always
"none" (zorkinator.openai_chat.OpenAIChat).

python -m adrs.core --config x9 --tag x10 --seeds 0 1 2 --games 16
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path

MODEL = "gpt-5.6-luna"
PRICE_IN, PRICE_OUT = 0.2, 1.2  # USD per million tokens (gpt-5.6-luna)


@dataclass(frozen=True)
class Config:
    name: str
    games: int = 16
    move_cap: int = 500
    usd_cap: float = 2.0
    scoring_every: int = 4
    patience: int = 3
    score_w: float = 2.0
    effort: str = "none"
    fact_cap: int = 150
    goal_theory: bool = True
    walls: bool = True
    plateau: bool = True
    wall_deaths: int = 2
    wall_stalls: int = 2
    plateau_games: int = 4
    max_plans: int = 4
    death_window: int = 15
    player: str = "model"  # "explorer" = the repo's offline player (tests)


CONFIGS = {
    "base": Config("base", walls=False, plateau=False),  # x5-equivalent, effort none, cap 150
    "x9": Config("x9"),  # base + failure-driven replanning
}

MOVES = {
    "n",
    "s",
    "e",
    "w",
    "u",
    "d",
    "ne",
    "nw",
    "se",
    "sw",
    "north",
    "south",
    "east",
    "west",
    "up",
    "down",
    "northeast",
    "northwest",
    "southeast",
    "southwest",
    "in",
    "out",
    "look",
    "l",
    "inventory",
    "i",
    "wait",
    "z",
}
TAKE = ("take ", "get ", "pick up ", "grab ")
_MARKER = re.compile(r"\s*\[score -?\d+->-?\d+\]\s*")


# --- small text helpers ---------------------------------------------------------------------
def norm_room(x: str | None) -> str:
    return re.sub(r"^the ", "", (x or "").casefold().strip())


def head(name: str) -> str:
    words = re.sub(r"[^a-z0-9' -]", " ", name.casefold()).split()
    return words[-1] if words else name.casefold()


def has_word(text: str, word: str) -> bool:
    return re.search(rf"\b{re.escape(word)}\b", text.casefold()) is not None


def _norm(s: str) -> str:
    return " ".join(s.casefold().split())


# --- engine: exact, free ----------------------------------------------------------------------
def inventory(game) -> frozenset[str]:
    """Items held, from `inventory` in a saved/restored engine state. Items are the INDENTED
    lines under "You are carrying:"; the game's event messages are not indented."""
    env = game._env
    state = env.get_state()
    text = game.step("inventory")["text"]
    env.set_state(state)
    return frozenset(
        " ".join(x.split()).casefold()
        for x in text.splitlines()
        if x.startswith("  ") and x.strip()
    )


def trace(cmds: list[str], seed: int) -> list[dict]:
    """State after every command (index into cmds, room, inventory, score); stops at death."""
    from zorkinator.adapter import GameAdapter
    from zorkinator.scribe import parse_stub

    out = []
    with GameAdapter() as game:
        room = parse_stub(game.reset(seed)).room
        for i, cmd in enumerate(cmds):
            try:
                r = game.step(cmd)
            except ValueError:
                continue
            p = parse_stub(r["text"])
            if p.died or r["done"]:
                break
            room = "(dark)" if p.dark else (p.room or room)
            out.append(
                {
                    "i": i,
                    "room": room,
                    "inv": inventory(game),
                    "score": r["score"],
                    "raw_room": p.room,
                }
            )
    return out


_START: dict[int, str | None] = {}


def start_room(seed: int) -> str | None:
    if seed not in _START:
        from zorkinator.adapter import GameAdapter
        from zorkinator.scribe import parse_stub

        with GameAdapter() as g:
            _START[seed] = parse_stub(g.reset(seed)).room
    return _START[seed]


def holds(inv, item: str) -> bool:
    return any(has_word(x, item) for x in inv)


# --- archive of spots -------------------------------------------------------------------------
def spot_key(room: str, score: int) -> str:
    return f"{room} @{score}"


def better(new: tuple, old: tuple) -> bool:
    """(score, items held, -length): higher score, then more items, then shorter."""
    return new > old


def update_archive(archive: dict, cmds: list[str], seed: int, states: list | None = None) -> int:
    new, seen = 0, set()
    for st in trace(cmds, seed) if states is None else states:
        k = spot_key(st["room"], st["score"])
        traj = cmds[: st["i"] + 1]
        cur = archive.get(k)
        if cur is None:
            archive[k] = {
                "key": k,
                "room": st["room"],
                "inv": sorted(st["inv"]),
                "score": st["score"],
                "traj": traj,
                "route": None,
                "seen": 0,
                "chosen": 0,
            }
            new += 1
        elif better(
            (st["score"], len(st["inv"]), -len(traj)),
            (cur["score"], len(cur["inv"]), -len(cur["traj"])),
        ):
            cur.update(score=st["score"], inv=sorted(st["inv"]), traj=traj, route=None, chosen=0)
        if k not in seen:
            archive[k]["seen"] += 1
            seen.add(k)
    return new


def best_spot(archive: dict) -> dict:
    return max(archive.values(), key=lambda c: (c["score"], len(c["inv"]), -len(c["traj"])))


def minimize_to(traj: list[str], seed: int, spot: dict) -> list[str]:
    """Shortest sub-sequence (chunk deletion) that still ends in the spot's room with at least its
    score and everything it held."""
    want = frozenset(spot["inv"])

    def ok(r: list[str]) -> bool:
        t = trace(r, seed)
        return (
            len(t) == len(r)
            and t[-1]["room"] == spot["room"]
            and want <= t[-1]["inv"]
            and t[-1]["score"] >= spot["score"]
        )

    route = list(traj)
    if not ok(route):
        return route
    for size in (16, 8, 4, 2, 1):
        i = 0
        while i < len(route):
            cand = route[:i] + route[i + size :]
            if cand and ok(cand):
                route = cand
            else:
                i += size
    return route


def route_for(spot: dict, seed: int) -> list[str]:
    if spot["route"] is None:
        spot["route"] = minimize_to(spot["traj"], seed, spot)
    return spot["route"]


def reference(route: list[str], seed: int) -> list[dict]:
    """Per-step expected (raw room title, score) for the speedrun's live checks."""
    return [
        {"cmd": route[s["i"]], "room": s["raw_room"], "score": s["score"]}
        for s in trace(route, seed)
    ]


# --- speedrun wrapper (chat + per-move hook) --------------------------------------------------
class Speedrun:
    def __init__(self, chat, route: list[str], ref: list[dict], frontier: str):
        self.chat, self.route, self.ref, self.frontier = chat, route, ref, frontier
        self.model = chat.model
        self.i, self.active, self.handoff = 0, bool(route), ""

    def cost(self, usage):
        # the offline explorer has no price table; real chats price themselves
        return self.chat.cost(usage) if hasattr(self.chat, "cost") else 0.0

    def hook(self, world, n: int, output: str) -> None:
        from zorkinator.scribe import parse_stub

        if self.active and self.i > 0:
            exp = self.ref[self.i - 1] if self.i - 1 < len(self.ref) else None
            p = parse_stub(output)
            if (
                exp is None
                or p.died
                or (exp["room"] and p.room != exp["room"])
                or world.state.score < exp["score"]
            ):
                self.active = False
                self.handoff = (
                    f"The harness replayed your known route for {self.i} moves; step "
                    f"{self.i} (`{self.route[self.i - 1]}`) did not go as expected. "
                    "Remaining route, adapt as needed: "
                    + ", ".join(self.route[self.i : self.i + 40])
                )
        if self.active and self.i >= len(self.route):
            self.active = False
            self.handoff = f"The harness replayed your known route ({self.i} moves)."
        if not self.active:
            world.routed = [x for x in (self.handoff, self.frontier) if x]

    def complete(self, messages, archive=None):
        from zorkinator.runner import ChatReply, Usage

        if self.active and self.i < len(self.route):
            cmd = self.route[self.i]
            self.i += 1
            return ChatReply(cmd, Usage(), [{"role": "assistant", "content": cmd}])
        return self.chat.complete(messages, archive)


# --- model calls ------------------------------------------------------------------------------
def ask(prompt: str, cfg: Config, tokens: int = 8000) -> tuple[dict | None, float]:
    from openai import OpenAI

    client = OpenAI(max_retries=8)
    cost = 0.0
    for _ in range(3):
        r = client.responses.create(
            model=MODEL,
            input=prompt,
            max_output_tokens=tokens,
            reasoning={"effort": cfg.effort},
            store=False,
        )
        cost += (r.usage.input_tokens * PRICE_IN + r.usage.output_tokens * PRICE_OUT) / 1e6
        try:
            return json.loads(
                r.output_text.strip().removeprefix("```json").removesuffix("```").strip()
            ), cost
        except Exception:
            continue
    return None, cost


def verified(evidence: list, moves: list[dict], min_distinct: int = 1) -> bool:
    """Every cited quote (with the harness's own [score a->b] markers removed) must appear in the
    cited move's game output or command."""
    by_n = {m["n"]: _norm((m.get("text") or "") + " " + m.get("command", "")) for m in moves}
    ev = [e for e in evidence or [] if isinstance(e, dict)]
    if not ev:
        return False
    for e in ev:
        q = _norm(_MARKER.sub(" ", str(e.get("quote", ""))))
        try:
            n = int(e.get("move", -1))
        except Exception:
            return False
        if not q or q not in by_n.get(n, ""):
            return False
    return len({int(e["move"]) for e in ev}) >= min_distinct


def transcript(moves: list[dict], limit: int = 240) -> str:
    out, last = [], 0
    for m in moves:
        text = " ".join((m.get("text") or "").split())[:limit]
        delta = f" [score {last}->{m['score']}]" if m.get("score", 0) != last else ""
        last = m.get("score", last)
        out.append(f"{m['n']}. [{m.get('room')}] > {m['command']}\n   {text}{delta}")
    return "\n".join(out)


REFLECT = """You maintain what a text-adventure player has learned from their OWN games; they
replay from the start every game. Use ONLY what the transcript's game output shows. No outside
knowledge about this or any game.

FACTS (numbered):
{facts}

GOAL THEORIES so far (numbered; how the game seems to be won or progressed):
{goals}

HYPOTHESES SHOWN THIS GAME:
{shown}

THINGS ALREADY TRACKED: {things}

TRANSCRIPT (score {score}, ended by {end} after {moves} moves; the first {speedrun} moves were the
harness replaying a known route). Score changes are marked [score a->b]:
{transcript}

Reply with JSON only:
{{"facts_add": [{{"where": "room or anywhere", "text": "...",
                  "evidence": [{{"move": 12, "quote": "exact words from that move's output"}}]}}],
  "facts_update": [{{"id": 0, "text": "...", "evidence": [{{"move": 3, "quote": "..."}}]}}],
  "facts_delete": [{{"id": 0, "reason": "what in this transcript shows it is wrong"}}],
  "rules": [{{"text": "a rule that holds in more than one place",
              "instances": [{{"move": 5, "quote": "..."}}, {{"move": 40, "quote": "..."}}]}}],
  "verdicts": [{{"id": 3, "status": "confirmed|refuted|inconclusive|not_attempted",
                 "evidence": [{{"move": 12, "quote": "..."}}], "conclusion": "..."}}],
  "new_things": [{{"name": "...", "kind": "object|room|barrier|message", "where": "room",
                   "hypothesis": "what it might be for", "test": "exact commands", "value": 1}}],
  "goal_theories": [{{"id": null, "theory": "how the game seems to reward progress / be won",
                      "explains": [{{"move": 30, "quote": "..."}}],
                      "contradicted_by": [{{"move": 50, "quote": "..."}}],
                      "test": "a concrete way to test it next game"}}]}}
Rules for you:
- Every fact, rule, verdict and goal theory needs evidence: move numbers from THIS transcript and
  the EXACT words of that move's game output. Anything without matching evidence is discarded.
- facts: what things do, what is needed where, root causes of deaths/setbacks.
- rules: the same effect seen in 2+ different places (cite each) -> one general rule.
- goal_theories: step back and ask what the game wants. Use score changes, what the game says
  about your actions, and things that seem to exist for a purpose.
- new_things: every object, room, blocked way or odd message not already tracked, one hypothesis
  and a concrete test each; value 1-5 = how likely it opens new places or progress.
"""


def reflect(
    state: dict, shown: list[dict], record, moves: list[dict], speedrun_len: int, cfg: Config
) -> tuple[dict, dict, float]:
    """Safe wrapper: a malformed model reply leaves the notebook unchanged instead of crashing."""
    try:
        return _reflect(state, shown, record, moves, speedrun_len, cfg)
    except Exception as exc:
        return state, {"reflect_error": repr(exc)[:200]}, 0.0


def _reflect(
    state: dict, shown: list[dict], record, moves: list[dict], speedrun_len: int, cfg: Config
) -> tuple[dict, dict, float]:
    out, cost = ask(
        REFLECT.format(
            facts=json.dumps(
                [
                    {"id": i, "where": f["where"], "text": f["text"]}
                    for i, f in enumerate(state["facts"])
                ]
            )
            or "(none)",
            goals=json.dumps(
                [{"id": i, "theory": g["theory"]} for i, g in enumerate(state["goals"])]
            )
            or "(none)",
            shown="\n".join(
                f"{h['id']}. [{h['where']}] {h['hypothesis']} (test: {h['test']})" for h in shown
            )
            or "(none)",
            things=", ".join(sorted(h["name"] for h in state["hyps"])) or "(none)",
            score=record.score,
            end=record.end_reason,
            moves=record.moves,
            speedrun=speedrun_len,
            transcript=transcript(moves),
        ),
        cfg,
        tokens=12000,
    )
    stats: Counter = Counter()
    if not isinstance(out, dict):
        stats["reflect_failed"] += 1
        return state, dict(stats), cost
    facts = {i: dict(f) for i, f in enumerate(state["facts"])}
    for u in out.get("facts_update", []):
        i = u.get("id")
        if isinstance(i, int) and i in facts and verified(u.get("evidence"), moves):
            facts[i]["text"] = str(u["text"])
            stats["fact_updates"] += 1
    for d in out.get("facts_delete", []):
        if isinstance(d.get("id"), int):
            facts.pop(d["id"], None)
    fact_list = list(facts.values())
    for a in out.get("facts_add", []):
        if verified(a.get("evidence"), moves):
            fact_list.append({"where": str(a["where"]), "text": str(a["text"])})
            stats["facts_added"] += 1
        else:
            stats["facts_rejected"] += 1
    for r in out.get("rules", []):
        if verified(r.get("instances"), moves, min_distinct=2):
            fact_list.append({"where": "anywhere", "text": f"Rule: {r['text']}"})
            stats["rules_added"] += 1
        else:
            stats["rules_rejected"] += 1
    hyps = [dict(h) for h in state["hyps"]]
    by_id = {h["id"]: h for h in hyps}
    shown_ids = {h["id"] for h in shown}
    for v in out.get("verdicts", []):
        hid = v.get("id")
        if hid not in shown_ids:
            continue
        h = by_id[hid]
        status = v.get("status", "inconclusive")
        if status in {"confirmed", "refuted"} and verified(v.get("evidence"), moves):
            h["status"], h["conclusion"] = status, str(v.get("conclusion", ""))
            fact_list.append(
                {
                    "where": h["where"],
                    "text": f"Tested ({status}): {h['hypothesis']} -> {h['conclusion']}",
                }
            )
            stats[f"verdict_{status}"] += 1
        elif h["attempts"] >= 3:
            h["status"] = "abandoned"
    known = {h["name"].casefold() for h in hyps}
    nid = max(by_id, default=-1) + 1
    for t in out.get("new_things", []):
        name = str(t.get("name", ""))
        if not name or name.casefold() in known:
            continue
        try:
            value = int(t.get("value", 1))
        except Exception:
            value = 1
        hyps.append(
            {
                "id": nid,
                "name": name,
                "kind": str(t.get("kind", "")),
                "where": str(t.get("where", "")),
                "hypothesis": str(t.get("hypothesis", "")),
                "test": str(t.get("test", "")),
                "value": value,
                "status": "open",
                "attempts": 0,
                "conclusion": "",
            }
        )
        known.add(name.casefold())
        nid += 1
    goals = [dict(g) for g in state["goals"]]
    if cfg.goal_theory:
        gains = {m["n"] for m in moves if (m.get("score_delta") or 0) > 0}
        for gt in out.get("goal_theories", []):
            ok = [e for e in gt.get("explains") or [] if verified([e], moves)]
            against = sum(verified([e], moves) for e in gt.get("contradicted_by") or [])
            if not ok and not against:
                stats["goals_rejected"] += 1
                continue
            gid = gt.get("id")
            g = goals[gid] if isinstance(gid, int) and 0 <= gid < len(goals) else None
            if g is None:
                g = {"theory": str(gt.get("theory", "")), "support": 0, "against": 0, "test": ""}
                goals.append(g)
            g["theory"] = str(gt.get("theory") or g["theory"])
            g["support"] += sum(int(e.get("move", -1)) in gains for e in ok)
            g["against"] += against
            g["test"] = str(gt.get("test") or g["test"])
        goals = sorted(goals, key=lambda g: g["support"] - g["against"], reverse=True)[:5]
    seen, dedup = set(), []
    for f in fact_list:
        k = (f["where"].casefold(), _norm(f["text"]))
        if k not in seen:
            seen.add(k)
            dedup.append(f)
    return ({"facts": dedup[-cfg.fact_cap :], "hyps": hyps, "goals": goals}, dict(stats), cost)


# --- what the chain has seen: items, deaths, attempts, room text, walked map ------------------
class Knowledge:
    def __init__(self):
        self.takes: dict[str, str] = {}  # item head -> room where it was taken
        self.seen_at: dict[str, str] = {}  # item head -> room whose text first named it
        self.deaths: dict[str, int] = Counter()
        self.attempts: dict[str, list[dict]] = defaultdict(list)
        self.room_text: dict[str, str] = {}
        self.graph: dict[str, dict[str, str]] = {}
        self.zero: Counter = Counter()

    def learn(
        self,
        played: list[tuple[str, dict]],
        seed: int,
        names: list[str],
        states: list | None = None,
    ) -> None:
        cmds = [c for c, _ in played]
        by_i = {s["i"]: s for s in (trace(cmds, seed) if states is None else states)}
        heads = sorted({head(n) for n in names if len(head(n)) > 2})
        room, inv = start_room(seed), frozenset()
        states = by_i
        for i, (cmd, m) in enumerate(played):
            c = cmd.casefold().strip()
            text = " ".join((m.get("text") or "").split())
            low = text.casefold()
            if room and c not in MOVES and not c.startswith(("go ", "walk ")):
                att = self.attempts[norm_room(room)]
                att.append(
                    {
                        "cmd": c,
                        "held": sorted({head(x) for x in inv}),
                        "reply": text[:140],
                        "died": "you have died" in low,
                    }
                )
                del att[:-40]
            if c.startswith(TAKE) and "taken" in low and room:
                for h in heads:
                    if has_word(c, h):
                        self.takes.setdefault(h, room)
            if "you have died" in low and room:
                self.deaths[norm_room(room)] += 1
            if i in states:
                new_room = states[i]["room"]
                if new_room and "(dark)" not in new_room:
                    for h in heads:
                        if h not in self.seen_at and has_word(low, h):
                            self.seen_at[h] = new_room
                    if new_room != room:
                        self.room_text.setdefault(norm_room(new_room), text[:500])
                        if room and "(dark)" not in room:
                            self.graph.setdefault(room, {})[c] = new_room
                room, inv = new_room, states[i]["inv"]

    def names(self, archive: dict) -> dict[str, str]:
        out = {norm_room(r): r for r in self.graph}
        out |= {norm_room(v): v for d in self.graph.values() for v in d.values()}
        out |= {
            norm_room(c["room"]): c["room"] for c in archive.values() if "(dark)" not in c["room"]
        }
        return out


def path(graph: dict, start: str, goal: str) -> list[str] | None:
    q, seen = deque([(start, [])]), {start}
    while q:
        room, cmds = q.popleft()
        if norm_room(room) == norm_room(goal):
            return cmds
        for cmd, nxt in graph.get(room, {}).items():
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, [*cmds, cmd]))
    return None


# --- walls: failure-driven replanning ---------------------------------------------------------
POSTMORTEM = """You are helping a player of a text adventure who keeps failing at the same place.
Think like a sensible person who just failed: repeating the same thing will not work. What do they
need that they don't have? What have they never tried? Use only what the player has seen (below).

WHERE THEY KEEP FAILING: {wall}
{wall_text}

WHAT THEY ALREADY TRIED THERE (command | items held | what the game said | died?):
{attempts}

PLANS ALREADY TRIED AND HOW THEY WENT:
{plans}

ITEMS THEY HAVE SEEN OR TAKEN, AND WHERE:
{items}

WHAT EACH ROOM LOOKED LIKE WHEN THEY ARRIVED (things mentioned here can be used too):
{rooms_text}

WHAT THEY HAVE LEARNED SO FAR:
{facts}

Propose 2-3 plans that are DIFFERENT from everything already tried. Each plan: what to get first
(only items named above, with the room where they are), which room to go to, and what to do there.
Reply with JSON only:
{{"plans": [{{"need": "...", "get": [{{"item": "...", "at": "room"}}], "go_to": "room",
             "then": "what to do there", "why": "..."}}]}}
"""


class Walls:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.plans: dict[str, list[dict]] = defaultdict(list)
        self.cleared: set[str] = set()
        self.exits_seen: dict[str, set[str]] = defaultdict(set)

    def find(self, kn: Knowledge, archive: dict) -> str | None:
        depth: dict[str, int] = defaultdict(int)
        for c in archive.values():
            depth[norm_room(c["room"])] = max(depth[norm_room(c["room"])], c["score"])
        cands = [
            r
            for r in set(kn.deaths) | set(kn.zero)
            if r not in self.cleared
            and r != "(dark)"
            and '"' not in r
            and len(r.split()) <= 5
            and len(self.plans.get(r, [])) < self.cfg.max_plans
            and (
                kn.deaths.get(r, 0) >= self.cfg.wall_deaths
                or kn.zero.get(r, 0) >= self.cfg.wall_stalls
            )
        ]
        return max(
            cands, key=lambda r: (depth[r], kn.deaths.get(r, 0) + kn.zero.get(r, 0)), default=None
        )

    def passed(
        self,
        wall: str,
        played: list[tuple[str, dict]],
        seed: int,
        known_exits: set[str] = frozenset(),
        handoff: int = 0,
        states: list | None = None,
    ) -> bool:
        """This game got past the wall: from the wall room into a room never walked to from it
        before (known_exits seeds the baseline from the walked map), or a score gain made by the
        player (at/after `handoff`, not by the replayed route) after reaching the wall room."""
        states = trace([c for c, _ in played], seed) if states is None else states
        rooms = [norm_room(s["room"]) for s in states]
        at = [k for k, r in enumerate(rooms) if r == wall]
        if not at:
            return False
        start = max(states[at[0]]["i"] + 1, handoff)
        gained = any((m.get("score_delta") or 0) > 0 for _, m in played[start:])
        exits = {rooms[k + 1] for k in at if k + 1 < len(rooms) and rooms[k + 1] != wall}
        baseline = self.exits_seen[wall] | {norm_room(x) for x in known_exits}
        new_exit = bool(exits - baseline)
        self.exits_seen[wall] |= exits
        return gained or new_exit

    def postmortem(
        self, wall: str, kn: Knowledge, state: dict, archive: dict, cfg: Config
    ) -> tuple[list[dict], float]:
        names = kn.names(archive)
        items = dict(kn.seen_at) | dict(kn.takes)
        lines, seen = [], set()
        for a in kn.attempts.get(wall, []):
            k = (a["cmd"], tuple(a["held"]))
            if k not in seen:
                seen.add(k)
                lines.append(
                    f"- {a['cmd']} | {', '.join(a['held']) or 'nothing'} | "
                    f"{a['reply'][:100]} | {'DIED' if a['died'] else ''}"
                )
        label = (
            "their score stopped rising (no new points for many games)"
            if wall == "__plateau__"
            else names.get(wall, wall)
        )
        out, cost = ask(
            POSTMORTEM.format(
                wall=label,
                wall_text=kn.room_text.get(wall, ""),
                attempts="\n".join(lines[-25:]) or "(none recorded)",
                plans="\n".join(
                    f"- {p['need']}: get {[g['item'] for g in p['get']]} then "
                    f"{p['then']} -> {p.get('result')}"
                    for p in self.plans[wall]
                )
                or "(none)",
                items="\n".join(f"- {h} (at {w})" for h, w in sorted(items.items())) or "(none)",
                rooms_text="\n".join(f"- {names.get(k, k)}: {v}" for k, v in kn.room_text.items())
                or "(none)",
                facts="\n".join(f"- [{f['where']}] {f['text']}" for f in state["facts"][-60:]),
            ),
            cfg,
            tokens=2000,
        )
        tried = {
            (tuple(sorted(g["item"] for g in p["get"])), p["then"].casefold())
            for p in self.plans[wall]
        }
        plans = []
        for p in (out or {}).get("plans", [])[:3]:
            try:
                get = [
                    {"item": head(g["item"]), "at": names.get(norm_room(g["at"]))}
                    for g in p.get("get", [])
                ][:2]
                go = names.get(norm_room(p.get("go_to", ""))) or (
                    names.get(wall) if wall != "__plateau__" else None
                )
                ok = go and all(
                    g["at"]
                    and (
                        g["item"] in items
                        or has_word(kn.room_text.get(norm_room(g["at"]), ""), g["item"])
                    )
                    for g in get
                )
                key = (tuple(sorted(g["item"] for g in get)), str(p.get("then", "")).casefold())
                if ok and key not in tried:
                    plans.append(
                        {
                            "need": str(p.get("need", "")),
                            "get": get,
                            "go_to": go,
                            "then": str(p.get("then", "")),
                            "why": str(p.get("why", "")),
                        }
                    )
                    tried.add(key)
            except Exception:
                continue
        return plans, cost


def died_holding(wall: str, played: list[tuple[str, dict]], states: list, items: list[str]) -> bool:
    """This game ended in a death in the wall room while holding every item of the plan."""
    if (
        not played
        or not states
        or "you have died" not in (played[-1][1].get("text") or "").casefold()
    ):
        return False
    last = states[-1]
    return norm_room(last["room"]) == wall and all(holds(last["inv"], i) for i in items)


def compile_plan(plan: dict, archive: dict, kn: Knowledge, seed: int) -> list[str] | None:
    """Splice each pickup into a route that already reaches go_to (it does the state steps on the
    way), at the first point it is in the item's room, else a walked-map detour there and back.
    Verified by replay: the take must succeed and the route must arrive holding every item."""
    spots = [c for c in archive.values() if norm_room(c["room"]) == norm_room(plan["go_to"])]
    if not spots:
        return None
    route = list(
        route_for(max(spots, key=lambda c: (c["score"], len(c["inv"]), -len(c["traj"]))), seed)
    )
    for g in plan["get"]:
        st = trace(route, seed)
        rooms = [norm_room(x["room"]) for x in st]
        spliced = None
        if norm_room(g["at"]) in rooms:
            cut = st[rooms.index(norm_room(g["at"]))]["i"] + 1
            spliced = [*route[:cut], f"take {g['item']}", *route[cut:]]
        else:
            for x in st[:: max(1, len(st) // 12)]:
                there = path(kn.graph, x["room"], g["at"])
                back = path(kn.graph, g["at"], x["room"]) if there else None
                if there and back:
                    cut = x["i"] + 1
                    spliced = [*route[:cut], *there, f"take {g['item']}", *back, *route[cut:]]
                    break
        if spliced is None:
            return None
        chk = trace(spliced, seed)
        if len(chk) < len(spliced) or not holds(chk[-1]["inv"], g["item"]):
            return None
        route = spliced
    end = trace(route, seed)
    if (
        len(end) < len(route)
        or norm_room(end[-1]["room"]) != norm_room(plan["go_to"])
        or not all(holds(end[-1]["inv"], g["item"]) for g in plan["get"])
    ):
        return None
    return route


# --- spot selection ---------------------------------------------------------------------------
def pick_spot(
    archive: dict, hyps: list[dict], rng: random.Random, danger: dict, cfg: Config
) -> tuple[dict, dict]:
    top = max((c["score"] for c in archive.values()), default=0) or 1
    levels = defaultdict(list)
    for c in archive.values():
        levels[c["score"]].append(c)

    def explore(c):
        open_here = sum(
            h["status"] == "open" and norm_room(h["where"]) == norm_room(c["room"]) for h in hyps
        )
        e = (
            1 / math.sqrt(1 + c["chosen"])
            + 1 / math.sqrt(1 + c["seen"])
            + 0.5 * min(open_here, 4) / 4
        )
        trips, bad = danger.get(c["key"], (0, 0))
        return e * (1 - 0.7 * bad / trips) if trips >= 2 else e

    lv = list(levels)
    level = rng.choices(
        lv, weights=[max(explore(c) for c in levels[s]) * (1 + cfg.score_w * s / top) for s in lv]
    )[0]
    pool = levels[level]
    ws = [explore(c) for c in pool]
    c = rng.choices(pool, weights=ws)[0]
    return c, {"mode": "explore", "level": level}


# --- one chain --------------------------------------------------------------------------------
def chain(job) -> list[dict]:
    cfg, tag, seed = job
    from dotenv import load_dotenv

    load_dotenv(".env")
    from zorkinator.harness import GameSpec, Trace, make_chat, play_game
    from zorkinator.runner import JsonlSink
    from zorkinator.world import WorldModel

    out = Path("runs/adrs") / tag / f"s{seed}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(asdict(cfg), indent=1))
    state = {"facts": [], "hyps": [], "goals": []}
    archive, danger, kn, walls = {}, {}, Knowledge(), Walls(cfg)
    rows, stall, best_seen, back_k, since_gain, active = [], 0, -1, 0, 0, None
    for g in range(cfg.games):
        rng = random.Random(seed * 1000 + g)
        scoring = bool(archive) and (g + 1) % cfg.scoring_every == 0
        pick, why, route, plan_text, pm_cost = None, {"mode": "cold"}, [], "", 0.0
        if archive and scoring:
            pick, why = best_spot(archive), {"mode": "scoring"}
            route = route_for(pick, seed)
        elif archive:
            wall = walls.find(kn, archive) if cfg.walls else None
            if (
                wall is None
                and cfg.plateau
                and since_gain >= cfg.plateau_games
                and "__plateau__" not in walls.cleared
                and len(walls.plans["__plateau__"]) < cfg.max_plans
            ):
                wall = "__plateau__"
            if wall is not None:
                pending = [p for p in walls.plans[wall] if p["result"] in (None, "inconclusive")]
                if not pending:
                    plans, pm_cost = walls.postmortem(wall, kn, state, archive, cfg)
                    for p in plans:
                        p["route"] = compile_plan(p, archive, kn, seed)
                        p["result"] = None if p["route"] else "unreachable"
                        walls.plans[wall].append(p)
                    pending = [p for p in walls.plans[wall] if p["result"] is None]
                    if not pending:
                        walls.plans[wall].append(
                            {
                                "need": "(no valid plan)",
                                "get": [],
                                "go_to": "",
                                "then": "",
                                "route": None,
                                "result": "no-plan",
                            }
                        )
                if pending:
                    plan = pending[0]
                    active, route = (wall, plan), plan["route"]
                    why = {"mode": "plan", "wall": wall, "need": plan["need"]}
                    fails = [a["cmd"] for a in kn.attempts.get(wall, []) if a["died"]][-3:]
                    got = ", ".join(f"the {x['item']}" for x in plan["get"]) or "what you need"
                    plan_text = (
                        f"Plan from reviewing your earlier failures: you now have {got} "
                        f"and are at {plan['go_to']}. Need: {plan['need']}. Try: "
                        f"{plan['then']}. Before, these failed here: "
                        + ("; ".join(f"`{x}`" for x in fails) or "(none)")
                        + ". If this fails too, try something different."
                    )
            if not route:
                active = None
                if stall >= cfg.patience:
                    best = best_spot(archive)
                    on_path = sorted(
                        [
                            c
                            for c in archive.values()
                            if c["key"] != best["key"]
                            and best["traj"][: len(c["traj"])] == c["traj"]
                        ],
                        key=lambda c: len(c["traj"]),
                    )
                    if on_path:
                        back_k = min(back_k + 1, len(on_path))
                        pick, why = on_path[-back_k], {"mode": f"backtrack-{back_k}"}
                if pick is None:
                    pick, why = pick_spot(archive, state["hyps"], rng, danger, cfg)
                pick["chosen"] += 1
                route = route_for(pick, seed)
        ref = reference(route, seed) if route else []
        parts = []
        if state["goals"]:
            parts.append(
                "Working theory of how this game is won (from your own games; may be "
                "wrong):\n"
                + "\n".join(
                    f"- {t['theory']} (supported by {t['support']} score gains)"
                    for t in state["goals"][:2]
                )
            )
        if state["facts"]:
            parts.append(
                "Facts from your earlier games (each seen in the game's own output):\n"
                + "\n".join(f"- [{f['where']}] {f['text']}" for f in state["facts"])
            )
        context = "\n\n".join(parts)
        shown = []
        if scoring:
            frontier = (
                "This is a scoring game: use your working theory and facts to make as "
                "much progress as you can."
            )
        elif plan_text:
            frontier = plan_text
        else:
            here = norm_room(pick["room"]) if pick else ""
            open_h = [h for h in state["hyps"] if h["status"] == "open"]
            local = sorted(
                [h for h in open_h if norm_room(h["where"]) == here],
                key=lambda h: (-h["value"], h["attempts"]),
            )[:5]
            rest = sorted(
                [h for h in open_h if h not in local], key=lambda h: (-h["value"], h["attempts"])
            )[:3]
            shown = local + rest
            for h in shown:
                h["attempts"] += 1
            frontier = (
                (
                    "You are at a spot chosen for exploring. Test these open hypotheses, "
                    "starting with the ones here:\n"
                    + "\n".join(
                        f"- [{h['where']}] {h['name']}: {h['hypothesis']} Try: {h['test']}"
                        for h in shown
                    )
                )
                if shown
                else ("You are at a spot chosen for exploring: try what no earlier game has tried.")
            )
            if state["goals"] and state["goals"][0].get("test"):
                frontier += (
                    f"\nAlso test your working theory of the goal: {state['goals'][0]['test']}"
                )
        result, sr = None, None
        for _ in range(3):
            try:
                sr = Speedrun(make_chat(cfg.player, MODEL, seed), route, ref, frontier)
                result = play_game(
                    GameSpec(
                        seed,
                        cfg.move_cap,
                        cfg.usd_cap,
                        world=WorldModel.empty(tag),
                        stuck_after=10_000,
                        context=context,
                        router=sr.hook,
                    ),
                    chat=sr,
                    sink=JsonlSink(out),
                    trace=Trace(out / f"g{g}.trace.md"),
                )
                break
            except Exception as exc:
                print(f"[{tag} s{seed} g{g}] retry: {exc!r}"[:200], flush=True)
        if result is None:
            continue
        r = result.record
        with (out / f"{r.run_id}.moves.jsonl").open() as fh:
            moves = [json.loads(line) for line in fh]
        played = [(m["command"], m) for m in moves if m["command"] != "I give up"]
        states = trace([c for c, _ in played], seed)  # traced once, shared below
        if pick is not None and route and why["mode"] not in ("plan", "scoring"):
            died_soon = any(
                "you have died" in (m.get("text") or "").casefold()
                for m in moves[sr.i : sr.i + cfg.death_window]
            )
            t, b = danger.get(pick["key"], (0, 0))
            danger[pick["key"]] = (t + 1, b + died_soon)
        best_before = max((c["score"] for c in archive.values()), default=-1)
        new_cells = update_archive(archive, [c for c, _ in played], seed, states)
        best_now = max((c["score"] for c in archive.values()), default=-1)
        if not scoring:
            if new_cells == 0 and best_now <= best_seen:
                stall += 1
                if pick is not None and why["mode"] != "plan":
                    kn.zero[norm_room(pick["room"])] += 1
            else:
                stall, back_k = 0, 0
            since_gain = 0 if best_now > best_before else since_gain + 1
        best_seen = max(best_seen, best_now)
        if active is not None:
            wall, plan = active
            if wall == "__plateau__":
                past = best_now > best_before
            else:
                known = {
                    v for r_, d in kn.graph.items() if norm_room(r_) == wall for v in d.values()
                }
                past = walls.passed(wall, played, seed, known, sr.i, states)
            if past:
                plan["result"] = "cleared"
                if wall == "__plateau__":
                    # a plateau recurs: start fresh instead of retiring the mechanism
                    walls.plans["__plateau__"] = []
                    since_gain = 0
                else:
                    walls.cleared.add(wall)
            else:
                died_armed = died_holding(wall, played, states, [x["item"] for x in plan["get"]])
                plan["result"] = (
                    "inconclusive" if died_armed and not plan.get("retried") else "failed"
                )
                plan["retried"] = plan.get("retried", False) or plan["result"] == "inconclusive"
            active = None
        state, rstats, rcost = reflect(state, shown, r, moves, sr.i, cfg)
        kn.learn(played, seed, [h["name"] for h in state["hyps"]], states)
        peak = max((m.get("score", 0) for m in moves), default=0)
        deposits = sum(
            " in case" in m["command"].casefold() and (m.get("score_delta") or 0) > 0 for m in moves
        )
        row = {
            "seed": seed,
            "game": g,
            "kind": why["mode"].split("-")[0],
            "why": why,
            "spot": pick["room"] if pick else None,
            "route_len": len(route),
            "score": r.score,
            "peak": peak,
            "moves": r.moves,
            "new_cells": new_cells,
            "rooms_known": len({c["room"] for c in archive.values()}),
            "best": best_now,
            "facts": len(state["facts"]),
            "reflect": rstats,
            "deposits_scored": deposits,
            "cleared": sorted(walls.cleared),
            "plans": {
                k: [(p["need"][:50], [x["item"] for x in p["get"]], p["result"]) for p in v]
                for k, v in walls.plans.items()
                if v
            },
            "stall": stall,
            "cost": round(r.cost_usd + rcost + pm_cost, 5),
            "goal": state["goals"][0]["theory"][:140] if state["goals"] else None,
        }
        rows.append(row)
        with (out / "rows.jsonl").open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        (out / f"state-g{g}.json").write_text(json.dumps(state, indent=1))
        print(
            f"[{tag} s{seed}] g{g} {row['kind']}: {why} -> score {r.score} peak {peak} | "
            f"+{new_cells} spots, rooms {row['rooms_known']}, best {best_now}, cleared "
            f"{row['cleared']} plans {row['plans']} deposits {deposits}",
            flush=True,
        )
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True, choices=sorted(CONFIGS))
    p.add_argument("--tag", required=True)
    p.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    p.add_argument("--games", type=int, default=None)
    a = p.parse_args()
    cfg = CONFIGS[a.config] if a.games is None else replace(CONFIGS[a.config], games=a.games)
    with ProcessPoolExecutor(len(a.seeds)) as ex:
        rows = [r for rs in ex.map(chain, [(cfg, a.tag, s) for s in a.seeds]) for r in rs]
    print(
        json.dumps(
            {"tag": a.tag, "config": cfg.name, "cost": round(sum(r["cost"] for r in rows), 2)}
        )
    )


if __name__ == "__main__":
    main()
