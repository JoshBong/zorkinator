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
| **Josh** — game connector + verifier | `adapter.py` `parser.py` `runner.py` `verifier.py` | `adapter.reset/step`, `parser.parse`, `runner.play` (paper mode first), then `verifier.check` + promotion check |
| **Himali** — Atlas + display | `db.py` `view/` | Atlas cluster, indexes, `db.py` helper, change stream, side-by-side replay view |
| **Seb** — inner loop | `builder.py` `player.py` `scribe.py` `monitor.py` | `builder.build_prompt`, `player.propose`, `scribe.update` |
| **Elliott** — outer loop | `reflector.py` `versions.py` | rule format, `reflector.propose`, `versions.commit` (calls Josh's promotion check) |

Changing a signature or document shape in `docs/CONTRACTS.md` = tell the other two first, then update the file in the same commit.
Josh + Elliott agree the rule format and the allowed `when` fields first (Reflector writes rules, verifier reads them).
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
- Jericho is untyped upstream. `typings/jericho/__init__.pyi` is the enforced local API surface: any change
  that uses another Jericho symbol, argument, or return value must update that stub in the same commit.
  Do not silence missing imports globally; add a minimal verified stub or a documented module-specific exception.
- Secrets live in `.env` (gitignored). Never commit keys or print them.
- Repo is public and judged on work done today: no code copied from other projects (ZorkGPT, jev-zork, orbit).
- No Streamlit (banned by the hackathon). The view is evidence; the loop is the product.

## Timeline

| Time | Milestone |
|---|---|
| 10:50 | Stubs for all contract functions; loop runs end to end on fake data |
| 11:30 | **Josh:** paper-mode baseline game running on real Jericho; smoke test gives seconds/move |
| 12:30 | **Seb:** harness game 1 (cold) running with Atlas state |
| 1:30 | **Elliott:** Reflector closes the loop; game 2 starts with learned rules |
| 1:30–4:00 | Learning games run; move cap set so ~6 games fit |
| 4:00 | Freeze. Pick final version. |
| 4:00–4:45 | Side-by-side replay + 1-min demo video. Submit by 4:45. |

Cut order if behind: tool pool → skills/vector search → Planner → live view (replay is enough).

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **zorkinator** (115 symbols, 132 relationships, 1 execution flow).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "main"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "main" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/zorkinator/context` | Codebase overview, check index freshness |
| `gitnexus://repo/zorkinator/clusters` | All functional areas |
| `gitnexus://repo/zorkinator/processes` | All execution flows |
| `gitnexus://repo/zorkinator/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
