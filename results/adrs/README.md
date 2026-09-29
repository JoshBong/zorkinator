# ADRS on a Zork I harness: results (2026-09-27 to 09-29)

An outer optimization loop (ADRS: propose a harness change → run it → grade it against predictions written
beforehand → keep or discard) was used to improve the harness an LLM plays Zork I through. The model plays; the
harness decides what it remembers, where each game starts, and what it tries when it is stuck.

The rules for every version:

- **Model:** `gpt-5.6-luna`, no reasoning on any call.
- **Prompt:** the paper's `basic` prompt (arXiv 2602.15867, Appendix A). It has no Zork knowledge.
- **Evidence-only memory:** every fact the notebook keeps must quote the game's own output, and the harness never
  tells the model anything about Zork.
- **Fixed seed per chain:** Jericho is deterministic on a fixed seed, so a known route can be replayed.

## Setup

| | |
|---|---|
| Engine | Jericho (Z-machine), Zork I, fixed seed per chain |
| Move cap | 500 per game |
| Chain | 16 games on one seed; seeds 0, 1, 2 (development seeds) |
| Scoring games | every 4th game: replay the best known route, then play for points |
| Other games | explore from a chosen spot, run a plan at a wall, or (x11) run experiments first |
| Headline metric | mean final score of the 12 scoring games (3 chains × 4) |

## How a chain works (x10)

1. **Archive (Go-Explore):** every spot reached (room + score) is kept with the best route to it. Better means
   higher score, then more items held, then shorter. Routes are shortened by deleting chunks and re-checking by
   replay that the spot, its score and its items are still reached.
2. **Speedrun:** the harness replays the route itself, with no model calls, and hands control to the model at the
   first step that doesn't match (room or score).
3. **Notebook:** after each game a reflector writes facts, rules (the same effect seen in 2+ places) and a goal
   theory. Each needs exact quotes from that game's transcript, or it is dropped. All facts are shown on every move.
4. **Walls:** a room with 2+ deaths or 2+ visits without progress gets a post-mortem. The post-mortem proposes plans
   that must differ from what already failed ("get X, go to Y, try Z"). Each plan is compiled into a route that is
   checked by replay to arrive holding X.

Code: `adrs/core.py` (one module, configs `x9` and `x11`). Tests: `adrs/test_core.py`, 34 tests against the real
game engine, no model calls.

## Results by version

| Version | What changed | Games | Result |
|---|---|---|---|
| Bare loop (paper protocol) | full chat history, no harness, seeds 0–4 | 20 | mean **40.8** (SD 7.8), max 49 |
| Harness v0 | map, exit lists, leads, state lines in the prompt | 6 | mean **17.5** |
| v4c | v0 with those views removed | 12 | mean **36.8** |
| Frozen notebook | best learned notebook shown every move | 12 | 46.2, **retracted**: a replication gave 40.8 |
| x4 | Go-Explore: archive, route replay, 16-game chains | 48 | scoring mean **44.2** |
| x5 | + quote-checked notebook, evidence-only goal theory | 48 | **45.6** |
| x6 | notebook scoped to the current room | 48 | **36.2**: showing every fact beat scoped retrieval |
| x7–x9 | curiosity lists, failure-driven replanning | stopped | bugs found mid-run; rewritten as `adrs/core.py` |
| **x10** | clean rewrite of x9 (replanning at walls) | 48 | **55.1**, best run **78** |
| x11 | + experiment sessions on items of unknown purpose | 48 | **37.8**, 0 score gains from 47 experiments |

The first finding came before any learning: the structured views were wrong often enough that the model trusted
them over the game's text, and removing them doubled the cold score.

## x10 (headline)

| Seed | Scoring games (final) | Best | Rooms seen |
|---|---|---|---|
| 0 | 49 / 49 / 64 / 64 | 74 | 32 |
| 1 | 49 / 49 / 68 / 78 | 78 | 41 |
| 2 | 44 / 39 / 49 / 59 | 59 | 30 |

Cost $10.37. The score gains came from exploration games, not from the wall replanning (x10 cleared no walls).
Two things the model worked out from the game's own text (`excerpts/discoveries.md`):

