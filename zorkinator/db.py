"""MongoDB Atlas access layer.

This is the only module that imports `pymongo` directly. Every other component
(builder, verifier, scribe, reflector, versions, runner) reads and writes Atlas
through the functions here, so the document shapes in docs/CONTRACTS.md stay in
one place.

`OuterLoopStore` / `MongoOuterLoopStore` at the bottom are Elliott's outer-loop
memory boundary (memories/memory_events/harness_versions). Only trusted code
should call their write methods; the Reflector emits `ReflectionProposal`
values and never receives a Mongo query interface.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime
from itertools import pairwise
from typing import Any, Literal, Protocol, TypeVar

from bson import BSON
from dotenv import load_dotenv
from pydantic import JsonValue
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError, OperationFailure
from pymongo.operations import SearchIndexModel

from .models import (
    ContractModel,
    GameEvaluation,
    HarnessVersionRecord,
    MemoryEvent,
    MemoryRevision,
    MoveRecord,
    PublishedDiffCounts,
    ReflectionProposal,
    RetrievalScore,
    RuleDoc,
    RunRecord,
    WorldFactDoc,
)

load_dotenv()

DB_NAME = "zork"
DEFAULT_RECALL_LIMIT = 10
MAX_RECALL_LIMIT = 50
MAX_RECALL_BYTES = 64 * 1024

# Atlas Vector Search index on memories.content.text, using Automated Embedding: Atlas
# calls a Voyage AI model to embed both the indexed text and each query itself, so there
# is no embedding API key or pipeline code in this codebase. Public preview as of 2026-05.
VECTOR_INDEX_NAME = "memories_vector"
VECTOR_EMBED_MODEL = "voyage-4-lite"

_T = TypeVar("_T", bound=ContractModel)

_client: MongoClient[dict[str, Any]] | None = None


def get_client() -> MongoClient[dict[str, Any]]:
    """Return the process-wide MongoClient, creating it from MONGODB_URI on first use."""
    global _client
    if _client is None:
        uri = os.environ.get("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is not set. Copy .env.example to .env and fill it in.")
        _client = MongoClient(uri)
    return _client


def get_db() -> Database[dict[str, Any]]:
    """Return the `zork` sandbox database."""
    return get_client()[DB_NAME]


def close() -> None:
    """Close the process-wide MongoClient, if one was opened."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


def ensure_indexes() -> None:
    """Create the runs/moves/world_facts/rules indexes docs/CONTRACTS.md requires.

    Safe to call repeatedly. See MongoOuterLoopStore.ensure_indexes() for the
    memories/memory_events/harness_versions indexes.
    """
    db = get_db()
    db.moves.create_index([("run_id", ASCENDING), ("n", ASCENDING)])
    db.world_facts.create_index(
        [("run_id", ASCENDING), ("subject", ASCENDING), ("attr", ASCENDING)],
        unique=True,
    )


