# Outer-loop memory design

Status: agreed product direction from Elliott's discussion. The base typed interface, MongoDB
representation, indexes, and exact-version read path below are implemented; reflection and version-commit
orchestration remain follow-up work.

## Scope and decisions

The outer loop learns between completed games. It can preserve whatever human-visible knowledge it finds useful: maps, routes, observations, hypotheses, failed approaches, procedures, and strategies. Memory kinds and payloads are open-ended. There is no general-only or no-room-name restriction in this design.

Memory lives in MongoDB Atlas. Raw run/move evidence remains separate from agent-authored interpretations. Memories are advisory: storing a memory never gives it authority to block a command. Hard rules still require Josh's fixed mechanical promotion procedure.

The agent chooses what to remember, revise, and retire. Fixed code controls provenance, experiment isolation, size budgets, validation, version publication, and enforcement. The Reflector uses the same model as the source run and receives only human-visible information.

This document covers Elliott's outer-loop interfaces and persistence only. It does not implement baseline history, per-move memory tools, current-state tracking, retrieval in the player, or stall recovery. Current inventory/location and other live state are not inherited as current facts in the next game; a memory may describe them as historical observations with evidence.

### Reconciliation with existing documents

The latest discussion explicitly allows persistent maps. This supersedes the obsolete “L2 (general only)” restriction in `DECISIONS.md`; any memory type is allowed. This product decision is settled, not awaiting approval. `CONTRACTS.md` now defines the memory collections and Reflector proposal contract and remains the implementation authority.

The base implementation replaces the old `lessons` contract rather than maintaining two conflicting
authoritative memory stores. Rule promotion remains Josh's separate fixed path, and exact-version memory
consumption remains an integration point for Seb's inner loop.

## Lifecycle

1. Load a completed run, its ordered public transcript, and the exact parent version's active memories/rules.
2. Build a bounded evidence packet: outcome, relevant events, and cited transcript windows. Preserve original move references. Allow targeted retrieval of more public evidence within a fixed budget.
3. Reflector proposes a batch of memory additions, revisions, and retirements, plus separately typed rule diffs if needed. An empty batch is valid.
4. Fixed validation checks structure, evidence, scope, parent consistency, and resource limits. Reject the batch with actionable errors if any operation is invalid; do not silently apply half of it.
5. Version manager stages immutable memory revisions, invokes the existing promotion path for rule diffs, and publishes a complete child version.
6. The orchestrator starts the next game with the returned version ID. The source run's score belongs to the parent that played it, not the newly generated child.

No memory becomes visible to another game just because it was staged. Readers use a published version manifest, never “latest memories.” A failed reflection or commit leaves the parent usable. The orchestrator may continue with that parent, but must record the failure rather than report that learning succeeded.

## Proposed interface

Keep Mongo queries and persistence out of the LLM interface. The agent proposes semantic operations; trusted code assigns IDs, metadata, timestamps, scope, and versions.

```python
reflector.propose(run_id: str) -> ReflectionProposal
versions.commit(parent_id: str, diffs: ReflectionProposal, run_id: str) -> str

# Read interface used by the outer loop; not a new inner-loop API.
memories.read(version_id: str, memory_id: str) -> MemoryRevision | None
memories.recall(
    version_id: str,
    *,
    query: str | None = None,
    subjects: list[str] | None = None,
    kinds: list[str] | None = None,
    limit: int = 10,
) -> list[MemoryRevision]
```

The service resolves experiment scope from the trusted version/run records and enforces a configured maximum `limit` and total serialized byte/token budget. Query text is plain text, never a Mongo query or executable expression. Recall searches only the exact revisions active in that version. Start with subject/kind filters and bounded text lookup; vector retrieval is deferred.

`ReflectionProposal` has this logical shape:

```text
proposal_id          service-issued stable ID, persisted before commit attempts
run_id               source completed run
parent_id            version that played the source run
memory_ops           ordered list of Add | Revise | Retire
rule_diffs           existing rule proposal path; separate from memories
summary              short explanation of what changed and why
```

