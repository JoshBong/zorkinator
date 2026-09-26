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
| **Josh** — game connector | `adapter.py` `parser.py` `runner.py` | `adapter.reset/step`, `parser.parse`, `runner.play` (paper mode first) |
| **Hiamil** — Atlas + display | `db.py` `view/` | Atlas cluster, indexes, `db.py` helper, change stream, side-by-side replay view |
| **Seb** — inner loop | `builder.py` `player.py` `scribe.py` `monitor.py` | `builder.build_prompt`, `player.propose`, `scribe.update` |
| **Elliott** — outer loop | `verifier.py` `reflector.py` `versions.py` | `verifier.check`, `reflector.propose`, `versions.commit` |

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
| 11:30 | **Josh:** paper-mode baseline game running on real Jericho; smoke test gives seconds/move |
| 12:30 | **Seb:** harness game 1 (cold) running with Atlas state |
| 1:30 | **Elliott:** Reflector closes the loop; game 2 starts with learned rules |
| 1:30–4:00 | Learning games run; move cap set so ~6 games fit |
| 4:00 | Freeze. Pick final version. |
| 4:00–4:45 | Side-by-side replay + 1-min demo video. Submit by 4:45. |

Cut order if behind: tool pool → skills/vector search → Planner → live view (replay is enough).

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **zorkinator** (78 symbols, 76 relationships, 0 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/zorkinator/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/zorkinator/context` | Codebase overview, check index freshness |
| `gitnexus://repo/zorkinator/clusters` | All functional areas |
| `gitnexus://repo/zorkinator/processes` | All execution flows |
| `gitnexus://repo/zorkinator/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
