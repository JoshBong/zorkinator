"""MongoDB boundary for immutable outer-loop memory.

Only trusted code should call the write methods. The Reflector emits
``ReflectionProposal`` values and never receives a Mongo query interface.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol, TypeVar

from dotenv import load_dotenv
from pydantic import JsonValue
from pymongo import ASCENDING, MongoClient
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from .models import (
    ContractModel,
    HarnessVersionRecord,
    MemoryEvent,
    MemoryRevision,
    ReflectionProposal,
)

DB_NAME = "zork"
DEFAULT_RECALL_LIMIT = 10
MAX_RECALL_LIMIT = 50
MAX_RECALL_BYTES = 64 * 1024

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

    def get_version(self, version_id: str) -> HarnessVersionRecord | None: ...

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None: ...

    def recall(
        self,
        version_id: str,
        *,
        query: str | None = None,
        subjects: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> list[MemoryRevision]: ...


class MongoOuterLoopStore:
    """PyMongo implementation with exact-version visibility and idempotent writes."""

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
        )
        self.put_memory_event(event)
        return event

    def put_memory_revision(self, revision: MemoryRevision) -> None:
        self._insert_immutable("memories", _document(revision, "revision_id"))

    def put_memory_event(self, event: MemoryEvent) -> None:
        self._insert_immutable("memory_events", _document(event, "event_id"))

    def publish_version(self, version: HarnessVersionRecord) -> None:
        """Publish the complete manifest, the authoritative visibility marker."""
        self._insert_immutable("harness_versions", _document(version, "version_id"))

    def get_version(self, version_id: str) -> HarnessVersionRecord | None:
        raw = self._database.harness_versions.find_one({"_id": version_id})
        return None if raw is None else _model(HarnessVersionRecord, raw, "version_id")

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
        limit: int = DEFAULT_RECALL_LIMIT,
    ) -> list[MemoryRevision]:
        """Search only immutable revisions active in the requested version."""
        if limit < 1 or limit > self._max_recall_limit:
            raise ValueError(f"limit must be between 1 and {self._max_recall_limit}")
        version = self._require_version(version_id)
        if not version.memory_refs:
            return []

        revision_ids = [reference.revision_id for reference in version.memory_refs]
        raws = self._database.memories.find(
            {"_id": {"$in": revision_ids}, "experiment_id": version.experiment_id}
        )
        revisions_by_id = {raw["_id"]: _model(MemoryRevision, raw, "revision_id") for raw in raws}
        subject_filter = set(subjects or ())
        kind_filter = set(kinds or ())
        query_folded = None if query is None else query.casefold().strip()
        results: list[MemoryRevision] = []
        total_bytes = 0

        for reference in version.memory_refs:
            revision = revisions_by_id.get(reference.revision_id)
            if revision is None:
                raise ValueError(
                    f"version {version_id!r} references missing revision {reference.revision_id!r}"
                )
            if revision.memory_id != reference.memory_id:
                raise ValueError(
                    f"revision {reference.revision_id!r} does not match its manifest memory_id"
                )
            if kind_filter and revision.kind not in kind_filter:
                continue
            if subject_filter and subject_filter.isdisjoint(revision.subjects):
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

    def _insert_immutable(self, collection_name: str, document: dict[str, Any]) -> None:
        collection = self._database[collection_name]
        try:
            collection.insert_one(document)
        except DuplicateKeyError:
            existing = collection.find_one({"_id": document["_id"]})
            if existing != document:
                raise ValueError(
                    f"immutable document {document['_id']!r} already exists with different content"
                ) from None


def _document(model: ContractModel, id_field: str) -> dict[str, Any]:
    document: dict[str, Any] = model.model_dump(mode="python")
    document["_id"] = document.pop(id_field)
    return document


def _model(model_type: type[_ContractT], raw: dict[str, Any], id_field: str) -> _ContractT:
    document = dict(raw)
    document[id_field] = document.pop("_id")
    return model_type.model_validate(document)
