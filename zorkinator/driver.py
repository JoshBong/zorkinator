"""Between-game orchestration for sequential harness learning chains."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue

from . import db
from .models import HarnessVersionRecord, MemoryEvent, ReflectionProposal, RunRecord
from .reflector import (
    ReflectionError,
    ReflectionModel,
    ReflectionRepository,
    Reflector,
    ReflectorLimits,
)
from .runner import PRICES
from .versions import CommitError


class DriverError(RuntimeError):
    """Persisted chain state is incomplete or internally inconsistent."""


class DriverStore(Protocol):
    def get_version(self, version_id: str) -> HarnessVersionRecord | None: ...

    def publish_version(self, version: HarnessVersionRecord) -> None: ...

    def get_memory_event(self, event_id: str) -> MemoryEvent | None: ...


class RunRepository(Protocol):
    def get_run(self, run_id: str) -> RunRecord | None: ...

    def get_chain_runs(self, chain: str) -> Sequence[RunRecord]: ...


class ProposalCreator(Protocol):
    def create(self, run_id: str, proposal_id: str) -> ReflectionProposal: ...


class VersionCommitter(Protocol):
    def commit(self, parent_id: str, proposal: ReflectionProposal, run_id: str) -> str: ...


PlayGame = Callable[[str, int], RunRecord]


class MongoRunRepository:
    """Run lookup adapter for the existing Atlas-backed DB helpers."""

    def get_run(self, run_id: str) -> RunRecord | None:
        return db.get_run(run_id)

    def get_chain_runs(self, chain: str) -> Sequence[RunRecord]:
        return [run for run in db.get_runs("harness") if run.chain == chain]


class ReflectorProposalCreator:
    """Bind the production Reflector to the driver's stable proposal ID."""

    def __init__(
        self,
        repository: ReflectionRepository,
        model: ReflectionModel,
        *,
        limits: ReflectorLimits | None = None,
    ) -> None:
        self._repository = repository
        self._model = model
        self._limits = limits

    def create(self, run_id: str, proposal_id: str) -> ReflectionProposal:
        reflector = Reflector(
            self._repository,
            self._model,
            limits=self._limits,
            id_factory=lambda: proposal_id,
        )
        return reflector.propose(run_id)


@dataclass(frozen=True)
class ReflectionBudget:
    """Persisted per-chain budget checked before every new reflection call."""

    max_calls: int = 10
    max_usd: float = 5.0

    def __post_init__(self) -> None:
        if self.max_calls < 0 or self.max_usd < 0:
            raise ValueError("reflection budget limits must be non-negative")


BoundaryOutcome = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class ChainCursor:
    version_id: str
    game_index: int


