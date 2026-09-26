# Architecture map

**The AHA:** same model, same prompt (copied verbatim from the paper), same 500-move limit. One switch
flips between **paper mode** (the paper's bare loop) and **harness mode**. Any score difference is the harness.

---

## 1. Data flow

```
                         ┌──────────────────────── MongoDB Atlas ─────────────────────────┐
                         │ world_facts · transcript · rules · skills · lessons            │
                         │ runs · moves · harness_versions                                │
                         └───▲────────────▲──────────────▲───────────────▲───────────────┘
                             │            │              │               │
 ┌─ INNER LOOP (every move) ─┼────────────┼──────────────┼───────────────┼──────────────┐
 │                           │            │              │               │              │
 │  [2 Context Builder] ─────┘            │              │               │              │
 │        │ paper prompt + state blocks   │              │               │              │
 │        v                               │              │               │              │
 │  [3 Player LLM] ── one command ──> [4 Verifier] ─ rejected + reason ─> back to Player │
 │                                        │ pass (max 3 tries)          │              │
 │                                        v                             │              │
 │                              [5 Tool Expander] (macro -> game moves)  │              │
 │                                        │                             │              │
 │                                        v                             │              │
 │                              [0 Game Adapter / Jericho]              │              │
 │                                        │ game text, score, moves     │              │
 │                                        v                             │              │
 │                              [1 Parser] ──> [6 Scribe LLM] ──────────┘ fact upserts │
 │                                        │                                            │
 │                                        v                                            │
 │                              [7 Progress Monitor] ─ stuck ─> [8 Planner LLM] tactic │
 └──────────────────────────────── death / move 500 ───────────────────────────────────┘
                                          │
 ┌─ OUTER LOOP (between games) ───────────v───────────────────────────────────────────┐
 │  [9 Reflector LLM] reads game summary -> proposes diffs:                            │
 │     new soft rules · lessons · context-policy changes · tool on/off                 │
 │  [4 Verifier: promotion check] soft -> hard only with evidence                      │
 │  [10 Version Manager] validity gate -> save new harness_version (parent + scores)   │
 │  [13 Between-game Driver] resumes one cold chain and passes each child to its game  │
 └─────────────────────────────────────────────────────────────────────────────────────┘
   [11 Run Manager] parallel games, seeds, $ caps      [12 Live View] change stream on moves
```

---

## 2. Components

| # | Component | Type | Job | Reads | Writes |
|---|---|---|---|---|---|
| 0 | Game Adapter | code (Jericho) | reset(seed), step(cmd) -> text, score, moves, done. Blocks RESTART / SAVE / RESTORE. Game internals logged for analysis only. | | moves, runs |
| 1 | Parser | code | From game text only: room title, death, score change, no-op responses ("nothing happens", "I don't understand"), inventory replies | game text | moves |
| 2 | Context Builder | code | Builds each prompt: **paper's prompt verbatim** + state blocks (facts, rules, skills, lessons, last 5 moves, available tools, current tactic). No chat history. | world_facts, rules, skills, lessons | |
| 3 | Player | LLM (paper's model) | Returns ONE command, same output contract as the paper | prompt | |
| 4 | Verifier | code, **fixed** | Checks the command against rules + tried-actions log. HARD = block. SOFT = warn; Player may override with `OVERRIDE: <reason>`. Also the only thing that can promote rules (see section 4). | rules, world_facts, moves | rules (status, fire count) |
| 5 | Tool Expander | code | Expands enabled tool commands into real game moves, each verified. Every expanded move counts toward the 500. | world_facts, skills | |
| 6 | Scribe | LLM (same model) | Turns new game text into atomic fact upserts `{subject, attr, value, move}`. Newest wins. | game text, relevant facts | world_facts |
| 7 | Progress Monitor | code | Tracks recent score gain + new rooms/items. Below threshold -> stuck. Goal failed 4x -> shelved. | moves, world_facts | |
| 8 | Planner | LLM (same model) | When stuck: picks a new tactic (explore nearest untried exit, revisit an open puzzle...), rotating away from failed ones | world_facts, failure log | runs (tactics) |
| 9 | Reflector | LLM (same model) | After each game: reads a summary and proposes **diffs** (small edits, not rewrites) to rules, lessons, context policy, tool access | runs, moves, rules | proposals |
| 10 | Version Manager | code, **fixed** | Applies accepted diffs, checks the result is valid, saves a new version with its parent and scores. Picks the version for the next game. | harness_versions | harness_versions |
| 11 | Run Manager | code, **fixed** | Runs games in parallel, sets seeds, enforces per-run $ cap, logs every run | | runs |
| 12 | Live View | UI | Change stream on `moves`: game text, world map, rejections, rule promotions, score vs the paper's ~75 line | moves, rules | |
| 13 | Between-game Driver | code, **fixed** | Creates an empty, experiment-isolated root; for each persisted harness game, reflects once, commits idempotently, and supplies the child version to the next game. Resumes without repeating a persisted proposal and stops new reflection at the chain budget. | runs, memory_events, harness_versions | harness_versions, memory_events |

---

## 3. What the harness can change vs what is fixed ("the constitution")

| Evolvable (Statement One) | How it changes |
|---|---|
| **Rules** | Reflector proposes; start soft |
| **Guardrails** | Soft rules promoted to hard by the Verifier with evidence |
| **Context policy** | Memory share of the prompt, # skills retrieved, # recent moves shown, stuck threshold, retry cap |
| **Tool access** | Which optional tools are enabled (see section 5) |
| Lessons + skills | Reflector writes lessons; skills saved when a sequence led to progress |

| Fixed (never evolvable) | Why |
|---|---|
| Verifier logic + promotion procedure | Otherwise the harness learns to delete its own safety checks |
| Paper's prompt text | Keeps the comparison to the paper clean |
| Scoring, 500-move cap, $ caps, seeds | The benchmark itself |
| No SAVE/RESTORE/RESTART, human-visible info only | The fairness rules |

---

## 4. Rule format + promotion (the Verifier's second job)

```json
{
  "id": "r12",
  "when": { "action": "drop (lamp|lantern)", "room_is_dark": false, "next_rooms_dark": true },
  "verdict": "block",
  "reason": "Without a light source the next room is dark and a grue kills you",
  "status": "soft",
  "evidence": ["run7:move212", "run7:move215"],
  "fired": 0
}
```

- **Birth:** Reflector proposes a rule, citing the moves that motivated it. It starts **soft**.
- **Promotion to hard:** mechanical check, no LLM. The Verifier (a) confirms a cited move ended in death or an
  irreversible loss, and (b) replays the rule against the logged state at that move to confirm it
  **would have blocked it**. Both true -> hard.
- **Demotion:** only if later evidence shows the rule blocked a move that was needed for progress. Same
  replay check.

---

## 5. Optional tool pool (tool access evolves)

Tools look like commands to the Player, get expanded by component 5, and every step is verified and counted.

| Tool | Does | Built from |
|---|---|---|
| `GOTO <room>` | Walks the shortest known path | map graph in world_facts |
| `RECALL <query>` | Searches the transcript, returns matching lines | transcript |
| `DO <skill>` | Replays a saved successful sequence | skills (Atlas Vector Search) |
| `MAP` | Shows the known map + untried exits | world_facts |

Game 1: all off, or all on. The Reflector toggles them per version based on use + score.

---

## 6. Paper mode vs harness mode

| | Paper mode (baseline) | Harness mode |
|---|---|---|
| Prompt | Paper's prompt verbatim | Same prompt + state blocks |
| History | Full chat history | None; state from Atlas |
| Components on | 0, 11 only | all |
| Model / moves / seed | same | same |
| SAVE/RESTORE | blocked (stricter than the paper, which listed it) | blocked |

---

## 7. LLM calls per move (all the paper's model, no thinking)

- Player: 1, plus up to 2 retries on rejection
- Scribe: 1, small prompt
- Planner: only when stuck
- Reflector: once per game

---

## 8. Build order for the day

1. Game Adapter + Run Manager + logging to Atlas -> run **paper mode**, get the baseline running
2. Parser + Scribe + Context Builder -> harness plays with memory, no rules
3. Verifier (repeat detection first, then rules) + Reflector + promotion
4. Progress Monitor + Planner
5. Version Manager + Between-game Driver; wire the game runner callback to persist each version-scoped game
6. Run a 20-move, two-game harness smoke chain and verify game 2 receives only game 1's committed manifest
7. Live View
Each step is a working, demoable harness. Cut from the bottom if time runs out.

---

## 9. Team split

| Person | Owns (component #) |
|---|---|
| **A: Game, infra, demo** | 0 Game Adapter · 1 Parser · 11 Run Manager · logging `runs`/`moves` · paper-mode baseline · 12 Live View · demo video · integration |
| **B: Inner loop** | 2 Context Builder · 3 Player · 6 Scribe (`world_facts`) · 7 Progress Monitor · 8 Planner · 5 Tool Expander |
| **C: Rules + evolution** | rule format · 4 Verifier (`check()` called by B every move, plus promotion) · 9 Reflector · skills + lessons (Vector Search, LangGraph store) · 10 Version Manager |
| **Together first** | Mongo doc shapes · rule format · function signatures (`adapter.step()`, `verifier.check()`, `builder.build_prompt()`, `reflector.propose()`), stubbed so everything runs end to end |