Operations:

```text
Add     {op: "add", key, kind, subjects, content, status, evidence, rationale}
Revise  {op: "revise", memory_id, expected_revision_id,
         kind, subjects, content, status, evidence, rationale}
Retire  {op: "retire", memory_id, expected_revision_id, rationale, evidence}
```

- `key` is unique within the proposal and lets the service assign stable IDs on retry.
- Revision supplies the complete replacement payload, avoiding ambiguous deep JSON merges.
- `expected_revision_id` must equal the revision in the parent manifest.
- At most one operation per existing memory per batch; no add-then-revise chains.
- `kind` is an agent-chosen label, not a closed enum. `subjects` are explicit retrieval tags.
- `content` is a bounded JSON object; free prose goes in a `text` property. No executable content is evaluated.
- `status` is `hypothesis`, `supported`, or `contradicted`. These describe epistemic claims, not enforcement or verified causal truth. Retirement is a separate lifecycle operation.
- Every add/revise has at least one valid public evidence reference. Evidence existence is mechanically checked; whether it actually supports the claim remains a separate question.
- Retirement requires a reason; it may cite no moves when consolidating a duplicate.

The earlier conversational `remember/revise/retire` tools map to these batch operations. For the outer loop, they do not perform immediate independent database writes. This gives one reviewable, retryable change set per completed game.

## MongoDB representation

Database: existing Atlas `zork` database. Names below are proposed additions/extensions.

### `memories`: immutable revisions

Use one document per revision, not a mutable “current memory” document. A logical memory ID is stable; `_id` identifies exact content. This avoids needing an event log to reconstruct old content.

```json
{
  "_id": "memrev_123_1",
  "schema_version": 1,
  "experiment_id": "exp_01",
  "memory_id": "mem_123",
  "supersedes_revision_id": null,
  "kind": "map_edge",
  "subjects": ["room_a", "room_b"],
  "content": {"from": "room_a", "direction": "north", "to": "room_b"},
  "status": "supported",
  "evidence": [{"run_id": "run_02", "n": 17}],
  "rationale": "Preserve an observed connection for later games.",
  "source_run_id": "run_02",
  "proposal_id": "proposal_02",
  "operation_key": "add_0",
  "born_version_id": "v3",
  "created_at": "BSON datetime"
}
```

The example is a representation illustration, not seed knowledge. Map edges can be separate memories for targeted revision/retrieval; the agent may choose another bounded representation. Content can include unproven routes or strategies, clearly labelled as hypotheses and linked to their source observations.

All documents here represent candidate cross-game knowledge, so a per-run/persistent scope flag is unnecessary. Run-local state remains outside this collection. No delete or in-place content update is used for normal revision/retirement.

### `harness_versions`: exact active-memory manifest

Extend the existing version document with:

```text
experiment_id
source_run_id             null for the cold root version
proposal_id               null for the cold root version
memory_refs               [{memory_id, revision_id}, ...]
```

Retain existing `parent_id`, `rule_ids`, `context_policy`, `scores`, and `created_at`. `memory_refs` is a complete active set, inherited from the parent then modified by the validated batch. Enforce unique logical memory IDs within each manifest. Retirement removes the reference from the child; older versions retain it. Referenced rule content also needs immutable revisions for full reproducibility; coordinate that with Josh.

The cold root manifest is empty, with no inherited rules or lessons. Experiments use separate root versions. Runs reach their experiment via their version, avoiding an immediate requirement to extend every move document. All source evidence must resolve to completed runs in the same experiment's parent ancestry, including the current source run; unrelated experiments and sibling futures are excluded.

For the hackathon, bound total active memory size and manifest length. Do not let arbitrary growth approach Mongo document limits; require consolidation or retirement when the configured budget is exceeded. A larger-scale manifest representation is deferred.

### `memory_events`: durable proposal audit

