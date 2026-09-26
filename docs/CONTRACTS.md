# Contracts

> Draft — agree at kickoff. A / B / C = owner.

## Mongo collections (Atlas sandbox, db `zork`)

runs            {_id, version_id, mode: "paper"|"harness", model, seed, move_cap,
                 score, moves, died: bool, death_move, end_reason: "death"|"cap"|"stuck40",
                 tokens_in, tokens_out, cost_usd, started_at, ended_at}

moves           {run_id, n, room, command, proposals: [str], rejections: [{cmd, rule_id, reason}],
                 text, score, score_delta, died: bool, latency_ms, ts}
                 index: {run_id: 1, n: 1}

world_facts     {run_id, subject, attr, value, move}       upsert on (run_id, subject, attr)

rules           {_id: "r12", text, when: {...}, verdict: "warn"|"block",
                 status: "soft"|"hard", evidence: ["<run_id>:<n>"], fired: int,
                 born_version, promoted_version}

harness_versions {_id, parent_id, rule_ids: [...], context_policy: {...},
                  scores: [{run_id, score}], created_at}

## Function signatures

A  adapter.reset(seed) -> text
A  adapter.step(cmd) -> {text, score, moves, done}
A  parser.parse(text, prev) -> {room, score_delta, died, new_items}
A  runner.play(version_id, mode, seed, move_cap) -> run_id        # stuck40 stop rule here
B  builder.build_prompt(run_id, n, version) -> str               # state from Atlas, no chat history
B  player.propose(prompt, feedback=None) -> cmd
B  scribe.update(run_id, n, text, parsed) -> None                # world_facts upserts
C  verifier.check(cmd, state, version) -> {ok, rule_id, reason, hard}
       soft -> warning appended to prompt, never blocks (ZorkGPT: LLM critic 88% wrong)
       hard -> block, Player retries (max 3 total)
C  reflector.propose(run_id) -> [rule diffs]                     # once per game
C  versions.commit(parent_id, diffs, run_id) -> version_id       # promotion check here

## Fixed
same model every call · same seed for game 1 and game N · no SAVE/RESTORE/RESTART
human-visible info only · move cap from smoke-test latency · per-run $ cap