class BetweenGameDriver:
    """Advance one isolated chain through reflect -> commit -> next game.

    Proposal IDs are deterministic for a source run. A restart can therefore
    recover the persisted ``proposed`` event without making another model call,
    while ``VersionManager.commit`` supplies idempotent publication recovery.
    """

    def __init__(
        self,
        chain: str,
        store: DriverStore,
        runs: RunRepository,
        proposals: ProposalCreator,
        versions: VersionCommitter,
        *,
        budget: ReflectionBudget | None = None,
        context_policy: dict[str, JsonValue] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not chain:
            raise ValueError("chain must be non-empty")
        self.chain = chain
        self._store = store
        self._runs = runs
        self._proposals = proposals
        self._versions = versions
        self._budget = budget or ReflectionBudget()
        self._context_policy = dict(context_policy or {})
        self._clock = clock or (lambda: datetime.now(UTC))
        digest = hashlib.sha256(chain.encode("utf-8")).hexdigest()[:20]
        self.experiment_id = f"experiment_{digest}"
        self.root_version_id = f"version_root_{digest}"

    def ensure_cold_root(self) -> str:
        existing = self._store.get_version(self.root_version_id)
        if existing is not None:
            self._validate_root(existing)
            return existing.version_id

        root = HarnessVersionRecord(
            version_id=self.root_version_id,
            experiment_id=self.experiment_id,
            parent_id=None,
            source_run_id=None,
            proposal_id=None,
            memory_refs=[],
            rule_ids=[],
            context_policy=self._context_policy,
            scores=[],
            created_at=self._clock(),
        )
        try:
            self._store.publish_version(root)
        except Exception:
            # Publication may have succeeded before its acknowledgement was lost,
            # or another process may have created this chain's root concurrently.
            recovered = self._store.get_version(self.root_version_id)
            if recovered is None:
                raise
            self._validate_root(recovered)
        return self.root_version_id

    def recover(self) -> ChainCursor:
        """Replay completed chain boundaries and return the next game cursor."""
        version_id = self.ensure_cold_root()
        completed = sorted(self._runs.get_chain_runs(self.chain), key=_game_index)
        seen_indexes: set[int] = set()
        for expected_index, run in enumerate(completed):
            index = _game_index(run)
            if index in seen_indexes or index != expected_index:
                raise DriverError(
                    "completed chain games must have unique contiguous indexes from 0"
                )
            seen_indexes.add(index)
            if run.version_id != version_id:
                raise DriverError(
                    f"chain game {index} used {run.version_id!r}, expected {version_id!r}"
                )
            version_id = self.advance(run.run_id)
        return ChainCursor(version_id=version_id, game_index=len(completed))

    def advance(self, run_id: str) -> str:
        """Reflect a completed run once, commit it, and return the next version."""
        run = self._require_chain_run(run_id)
        parent_id = run.version_id
        if parent_id is None:
            raise DriverError("completed harness run has no parent version")
        parent = self._store.get_version(parent_id)
        if parent is None or parent.experiment_id != self.experiment_id:
            raise DriverError("source run parent is outside this chain's experiment")

        proposal_id = _proposal_id(self.experiment_id, run.run_id, parent_id)
        event = self._store.get_memory_event(f"{proposal_id}:proposed")
        if event is None:
            calls, spent = self._reflection_spend()
            if calls >= self._budget.max_calls or spent >= self._budget.max_usd:
                return parent_id
            proposal = self._proposals.create(run.run_id, proposal_id)
            if proposal.proposal_id != proposal_id:
                raise DriverError("proposal creator did not use the assigned stable proposal id")
        else:
            proposal = self._proposal_from_event(event, run, parent_id, proposal_id)

        child_id = self._versions.commit(parent_id, proposal, run.run_id)
        child = self._store.get_version(child_id)
        if (
            child is None
            or child.experiment_id != self.experiment_id
            or child.parent_id != parent_id
            or child.source_run_id != run.run_id
            or child.proposal_id != proposal_id
        ):
            raise DriverError("version commit did not publish the expected child")
        return child_id

    def run(
        self, games: int, play_game: PlayGame, on_outcome: BoundaryOutcome | None = None
    ) -> ChainCursor:
        """Resume the chain and pass every committed child to the next game.

        Expected learning failures (a malformed reflection, a rejected proposal) never stop the
        chain: the outcome is reported through ``on_outcome`` and the next game keeps the parent
        version. Transient persistence errors still propagate so a rerun can retry them.
        """
        if games < 0:
            raise ValueError("games must be non-negative")
        cursor = self.recover()
        version_id = cursor.version_id
        game_index = cursor.game_index
        while game_index < games:
            played = play_game(version_id, game_index)
            persisted = self._runs.get_run(played.run_id)
            if persisted is None or _canonical_persisted_run(persisted) != _canonical_persisted_run(
                played
            ):
                raise DriverError("play_game must persist the completed run before returning")
            if (
                played.mode != "harness"
                or played.chain != self.chain
                or played.game_index != game_index
                or played.version_id != version_id
            ):
                raise DriverError("play_game returned a run outside the requested chain cursor")
            parent_id = version_id
            outcome: dict[str, object] = {
                "chain": self.chain,
                "experiment_id": self.experiment_id,
                "game_index": game_index,
                "run_id": played.run_id,
                "parent_id": parent_id,
                "score": played.score,
                "end_reason": played.end_reason,
                "created_at": datetime.now(UTC),
            }
            try:
                version_id = self.advance(played.run_id)
            except (ReflectionError, CommitError) as exc:
                version_id = parent_id
                outcome.update(status="learning_failed", reason=str(exc)[:500])
            else:
                if version_id == parent_id:
                    outcome.update(status="budget_exhausted")
                else:
                    outcome.update(status="committed", child_id=version_id)
            print(
                f"[{self.chain} game {game_index + 1}] score={played.score} "
                f"end={played.end_reason} learning={outcome['status']}"
                + (f" ({outcome['reason']})" if "reason" in outcome else ""),
                flush=True,
            )
            if on_outcome is not None:
                on_outcome(outcome)
            game_index += 1
        return ChainCursor(version_id=version_id, game_index=game_index)

    def _reflection_spend(self) -> tuple[int, float]:
        calls = 0
        spent = 0.0
        for run in self._runs.get_chain_runs(self.chain):
            if run.version_id is None:
                continue
            proposal_id = _proposal_id(self.experiment_id, run.run_id, run.version_id)
            event = self._store.get_memory_event(f"{proposal_id}:proposed")
            if event is not None:
                calls += 1
                spent += _event_cost_usd(event)
        return calls, spent

    def _require_chain_run(self, run_id: str) -> RunRecord:
        run = self._runs.get_run(run_id)
        if run is None:
            raise DriverError(f"unknown completed run: {run_id}")
        if run.mode != "harness" or run.chain != self.chain or run.game_index is None:
            raise DriverError("source run is not a completed game in this harness chain")
        return run

    def _proposal_from_event(
        self, event: MemoryEvent, run: RunRecord, parent_id: str, proposal_id: str
    ) -> ReflectionProposal:
        if (
            event.experiment_id != self.experiment_id
            or event.source_run_id != run.run_id
            or event.parent_id != parent_id
            or event.proposal_id != proposal_id
            or event.phase != "proposed"
            or event.proposal is None
        ):
            raise DriverError("persisted proposal event does not match this chain boundary")
        return event.proposal

    def _validate_root(self, root: HarnessVersionRecord) -> None:
        if (
            root.experiment_id != self.experiment_id
            or root.parent_id is not None
            or root.source_run_id is not None
            or root.proposal_id is not None
            or root.memory_refs
            or root.rule_ids
            or root.context_policy != self._context_policy
        ):
            raise DriverError("persisted chain root is not the expected cold root")


def _proposal_id(experiment_id: str, run_id: str, parent_id: str) -> str:
    raw = f"{experiment_id}\0{run_id}\0{parent_id}".encode()
    return f"proposal_{hashlib.sha256(raw).hexdigest()[:24]}"


def _game_index(run: RunRecord) -> int:
    if run.game_index is None:
        raise DriverError("completed chain run is missing game_index")
    return run.game_index


def _event_cost_usd(event: MemoryEvent) -> float:
    explicit = event.usage.get("cost_usd")
    if isinstance(explicit, int | float) and not isinstance(explicit, bool) and explicit >= 0:
        return float(explicit)

    price = PRICES.get(event.model)
    token_keys = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    if price is None or not any(key in event.usage for key in token_keys):
        raise DriverError("persisted reflection usage is insufficient to enforce the USD budget")
    values: dict[str, int] = {}
    for key in token_keys:
        value = event.usage.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DriverError(f"persisted reflection usage has invalid {key}")
        values[key] = value
    price_in, price_out = price
    return (
        values["input_tokens"] * price_in
        + values["cache_creation_input_tokens"] * price_in * 1.25
        + values["cache_read_input_tokens"] * price_in * 0.1
        + values["output_tokens"] * price_out
    ) / 1_000_000


def _canonical_persisted_run(run: RunRecord) -> dict[str, object]:
    """Match MongoDB's BSON datetime normalization for persistence verification."""
    data: dict[str, object] = run.model_dump()
    for field in ("started_at", "ended_at"):
        value = data[field]
        if not isinstance(value, datetime):
            raise DriverError(f"persisted run has invalid {field}")
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        data[field] = value.replace(microsecond=(value.microsecond // 1000) * 1000)
    return data
