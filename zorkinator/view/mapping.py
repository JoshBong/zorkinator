"""Maps real Mongo documents (see docs/CONTRACTS.md) onto the GRUE LAB response shapes.

Several frontend fields have no real producer yet: the Parser/Scribe/Reflector/Verifier
aren't built, so retrieved_rules, per-move repeated_action, rules_learned per life,
reflections, and the room map are returned honestly empty/best-effort rather than faked.
Each gap is called out in a comment below rather than filled with invented numbers.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import groupby
from typing import Any, Literal

from .. import db
from ..models import GameEvaluation, MoveRecord, RuleDoc, RunRecord
from .schemas import (
    Condition,
    EvalConditionOut,
    EvalSummaryOut,
    GuardrailOut,
    HarnessConfig,
    LearnedFromOut,
    LifeOut,
    MapEdgeOut,
    MapRoomOut,
    MoveOut,
    PerLifePointOut,
    ReflectionOut,
    RuleOut,
    RunMapOut,
    RunOut,
)

_END_REASON_LABEL: dict[str, str] = {
    "death": "died",
    "won": "won the game",
    "game_over": "game over",
    "gave_up": "gave up",
    "cap": "hit the move cap",
    "usd_cap": "hit the $ cap",
    "stuck40": "stuck (no progress)",
}


def _group_key(run: RunRecord) -> str:
    """A `chain` is one sequential series (CONTRACTS.md); an unchained run is its own group."""
    return run.chain or run.run_id


def _life_of(run: RunRecord) -> int:
    return run.game_index if run.game_index is not None else 1


def _condition_of(mode: str) -> Condition:
    return "harness" if mode == "harness" else "baseline"


def _config_for_run(run: RunRecord) -> HarnessConfig:
    """Best-effort: reads the run's harness_version context_policy when set, else defaults
    to all-on for harness mode / all-off for paper mode. Ablation toggles (Ablation Lab)
    aren't wired into context_policy yet, so every harness run currently looks "full-on".
    """
    on = run.mode == "harness"
    policy: dict[str, Any] = {}
    if run.version_id is not None:
        version = db.MongoOuterLoopStore(db.get_db()).get_version(run.version_id)
        if version is not None:
            policy = version.context_policy
    return HarnessConfig(
        reflection=bool(policy.get("reflection", on)),
        rules=bool(policy.get("rules", on)),
        memory=bool(policy.get("memory", on)),
        guardrails=bool(policy.get("guardrails", on)),
    )


def _members_of(group_id: str) -> list[RunRecord]:
    return sorted((r for r in db.get_runs() if _group_key(r) == group_id), key=_life_of)


def list_runs() -> list[RunOut]:
    """Group logged `runs` by chain into the frontend's Run (a chain = many lives)."""
    all_runs = sorted(db.get_runs(), key=_group_key)
    out: list[RunOut] = []
    for group_id, members_iter in groupby(all_runs, key=_group_key):
        members = list(members_iter)
        first = members[0]
        out.append(
            RunOut(
                run_id=group_id,
                condition=_condition_of(first.mode),
                model=first.model,
                config=_config_for_run(first),
                status="done",  # a `runs` doc is only written once MongoSink.run() finishes
                lives_count=len(members),
                created_at=min(m.started_at for m in members).isoformat(),
            )
        )
    return out


def list_lives(group_id: str) -> list[LifeOut]:
    return [
        LifeOut(
            life=_life_of(r),
            score=r.score,
            moves=r.moves,
            death_cause=_END_REASON_LABEL.get(r.end_reason, r.end_reason),
            rules_learned=[],  # no per-life rule attribution yet (Reflector not built)
        )
        for r in _members_of(group_id)
    ]


def _run_for_life(group_id: str, life: int) -> RunRecord | None:
    return next((r for r in _members_of(group_id) if _life_of(r) == life), None)


def move_out(m: MoveRecord) -> MoveOut:
    """Shared by list_moves() and the SSE stream (view/app.py) so both agree on one mapping."""
    guardrail = None
    if m.rejections:
        last = m.rejections[-1]
        guardrail = GuardrailOut(
            blocked=True,
            rule_id=last.get("rule_id", ""),
            original_command=last.get("cmd", m.command),
            replacement_command=m.command,
        )
    return MoveOut(
        move=m.n,
        observation=m.text,
        command=m.command,
        room=m.room or "",
        score=m.score,
        retrieved_rules=[],  # Context Builder / vector retrieval not built yet
        guardrail=guardrail,
        repeated_action=False,  # Progress Monitor not built yet
    )


def list_moves(group_id: str, life: int) -> list[MoveOut]:
    run = _run_for_life(group_id, life)
    if run is None:
        return []
    return [move_out(m) for m in db.get_moves(run.run_id)]


