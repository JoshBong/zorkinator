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

The proposed interface, immutable revision schema, version manifests, and implementation plan are documented in [OUTER_LOOP_MEMORY.md](OUTER_LOOP_MEMORY.md). Those concrete schema/signature changes require coordination with Josh, Himali, and Seb and an accompanying `CONTRACTS.md` amendment before implementation; this decision entry does not silently change the current shared contract.

## 2026-09-26 — Benchmark model = Claude Haiku 4.5; 10 chains x 10 games (by Josh Huang)

We benchmark against our own baselines, not the paper's number, so every condition (paper loop, paper loop + past-chat memory, harness) uses `claude-haiku-4-5` to save cost; the Opus 4.5 run (44/350) is a side note. Each condition runs as 10 independent chains of 10 sequential games (100 games); chats are shared only within a chain.
