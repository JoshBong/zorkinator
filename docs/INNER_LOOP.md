# Inner loop design (Seb)

Status: agreed direction (2026-09-26). The code isn't built yet. This file describes harness mode for one game. Between games, see
[OUTER_LOOP_MEMORY.md](OUTER_LOOP_MEMORY.md) (Elliott). For shapes and signatures, [CONTRACTS.md](CONTRACTS.md) is still the authority.

## Objective

The harness plays Zork I one command per move. Before each move it tells the model where it is, where it can
go, what it's carrying, and what it's trying to do. That information comes from knowledge bases (KBs) that
**start empty** and fill up from the harness's own play. After each game the outer loop decides what to keep. The next
game starts from that published version. We want the model to **explore and experiment**, not to replay a
route or walk the map exhaustively.

## Loop shape

```
outer loop, n iterations (Elliott)
  version = root (empty map, empty items, empty objectives)
  for i in 1..n:
      run = play("harness", seed, 500, version_id=version)      # inner loop, below
      proposal = reflector.propose(run.run_id)                   # what to keep from this game
      version = versions.commit(version, proposal, run.run_id)   # publishes the child version

inner loop, one game (Seb)
  world = WorldModel.load(version_id)       # KB snapshot from the version + empty per-game state
  text = adapter.reset(seed); scribe.observe(world, 0, None, text)
  for n in 1..500:
      prompt = builder.build_prompt(world, n)
      cmd, goal = player.propose(prompt, feedback)                # feedback = verifier rejection, max 3 tries
      verdict = verifier.check(cmd, world, version)               # Josh; stub returns ok until built
      result = adapter.step(cmd)
      parsed = parser.parse(result.text, prev)                    # Josh; local stub until built
      scribe.update(world, n, cmd, result, parsed)                # fill the working KBs, write world_facts
      monitor.update(world, n, parsed)                            # novelty + stuck tracking
      sink.move(MoveRecord)
      stop on: death · won · "I give up" · stuck (monitor) · $ cap
  sink.run(RunRecord)
```

A game ends at **500 commands, death, or giving up**. Giving up happens in two ways: the model says "I give up" (`gave_up`),
or the monitor sees no progress for too long (`stuck`). Progress means a new room, a new item, a new interaction or a score
change. The limit is tunable and starts at 40 moves, matching the existing `stuck40`.

## Knowledge bases

There are two layers. **Working KBs** live in memory during a game. They start from the version's memories and get
filled in move by move. **Version memories** in Atlas are the only thing that carries between games, and only
the outer loop writes them.

| KB | Working (per game) | Carried across games as memory `kind` |
|---|---|---|
| **Map** | rooms and their exits, current position | `room`, `map_edge` |
| **Items** | where each item was seen, what's carried, what worked on it | `item` |
| **Objectives** | the current goal, plus open leads and hypotheses | `objective`, `hypothesis` |
| **Run history** | the last K moves as command → outcome | `run_summary` (score events, death cause, rooms reached) |

The working shapes, in Python dataclasses:

```text
Room      {name, description, visits, dark, exits: {dir: Exit}, items_seen, tried: {command: outcome}, notes}
Exit      {dir, to: room | None, status: "mentioned" | "known" | "blocked", evidence: (run_id, n), blocked_text}
Item      {name, last_seen_room, carried, notes, tried: {verb: outcome}}
Objective {text, status: "open" | "done" | "dropped", source: "this_game" | "memory"}
RunState  {room, prev_room, inventory, score, moves, goal, recent: [(n, command, outcome)]}
```

Filling entries that start empty:
- **Room change.** The command `north` moved us from A to B, so the exit `A.north` becomes `to: B, status: known`. This is a plain
  code upsert with no LLM call.
- **First visit to a room.** One small LLM call reads the room text and lists the exits and items it mentions. Mentioned exits
  get `to: None, status: mentioned`, which makes them *leads*. Standard compass directions that the text doesn't mention are **not**
  added.
- **"Taken." / "Dropped." / inventory replies** update the inventory. Every command records its outcome in
  `room.tried` (and in `item.tried` when the command names an item).
