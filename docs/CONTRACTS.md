# Contracts

> Draft — agree at kickoff. Owners: A = Josh (game) + Himali (Atlas, view), B = Seb, C = Elliott (Reflector, versions); verifier + promotion = Josh.

## Mongo collections (Atlas sandbox, db `zork`)

runs            {run_id, version_id, mode: "paper"|"harness", prompt, model, seed, move_cap,
                 score, moves, died: bool, death_move,
                 end_reason: "death"|"won"|"game_over"|"gave_up"|"cap"|"usd_cap"|"stuck40",
                 tokens_in, tokens_out, tokens_cache_write, tokens_cache_read, cost_usd, started_at, ended_at,
                 chain, game_index}   (chain = one sequential series; game_index = position in it)
                 (pydantic: models.RunRecord; Mongo _id = run_id)

moves           {run_id, n, room, command, proposals: [str], rejections: [{cmd, rule_id, reason}],
                 text, score, score_delta, died: bool, latency_ms, ts}    (pydantic: models.MoveRecord)
                 index: {run_id: 1, n: 1}

world_facts     {run_id, subject, attr, value, move}       upsert on (run_id, subject, attr)

memories        {_id: revision_id, schema_version: 1, experiment_id, memory_id,
                 supersedes_revision_id, kind, subjects: [str], content: JSON,
                 status: "hypothesis"|"supported"|"contradicted",
                 evidence: [{run_id, n}], rationale, source_run_id, proposal_id,
                 operation_key, born_version_id, created_at}
                 immutable revisions; no in-place update/delete

memory_events   {_id, experiment_id, proposal_id,
                 phase: "proposed"|"validated"|"rejected"|"committed"|"failed",
                 source_run_id, parent_id, child_version_id, operations,
                 memory_revision_ids, reason, model, usage, created_at,
                 proposal: ReflectionProposal}
                 immutable proposal/audit events, not current memory state

rules           {_id: "r12", text, when: {...}, verdict: "warn"|"block",
                 status: "soft"|"hard", evidence: ["<run_id>:<n>"], fired: int,
                 born_version, promoted_version}

harness_versions {_id, experiment_id, parent_id, source_run_id, proposal_id,
                  memory_refs: [{memory_id, revision_id}], rule_ids: [...],
                  context_policy: {...}, scores: [{run_id, score}], created_at}
                  memory_refs is the complete active set for that exact version

indexes          memories unique (experiment_id, proposal_id, operation_key)
                 memories (experiment_id, memory_id)
                 memory_events unique (experiment_id, proposal_id, phase)
                 harness_versions unique partial (experiment_id, proposal_id)
                 memories_vector: Atlas Vector Search, autoEmbed on content.text
                 (voyage-4-lite), filter fields experiment_id/_id/kind

## Function signatures

A  adapter.reset(seed) -> text
A  adapter.step(cmd) -> {text, score, moves, done}
A  parser.parse(text, prev) -> {room, score_delta, died, new_items}
A  runner.play(mode, seed, move_cap, *, chat, sink, prompt, usd_cap) -> RunRecord   # paper mode built; stuck40 = harness only
H  sink.move(MoveRecord) / sink.run(RunRecord)                    # db.MongoSink; JsonlSink still used by CLI default
B  builder.build_prompt(run_id, n, version) -> str               # state from Atlas, no chat history
B  player.propose(prompt, feedback=None) -> cmd
B  scribe.update(run_id, n, text, parsed) -> None                # world_facts upserts
J  verifier.check(cmd, state, version) -> {ok, rule_id, reason, hard}
       soft -> warning appended to prompt, never blocks (ZorkGPT: LLM critic 88% wrong)
       hard -> block, Player retries (max 3 total)
C  reflector.propose(run_id) -> ReflectionProposal              # once per game
J  verifier.promote(rule, runs) -> "hard"|"soft"                 # death replay + false-positive replay over logged moves
C  versions.commit(parent_id, proposal, run_id) -> version_id    # calls verifier.promote
C  memories.read(version_id, memory_id) -> MemoryRevision | None
C  memories.recall(version_id, *, query=None, subjects=None, kinds=None, limit=10)
      -> list[MemoryRevision]                                    # exact version only, substring query
H  memories.recall_scored(version_id, query, *, subjects=None, kinds=None, limit=10)
      -> list[tuple[MemoryRevision, float]]                      # Atlas Vector Search, Automated
      # Embedding (voyage-4-lite, no key/pipeline needed); score = vectorSearchScore, 0-1

ReflectionProposal = {proposal_id, run_id, parent_id, memory_ops, rule_diffs, summary}
memory_ops = Add | Revise | Retire; add/revise carry complete payloads and public evidence.
All memory is advisory. Only verifier.promote can grant blocking authority to a rule.

## Fixed
same model every call · same seed for game 1 and game N · no SAVE/RESTORE/RESTART
human-visible info only · move cap from smoke-test latency · per-run $ cap
