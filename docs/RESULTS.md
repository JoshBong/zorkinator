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
