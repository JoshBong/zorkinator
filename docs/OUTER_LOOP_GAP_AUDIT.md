# Outer-loop gap audit

Status: implementation audit on 2026-09-26. This document prioritizes the remaining work needed for a
credible unattended benchmark and the promised avoided-death demo. `CONTRACTS.md` remains the authority for
document shapes and function signatures; `DECISIONS.md` remains the authority for agreed product choices.

## Bottom line

The outer-loop foundation is implemented and well covered: experiment-isolated cold roots, bounded public
reflection evidence, immutable memory revisions, exact-version manifests, evidence validation, atomic child
publication, persisted proposal reuse, and version-safe retrieval. The focused driver, reflector, versioning,
Atlas-store, and verifier suite currently passes 53 tests.

The system should not yet be described as a complete self-improving harness. Three gaps are release-critical.

## P0 — Contain reflection failures and preserve chain progress

An unattended chain must survive an expected failure at the learn-between-games boundary. Today, malformed
model output, reflection validation errors, and terminal proposal rejection can escape the driver and stop the
chain. Reflection budget exhaustion does continue with the parent version, but it is not recorded as an
operator-visible boundary outcome.

Add a persisted result for every completed game's learning boundary, with outcomes such as:

- `committed`: a child version was atomically published;
- `no_change`: a valid empty proposal intentionally retained the parent;
- `reflection_failed`: the model call or response validation failed;
- `rejected`: fixed validation rejected the persisted proposal;
- `budget_exhausted`: no new reflection call was allowed;
- `publication_failed`: a transient commit failed and remains retryable.

Expected reflection or validation failures should record the outcome and allow the next game to use the parent
version. Transient publication failures should retain the existing idempotent retry path. Recovery must not
regenerate a proposal that was already persisted, and it must not retry a terminally rejected proposal forever.
CLI and report output must state explicitly when a game produced no new published knowledge.

This is required before running long multi-chain experiments: otherwise one bad structured response can waste
the rest of a chain and bias results toward chains that happened not to encounter a transport or parsing error.

## P0 — Connect mechanical rule promotion and command enforcement together

The fixed replay-based `verifier.Promoter` exists and tests the two required conditions: a rule would have
prevented a cited death, and it would not have blocked a survived move that later led to points. Production
currently injects a soft-only promoter, so no learned rule can become hard.

Replace the production soft-only adapter with the fixed promoter, using persisted moves as the replay source.
Do this only with the corresponding inner-loop enforcement path:

1. call `verifier.check(cmd, state, version)` before executing a proposed command;
2. append soft-rule matches as warnings without blocking;
3. block only hard-rule matches;
4. retry the Player at most three total proposals while preserving rejection evidence in `MoveRecord`;
5. log the fired rule and its evidence so the replay can explain the intervention.

Promotion without enforcement would create hard labels that do not change play. Enforcement without mechanical
promotion would violate the benchmark contract. These two integrations therefore form one release gate.

## P0 — Feed surprise evidence into reflection

The agreed design makes prediction error a score-independent learning signal: the Player predicts the outcome,
then marks whether the observed result was surprising. The Reflector currently prioritizes deaths and score
changes, then fills its evidence budget from recent moves. Its evidence packet does not include the move's
`expected` or `surprise` fields.

Update bounded evidence selection to rank:

1. death moves and the immediately relevant lead-in window;
2. moves marked `surprise=True` and their expected-versus-actual outcome;
3. score-changing or otherwise progress-bearing moves;
4. recent moves to fill the remaining budget.

Expose `expected` and `surprise` in the public reflection packet. They are model-authored and derived from
human-visible game output, so they stay within the information boundary. Keep original `(run_id, n)` evidence
references unchanged.

Without this, the advertised “learn from surprises” mechanism is recorded but does not affect cross-game
learning.

## P1 — Produce the demo explanation from version lineage

Add a read-only `version_diff(parent_id, child_id)` helper and a chain report rather than teaching the replay UI
to reconstruct version semantics. The report should include:

- run ID, game index, played version, score, outcome, death move, and cost;
- exact parent/child lineage;
- added, revised, and retired memories;
- added, revised, retired, and promoted rules;
- evidence moves for every published change;
- rule firings and blocked proposals in the later game;
- an assertion that each game consumed only its persisted manifest.

The minimum demo artifact is one traceable chain:

`game 1 death move -> committed rule revision -> mechanical promotion -> game N rejected command -> survival`

The existing evaluation view's aggregate diff counts are useful for charts but are not sufficient to establish
that causal story.

## P1 — Complete accounting and experiment operations

- Populate `HarnessVersionRecord.scores` consistently, including retries, ancestry, and branches. Scores are
  metadata only and must never enter verifier authority or leak future outcomes into a prompt.
- Provide practical orchestration for the agreed 10 chains × 10 games benchmark, including a total experiment
  budget, per-chain status, resume behavior, and a machine-readable final summary.
- Make reflection call/USD exhaustion visible in CLI and evaluation output.
- Run a real three-or-more-game add -> revise -> retire sequence.
- Run multiple cold chains and verify experiment, memory, rule, and evidence isolation.

## Not release-critical for the hackathon

Do not delay the work above for automatic usefulness scoring, a second mutable current-knowledge store, policy
self-modification, Stream Processing, Online Archive, time-series collections, Database Triggers, or richer
vector-search tooling. Exact-version advisory memory plus fixed verifier authority is sufficient for the demo
and benchmark.

## Recommended execution order

1. Failure containment and persisted boundary outcomes.
2. Mechanical promoter plus inner-loop verifier enforcement.
3. Surprise-aware reflection evidence.
4. Version diff and chain explanation report.
5. Score accounting and multi-chain benchmark operations.
