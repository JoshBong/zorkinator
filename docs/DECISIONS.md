# Decisions

Newest at the bottom. Anything marked PROPOSED is not agreed yet; do not build on it until it is.

## 2026-09-26 — Demo = game 1 vs game N side by side (by Josh Huang)

Replay two runs from `moves` (same seed) instead of relying on live play; the outer loop is the critical path, and Planner / tool pool / skills vector search get cut first. The pitch is one death that was avoided because of one learned rule.

## 2026-09-26 — Soft rules warn, only hard rules block (by Josh Huang)

ZorkGPT's LLM critic was wrong in 68 of 77 overrides and got removed; letting unproven rules block would repeat that. Soft = warning appended to the prompt; hard = block, and only after death-backed promotion.

## 2026-09-26 — No Jev / specialist decision model (by Josh Huang)

It would be a different project and breaks the same-model comparison against the paper. Every LLM call uses the same model.

## 2026-09-26 — PROPOSED, not yet agreed: v4 amendments

1. Promotion also replays the rule over all logged moves; if it would have blocked any move that later scored, it stays soft (the death-only check is near-circular).
2. `when` conditions are limited to a fixed field list (room, inventory, room_is_dark, known world_facts about the destination, command pattern).
3. Lean per-move prompt: cached prefix (paper prompt + this version's lessons) + ~500-token dynamic tail; rules only enter the prompt when they fire; facts are looked up by room, not searched.
4. Scribe LLM only on the first visit to a room; the parser handles the rest.

## SUPERSEDED — 2026-09-26 — Memory split: inner loop = L1 (per game), outer loop = L2 (general only) (by Josh Huang)

**Historical decision; the open-ended memory decision below supersedes its cross-game content restrictions. Do not implement the general-only or room-name rejection policy.**

Inner loop builds map/facts/notes from scratch each game (`world_facts` keyed by `run_id`, discarded after the game). Outer loop carries only general rules and procedures across games; nothing cross-game may contain a room name, and `versions.commit` rejects any rule/lesson that mentions a room name the parser has seen. No routes / skills / persistent map. Demo shows two effects: harness game 1 vs baseline (in-game scaffolding) and game N vs game 1 (general learning); a held-out seed tests that the learning transfers. A persistent-map run (L3) is optional comparison only, never the headline.

## 2026-09-26 — Outer-loop direction updated: open-ended persistent memories (Elliott discussion)

The latest outer-loop discussion permits storing whatever human-visible knowledge the agent finds useful across games, including maps, routes, hypotheses, and procedures. This revises the intended general-only/no-room-name direction above. Memory remains advisory; hard blocking still requires the fixed promotion procedure. Atlas stores evidence separately from versioned memory interpretations, and cold experiments must not inherit prior experiment memories.

The immutable revision schema, version manifests, and implementation status are documented in [OUTER_LOOP_MEMORY.md](OUTER_LOOP_MEMORY.md), and the shared shapes/signatures are now in `CONTRACTS.md`. Durable Reflector, versioning, Atlas access, the restart-safe between-game driver, exact-manifest prompt construction, and the Atlas smoke chain are implemented. Remaining outer/inner integration work is tracked in [INTEGRATION_HANDOFF.md](INTEGRATION_HANDOFF.md).

## 2026-09-26 — Benchmark model = Claude Haiku 4.5; 10 chains x 10 games (by Josh Huang)

We benchmark against our own baselines, not the paper's number, so every condition (paper loop, paper loop + past-chat memory, harness) uses `claude-haiku-4-5` to save cost; the Opus 4.5 run (44/350) is a side note. Each condition runs as 10 independent chains of 10 sequential games (100 games); chats are shared only within a chain.

## 2026-09-26 — Inner loop: empty KBs, n-game outer loop, exploration over brute force (by Seb)

The first version starts with an empty map, an empty item knowledge base and no objective notes. The outer loop runs n games. After each one it turns what was learned into versioned memories (kinds `room`, `map_edge`, `item`, `objective`, `hypothesis`, `run_summary`), and the next game starts from that version. Each inner-loop move injects location, exits, inventory, the model's own goal and open leads into a fresh prompt. A game ends at 500 moves, at death, on "I give up", or when the monitor finds no progress (new room, item, interaction or score change) for too long.

To keep play exploratory rather than a brute-force search or memorization: leads come only from the game text (never untried compass directions or valid actions); the prompt shows what was already tried and favors new actions and unexamined items; repetition triggers a warning, not a block; raw command sequences from earlier games never enter the prompt. The score is reported, never optimized directly. Design: [INNER_LOOP.md](INNER_LOOP.md).

## 2026-09-26 — Learn from surprises: the Player predicts each outcome (by Josh Huang)

The harness learns cause and effect from consequences, not from the score: the score is measured, never trained on. To make that measurable, each harness reply adds `Expect: <predicted outcome>` under the command (with `Goal:`); the next prompt shows "You expected: … / What happened: …", and the next reply opens with `Surprise: yes|no`, the model judging its own prediction (no extra model call). Changes: Seb, `player.py` parses `Expect:`/`Surprise:` and `builder.py` shows expected vs actual, with `extract_command` skipping `Surprise:`/`Goal:`/`Expect:` lines; Josh, `MoveRecord` gains `expected: str | None` and `surprise: bool | None`; Elliott, the Reflector's evidence packet ranks deaths first, then surprise moves; Himali, the live view shows expected vs actual with a surprise badge. New metric: surprise rate per game, which should fall as the harness learns, independent of score. Paper mode is unchanged. Seb reruns the 20-move smoke test after the change to confirm commands still extract cleanly.

## 2026-09-26 — Development and smoke tests use the OpenAI key and cheapest supported GPT model

From this point forward, model-backed development tests and smoke chains use `OPENAI_API_KEY`, not the Anthropic
key. The default test model is `gpt-5.6-luna`, currently documented by OpenAI as the cost-sensitive model for
high-volume workloads. Configure it through `OPENAI_TEST_MODEL` so the test default can be updated without
changing benchmark records when availability or pricing changes.

This does not silently rewrite the benchmark result above: published comparison runs keep their recorded model
until the benchmark owner explicitly changes that decision. Within any individual run, player, scribe, planner,
and reflector must still use the same provider and exact model. Never fall back between providers mid-chain.
Keys remain in `.env`, are gitignored, and must never be logged or committed.

Reference: [OpenAI API model guide](https://platform.openai.com/docs/models).

## 2026-09-26 — Atlas outer loop: publish atomically, retrieve version-safely, measure from existing evidence

The outer loop will keep Atlas narrowly focused on correctness, retrieval, and demonstrable evidence:

1. **Atomic version publication is required.** A committed child version, its immutable memory revisions,
   and its terminal `memory_events` audit record are one transaction. The existing transaction-backed
   publication path is the required path; a reader may treat only a committed `harness_versions` manifest
   as visible knowledge.
2. **Exact-version membership is the retrieval authority.** `recall_scored()` may use Atlas Vector Search
   only to rank immutable revisions referenced by the requested version's manifest. Similarity, subjects,
   kinds, or Atlas filter fields must never surface a historical, sibling-experiment, or uncommitted memory.
3. **Build a compact per-game evaluation query/view from existing `runs`, `memory_events`, and
   `harness_versions`.** It must report score, end reason/death move, cost, published memory/rule diff
   counts, and retrieval scores when available. This is the source for the learning curve and replay
   explanation; it does not introduce a second mutable "current knowledge" store.
4. **Do not add Atlas Stream Processing, Online Archive, time-series collections, or Database Triggers to
   the learning critical path.** They add deployment and asynchronous-failure surface without improving the
   sequential reflect → validate → commit → next-game boundary. The existing change stream remains for the
   live/replay view only.

This refinement changes no LLM authority: memories remain advisory and only the fixed verifier promotion
path may make a rule blocking. It also preserves the human-visible-information boundary.
