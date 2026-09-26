# Outer/inner loop integration handoff

This document records the working boundary after the first real two-game Atlas smoke chain. The authoritative
document shapes and signatures remain in [`CONTRACTS.md`](CONTRACTS.md); this file explains how to integrate
against them without coupling the inner and outer loops.

## Current working state

The following path is implemented and exercised against Atlas and Anthropic:

1. `BetweenGameDriver` creates an experiment-isolated cold root with no memories or rules.
2. `play_harness_chain` calls `driver.run(games, play_game)`.
3. The driver calls `play_game(version_id, game_index)` with one exact immutable version.
4. The callback loads that exact manifest, constructs prompts from only its memory/rule references, plays the
   game, and persists the completed run with the driver's `chain`, `game_index`, and `version_id`.
5. The driver verifies persistence, reflects over the completed public transcript, commits a child version, and
   supplies that child to the next game.

The successful Atlas smoke chain `codex-smoke-20260926-01a0dec2-v2` ran two games with a 20-move cap. Game 1
used an empty root. Game 2's manifest contained three revisions, all sourced from game 1, and no revisions from
another experiment. The child committed after game 2 was not visible to game 2.

## Interface for the inner-loop owner

The sequential boundary is:

```python
def play_game(version_id: str, game_index: int) -> RunRecord:
    version = outer_store.get_version(version_id)
    if version is None:
        raise LookupError(version_id)

    record = play_inner_game(
        version=version,
        chain=driver.chain,
        game_index=game_index,
    )
    sink.run(record)
    return record


driver.run(total_games, play_game)
```

The returned record must already be persisted and must have:

- `mode == "harness"`;
- `version_id` equal to the callback argument;
- `chain` equal to `driver.chain`;
- `game_index` equal to the callback argument, using contiguous zero-based indexes.

Do not select a global or most-recent version inside the callback. Do not copy memories between runs. The driver
is the only component that chooses the version for the next game.

### Prompt construction

`builder.build_version_context(run_id, version, repository=...)` materializes the complete advisory memory/rule block
from the exact passed manifest. It resolves every `memory_ref` by `(version_id, memory_id)`, verifies the exact
`revision_id`, and resolves rules in manifest order. Missing or mismatched references are fatal rather than
silently replaced with newer data.

The inner loop may add current-game, human-visible state around this immutable block:

- latest game output;
- parser-derived room, score change, death, and inventory;
- `world_facts` scoped to the current `run_id`;
- recent commands and public results;
- verifier warnings and retry feedback.

It must not add chat history, memories outside the manifest, another chain's facts, valid-action lists, object
trees, RAM, world hashes, walkthrough data, or any other non-human-visible game state. Keep the fixed paper prompt
first for prompt caching. The integrated inner loop sends one stateless message containing the fixed paper prompt,
the exact manifest, its run-scoped working state, and the current observation; its harness protocol explicitly
prevents a second readiness acknowledgement from becoming a game command.

### Where inner-loop work should connect

The inner-loop implementation can replace the harness branch inside `runner.play` while preserving the callback
and persistence contract above. Recommended move flow:

1. Build the prompt from the exact `HarnessVersionRecord` plus current run-scoped state.
2. Ask `player.propose` for one command.
3. Run `verifier.check(cmd, state, version)`; append soft warnings, block only hard rules, and retry at most three
   total proposals.
4. Execute the accepted command through `adapter.step`.
5. Persist the `MoveRecord` immediately.
6. Parse public output and call `scribe.update` for run-scoped facts.
7. Stop on game end, the configured move/USD cap, or 40 moves without a score change.
8. Persist the completed `RunRecord` before returning to the driver.

The inner-loop owner should primarily touch `builder.py`, `player.py`, `scribe.py`, `monitor.py`, and the per-move
harness branch in `runner.py`. Coordinate changes to shared contract models or `docs/CONTRACTS.md` first.

## Outer-loop work that remains

### P0: validate the complete proposal before persistence

Memory operations are validated before `record_proposal`, but rule diffs currently receive their strongest
validation during `VersionManager.commit`. A malformed rule diff can therefore be recorded as the deterministic
proposal and rejected on every restart. Move/share the version manager's rule-diff shape validation so invalid
`op`, `when`, `verdict`, evidence, and revision targets are rejected before the `proposed` event is written.

Preserve the audit and idempotency rules: never mutate a persisted proposal and never silently ask the model for
a different answer under the same proposal ID.

### P0: connect the fixed promotion implementation

The CLI currently injects a conservative soft-only `RulePromoter`. Replace it with Josh's fixed verifier
promotion implementation when available. Reflection and memory content must never grant hard authority.

### P1: expose version and chain evidence for the demo

Add a read-only `version_diff(parent_id, child_id)` helper and a chain report containing:

- run IDs, game indexes, scores, and version IDs;
- version parent/child lineage;
- added, revised, and retired memory revisions;
- added, revised, retired, and promoted rules;
- evidence moves for every change;
- an assertion that each game used only its persisted manifest.

The replay view should consume this output instead of reconstructing version semantics itself.

### P1: complete version score accounting

Populate and test `HarnessVersionRecord.scores` consistently, including ancestry, retry, and branch behavior.
This is version metadata; it must not influence verifier authority or leak future outcomes into a game prompt.

### P2: unattended-chain hardening

- Exercise add → revise → retire across three or more real games.
- Test interruption before and after proposal persistence and version publication.
- Surface reflection call/USD budget exhaustion in CLI/report output.
- Add an explicit operator-facing status for terminal rejected boundaries.
- Run multiple cold chains and assert experiment isolation.

## Known limitations

- The integrated harness has a code parser/scribe and stuck monitor; verifier checks, retry feedback, and
  structured hydration of version memory into the working map/item/objective dataclasses remain.
- The production CLI keeps every proposed rule soft until the fixed promoter is integrated.
- Anthropic structured output is not used for the open-ended memory JSON payload; strict local validation remains
  mandatory, and proposal validation must be completed before unattended runs.
- The successful smoke run demonstrated persistence and memory isolation, not score improvement; both games
  scored zero with the minimal move loop.

## Verification commands

```bash
ruff check .
ruff format --check .
mypy
python -m unittest discover -v
# After the OpenAI Chat/reflection adapter lands:
python -m zorkinator harness --chain <unique-name> --games 2 --moves 20 --seed 0
```

Atlas tests and real chains require `MONGODB_URI`. New development/smoke model calls use `OPENAI_API_KEY` and
default to `OPENAI_TEST_MODEL=gpt-5.6-luna`. The current runtime transport is Anthropic-specific, so an OpenAI
`Chat`/reflection adapter must land before the next model-backed smoke run; do not route the OpenAI key through
the Anthropic client. Benchmark credentials and models remain separate from smoke-test defaults.