def _without_id(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop Mongo's `_id` for models that carry their own natural key (`run_id`, ...)."""
    data = dict(raw)
    data.pop("_id", None)
    return data


def _to_mongo(model: ContractModel) -> dict[str, Any]:
    """`id` -> `_id` for models that use the `id` convention (rules)."""
    data = model.model_dump()
    if "id" in data:
        data["_id"] = data.pop("id")
    return data


def _from_mongo(model_cls: type[_T], raw: dict[str, Any]) -> _T:
    """Map Mongo's `_id` back to `id`, or drop it for models with no `id` field
    (e.g. WorldFactDoc, whose `_id` is a Mongo-assigned ObjectId nobody uses)."""
    data = dict(raw)
    if "_id" in data:
        if "id" in model_cls.model_fields:
            data["id"] = data.pop("_id")
        else:
            del data["_id"]
    return model_cls.model_validate(data)


# --- runs + moves: the Sink protocol runner.play() writes through (see runner.Sink) ---


class MongoSink:
    """Atlas-backed drop-in for runner.JsonlSink: `play(..., sink=MongoSink())`."""

    def move(self, record: MoveRecord) -> None:
        get_db().moves.insert_one(record.model_dump())

    def run(self, record: RunRecord) -> None:
        doc = record.model_dump()
        doc["_id"] = record.run_id
        get_db().runs.replace_one({"_id": record.run_id}, doc, upsert=True)


def get_run(run_id: str) -> RunRecord | None:
    """Fetch one `runs` document by id."""
    raw = get_db().runs.find_one({"_id": run_id})
    return None if raw is None else RunRecord.model_validate(_without_id(raw))


def get_runs(mode: Literal["paper", "harness"] | None = None) -> list[RunRecord]:
    """Fetch `runs` documents, newest first, optionally filtered by mode."""
    query: dict[str, Any] = {} if mode is None else {"mode": mode}
    cursor = get_db().runs.find(query).sort("started_at", DESCENDING)
    return [RunRecord.model_validate(_without_id(raw)) for raw in cursor]


def get_moves(run_id: str) -> list[MoveRecord]:
    """Fetch every move of a run, in order."""
    cursor = get_db().moves.find({"run_id": run_id}).sort("n", ASCENDING)
    return [MoveRecord.model_validate(_without_id(raw)) for raw in cursor]


# --- world_facts ---


def upsert_world_fact(fact: WorldFactDoc) -> None:
    """Upsert one (run_id, subject, attr) fact; newest `move` wins."""
    collection = get_db().world_facts
    key = {"run_id": fact.run_id, "subject": fact.subject, "attr": fact.attr}
    existing = collection.find_one(key, {"move": 1})
    if existing is not None and existing["move"] > fact.move:
        return
    collection.update_one(key, {"$set": fact.model_dump()}, upsert=True)


def get_world_facts(run_id: str, subject: str | None = None) -> list[WorldFactDoc]:
    """Fetch facts for a run, optionally scoped to one subject (e.g. a room)."""
    query: dict[str, Any] = {"run_id": run_id}
    if subject is not None:
        query["subject"] = subject
    cursor = get_db().world_facts.find(query)
    return [_from_mongo(WorldFactDoc, raw) for raw in cursor]


# --- rules ---


def insert_rule(rule: RuleDoc) -> str:
    """Insert a new rule version, born soft, as proposed by the Reflector."""
    get_db().rules.insert_one(_to_mongo(rule))
    return rule.id


def get_rules(status: Literal["soft", "hard"] | None = None) -> list[RuleDoc]:
    """Fetch rules, optionally filtered by status."""
    query: dict[str, Any] = {} if status is None else {"status": status}
    cursor = get_db().rules.find(query)
    return [_from_mongo(RuleDoc, raw) for raw in cursor]


def record_rule_fired(rule_id: str) -> None:
    """Bump a rule's fire count when the Verifier applies it."""
    get_db().rules.update_one({"_id": rule_id}, {"$inc": {"fired": 1}})


def promote_rule(rule_id: str, promoted_version: str) -> None:
    """Promote a rule from soft to hard, recording the version that promoted it."""
    get_db().rules.update_one(
        {"_id": rule_id},
        {"$set": {"status": "hard", "promoted_version": promoted_version}},
    )


# =====================================================================
# Outer-loop memory store (owned by Elliott; see docs/OUTER_LOOP_MEMORY.md)
# memories / memory_events / harness_versions (the v2, memory-manifest shape)
# =====================================================================

_ContractT = TypeVar("_ContractT", bound=ContractModel)


class OuterLoopStore(Protocol):
    """Persistence interface used by the Reflector and version manager."""

    def ensure_indexes(self) -> None: ...

    def record_proposal(
        self,
        proposal: ReflectionProposal,
        *,
        experiment_id: str,
        model: str,
        created_at: datetime,
        usage: dict[str, JsonValue] | None = None,
    ) -> MemoryEvent: ...

    def put_memory_revision(self, revision: MemoryRevision) -> None: ...

    def put_memory_event(self, event: MemoryEvent) -> None: ...

    def publish_version(self, version: HarnessVersionRecord) -> None: ...

    def publish_bundle(
        self,
        version: HarnessVersionRecord,
        revisions: Sequence[MemoryRevision],
        rules: Sequence[RuleDoc],
        events: Sequence[MemoryEvent],
    ) -> None: ...

    def get_version(self, version_id: str) -> HarnessVersionRecord | None: ...

    def get_memory_event(self, event_id: str) -> MemoryEvent | None: ...

    def get_rule(self, rule_id: str) -> RuleDoc | None: ...

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None: ...

    def recall(
        self,
        version_id: str,
        *,
        query: str | None = None,
        subjects: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        locations: Sequence[str] | None = None,
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> list[MemoryRevision]: ...


class MongoOuterLoopStore:
    """Production outer-loop repository over runs, moves, versions, rules, and memories."""

    def __init__(
        self,
        database: Database[dict[str, Any]],
        *,
        client: MongoClient[dict[str, Any]] | None = None,
        max_recall_limit: int = MAX_RECALL_LIMIT,
        max_recall_bytes: int = MAX_RECALL_BYTES,
    ) -> None:
        if max_recall_limit < 1 or max_recall_bytes < 1:
            raise ValueError("recall limits must be positive")
        self._database = database
        self._client = client
        self._max_recall_limit = max_recall_limit
        self._max_recall_bytes = max_recall_bytes

    @classmethod
    def from_env(cls) -> MongoOuterLoopStore:
        """Connect to the existing Atlas `zork` database using ``MONGODB_URI``."""
        load_dotenv()
        uri = os.environ.get("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is not set")
        client: MongoClient[dict[str, Any]] = MongoClient(uri)
        return cls(client[DB_NAME], client=client)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()

    def ensure_indexes(self) -> None:
        """Create the outer-loop indexes; safe to call repeatedly."""
        self._database.memories.create_index(
            [
                ("experiment_id", ASCENDING),
                ("proposal_id", ASCENDING),
                ("operation_key", ASCENDING),
            ],
            unique=True,
            name="memory_operation",
        )
        self._database.memories.create_index(
            [("experiment_id", ASCENDING), ("memory_id", ASCENDING)],
            name="memory_history",
        )
        self._database.memory_events.create_index(
            [("experiment_id", ASCENDING), ("proposal_id", ASCENDING), ("phase", ASCENDING)],
            unique=True,
            name="proposal_phase",
        )
        self._database.harness_versions.create_index(
            [("experiment_id", ASCENDING), ("proposal_id", ASCENDING)],
            unique=True,
            partialFilterExpression={"proposal_id": {"$type": "string"}},
            name="published_proposal",
        )
        self._ensure_vector_index()

    def _ensure_vector_index(self) -> None:
        """Create the memories.content.text Atlas Vector Search index. create_search_index
        isn't naturally idempotent like create_index, so an "already exists" failure is
        swallowed; any other failure (e.g. a genuinely bad definition) still raises."""
        model = SearchIndexModel(
            name=VECTOR_INDEX_NAME,
            type="vectorSearch",
            definition={
                "fields": [
                    {
                        "type": "autoEmbed",
                        "modality": "text",
                        "path": "content.text",
                        "model": VECTOR_EMBED_MODEL,
                    },
                    {"type": "filter", "path": "experiment_id"},
                    {"type": "filter", "path": "_id"},
                    {"type": "filter", "path": "kind"},
                    {"type": "filter", "path": "locations"},
                ]
            },
        )
        try:
            self._database.memories.create_search_index(model)
        except OperationFailure as exc:
            if (
                "already exists" not in str(exc).casefold()
                and "duplicate" not in str(exc).casefold()
            ):
                raise

    def get_run(self, run_id: str) -> RunRecord | None:
        """Load one completed run for reflection or version evidence validation."""
        raw = self._database.runs.find_one({"_id": run_id})
        return None if raw is None else RunRecord.model_validate(_without_id(raw))

    def get_moves(self, run_id: str) -> list[MoveRecord]:
        """Load a run's public transcript in move order."""
        cursor = self._database.moves.find({"run_id": run_id}).sort("n", ASCENDING)
        return [MoveRecord.model_validate(_without_id(raw)) for raw in cursor]

    def has_move(self, run_id: str, n: int) -> bool:
        """Return whether an exact persisted move exists for a cited evidence reference."""
        return self._database.moves.find_one({"run_id": run_id, "n": n}, {"_id": 1}) is not None

    def get_rules(self, rule_ids: Sequence[str]) -> list[RuleDoc]:
        """Resolve exactly the rules named by a version manifest, preserving its order."""
        if not rule_ids:
            return []
        raws = self._database.rules.find({"_id": {"$in": list(rule_ids)}})
        rules_by_id = {raw["_id"]: _model(RuleDoc, raw, "id") for raw in raws}
        missing = [rule_id for rule_id in rule_ids if rule_id not in rules_by_id]
        if missing:
            raise ValueError(f"version references missing rules: {', '.join(missing)}")
        return [rules_by_id[rule_id] for rule_id in rule_ids]

    def record_proposal(
        self,
        proposal: ReflectionProposal,
        *,
        experiment_id: str,
        model: str,
        created_at: datetime,
        usage: dict[str, JsonValue] | None = None,
    ) -> MemoryEvent:
        event = MemoryEvent(
            event_id=f"{proposal.proposal_id}:proposed",
            experiment_id=experiment_id,
            proposal_id=proposal.proposal_id,
            phase="proposed",
            source_run_id=proposal.run_id,
            parent_id=proposal.parent_id,
            child_version_id=None,
            operations=proposal.memory_ops,
            memory_revision_ids=[],
            reason=None,
            model=model,
            usage={} if usage is None else usage,
            created_at=created_at,
            proposal=proposal,
        )
        self.put_memory_event(event)
        return event

    def put_memory_revision(self, revision: MemoryRevision) -> None:
        self._insert_immutable("memories", _document(revision, "revision_id"))

    def put_memory_event(self, event: MemoryEvent) -> None:
        self._insert_immutable("memory_events", _document(event, "event_id"))

    def publish_version(self, version: HarnessVersionRecord) -> None:
        """Publish a root manifest; non-root children must use ``publish_bundle``."""
        if (
            version.parent_id is not None
            or version.source_run_id is not None
            or version.proposal_id is not None
        ):
            raise ValueError("non-root versions require atomic publish_bundle publication")
        self._insert_immutable("harness_versions", _document(version, "version_id"))

    def publish_bundle(
        self,
        version: HarnessVersionRecord,
        revisions: Sequence[MemoryRevision],
        rules: Sequence[RuleDoc],
        events: Sequence[MemoryEvent],
    ) -> None:
        """Atomically publish every record that makes one child version usable."""
        if self._client is None:
            raise RuntimeError("atomic publication requires a MongoClient")
        if (
            version.parent_id is None
            or version.source_run_id is None
            or version.proposal_id is None
        ):
            raise ValueError("publish_bundle requires a non-root child version")
        committed = [
            event
            for event in events
            if event.phase == "committed"
            and event.child_version_id == version.version_id
            and event.proposal_id == version.proposal_id
            and event.source_run_id == version.source_run_id
            and event.parent_id == version.parent_id
            and event.experiment_id == version.experiment_id
        ]
        if len(committed) != 1:
            raise ValueError("publish_bundle requires exactly one matching committed event")
        revision_ids = [revision.revision_id for revision in revisions]
        if committed[0].memory_revision_ids != revision_ids:
            raise ValueError("committed event revision ids do not match the publication bundle")
        manifest_revision_ids = {reference.revision_id for reference in version.memory_refs}
        if not set(revision_ids).issubset(manifest_revision_ids):
            raise ValueError("publication bundle contains a revision outside the child manifest")
        if not {rule.id for rule in rules}.issubset(version.rule_ids):
            raise ValueError("publication bundle contains a rule outside the child manifest")
        with self._client.start_session() as session, session.start_transaction():
            for revision in revisions:
                self._insert_immutable(
                    "memories", _document(revision, "revision_id"), session=session
                )
            for rule in rules:
                self._insert_immutable("rules", _document(rule, "id"), session=session)
            for event in events:
                self._insert_immutable(
                    "memory_events", _document(event, "event_id"), session=session
                )
            # The manifest is the visibility authority. Insert it last even though
            # the transaction makes the whole bundle visible atomically.
            self._insert_immutable(
                "harness_versions", _document(version, "version_id"), session=session
            )

    def get_version(self, version_id: str) -> HarnessVersionRecord | None:
        raw = self._database.harness_versions.find_one({"_id": version_id})
        return None if raw is None else _model(HarnessVersionRecord, raw, "version_id")

    def get_memory_event(self, event_id: str) -> MemoryEvent | None:
        raw = self._database.memory_events.find_one({"_id": event_id})
        return None if raw is None else _model(MemoryEvent, raw, "event_id")

    def get_rule(self, rule_id: str) -> RuleDoc | None:
        raw = self._database.rules.find_one({"_id": rule_id})
        return None if raw is None else _model(RuleDoc, raw, "id")

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None:
        version = self._require_version(version_id)
        revision_id = next(
            (
                reference.revision_id
                for reference in version.memory_refs
                if reference.memory_id == memory_id
            ),
            None,
        )
        if revision_id is None:
            return None
        raw = self._database.memories.find_one(
            {"_id": revision_id, "experiment_id": version.experiment_id}
        )
        if raw is None:
            raise ValueError(f"version {version_id!r} references missing revision {revision_id!r}")
        revision = _model(MemoryRevision, raw, "revision_id")
        if revision.memory_id != memory_id:
            raise ValueError(f"revision {revision_id!r} does not belong to memory {memory_id!r}")
        return revision

    def recall(
        self,
        version_id: str,
        *,
        query: str | None = None,
        subjects: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        locations: Sequence[str] | None = None,
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> list[MemoryRevision]:
        """Search only immutable revisions active in the requested version.

        `query` here is a plain substring filter, unchanged from before. For real
        semantic ranking via Atlas Vector Search, see recall_scored() below — kept
        as a separate method rather than changed in place here, since this one
        already has callers and tests that depend on the substring behavior and
        don't require a live Vector Search index to run.
        """
        if limit < 1 or limit > self._max_recall_limit:
            raise ValueError(f"limit must be between 1 and {self._max_recall_limit}")
        version = self._require_version(version_id)
        if not version.memory_refs:
            return []
        return self._manifest_scan(
            version,
            query=query,
            subjects=subjects,
            kinds=kinds,
            locations=locations,
            limit=limit,
        )

    def recall_scored(
        self,
        version_id: str,
        query: str,
        *,
        subjects: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        locations: Sequence[str] | None = None,
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> list[tuple[MemoryRevision, float]]:
        """Real semantic search via Atlas Vector Search (see _ensure_vector_index: an
        autoEmbed index on content.text, so Atlas calls Voyage AI to embed the query
        itself — no embedding call on our side). Keeps each result's vectorSearchScore
        (0-1) for display, e.g. the frontend's "retrieved from Atlas, similarity 0.94"
        panel. Requires ensure_indexes() to have been run at least once."""
        if limit < 1 or limit > self._max_recall_limit:
            raise ValueError(f"limit must be between 1 and {self._max_recall_limit}")
        version = self._require_version(version_id)
        if not version.memory_refs:
            return []
        return self._vector_recall(
            version,
            query,
            subjects=subjects,
            kinds=kinds,
            locations=locations,
            limit=limit,
        )

    def _vector_recall(
        self,
        version: HarnessVersionRecord,
        query: str,
        *,
        subjects: Sequence[str] | None,
        kinds: Sequence[str] | None,
        locations: Sequence[str] | None,
        limit: int,
    ) -> list[tuple[MemoryRevision, float]]:
        manifest = {reference.revision_id: reference.memory_id for reference in version.memory_refs}
        revision_ids = list(manifest)
        match: dict[str, Any] = {
            "_id": {"$in": revision_ids},
            "experiment_id": version.experiment_id,
        }
        if kinds:
            match["kind"] = {"$in": list(kinds)}
        if locations:
            match["locations"] = {"$in": list(locations)}
        over_fetch = min(self._max_recall_limit, limit * 3)
        pipeline: list[dict[str, Any]] = [
            {
                "$vectorSearch": {
                    "index": VECTOR_INDEX_NAME,
                    "path": "content.text",
                    "query": query,
                    "filter": match,
                    "numCandidates": max(over_fetch * 10, 50),
                    "limit": over_fetch,
                }
            },
            {"$set": {"_vectorSearchScore": {"$meta": "vectorSearchScore"}}},
        ]
        subject_filter = set(subjects or ())
        location_filter = set(locations or ())
        results: list[tuple[MemoryRevision, float]] = []
        total_bytes = 0
        for stored in self._database.memories.aggregate(pipeline):
            raw = dict(stored)
            score = raw.pop("_vectorSearchScore")
            revision_id = raw.get("_id")
            # Atlas enforces these constraints in $vectorSearch too, but the
            # manifest remains the application-level retrieval authority.
            if revision_id not in manifest or raw.get("experiment_id") != version.experiment_id:
                continue
            revision = _model(MemoryRevision, raw, "revision_id")
            if revision.memory_id != manifest[revision.revision_id]:
                raise ValueError(
                    f"revision {revision.revision_id!r} does not match its manifest memory_id"
                )
            if subject_filter and subject_filter.isdisjoint(revision.subjects):
                continue
            if location_filter and location_filter.isdisjoint(revision.locations):
                continue
            serialized = json.dumps(revision.model_dump(mode="json"), sort_keys=True)
            item_bytes = len(serialized.encode("utf-8"))
            if total_bytes + item_bytes > self._max_recall_bytes:
                break
            results.append((revision, score))
            total_bytes += item_bytes
            if len(results) == limit:
                break
        return results

    def get_game_evaluations(
        self,
        *,
        chain: str | None = None,
        retrieval_query: str | None = None,
        retrieval_limit: int = DEFAULT_RECALL_LIMIT,
    ) -> list[GameEvaluation]:
        """Build per-game evidence rows without materializing another state store.

        Published diffs count only a committed audit event whose child manifest
        exists and matches the run's played version. Optional retrieval scores are
        computed live against that exact played manifest.
        """
        run_filter: dict[str, Any] = {} if chain is None else {"chain": chain}
        run_cursor = self._database.runs.find(run_filter).sort(
            [("chain", ASCENDING), ("game_index", ASCENDING), ("started_at", ASCENDING)]
        )
        runs = [RunRecord.model_validate(_without_id(raw)) for raw in run_cursor]
        if not runs:
            return []

        run_ids = [run.run_id for run in runs]
        version_raws = self._database.harness_versions.find({"source_run_id": {"$in": run_ids}})
        children_by_run: dict[str, list[HarnessVersionRecord]] = {}
        for raw in version_raws:
            child = _model(HarnessVersionRecord, raw, "version_id")
            if child.source_run_id is not None:
                children_by_run.setdefault(child.source_run_id, []).append(child)

        event_raws = self._database.memory_events.find(
            {"source_run_id": {"$in": run_ids}, "phase": "committed"}
        )
        events_by_child: dict[str, MemoryEvent] = {}
        for raw in event_raws:
            event = _model(MemoryEvent, raw, "event_id")
            if event.child_version_id is not None:
                events_by_child[event.child_version_id] = event

        next_versions = {
            current.run_id: following.version_id
            for current, following in pairwise(runs)
            if current.chain is not None
            and current.chain == following.chain
            and current.game_index is not None
            and following.game_index == current.game_index + 1
        }
        rows: list[GameEvaluation] = []
        for run in runs:
            selected_child = _published_child(
                run,
                children_by_run.get(run.run_id, []),
                events_by_child,
                preferred_version_id=next_versions.get(run.run_id),
            )
            committed_event = (
                None if selected_child is None else events_by_child.get(selected_child.version_id)
            )
            memory_diffs = _diff_counts(
                [] if committed_event is None else committed_event.operations
            )
            rule_diffs = _diff_counts(
                []
                if committed_event is None or committed_event.proposal is None
                else committed_event.proposal.rule_diffs
            )
            retrieval_scores: list[RetrievalScore] | None = None
            if retrieval_query is not None and run.version_id is not None:
                scored = self.recall_scored(run.version_id, retrieval_query, limit=retrieval_limit)
                retrieval_scores = [
                    RetrievalScore(
                        memory_id=revision.memory_id,
                        revision_id=revision.revision_id,
                        score=score,
                    )
                    for revision, score in scored
                ]
            rows.append(
                GameEvaluation(
                    run_id=run.run_id,
                    chain=run.chain,
                    game_index=run.game_index,
                    version_id=run.version_id,
                    child_version_id=(
                        None if selected_child is None else selected_child.version_id
                    ),
                    score=run.score,
                    end_reason=run.end_reason,
                    death_move=run.death_move,
                    cost_usd=run.cost_usd,
                    memory_diffs=memory_diffs,
                    rule_diffs=rule_diffs,
                    retrieval_scores=retrieval_scores,
                )
            )
        return rows

    def _manifest_scan(
        self,
        version: HarnessVersionRecord,
        *,
        query: str | None,
        subjects: Sequence[str] | None,
        kinds: Sequence[str] | None,
        locations: Sequence[str] | None,
        limit: int,
    ) -> list[MemoryRevision]:
        revision_ids = [reference.revision_id for reference in version.memory_refs]
        raws = self._database.memories.find(
            {"_id": {"$in": revision_ids}, "experiment_id": version.experiment_id}
        )
        revisions_by_id = {raw["_id"]: _model(MemoryRevision, raw, "revision_id") for raw in raws}
        subject_filter = set(subjects or ())
        kind_filter = set(kinds or ())
        location_filter = set(locations or ())
        query_folded = None if query is None else query.casefold().strip()
        results: list[MemoryRevision] = []
        total_bytes = 0

        # Additions append to the manifest. Prefer recent learning whenever the
        # item or byte limit means reflection can only inspect a subset.
        for reference in reversed(version.memory_refs):
            revision = revisions_by_id.get(reference.revision_id)
            if revision is None:
                raise ValueError(
                    f"version {version.version_id!r} references missing revision "
                    f"{reference.revision_id!r}"
                )
            if revision.memory_id != reference.memory_id:
                raise ValueError(
                    f"revision {reference.revision_id!r} does not match its manifest memory_id"
                )
            if kind_filter and revision.kind not in kind_filter:
                continue
            if subject_filter and subject_filter.isdisjoint(revision.subjects):
                continue
            if location_filter and location_filter.isdisjoint(revision.locations):
                continue
            serialized = json.dumps(revision.model_dump(mode="json"), sort_keys=True)
            if query_folded and query_folded not in serialized.casefold():
                continue
            item_bytes = len(serialized.encode("utf-8"))
            if total_bytes + item_bytes > self._max_recall_bytes:
                break
            results.append(revision)
            total_bytes += item_bytes
            if len(results) == limit:
                break
        return results

    def _require_version(self, version_id: str) -> HarnessVersionRecord:
        version = self.get_version(version_id)
        if version is None:
            raise LookupError(f"unknown harness version: {version_id}")
        return version

    def _insert_immutable(
        self,
        collection_name: str,
        document: dict[str, Any],
        *,
        session: ClientSession | None = None,
    ) -> None:
        collection = self._database[collection_name]
        try:
            collection.insert_one(document, session=session)
        except DuplicateKeyError:
            existing = collection.find_one({"_id": document["_id"]}, session=session)
            # Compare the representation MongoDB persists: BSON truncates
            # datetimes to milliseconds and decodes them without tzinfo.
            if existing != _bson_round_trip(document):
                raise ValueError(
                    f"immutable document {document['_id']!r} already exists with different content"
                ) from None


def _published_child(
    run: RunRecord,
    candidates: Sequence[HarnessVersionRecord],
    events_by_child: dict[str, MemoryEvent],
    *,
    preferred_version_id: str | None,
) -> HarnessVersionRecord | None:
    """Select only a child backed by a matching committed audit event."""
    published: list[HarnessVersionRecord] = []
    for child in candidates:
        event = events_by_child.get(child.version_id)
        if (
            child.parent_id == run.version_id
            and child.source_run_id == run.run_id
            and event is not None
            and event.experiment_id == child.experiment_id
            and event.proposal_id == child.proposal_id
            and event.source_run_id == run.run_id
            and event.parent_id == run.version_id
        ):
            published.append(child)
    if preferred_version_id is not None:
        preferred = next(
            (child for child in published if child.version_id == preferred_version_id), None
        )
        if preferred is not None:
            return preferred
    return max(published, key=lambda child: (child.created_at, child.version_id), default=None)


def _diff_counts(operations: Sequence[Any]) -> PublishedDiffCounts:
    counts = {"add": 0, "revise": 0, "retire": 0}
    for operation in operations:
        op = operation.get("op") if isinstance(operation, dict) else operation.op
        if op in counts:
            counts[op] += 1
    return PublishedDiffCounts(
        added=counts["add"],
        revised=counts["revise"],
        retired=counts["retire"],
    )


def _bson_round_trip(document: dict[str, Any]) -> dict[str, Any]:
    """Canonicalize a document exactly as a MongoDB BSON write/read does."""
    return BSON(BSON.encode(document)).decode()


def _document(model: ContractModel, id_field: str) -> dict[str, Any]:
    document: dict[str, Any] = model.model_dump(mode="python")
    document["_id"] = document.pop(id_field)
    return document


def _model(model_type: type[_ContractT], raw: dict[str, Any], id_field: str) -> _ContractT:
    document = dict(raw)
    document[id_field] = document.pop("_id")
    return model_type.model_validate(document)