Store immutable proposal/audit events, not another copy of current memory state:

```text
_id                       stable event ID
experiment_id
proposal_id
phase                     proposed | validated | rejected | committed | failed
source_run_id
parent_id
child_version_id          when applicable
operations                exact proposed operations on the proposed event
memory_revision_ids       when applicable
reason                    validation/error summary when applicable
model                     source run's model
usage                     tokens/cost for the reflection call, when available
created_at                BSON datetime
```

Persist the generated proposal before commit. Retries reuse the saved proposal rather than making another LLM call under the same ID. Record operational errors without secrets. Fine-grained per-move retrieval telemetry is an inner-loop integration follow-up, outside this document's implementation scope.

### Indexes and visibility

- `memories._id`: built-in unique revision identity.
- `memories (experiment_id, proposal_id, operation_key)`: unique, one persisted revision per add/revise operation.
- `memories (experiment_id, memory_id)`: revision history lookup.
- `memory_events (experiment_id, proposal_id, phase)`: unique for this single-terminal-attempt audit model; repeated transport retries reuse the event. A deliberate new attempt gets a new proposal ID.
- `harness_versions (experiment_id, proposal_id)`: unique partial index for non-null proposal IDs; root versions are excluded.
- Add subject/kind retrieval indexes only after Hiamil chooses the actual query shape. Membership in the requested version manifest is mandatory regardless of indexing.

A historical revision matching a text query must never appear unless it is active in the requested version. For initial bounded manifests, fetch referenced IDs and filter/rank those records. This favors correctness and simplicity over premature search infrastructure.

## Commit consistency and failure handling

Use stable service-generated child/revision IDs tied to the persisted proposal. On retry, compare stored content to the expected content; mismatches are errors, not overwrite opportunities.

Prefer a Mongo transaction for publishing the child manifest and committed audit event when the existing DB helper supports it. Persist immutable revisions first; readers cannot see them through a version until publication. If transactions are deferred, insert the complete version document last, treat it as the authoritative commit marker, and repair a missing committed audit event on retry. Staged, unreferenced revisions are harmless and can be cleaned up separately.

The same proposal committed twice returns the same version ID. Two different proposals from one parent may form explicit branches, but the orchestrator chooses one version for the next game; no implicit global “latest” pointer is needed for the initial sequential learning chain.

Reject wrong source-run/parent associations, unknown memory IDs, stale expected revisions, duplicate operations, missing evidence, cross-experiment references, unsupported payload types, and exceeded limits before publication. Validation failure preserves the parent unchanged. A failed batch never grants hard-rule authority through memory content.

## Implementation plan and acceptance checks

1. Coordinate and amend `CONTRACTS.md`: proposal type, memory read interface, three collection shapes/extensions, lesson compatibility, and open-ended persistence policy.
2. Add strict typed envelope/operation models, retaining flexible bounded JSON content. Stub Reflector output and exercise a fake proposal end to end.
3. Implement immutable persistence, exact-version reads, retirement, experiment isolation, and idempotent publication through Hiamil's DB interface.
4. Implement bounded public-evidence assembly and the real structured Reflector call. Configure input/output, operation-count, content-size, and cost limits in fixed code; exact initial values remain tuning parameters.
5. Integrate rule promotion separately and hand the published version ID to the next-game orchestrator. Coordinate version-scoped memory consumption with Seb.
6. Verify with synthetic transcripts: add → retrieve in child; revise → parent unchanged; retire → absent only in child; retry → same version; unrelated cold experiment → no memories; invalid evidence/stale revision → no publication; failure between staging/publication → parent still usable.
7. Run the required 20-move integration smoke games: an observed fact becomes a cited memory after game 1 and is available to game 2 under the published child. Check the earlier game's memory view remains exactly reproducible.

Success is persistent, evidence-backed learning with an auditable version boundary. Automatic usefulness scoring, vector retrieval, policy self-modification, and inner-loop stall recovery are not required to establish that boundary.
