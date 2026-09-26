# Prior art (read 2026-09-26) — learn from, never copy code (new-work rule)

## Prior art: stickystyle/ZorkGPT
Learn-from only (new-work rule). zorkgpt.com live; author abandoned for ZorkGPT2. Its own reports:
- LLM critic (= an LLM verifier) wrong 88% of the time it was overridden (68/77), +38% cost -> removed.
  Kept the pre-LLM object-tree validation. => our Verifier stays mechanical; soft rules advise, never block.
- Stuck periods of 46-102 turns at flat score; escapes were tactical finds, not anti-loop behavior.
  Fix they shipped: terminate after 40 turns without score change. => cheap stop rule for short learning games.
- Cross-episode learning = LLM rewrites one markdown "insights" section, turn data truncated to 2000 chars;
  uses Z-machine object tree + valid-verb lists (not human-visible). => our contrast: structured rules with
  evidence moves, death-backed promotion, human-visible info only.
- ROM in repo: jericho-game-suite/zork1.z5, md5 matches b732a93a... (use it; it's the game file, not their code).

## Prior art: Resadan-dev/jev-zork
TypeSafe "Jev" typed-decision model picks from Jericho's valid-action list (<=255/turn); anti-loop = halve
prob of actions already tried in the same world-state hash; last 8 turns in state; NO cross-game learning.
Reference game: 44/350 in 150 moves, 61 s, 262 ms/decision, $0.0075. Has an HTML replay player + video renderer.
=> 44@150 is a useful short-game reference, but it's with the valid-action list + state hash (not
human-visible); say so if compared. Differentiator again = learning across games.
