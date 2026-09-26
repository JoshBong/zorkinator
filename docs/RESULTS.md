# Results

## 2026-09-26 — Baseline: paper loop + past-chat memory, Claude Haiku 4.5

Setup: paper's basic prompt (verbatim), full chat history, 500-move cap, seed 0, no thinking. Past-chat memory =
`recent_chats` / `conversation_search` tools over the chain's earlier games plus a one-line note that they exist
(see `PAPER_BASELINE.md`). 10 independent chains x 10 sequential games = 100 games, 0 failures, $39.54 total.
Raw data: `runs/runs.jsonl` (rows with `chain` = `chats-chain*`), transcripts in `chats/haiku-memory/`.

| | |
|---|---|
| Mean / median score | **32.0 / 34** (of 350) |
| SD / range | ~13 / 0–59 |
| Game 1 of each chain (no memory yet) | 25.9 (n=10) |
| Games 2–10 | 32.7 (n=90) |
| Mean moves | 244 |

Mean score by position in chain:

| Game | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| Mean | 25.9 | 31.0 | 35.0 | 31.1 | 31.5 | 33.1 | 30.7 | 32.5 | 35.4 | 33.9 |

**Memory was almost never used:** 6 `conversation_search` calls in 2 of the 90 games that had past chats (scores 25
and 0); zero lookups at game start. The game-1 vs later gap (one-sided permutation p≈0.06) therefore cannot be
attributed to memory. This reproduces the paper's finding that access to previous chats did not produce learning.
These 100 games double as the no-memory baseline.

How games ended:

| Ending | Games |
|---|---|
| Gave up ("I give up") | 45 |
| Died | 44 — troll 29, thief 7, grue 3, drowned 3, other 2 |
| 500-move cap | 11 |

**Harness targets:** beat mean ~32 / median 34 with Haiku 4.5 on seed 0. First rule to learn: the troll (2/3 of
deaths). Cheapest points: stop the 45% give-up rate (Planner offers a new tactic when progress stalls).

## Side note — Opus 4.5, single game
44/350 in 113 moves, killed by the thief. $1.22. (The paper's best Opus 4.5 run: ~75, different game build and
app setting.)

## 2026-09-26 — Does the harness act on learned knowledge? (seeded chain)

Question: when the version a game plays contains real knowledge of the world, does the model use it?
Setup: chain `haiku-seeded-02`. Game 1 is a **scripted** game (the first 60 moves of the Zork I walkthrough,
recorded as model `scripted-walkthrough`), reflected on by Haiku like any game. Game 2 is Claude Haiku 4.5
playing on that version, 100-move cap, no route in the prompt.

What the seeded version held (30 memories, 0 rules): the window at Behind House opens into the Kitchen; the
lantern is on the trophy case and is needed in the dark; moving the rug reveals the trap door; the troll can
be killed with the elvish sword; treasures go in the trophy case (egg 5, torch 14, sceptre ...).

| Game | Moves | Rooms | Examines | Score | Ended |
|---|---|---|---|---|---|
| 1 (scripted) | 60 | 19 | 0 | 63 | cap |
| 2 (Haiku, seeded) | 48 | 13 | 6 | **25** | died to the troll |
| unseeded Haiku games (12, chains `haiku-short-01/02`) | 100 | 7–11 | 19–52 | **0–10** | cap / stuck |

Game 2's key commands, in order: `open window` (13), `get lantern` (29), `move rug` (30), `light lantern`
(31), `open trapdoor` (33), `down` (34), `get sword` (45), then it fought the troll and died at 48. Every one
of those is a fact from the seeded version; the route to the house (via the forest and the Clearing) was its
own. Unseeded games reached 5–10 points in 100 moves and never opened the trap door.

Reading: the model uses learned world knowledge when it has it; what the unseeded chains lacked was
knowledge worth acting on, not the ability to act on it. The frontier (`lead` memories, idle → explore) is
the mechanism for building that knowledge without a scripted game.