- **Loud Room:** in game 0 the room echoed every command ("bar bar ..."). In game 1 the model typed `echo bar`,
  the room went quiet, and taking the platinum bar scored +10. Asked cold (no play, no tools) how to get the bar in
  Zork I's Loud Room, the same model answered wrong 3 times out of 3.
- **Dam:** `turn bolt with wrench` → "The sluice gates open"; trying to cross → "You would drown"; `wait` → "the
  water level is now quite low". It crossed and took the trunk of jewels (+15). Whether the bolt idea was inference
  or recall is not separable; the cold probe didn't ask about the dam.

What it never did: put a treasure in the trophy case. 0 deposits in 48 games. Every point came from taking an item or
reaching a place, and the goal theory, built only from evidence, settled on "the game rewards collection".

## x11 (negative result)

x11 added experiment sessions. When progress stalled, the model proposed up to 5 experiments with items whose purpose
was unknown: where to go, what to carry, 1–3 commands, and what it expected. Each ran from a restore (replay); only
the game text and the score came back.

- 90 proposed, 47 ran, 32 unreachable, 11 repeats; **0 raised the score, 0 deposits**. Scoring mean 37.8.
- **Why it failed:** "purpose found" was defined as "a command using the item raised the score", so it was the score
  again under another name. It never fired. Real findings with no points attached were thrown away, e.g. the
  Maintenance Room buttons: yellow → "Click.", red → lights on, blue → jammed.
- **Logistics:** a third of the experiments were unreachable, and many that ran failed with "You don't have that!"
  because only items picked up before could be carried.
- **One positive:** wall plans cleared walls in play for the first time (seed 2: troll, with the sword; seed 0: the
  maze).

Lesson: a progress signal that can only fire after points arrive cannot steer toward the points. A verifier for
understanding has to credit visible change in the world (a new reply, a changed room, a new exit), with points and
death as nudges on top.

## Comparing with the paper (arXiv 2602.15867)

| | Paper | This harness (x10) |
|---|---|---|
| Move cap | 500 | 500 |
| Attempts | 5 runs per model × prompt | 16 games per chain, 3 chains |
| Memory across attempts | chatbot apps with access to previous chats | quote-checked notebook + route replay by the harness |
| Model / build | Opus 4.5 (best) and others, browser Zork | gpt-5.6-luna, Jericho |
| Reported | ~10% of 350 on average; best run ~75 | best run 78; scoring-game mean 55.1 |

The fair statement: with the same move cap and memory across attempts, the best run here was 78 against the paper's
best of about 75, using a different model and a harness that remembers and replays routes for it.

## Caveats

- **Development seeds only.** Every version was built and graded on seeds 0–2. There is no evaluation yet on unseen
  seeds or on a second game.
- **Variance.** The model is sampled, so the same seed and code give different games: seed 0's first game scored 49
  in x10 and 0 in x11, and Go-Explore builds on that first game. With 3 chains, differences of about ±15 between
  versions are within noise.
- **Not one-knob comparisons.** Versions change several things at once.

## Reproduce

```bash
# needs OPENAI_API_KEY; runs write to runs/adrs/<tag>/ (gitignored)
git checkout ade8319 && python -m adrs.core --config x9  --tag x10 --seeds 0 1 2 --games 16
git checkout 3bbaab9 && python -m adrs.core --config x11 --tag x11 --seeds 0 1 2 --games 16
PYTHONPATH=. python -m unittest adrs.test_core      # engine-backed tests, no model calls
```

## Data here

- `x10/s*/`, `x11/s*/`: `config.json`, `rows.jsonl` (one row per game: mode, spot, score, peak, new spots, walls,
  plans, cost), `notebook-final.json` (facts, hypotheses, goal theories after the last game).
- `x11/s*/experiments.jsonl`: every experiment. Ones that ran have what was expected and what the game said;
  rejected ones have `status` (unreachable, repeat, invalid).
- `excerpts/discoveries.md`: the Loud Room and dam traces.

Full per-move traces (about 80 MB for x10 and x11) are available on request.
