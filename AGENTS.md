# Agent kickstart — zorkinator

Read this whole file before writing code. Then read, in order:
1. `docs/CONTRACTS.md` — Mongo document shapes + function signatures. **Final authority.** If code and this file disagree, fix the code.
2. `docs/DECISIONS.md` — what's decided. Items marked PROPOSED are not agreed; don't build on them.
3. `docs/ARCHITECTURE_MAP.md` — components, data flow, rule format (read the part for your owner letter).
4. `docs/DESIGN.md` / `docs/PRIOR_ART.md` — on demand.

## What we're building (hackathon, due 5:00 PM today)

A harness that plays Zork I through Jericho and **gets better game over game by learning rules from its own
deaths**. Baseline: arXiv 2602.15867, frontier LLMs in a bare loop score <10% of Zork I, best ~75/350.
Same model + same prompt in two modes: **paper mode** (bare loop) and **harness mode**. Any score gap is the harness.

**The demo:** game 1 (cold, zero rules) vs game N (final harness), replayed side by side from the `moves`
collection, same seed, plus the rule diff that explains one avoided death. Everything serves that.

## Ownership — stay in your lane

| Owner | Files (proposed, under `zorkinator/`) | Contract functions |
|---|---|---|
| **A** game, infra, demo | `adapter.py` `parser.py` `runner.py` `db.py` `view/` | `adapter.reset/step`, `parser.parse`, `runner.play` |
| **B** inner loop | `builder.py` `player.py` `scribe.py` `monitor.py` | `builder.build_prompt`, `player.propose`, `scribe.update` |
| **C** outer loop | `verifier.py` `reflector.py` `versions.py` | `verifier.check`, `reflector.propose`, `versions.commit` |

Changing a signature or document shape in `docs/CONTRACTS.md` = tell the other two first, then update the file in the same commit.
First step for everyone: a stub of your functions that returns plausible fake data, so the loop runs end to end before anything is real.

## Hard rules (the benchmark — never change these)

- **Human-visible info only** goes to any LLM: game text, score/moves, rooms/inventory parsed from text.
  Never `get_valid_actions`, world-state hash, object tree, RAM, or the walkthrough. Log them for analysis if you want; never prompt with them.
- **No SAVE / RESTORE / RESTART.** Death ends the game.
- **No prewritten rules or Zork hints.** Game 1 starts with zero rules and zero lessons.
- **Same model for every LLM call in a run** (player, scribe, planner, reflector).
- **Same seed** for game 1 and game N.
- **Verifier logic and promotion procedure are fixed code**, never editable by the Reflector.
- Soft rules only **warn** (append a line to the prompt). Only hard rules **block**, and a rule becomes hard only via the mechanical promotion check.
- Log **every move** to `moves` and every game to `runs` from the first run — these are also research data.

## Practical

- Setup: see `README.md`. Python 3.11 (Jericho breaks on 3.14). `zork1.z5` md5 must be `b732a93a6244ddd92a9b9a3e3a46c687`.
- Models: `claude-sonnet-4-5-20250929` (primary), `claude-opus-4-5-20251101` (headline). Anthropic Python SDK v1+:
  **do not pass `temperature`/`top_p`/`top_k`** — it raises `TypeError`.
- Keep the per-move prompt small: no chat history; state comes from Atlas each move. Put fixed text first so prompt caching works.
- Per-run dollar cap and a stop after 40 moves without a score change (in `runner.play`).
- Smoke-test everything on 20-move games before any long run.
- Secrets live in `.env` (gitignored). Never commit keys or print them.
- Repo is public and judged on work done today: no code copied from other projects (ZorkGPT, jev-zork, orbit).
- No Streamlit (banned by the hackathon). The view is evidence; the loop is the product.

## Timeline

| Time | Milestone |
|---|---|
| 10:50 | Stubs for all contract functions; loop runs end to end on fake data |
| 11:30 | **A:** paper-mode baseline game running on real Jericho; smoke test gives seconds/move |
| 12:30 | **B:** harness game 1 (cold) running with Atlas state |
| 1:30 | **C:** Reflector closes the loop; game 2 starts with learned rules |
| 1:30–4:00 | Learning games run; move cap set so ~6 games fit |
| 4:00 | Freeze. Pick final version. |
| 4:00–4:45 | Side-by-side replay + 1-min demo video. Submit by 4:45. |

Cut order if behind: tool pool → skills/vector search → Planner → live view (replay is enough).
