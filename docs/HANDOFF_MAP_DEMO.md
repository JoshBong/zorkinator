# Handoff: map-across-games demo mode

Written 2026-09-26, end of a session with Himali's Claude Code instance, for whoever
(human or another Claude session) picks this up next. Everything below "Done this
session" is committed and pushed to `main` (last commit `88a8f15`). The one open item
is "Next: auto-advance demo mode" — not started, don't guess past what's written here
without checking with Himali first, especially the ambiguity flagged at the bottom.

## Done this session (context, in case you need it)

Roughly in order:

1. **`zorkinator/db.py`** — the Atlas access layer. `MongoSink` (implements `runner.Sink`
   for `runs`/`moves`), `world_facts` upsert helpers, `rules` helpers. Reconciled twice
   with concurrent teammate work on the same file (Elliott's `MongoOuterLoopStore` for
   `memories`/`memory_events`/`harness_versions` landed in the same window).
2. **`zorkinator/view/`** — new FastAPI backend (`schemas.py`, `mapping.py`, `app.py`)
   implementing the exact contract `frontend/src/api/client.ts` expects. `/api/stream/{run_id}`
   is real Server-Sent Events over an Atlas change stream on `moves`, not polling. Several
   fields are honestly empty (not faked) because the Parser/Scribe/Reflector weren't
   built yet at the time: `retrieved_rules`, `repeated_action`, per-life `rules_learned`,
   reflections, the room map.
3. **`frontend/`** — unzipped the Lovable/TanStack Start export (GRUE LAB), `npm install`,
   verified it runs standalone on mock data and switches to the live API via
   `VITE_USE_MOCK` / `VITE_API_BASE_URL` (`frontend/.env.local`, gitignored).
4. **Frontend polish**: new favicon, removed the redundant Race Mode page (Live Run
   already does baseline-vs-harness side by side), removed the header's mock/live +
   presenter badge (presenter mode itself still toggles with the `P` key), all page
   titles simplified to "GRUE LAB". Live Run layout: Agent's Mind and Harness Rules &
   Memory each got their own column beside the two game terminals (was a shared 360px
   sidebar), so the harness's reasoning doesn't require scrolling to see.
5. **Map animation** (`MapGraph.tsx`): rooms spring-pop in when first discovered, the
   camera gently zooms toward the agent's current room, current room gets a pulsing
   ring, the edge just walked flashes.
6. **Real Atlas Vector Search** — `db.MongoOuterLoopStore.recall_scored(version_id, query)`
   using Atlas Vector Search's **Automated Embedding** (public preview since 2026-05):
   Atlas calls a Voyage AI model (`voyage-4-lite`) to embed both indexed text
   (`memories.content.text`) and the query itself, entirely server-side — no embedding
   API key or pipeline code in this repo. `recall()` itself (Elliott's existing
   substring/kind/subject scan) is untouched, so nothing broke; `recall_scored()` is
   purely additive. Verified live: a query sharing zero keywords with the correct
   memory's text still ranked it first. `tests/test_vector_search.py` is a permanent,
   opt-in (`MONGODB_URI`) test of this — worth reading before touching `db.py`'s
   `MongoOuterLoopStore` again.
7. **Map-across-games** (this session's last piece, described in detail below).

Everything has been kept green throughout: `ruff check`, `ruff format --check`, strict
`mypy` on the Python side; `eslint` + `tsc --noEmit` on the frontend. Re-run all four
before committing whatever you build next.

## What "map across games" means, and what's built so far

Himali's ask: the map shouldn't reset every time you look at a different game — it
should accumulate, like the harness's own memory does. Concretely, stepping through
G1 → G10 (the existing game-selector buttons in Live Run) should:

- Show rooms as soon as they're first discovered, and keep them visible in later
  games without waiting for that game's own replay to re-reach them.
- Fade older discoveries (brighter = discovered more recently) rather than treating
  "known" as a flat on/off.
- Turn a room red once a death has happened there, but **only from the game currently
  being viewed onward** — a death in game 7 shouldn't show up while you're looking at
  game 3 (no spoilers stepping backward through the history).

### What's implemented (commit `88a8f15`)

- **`frontend/src/api/types.ts`**: `MapRoom.death_count: number` replaced with
  `death_lives: number[]` (life/game numbers in which a death happened in that room).
- **`frontend/src/api/mock.ts`**: `getMap()` now derives both `first_seen_life` and
  `death_lives` from each life's *actual* generated move path (previously
  `first_seen_life` was an unrelated arbitrary formula — `Math.ceil((i+1)/2)` — which
  could show a death in a room "before" it was ever discovered; fixed by deriving both
  from the same data). Also: `getMoves()` now makes the harness's reachable frontier
  grow by one room per game (`Math.min(ROOMS.length - 1, 1 + life)`) instead of every
  single life reaching all 12 rooms by its own move 60 — without this the accumulation
  has nothing to show, since game 1 would already "discover" everything.
- **`frontend/src/components/MapGraph.tsx`**: new required prop `currentGame: number`.
  `knownByNow(name)` = discovered in a strictly earlier game OR present in this game's
  own live `visited` set. Age-based fade (`AGE_FADE_PER_GAME = 0.15`, floors at
  `AGE_FADE_FLOOR = 0.45` so nothing already-known ever fully disappears). A room is a
  "grave" (`fill-baseline`, red) when `seen && death_lives.some(life => life <= currentGame)`.
