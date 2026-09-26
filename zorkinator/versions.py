"""Fixed validation and publication of immutable harness versions.

All I/O and rule promotion are injected.  Memory revisions may be staged before
publication, but remain invisible until the complete child manifest is written.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast

from pydantic import JsonValue, ValidationError

from .models import (
    AddMemoryOperation,
    EvidenceRef,
    HarnessVersionRecord,
    MemoryEvent,
    MemoryRef,
    MemoryRevision,
    ReflectionProposal,
    RetireMemoryOperation,
    RuleDoc,
    RunRecord,
)


class CommitError(ValueError):
    """A proposal is invalid for its requested parent/source run."""


DEFAULT_MAX_RULE_DIFF_BYTES = 16 * 1024


class VersionStore(Protocol):
    def get_version(self, version_id: str) -> HarnessVersionRecord | None: ...

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None: ...

    def put_memory_revision(self, revision: MemoryRevision) -> None: ...

    def put_memory_event(self, event: MemoryEvent) -> None: ...

    def publish_bundle(
        self,
        version: HarnessVersionRecord,
        revisions: Sequence[MemoryRevision],
        rules: Sequence[RuleDoc],
        events: Sequence[MemoryEvent],
    ) -> None: ...

    def get_memory_event(self, event_id: str) -> MemoryEvent | None: ...


class RunEvidence(Protocol):
    def get_run(self, run_id: str) -> RunRecord | None: ...

    def has_move(self, run_id: str, n: int) -> bool: ...


class RuleStore(Protocol):
    def get_rule(self, rule_id: str) -> RuleDoc | None: ...


class RulePromoter(Protocol):
    def promote(self, rule: RuleDoc, runs: Sequence[RunRecord]) -> Literal["hard", "soft"]: ...


@dataclass(frozen=True)
class VersionLimits:
    max_operations: int = 20
    max_rule_diffs: int = 10
    max_active_memories: int = 250
    max_memory_bytes: int = 256 * 1024
    max_content_bytes: int = 16 * 1024
    max_rule_diff_bytes: int = DEFAULT_MAX_RULE_DIFF_BYTES
    max_ancestry_depth: int = 1_000

    def __post_init__(self) -> None:
        if (
            min(
                self.max_operations,
                self.max_rule_diffs,
                self.max_active_memories,
                self.max_memory_bytes,
                self.max_content_bytes,
                self.max_rule_diff_bytes,
                self.max_ancestry_depth,
            )
            < 1
        ):
            raise ValueError("version limits must be positive")


class VersionManager:
    """Validate a whole proposal, stage immutable records, then publish once."""

    def __init__(
        self,
        store: VersionStore,
        evidence: RunEvidence,
        rules: RuleStore,
        promoter: RulePromoter,
        *,
        limits: VersionLimits | None = None,
    ) -> None:
        self._store = store
        self._evidence = evidence
        self._rules = rules
        self._promoter = promoter
        self._limits = limits or VersionLimits()

    def commit(self, parent_id: str, proposal: ReflectionProposal, run_id: str) -> str:
        self._verify_persisted_proposal(proposal)
        child_id = _stable_id("version", proposal.proposal_id)
        existing = self._store.get_version(child_id)
        if existing is not None:
            self._verify_existing(existing, parent_id, proposal, run_id)
            run = self._require_run(run_id)
            self._record_event("committed", proposal, existing, run, existing.created_at)
            return child_id

        parent = self._store.get_version(parent_id)
        if parent is None:
            raise CommitError(f"unknown parent version: {parent_id}")
        run = self._require_run(run_id)
        timestamp = run.ended_at
        try:
            self._validate_associations(parent, proposal, run_id, run)
            allowed_runs = self._ancestry_run_ids(parent)
            allowed_runs.add(run_id)
            cited_runs = self._validate_evidence(proposal, allowed_runs)
            memory_refs, revisions = self._apply_memory_ops(parent, proposal, timestamp)
            rule_ids, staged_rules = self._apply_rule_diffs(parent, proposal, child_id, cited_runs)
            self._validate_memory_budget(memory_refs, revisions, parent_id)
        except (CommitError, ValidationError, TypeError, ValueError) as exc:
            error = exc if isinstance(exc, CommitError) else CommitError(str(exc))
            self._record_event("rejected", proposal, None, run, timestamp, reason=str(error))
            raise error from exc

        child = HarnessVersionRecord(
            version_id=child_id,
            experiment_id=parent.experiment_id,
            parent_id=parent.version_id,
            source_run_id=run_id,
            proposal_id=proposal.proposal_id,
            memory_refs=memory_refs,
            rule_ids=rule_ids,
            context_policy=dict(parent.context_policy),
            scores=[],
            created_at=timestamp,
        )
        validated = self._event("validated", proposal, child, run, timestamp)
        committed = self._event("committed", proposal, child, run, timestamp)
        try:
            self._store.publish_bundle(
                child,
                revisions,
                staged_rules,
                [validated, committed],
            )
        except Exception as exc:
            self._record_event("failed", proposal, child, run, timestamp, reason=str(exc))
            raise
        return child_id

    def _validate_associations(
        self,
        parent: HarnessVersionRecord,
        proposal: ReflectionProposal,
        run_id: str,
        run: RunRecord,
    ) -> None:
        if proposal.parent_id != parent.version_id or proposal.run_id != run_id:
            raise CommitError("proposal parent/source run does not match commit arguments")
        if run.mode != "harness" or run.version_id != parent.version_id:
            raise CommitError("source must be a harness run played by the parent version")
        if len(proposal.memory_ops) > self._limits.max_operations:
            raise CommitError("proposal exceeds memory operation limit")
        if len(proposal.rule_diffs) > self._limits.max_rule_diffs:
            raise CommitError("proposal exceeds rule diff limit")

    def _ancestry_run_ids(self, parent: HarnessVersionRecord) -> set[str]:
        allowed: set[str] = set()
        seen: set[str] = set()
        version = parent
        for _ in range(self._limits.max_ancestry_depth):
            if version.version_id in seen:
                raise CommitError("version ancestry contains a cycle")
            seen.add(version.version_id)
            if version.experiment_id != parent.experiment_id:
                raise CommitError("version ancestry crosses experiment boundary")
            if version.source_run_id is not None:
                allowed.add(version.source_run_id)
            if version.parent_id is None:
                return allowed
            ancestor = self._store.get_version(version.parent_id)
            if ancestor is None:
                raise CommitError(f"missing ancestor version: {version.parent_id}")
            version = ancestor
        raise CommitError("version ancestry exceeds configured depth")

    def _validate_evidence(
        self, proposal: ReflectionProposal, allowed_runs: set[str]
    ) -> dict[str, RunRecord]:
        refs: list[EvidenceRef] = []
        for operation in proposal.memory_ops:
            refs.extend(operation.evidence)
        for diff in proposal.rule_diffs:
            refs.extend(_parse_rule_evidence(diff.get("evidence", [])))
        runs: dict[str, RunRecord] = {}
        for ref in refs:
            if ref.run_id not in allowed_runs:
                raise CommitError(f"evidence run is outside parent ancestry: {ref.run_id}")
            evidence_run = runs.get(ref.run_id) or self._require_run(ref.run_id)
            runs[ref.run_id] = evidence_run
            if not self._evidence.has_move(ref.run_id, ref.n):
                raise CommitError(f"unknown evidence move: {ref.run_id}:{ref.n}")
        return runs

    def _apply_memory_ops(
        self,
        parent: HarnessVersionRecord,
        proposal: ReflectionProposal,
        timestamp: datetime,
    ) -> tuple[list[MemoryRef], list[MemoryRevision]]:
        refs = list(parent.memory_refs)
        positions = {ref.memory_id: index for index, ref in enumerate(refs)}
        revisions: list[MemoryRevision] = []
        for index, operation in enumerate(proposal.memory_ops):
            operation_key = (
                f"add:{operation.key}"
                if isinstance(operation, AddMemoryOperation)
                else f"{operation.op}:{index}"
            )
            if isinstance(operation, AddMemoryOperation):
                memory_id = _stable_id("memory", proposal.proposal_id, operation.key)
                if memory_id in positions:
                    raise CommitError(f"generated duplicate memory id: {memory_id}")
                revision_id = _stable_id("memory_revision", proposal.proposal_id, operation_key)
                revision = MemoryRevision(
                    revision_id=revision_id,
                    experiment_id=self._require_parent(proposal.parent_id).experiment_id,
                    memory_id=memory_id,
                    supersedes_revision_id=None,
                    kind=operation.kind,
                    subjects=operation.subjects,
                    content=operation.content,
                    status=operation.status,
                    evidence=operation.evidence,
                    rationale=operation.rationale,
                    source_run_id=proposal.run_id,
                    proposal_id=proposal.proposal_id,
                    operation_key=operation_key,
                    born_version_id=_stable_id("version", proposal.proposal_id),
                    created_at=timestamp,
                )
                positions[memory_id] = len(refs)
                refs.append(MemoryRef(memory_id=memory_id, revision_id=revision_id))
                revisions.append(revision)
                continue

            position = positions.get(operation.memory_id)
            if position is None:
                raise CommitError(f"memory is not active in parent: {operation.memory_id}")
            active = refs[position]
            if active.revision_id != operation.expected_revision_id:
                raise CommitError(f"stale expected revision for memory {operation.memory_id}")
            if isinstance(operation, RetireMemoryOperation):
                refs.pop(position)
                positions = {ref.memory_id: i for i, ref in enumerate(refs)}
                continue

            revision_id = _stable_id("memory_revision", proposal.proposal_id, operation_key)
            revision = MemoryRevision(
                revision_id=revision_id,
                experiment_id=self._require_parent(proposal.parent_id).experiment_id,
                memory_id=operation.memory_id,
                supersedes_revision_id=operation.expected_revision_id,
                kind=operation.kind,
                subjects=operation.subjects,
                content=operation.content,
                status=operation.status,
                evidence=operation.evidence,
                rationale=operation.rationale,
                source_run_id=proposal.run_id,
                proposal_id=proposal.proposal_id,
                operation_key=operation_key,
                born_version_id=_stable_id("version", proposal.proposal_id),
                created_at=timestamp,
            )
            refs[position] = MemoryRef(memory_id=operation.memory_id, revision_id=revision_id)
            revisions.append(revision)
        return refs, revisions

    def _apply_rule_diffs(
        self,
        parent: HarnessVersionRecord,
        proposal: ReflectionProposal,
        child_id: str,
        cited_runs: Mapping[str, RunRecord],
    ) -> tuple[list[str], list[RuleDoc]]:
        validate_rule_diffs(
            proposal.rule_diffs,
            parent.rule_ids,
            proposal.proposal_id,
            max_rule_diff_bytes=self._limits.max_rule_diff_bytes,
        )
        rule_ids = list(parent.rule_ids)
        staged: list[RuleDoc] = []
        for rule_id in rule_ids:
            if self._rules.get_rule(rule_id) is None:
                raise CommitError(f"parent references missing rule: {rule_id}")
        for index, raw in enumerate(proposal.rule_diffs):
            if len(_canonical_json(raw)) > self._limits.max_rule_diff_bytes:
                raise CommitError("rule diff exceeds size limit")
            op = raw.get("op")
            if op == "retire":
                rule_id = _required_string(raw, "rule_id")
                if rule_id not in rule_ids:
                    raise CommitError(f"rule is not active in parent: {rule_id}")
                rule_ids.remove(rule_id)
                continue
            if op not in {"add", "revise"}:
                raise CommitError("rule diff op must be add, revise, or retire")
            old_rule_id: str | None = None
            if op == "revise":
                old_rule_id = _required_string(raw, "rule_id")
                if old_rule_id not in rule_ids:
                    raise CommitError(f"rule is not active in parent: {old_rule_id}")
            key = str(raw.get("key") or f"rule_{index}")
            rule_id = _stable_id("rule", proposal.proposal_id, key)
            when = raw.get("when")
            if not isinstance(when, dict):
                raise CommitError("rule when must be an object")
            evidence = _parse_rule_evidence(raw.get("evidence", []))
            if not evidence:
                raise CommitError("new or revised rules require evidence")
            rule = RuleDoc(
                id=rule_id,
                text=_required_string(raw, "text"),
                when=dict(when),
                verdict=cast(Literal["warn", "block"], raw.get("verdict", "block")),
                status="soft",
                evidence=[f"{ref.run_id}:{ref.n}" for ref in evidence],
                fired=0,
                born_version=child_id,
                promoted_version=None,
            )
            promotion_runs = [
                cited_runs[run_id] for run_id in dict.fromkeys(ref.run_id for ref in evidence)
            ]
            status = self._promoter.promote(rule, promotion_runs)
            if status not in {"soft", "hard"}:
                raise CommitError("rule promoter returned an invalid status")
            if status == "hard":
                rule = rule.model_copy(update={"status": "hard", "promoted_version": child_id})
            if old_rule_id is None:
                rule_ids.append(rule_id)
            else:
                rule_ids[rule_ids.index(old_rule_id)] = rule_id
            staged.append(rule)
        if len(rule_ids) != len(set(rule_ids)):
            raise CommitError("child version contains duplicate rule ids")
        return rule_ids, staged

    def _validate_memory_budget(
        self,
        refs: Sequence[MemoryRef],
        staged: Sequence[MemoryRevision],
        parent_id: str,
    ) -> None:
        if len(refs) > self._limits.max_active_memories:
            raise CommitError("child exceeds active memory limit")
        staged_by_id = {revision.revision_id: revision for revision in staged}
        total = 0
        for ref in refs:
            revision = staged_by_id.get(ref.revision_id)
            if revision is None:
                revision = self._store.read(parent_id, ref.memory_id)
            if revision is None or revision.revision_id != ref.revision_id:
                raise CommitError(f"cannot resolve active memory {ref.memory_id}")
            content_bytes = len(_canonical_json(revision.content))
            if content_bytes > self._limits.max_content_bytes:
                raise CommitError(f"memory {ref.memory_id} exceeds content size limit")
            total += len(_canonical_json(revision.model_dump(mode="json")))
        if total > self._limits.max_memory_bytes:
            raise CommitError("child exceeds total active memory byte limit")

    def _record_event(
        self,
        phase: Literal["validated", "rejected", "committed", "failed"],
        proposal: ReflectionProposal,
        child: HarnessVersionRecord | None,
        run: RunRecord,
        timestamp: datetime,
        *,
        reason: str | None = None,
    ) -> None:
        self._store.put_memory_event(
            self._event(phase, proposal, child, run, timestamp, reason=reason)
        )

    def _event(
        self,
        phase: Literal["validated", "rejected", "committed", "failed"],
        proposal: ReflectionProposal,
        child: HarnessVersionRecord | None,
        run: RunRecord,
        timestamp: datetime,
        *,
        reason: str | None = None,
    ) -> MemoryEvent:
        return MemoryEvent(
            event_id=f"{proposal.proposal_id}:{phase}",
            experiment_id=(
                child.experiment_id
                if child is not None
                else self._require_parent(proposal.parent_id).experiment_id
            ),
            proposal_id=proposal.proposal_id,
            phase=phase,
            source_run_id=proposal.run_id,
            parent_id=proposal.parent_id,
            child_version_id=None if child is None else child.version_id,
            operations=proposal.memory_ops,
            memory_revision_ids=[] if child is None else _proposal_revision_ids(proposal),
            reason=reason,
            model=run.model,
            usage={},
            created_at=timestamp,
            proposal=proposal,
        )

    def _verify_persisted_proposal(self, proposal: ReflectionProposal) -> None:
        for phase in ("proposed", "validated", "rejected", "committed", "failed"):
            event = self._store.get_memory_event(f"{proposal.proposal_id}:{phase}")
            if event is None:
                continue
            if event.proposal is None:
                raise CommitError(
                    "persisted proposal cannot be verified against legacy audit event"
                )
            if event.proposal != proposal:
                raise CommitError("proposal id already belongs to different content")

    def _require_parent(self, version_id: str) -> HarnessVersionRecord:
        parent = self._store.get_version(version_id)
        if parent is None:
            raise CommitError(f"unknown parent version: {version_id}")
        return parent

    def _require_run(self, run_id: str) -> RunRecord:
        run = self._evidence.get_run(run_id)
        if run is None:
            raise CommitError(f"unknown run: {run_id}")
        return run

    @staticmethod
    def _verify_existing(
        version: HarnessVersionRecord,
        parent_id: str,
        proposal: ReflectionProposal,
        run_id: str,
    ) -> None:
        if (
            version.parent_id != parent_id
            or version.proposal_id != proposal.proposal_id
            or version.source_run_id != run_id
            or proposal.parent_id != parent_id
            or proposal.run_id != run_id
        ):
            raise CommitError("stable child id already belongs to a different commit")


def commit(
    parent_id: str,
    proposal: ReflectionProposal,
    run_id: str,
    *,
    store: VersionStore,
    evidence: RunEvidence,
    rules: RuleStore,
    promoter: RulePromoter,
    limits: VersionLimits | None = None,
) -> str:
    """Contract-shaped convenience entry point with explicit dependency injection."""

    return VersionManager(store, evidence, rules, promoter, limits=limits).commit(
        parent_id, proposal, run_id
    )


def validate_rule_diffs(
    rule_diffs: Sequence[Mapping[str, JsonValue]],
    parent_rule_ids: Sequence[str],
    proposal_id: str,
    *,
    max_rule_diff_bytes: int = DEFAULT_MAX_RULE_DIFF_BYTES,
) -> None:
    """Validate rule-diff shape and parent targets without writing state."""
    active = list(parent_rule_ids)
    generated_ids: set[str] = set()
    for raw in rule_diffs:
        if len(_canonical_json(raw)) > max_rule_diff_bytes:
            raise CommitError("rule diff exceeds size limit")
        op = raw.get("op")
        if op == "retire":
            _reject_unknown_rule_fields(raw, {"op", "rule_id"})
            rule_id = _required_string(raw, "rule_id")
            if rule_id not in active:
                raise CommitError(f"rule is not active in parent: {rule_id}")
            active.remove(rule_id)
            continue
        if op not in {"add", "revise"}:
            raise CommitError("rule diff op must be add, revise, or retire")

        allowed = {"op", "key", "text", "when", "verdict", "evidence"}
        old_rule_id: str | None = None
        if op == "revise":
            allowed.add("rule_id")
            old_rule_id = _required_string(raw, "rule_id")
            if old_rule_id not in active:
                raise CommitError(f"rule is not active in parent: {old_rule_id}")
        _reject_unknown_rule_fields(raw, allowed)
        key = _required_string(raw, "key")
        _required_string(raw, "text")
        if not isinstance(raw.get("when"), dict):
            raise CommitError("rule when must be an object")
        if raw.get("verdict") not in {"warn", "block"}:
            raise CommitError("rule verdict must be warn or block")
        if not _parse_rule_evidence(raw.get("evidence", [])):
            raise CommitError("new or revised rules require evidence")

        generated_id = _stable_id("rule", proposal_id, key)
        if generated_id in generated_ids or generated_id in active:
            raise CommitError(f"generated duplicate rule id: {generated_id}")
        generated_ids.add(generated_id)
        if old_rule_id is None:
            active.append(generated_id)
        else:
            active[active.index(old_rule_id)] = generated_id


def _reject_unknown_rule_fields(raw: Mapping[str, JsonValue], allowed: set[str]) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise CommitError(f"rule diff has unknown fields: {', '.join(unknown)}")


def _stable_id(kind: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join((kind, *parts)).encode()).hexdigest()[:24]
    return f"{kind}_{digest}"


def _proposal_revision_ids(proposal: ReflectionProposal) -> list[str]:
    revision_ids: list[str] = []
    for index, operation in enumerate(proposal.memory_ops):
        if isinstance(operation, RetireMemoryOperation):
            continue
        key = operation.key if isinstance(operation, AddMemoryOperation) else f"op_{index}"
        revision_ids.append(_stable_id("memory_revision", proposal.proposal_id, key))
    return revision_ids


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _required_string(raw: Mapping[str, JsonValue], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CommitError(f"rule {field} must be a non-empty string")
    return value.strip()


def _parse_rule_evidence(value: JsonValue) -> list[EvidenceRef]:
    if not isinstance(value, list):
        raise CommitError("rule evidence must be a list")
    try:
        return [EvidenceRef.model_validate(item) for item in value]
    except ValidationError as exc:
        raise CommitError(f"invalid rule evidence: {exc}") from exc
