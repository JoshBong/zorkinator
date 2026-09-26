# Design (v3, 2026-09-23; day-of changes in DECISIONS.md)


## Claim
Gerrits 2026 (arXiv 2602.15867): frontier LLMs in a bare loop score <10% of Zork I on average, best
~75/350 (Claude Opus 4.5). Same model + this harness plays far better on its first game, and keeps
improving game over game by learning from its own deaths. No walkthroughs, no rewinding, and only what a
human player could see.

## Ground rules
- Human-visible information only: game text, status-line score/moves, room names parsed from text,
  inventory from text or the `inventory` command. Jericho internals (valid actions, object tree, RAM
  location, state hash) are logged for analysis, never shown to the model or verifier.
- No SAVE/RESTORE. Death ends the game. Rewinding = search; remembering across games = learning.
- No prewritten rules or lessons. Game 1 starts empty.
- **Same models as the paper, one model per run for every call** (moves, verifier-side LLM calls,
  planner, reflection). Primary: Claude Sonnet 4.5 (paper: ~Opus-level, cheaper). Headline: Opus 4.5
  (paper best ~75). Optional second vendor: GPT-5.2 or Gemini 3. Rerun the bare loop ourselves on the
  same Jericho setup rather than relying only on the paper's numbers.

## Inner loop (one move)
1. Build prompt fresh from Atlas, no chat history (Voyager): world facts (~40% cap), top-5 skills,
   relevant rules/lessons, last 5 moves.
2. Model proposes one command.
3. Verifier checks it against learned rules. HARD = blocked. SOFT = model may override with a reason.
   Rejection reason goes back; max 3 tries (IMO bug-report loop; Cobbe: more tries backfire).
4. Jericho runs it. Read outcome from game text: score change, new room, item change, death, nothing.
5. Upsert facts with move stamp, newest wins (MemoryAgentBench selective-forgetting fix).
6. Progress flat -> separate planner picks a new tactic; goal failing 4x gets shelved (AdaEvolve, Voyager).

## Outer loop (between games)
7. One reflection call reads the game and edits rules/lessons as small diffs (Model Eats the Stack:
   single reflection ~83% @ $1.09 vs GEPA ~69% @ $12.16).
8. New rules start SOFT; promoted to HARD only with evidence (a death / irreversible loss), linked to the
   moves that justified it. Fixed: the procedure. Learned: the content.
9. Every harness version archived with parent + scores (DGM). Hard-rule procedure, scoring, budget are
   outside what the harness can change (MAST: optimizer deleted its own verifier).
10. Lessons tagged route-type (walkthrough-like) vs principle-type.

## MongoDB
| Collection | Holds | Feature |
|---|---|---|
| runs | one per game: version, model, seed, score, moves, deaths, tokens, cost | aggregation |
| moves | one per move: game text, proposals, rejections + reasons, command, score, room, latency | change stream -> live view |
| world_facts | {subject, attr, value, move} | upsert |
| rules | text, soft/hard, provenance moves, fire count | aggregation |
| skills | verified action sequences | Atlas Vector Search + automated Voyage embeddings |
| lessons | cross-game lessons | LangGraph MongoDB long-term memory store |
| harness_versions | settings + rules snapshot, parent, scores | archive |

## Results to show
1. Cold first game (zero lessons) vs the bare loop, same model, same 500 moves.
2. Learning curve over K full games; train and score on different Jericho seeds; curve also shown with
   route-type lessons removed.
3. Leave-one-out table: points each component is worth. Points per dollar alongside points.

## Demo
Live game on screen. Beside it: world map + facts filling in, a verifier rejection with its reason, a
rule getting promoted after a death, score climbing past the paper's 75 line. Then the learning curve
and ablation table. The view is fed by a change stream on `moves`. It shows the harness; it is not the product.

## Hackathon vs paper (2026-09-23)
- **Saturday = demo.** Judging: technical demo 35 / implementation difficulty 25 / creativity 25 / impact 15.
  Must-haves: live play with world model + verifier rejections + rule promotion visible; score past ~75
  with the paper's model; one small learning curve; Atlas visibly load-bearing. One own baseline run +
  the paper's number is enough.
- **After = paper** (Josh wants to write it if it works). Deferred to the paper: multi-seed, error bars,
  full leave-one-out table, cross-tier failure shift, route-lessons-removed curve.
- Log every run/move with seed, model, harness version from game one so hackathon runs = pilot data.
- Budget plan: GPT-5.2 ($1000) for bulk experiments, Opus 4.5 ($500) for headline runs. Cache the bare
  baseline (uncached Opus baseline game ~ $90). Per-run dollar cap in code; smoke-test on 20-move games.

## Framing: Statement One (Recursive Harnessing), per Elliott (2026-09-23)
Statement One names four things to evolve; map each explicitly:
- rules -> learned rules (empty at game 1)
- guardrails -> soft->hard promotion backed by deaths
- context policies -> memory share / k skills / stuck threshold, actually changed by the outer loop
- tool access -> NEW: optional tool pool (walk-to-known-room, transcript search, run skill, map lookup);
  reflection toggles tools per version based on use + score
Demo: game-1 vs game-N harness diff (one Atlas query over harness_versions). Fixed parts = "the
constitution" (promotion procedure, scoring, budget). Statement Two mentioned once, not a second pitch.