def get_reflection(group_id: str, life: int) -> ReflectionOut | None:
    """Best-effort: reads the most recent memory_event proposed for this life's run.

    Returns None (-> 404) once no Reflector has run for it yet; nothing is fabricated.
    """
    run = _run_for_life(group_id, life)
    if run is None:
        return None
    raw = db.get_db().memory_events.find_one(
        {"source_run_id": run.run_id}, sort=[("created_at", -1)]
    )
    if raw is None:
        return None
    op_ids = [str(op.get("memory_id") or op.get("key", "")) for op in raw["operations"]]
    return ReflectionOut(
        cause=f"See proposal {raw['proposal_id']} ({raw['phase']}).",
        effect=f"Run {run.run_id} ended: {_END_REASON_LABEL.get(run.end_reason, run.end_reason)}.",
        lesson_text=raw.get("reason") or "(no summary recorded)",
        rule_id=op_ids[0] if op_ids else "",
    )


def _rule_type_of(rule: RuleDoc) -> Literal["rule", "guardrail", "memory"]:
    """RuleDoc has no `type` field; infer it from verdict+status instead."""
    if rule.verdict == "warn":
        return "memory"
    if rule.status == "hard":
        return "guardrail"
    return "rule"


def rule_out(r: RuleDoc) -> RuleOut:
    """Best-effort mapping: RuleDoc has no version field the way the frontend expects
    (no rule-level version history is modeled yet), so every rule is its own v1 row.
    """
    evidence = r.evidence[0] if r.evidence else ""
    ev_run_id, _, ev_move = evidence.partition(":")
    return RuleOut(
        rule_id=r.id,
        version=1,
        type=_rule_type_of(r),
        text=r.text,
        status="active" if r.status == "hard" else "superseded",
        learned_from=LearnedFromOut(
            run_id=ev_run_id,
            life=1,
            move=int(ev_move) if ev_move.isdigit() else 0,
        ),
        score_impact=0.0,  # Verifier promotion doesn't record a score delta yet
        parent_version=None,
        created_at="",  # RuleDoc has no created_at field yet
    )


def list_rules() -> list[RuleOut]:
    return [rule_out(r) for r in db.get_rules()]


def get_map(group_id: str) -> RunMapOut:
    """Real query over world_facts; empty until the Scribe (Seb) writes room/exit facts."""
    rooms: dict[str, MapRoomOut] = {}
    edges: list[MapEdgeOut] = []
    for member in _members_of(group_id):
        for fact in db.get_world_facts(member.run_id):
            if fact.attr == "dark":
                room = rooms.setdefault(
                    fact.subject,
                    MapRoomOut(
                        name=fact.subject,
                        dark=False,
                        first_seen_life=_life_of(member),
                        death_lives=[],  # no per-room death detection yet (Scribe doesn't write it)
                    ),
                )
                room.dark = bool(fact.value)
            elif fact.attr.startswith("exit_"):
                direction = fact.attr.removeprefix("exit_")
                edges.append(
                    MapEdgeOut(from_=fact.subject, to=str(fact.value), direction=direction)
                )
    return RunMapOut(rooms=list(rooms.values()), edges=edges)


def get_eval_summary() -> EvalSummaryOut:
    """Real aggregation over whatever `runs` exist, grouped by mode. Empty until games run."""
    conditions: list[EvalConditionOut] = []
    for mode in ("paper", "harness"):
        runs = db.get_runs(mode=mode)
        if not runs:
            continue
        by_life: dict[int, list[int]] = defaultdict(list)
        for r in runs:
            by_life[_life_of(r)].append(r.score)
        per_life = [
            PerLifePointOut(
                life=life, mean=sum(scores) / len(scores), min=min(scores), max=max(scores)
            )
            for life, scores in sorted(by_life.items())
        ]
        run_ids = [r.run_id for r in runs]
        guardrail_blocks = db.get_db().moves.count_documents(
            {"run_id": {"$in": run_ids}, "rejections.0": {"$exists": True}}
        )
        conditions.append(
            EvalConditionOut(
                condition=_condition_of(mode),
                config=_config_for_run(runs[0]),
                n_runs=len({_group_key(r) for r in runs}),
                per_life=per_life,
                avg_score=sum(r.score for r in runs) / len(runs),
                avg_moves=sum(r.moves for r in runs) / len(runs),
                repeated_actions=0,  # Progress Monitor not built yet
                guardrail_blocks=guardrail_blocks,
            )
        )
    return EvalSummaryOut(conditions=conditions)


def list_game_evaluations(
    *, chain: str | None = None, retrieval_query: str | None = None
) -> list[GameEvaluation]:
    """Expose the read-only Atlas evidence view used by charts and replay explanations."""
    return db.MongoOuterLoopStore(db.get_db()).get_game_evaluations(
        chain=chain, retrieval_query=retrieval_query
    )