- **Memories from earlier games** load marked as `source: memory`. The prompt labels them "from earlier games, may be wrong".
  When this game's play contradicts one, the contradiction is recorded, so the Reflector can revise or retire that memory.

Every observation also goes to `world_facts` (keyed by `run_id`) with the move that produced it. That's the evidence the
Reflector cites. The full command sequence stays in `moves`. The outer loop may condense it into a `run_summary`,
but a **raw command sequence never goes into the prompt**. On the same seed, replaying a sequence would be
memorization, not play.

## Prompt layout

The prompt is rebuilt each move with no chat history. Fixed text comes first so prompt caching works.

1. **Cached prefix (fixed for the whole game):**
   - the paper's `basic` prompt, word for word;
   - the harness rules of the game: one command per reply, with an optional `Goal:` line;
   - a summary of the version's objectives, hypotheses and run summaries.
2. **Dynamic tail (target about 500 tokens):**
   - **Location:** the room name and a short description.
   - **Exits:** known (→ destination), mentioned but never taken, and blocked (with the reason).
   - **Here:** items in the room; commands already tried here, with their outcomes.
   - **Carrying:** the inventory.
   - **Goal:** the model's own current goal, carried from its last reply.
   - **Leads:**
     - exits that were mentioned but never taken;
     - items seen but never examined or used;
     - open hypotheses.
   - **Recent:** the last 5 moves (command → outcome).
   - **Warnings:** soft-rule warnings from the verifier; a note when the monitor sees repetition.
   - **Latest game output:** word for word.

The player replies with the command on line 1, which the existing `extract_command` reads. An optional second line,
`Goal: <one sentence>`, updates `RunState.goal`. That gives the loop a running intention ("where we want to go")
without a separate Planner call.

## Exploration over brute force

The aim is creative play that learns, not raising the score through search or memorization:
- **Leads come from the text only.** The harness never lists untried compass directions or valid actions. The map
  only knows what the game has described.
- **Novelty in the prompt, not in the score.** The prompt shows what was already tried here and what happened, plus
  unexamined items and open hypotheses. It says plainly: prefer actions you haven't tried, and treat notes from earlier games
  as hypotheses. The score is never a reward signal the harness optimizes directly.
- **Repetition shows up as a warning.** When the same command in the same room gets the same outcome more than twice, the prompt gets a
  warning line. Only the verifier's hard rules can block a command.
- **No replay.** Routes from earlier games can appear as map knowledge ("B is north of A"), never as a
  step-by-step script.
- **An honest measure.** Report harness game 1 against the paper baseline (the in-game scaffolding). Report game N against game 1
  (learning). Report game N on a held-out seed. Zork I's map doesn't depend on the seed; only random events
  (thief, combat) do. So map memory isn't generalization, and we don't present it as such.

## Integration points

| With | What | Status |
|---|---|---|
| Josh | Harness branch of `runner.play`, which calls these modules. `parser.parse`, `verifier.check`. Add `"stuck"` to `RunRecord.end_reason` (the contract lists `stuck40`; the model doesn't have it). | Stubs until built |
| Elliott | `memories.recall(version_id, kinds=…, subjects=…)` to load the KBs. Memory kinds `room`, `map_edge`, `item`, `objective`, `hypothesis`, `run_summary`. The Reflector reads `world_facts` plus `moves` as evidence. | Interface exists in `db.py` |
| Himali | A Mongo `Sink` and `world_facts` writes | Branch `himali/atlas-db-layer` |

## Build order

1. Working KB dataclasses, plus `WorldModel.load` from an empty version, so game 1 can run without Atlas.
2. `builder`, `player` (on the existing `AnthropicChat`), code-only `scribe`, `monitor`. Run a 20-move smoke game on real Jericho
   with a JSONL sink.
3. The first-visit LLM extraction in the scribe; the `Goal:` line; the leads block.
4. Load from a version through `memories.recall`, and write `world_facts` to Atlas.
5. With Elliott: a two-game chain in which a fact seen in game 1 shows up in game 2's prompt, labelled as coming from memory.
