"""FastAPI app exposing the GRUE LAB API (frontend/src/api/client.ts is the contract).

Run:  .venv/bin/uvicorn zorkinator.view.app:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .. import db
from ..models import GameEvaluation, MoveRecord
from . import mapping
from .schemas import (
    EvalSummaryOut,
    LifeOut,
    MemoryOut,
    MoveOut,
    ReflectionOut,
    RuleOut,
    RunMapOut,
    RunOut,
)

app = FastAPI(title="GRUE LAB API")

# The frontend dev server's port can shift (autoPort in .claude/launch.json), so the local
# default matches any localhost port; set VIEW_CORS_ORIGINS to a comma-separated list to
# lock this down (e.g. for the deployed demo).
_explicit_origins = os.environ.get("VIEW_CORS_ORIGINS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_explicit_origins.split(",") if _explicit_origins else [],
    allow_origin_regex=None if _explicit_origins else r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/runs")
def list_runs() -> list[RunOut]:
    return mapping.list_runs()


@app.get("/api/runs/{run_id}/lives")
def list_lives(run_id: str) -> list[LifeOut]:
    return mapping.list_lives(run_id)


@app.get("/api/runs/{run_id}/lives/{life}/moves")
def list_moves(run_id: str, life: int) -> list[MoveOut]:
    return mapping.list_moves(run_id, life)


@app.get("/api/runs/{run_id}/memories")
def list_memories(run_id: str) -> list[MemoryOut]:
    memories = mapping.list_memories(run_id)
    if memories is None:
        raise HTTPException(404, f"run {run_id!r} was not found")
    return memories


@app.get("/api/runs/{run_id}/lives/{life}/reflection")
def get_reflection(run_id: str, life: int) -> ReflectionOut:
    reflection = mapping.get_reflection(run_id, life)
    if reflection is None:
        raise HTTPException(404, f"no reflection recorded yet for {run_id} life {life}")
    return reflection


@app.get("/api/rules")
def list_rules() -> list[RuleOut]:
    return mapping.list_rules()


@app.get("/api/runs/{run_id}/map")
def get_map(run_id: str) -> RunMapOut:
    return mapping.get_map(run_id)


@app.get("/api/eval/summary")
def get_eval_summary() -> EvalSummaryOut:
    return mapping.get_eval_summary()


@app.get("/api/eval/games")
def list_game_evaluations(
    chain: str | None = None, retrieval_query: str | None = None
) -> list[GameEvaluation]:
    return mapping.list_game_evaluations(chain=chain, retrieval_query=retrieval_query)


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _watch_moves(run_id: str, life: int) -> Iterator[str]:
    """Tails Atlas via a change stream on `moves` scoped to this run_id.

    Only `move`, `death`, and `guardrail_block` are emitted: `rule_created` would need
    watching `rules` too, and nothing writes new rules yet (Reflector isn't built).
    This is a plain (non-async) generator so FastAPI runs it in its worker thread pool,
    keeping pymongo's blocking watch() off the asyncio event loop.
    """
    pipeline = [{"$match": {"operationType": "insert", "fullDocument.run_id": run_id}}]
    with db.get_db().moves.watch(pipeline, full_document="updateLookup") as stream:
        for change in stream:
            raw = dict(change["fullDocument"])
            raw.pop("_id", None)
            record = MoveRecord.model_validate(raw)
            yield _sse("move", mapping.move_out(record).model_dump())
            if record.died:
                yield _sse("death", {"life": life, "move": record.n, "cause": "death"})
            if record.rejections:
                last = record.rejections[-1]
                yield _sse(
                    "guardrail_block",
                    {
                        "blocked": True,
                        "rule_id": last.get("rule_id", ""),
                        "original_command": last.get("cmd", record.command),
                        "replacement_command": record.command,
                        "move": record.n,
                    },
                )


@app.get("/api/stream/{run_id}")
def stream_run(run_id: str) -> StreamingResponse:
    run = db.get_run(run_id)
    life = (run.game_index if run and run.game_index is not None else 1) if run else 1
    return StreamingResponse(_watch_moves(run_id, life), media_type="text/event-stream")


@app.get("/api/stream/{run_id}/poll")
def poll_run(run_id: str) -> list[dict[str, Any]]:
    """Fallback when SSE fails. Best-effort: returns the run's most recent moves as `move`
    events on every call rather than a true since-last-poll delta — the client (client.ts)
    sends no cursor, so a stateless server can't tell what a given poller already saw.
    """
    moves = db.get_moves(run_id)[-20:]
    return [{"type": "move", "data": mapping.move_out(m).model_dump()} for m in moves]
