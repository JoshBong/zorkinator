"""MongoDB Atlas access layer.

This is the only module that imports `pymongo` directly. Every other component
(builder, verifier, scribe, reflector, versions, runner) reads and writes Atlas
through the functions here, so the document shapes in docs/CONTRACTS.md stay in
one place.
"""

from __future__ import annotations

import os
from typing import Any, Literal, TypeVar

from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from .models import (
    ContractModel,
    HarnessVersionDoc,
    LessonDoc,
    MoveRecord,
    RuleDoc,
    RunRecord,
    WorldFactDoc,
)

load_dotenv()

DB_NAME = "zork"

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
    """Create the indexes docs/CONTRACTS.md requires. Safe to call repeatedly."""
    db = get_db()
    db.moves.create_index([("run_id", ASCENDING), ("n", ASCENDING)])
    db.world_facts.create_index(
        [("run_id", ASCENDING), ("subject", ASCENDING), ("attr", ASCENDING)],
        unique=True,
    )
    db.harness_versions.create_index([("created_at", DESCENDING)])


def _without_id(raw: dict[str, Any]) -> dict[str, Any]:
    """Drop Mongo's `_id` for models that carry their own natural key (`run_id`, ...)."""
    data = dict(raw)
    data.pop("_id", None)
    return data


def _to_mongo(model: ContractModel) -> dict[str, Any]:
    """`id` -> `_id` for models that use the `id` convention (rules, lessons, harness_versions)."""
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


# --- lessons ---


def insert_lesson(lesson: LessonDoc) -> str:
    """Insert a new cross-game lesson, as proposed by the Reflector."""
    get_db().lessons.insert_one(_to_mongo(lesson))
    return lesson.id


def get_lessons() -> list[LessonDoc]:
    """Fetch every cross-game lesson."""
    cursor = get_db().lessons.find()
    return [_from_mongo(LessonDoc, raw) for raw in cursor]


# --- harness_versions ---


def save_harness_version(version: HarnessVersionDoc) -> str:
    """Archive a new harness version with its parent and scores."""
    get_db().harness_versions.insert_one(_to_mongo(version))
    return version.id


def get_harness_version(version_id: str) -> HarnessVersionDoc | None:
    """Fetch one harness version by id."""
    raw = get_db().harness_versions.find_one({"_id": version_id})
    return None if raw is None else _from_mongo(HarnessVersionDoc, raw)


def get_latest_harness_version() -> HarnessVersionDoc | None:
    """Fetch the most recently created harness version, if any exist yet."""
    raw = get_db().harness_versions.find_one(sort=[("created_at", DESCENDING)])
    return None if raw is None else _from_mongo(HarnessVersionDoc, raw)