- **`frontend/src/routes/index.tsx`**: passes `currentGame={game}` through; caption
  under the map explains the encoding ("brighter = discovered more recently · red =
  died here").
- **`zorkinator/view/schemas.py` / `mapping.py`**: `MapRoomOut.death_count` → `death_lives:
  list[int]`, kept consistent even though still empty (no Scribe-written per-room death
  facts exist yet).

### Known issue — not yet fixed

Verified live in the browser (screenshots + JS inspection of rendered `<circle>`
classes, not just eyeballing): by around game 6-8, **8 out of 10 discovered rooms
render red**, not a handful. Root cause: `getLives()` in `mock.ts` gives *every*
non-final life a real `death_cause` — only `life === 10` is exempted ("survived (run
ended)"). Combined with the frontier-growth fix above, every life's last move lands
exactly on that life's frontier edge, so essentially every frontier-edge room
accumulates a death. The map ends up looking like "everywhere is a grave" instead of a
handful of notable early deaths that taper off as the harness learns — which undercuts
the exact story this feature is meant to tell.

**The fix** (scoped, not done): in `getLives()` (`frontend/src/api/mock.ts:257`), make
death probability for the harness condition decrease with `life` instead of being
unconditional — e.g. life 1 always dies (sets up "learns from its first death"),
death probability decays afterward (something like
`Math.max(0.15, 0.9 - life * 0.12)`), and lives that don't roll a death should get a
`death_cause` containing the substring `"survived"` (e.g. `"survived (hit the move
cap)"`) so they're correctly treated as non-deaths everywhere that already checks
`death_cause.includes("survived")` — that includes `getMap()`'s own death-life
collection, `getEvalSummary`'s survived-count, and `graveyard.tsx`'s display. Grep
`death_cause` in `mock.ts` before changing this to catch every call site; there are at
least four.

## Next: auto-advance demo mode (not started)

Himali's refined ask, for the actual pitch: instead of manually clicking G1, G2, G3...,
the Live Run page should be able to **auto-play through the whole run**: start on game
1, let its replay play out (existing mechanism — see below), then automatically advance
to game 2, and so on through game 10, with the map's accumulated state (discovered
rooms, death markers, age-fade) persisting across the auto-advance exactly as it does
today when you click between games manually.

### What already exists and should Just Work

- `useReplay(length)` (`frontend/src/lib/use-replay.ts`) already auto-plays a game's
  moves and stops (`setPlaying(false)`) when it reaches the end. It also already resets
  `index` to 0 whenever `length` changes — and `length` changes whenever `harn` changes,
  which happens whenever `game` changes (different `harnQ` query key). **So switching
  games already resets the live per-game replay position to the start with no new
  code** — verify this is still true before building around it, but it was true as of
  this session.
- The map's cross-game accumulation (discovered rooms, age-fade, death markers) is
  already driven by `currentGame`/`first_seen_life`/`death_lives`, independent of
  replay position — so it should already persist correctly across an auto-advance,
  once the death-rate mock fix above lands.

### What's missing

A layer in `frontend/src/routes/index.tsx`'s `HeadToHead` component that:
1. Adds a "demo mode" toggle (a button, probably near the existing game-selector row —
   don't repurpose Presenter Mode, which is a different concern: font size and hiding
   `.presenter-hide` elements for projecting, not auto-advance).
2. When enabled, watches for `replay.playing` transitioning to `false` while
   `replay.index` is at the end (i.e., it stopped *because it finished*, not because
   the user hit pause) and, after a short pause (a second or two, so the final state of
   the game — including any death — is visible before cutting away), calls
   `setGame(game + 1)` — up to `game === 10`, then either stops or loops back to 1;
   ask Himali which.
3. Needs to distinguish "stopped because finished" from "stopped because the user hit
   pause" — `useReplay` doesn't currently expose that distinction (it just has
   `playing: boolean`). Cheapest fix: check `replay.index === length - 1 && !replay.playing`
   as the "finished" signal, since a user-paused state mid-game wouldn't have
   `index` sitting exactly at the last move.

### Open question — confirm with Himali before implementing

Her exact words: *"it starts with game 1, the map builds but it shows red where the
agent died, then it automatically moves to the next game (tab), the previous red
remains, and the map starts from starting again and builds along and so on."*

"The map starts from starting again" is ambiguous between two readings:

- **(a) Position only resets.** The agent's current-position marker (the pulse ring +
  camera zoom) jumps back to the start room each new game, but already-discovered
  rooms stay visible (dimmed) throughout — this is what the current `knownByNow` logic
  already does with zero further work, once the death-rate fix lands.
- **(b) Visual reveal resets too.** Even rooms already known from earlier games
  re-animate as freshly "discovered" each game, as if the map is being redrawn from
  scratch every time and just happens to retrace the same path quickly before
  extending further — for dramatic effect, even though it's not literally new
  information to the harness.

(a) is already built and is more honest (it doesn't pretend rediscovery is happening).
(b) would need the `key={`${r.name}-${seen}`}` remount trick in `MapGraph.tsx` to key
on `game` as well, so it retriggers the pop-in spring every game regardless of whether
the room was already known — more visually dynamic for a demo, but slower to watch
back if a game only revisits already-known territory. Don't build either without
picking one with her first — it changes the interaction pattern, not just a visual
tweak.
