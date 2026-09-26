from __future__ import annotations

import unittest
from collections.abc import Sequence
from datetime import UTC, datetime

from zorkinator.driver import BetweenGameDriver, DriverError, ReflectionBudget
from zorkinator.models import (
    HarnessVersionRecord,
    MemoryEvent,
    ReflectionProposal,
    RunRecord,
)

NOW = datetime(2026, 9, 26, 17, 0, tzinfo=UTC)


class FakeState:
    def __init__(self) -> None:
        self.versions: dict[str, HarnessVersionRecord] = {}
        self.events: dict[str, MemoryEvent] = {}
        self.runs: dict[str, RunRecord] = {}

    def get_version(self, version_id: str) -> HarnessVersionRecord | None:
        return self.versions.get(version_id)

    def publish_version(self, version: HarnessVersionRecord) -> None:
        existing = self.versions.get(version.version_id)
        if existing is not None and existing != version:
            raise ValueError("immutable version mismatch")
        self.versions[version.version_id] = version

    def get_memory_event(self, event_id: str) -> MemoryEvent | None:
        return self.events.get(event_id)

    def get_run(self, run_id: str) -> RunRecord | None:
        return self.runs.get(run_id)

    def get_chain_runs(self, chain: str) -> Sequence[RunRecord]:
        return [run for run in self.runs.values() if run.chain == chain]


class FakeProposals:
    def __init__(self, state: FakeState, *, cost_usd: float = 0.25) -> None:
        self.state = state
        self.cost_usd = cost_usd
        self.calls: list[tuple[str, str]] = []

    def create(self, run_id: str, proposal_id: str) -> ReflectionProposal:
        self.calls.append((run_id, proposal_id))
        run = self.state.runs[run_id]
        assert run.version_id is not None
        proposal = ReflectionProposal(
            proposal_id=proposal_id,
            run_id=run_id,
            parent_id=run.version_id,
            memory_ops=[],
            rule_diffs=[],
            summary="No change.",
        )
        parent = self.state.versions[run.version_id]
        event = MemoryEvent(
            event_id=f"{proposal_id}:proposed",
            experiment_id=parent.experiment_id,
            proposal_id=proposal_id,
            phase="proposed",
            source_run_id=run_id,
            parent_id=run.version_id,
            child_version_id=None,
            operations=[],
            memory_revision_ids=[],
            reason=None,
            model=run.model,
            usage={"cost_usd": self.cost_usd},
            created_at=NOW,
            proposal=proposal,
        )
        self.state.events[event.event_id] = event
        return proposal


class FakeVersions:
    def __init__(self, state: FakeState) -> None:
        self.state = state
        self.fail_once = False

    def commit(self, parent_id: str, proposal: ReflectionProposal, run_id: str) -> str:
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("publication interrupted")
        child_id = f"child_{proposal.proposal_id}"
        parent = self.state.versions[parent_id]
        self.state.publish_version(
            parent.model_copy(
                update={
                    "version_id": child_id,
                    "parent_id": parent_id,
                    "source_run_id": run_id,
                    "proposal_id": proposal.proposal_id,
                    "created_at": NOW,
                }
            )
        )
        return child_id


def completed_run(run_id: str, chain: str, game_index: int, version_id: str) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        version_id=version_id,
        mode="harness",
        prompt="basic",
        model="claude-haiku-4-5",
        seed=0,
        move_cap=20,
        score=0,
        moves=1,
        died=True,
        death_move=1,
        end_reason="death",
        tokens_in=1,
        tokens_out=1,
        tokens_cache_write=0,
        tokens_cache_read=0,
        cost_usd=0.01,
        started_at=NOW,
        ended_at=NOW,
        chain=chain,
        game_index=game_index,
    )


class BetweenGameDriverTests(unittest.TestCase):
    def make_driver(
        self,
        chain: str = "chain-a",
        *,
        budget: ReflectionBudget | None = None,
    ) -> tuple[BetweenGameDriver, FakeState, FakeProposals, FakeVersions]:
        state = FakeState()
        proposals = FakeProposals(state)
        versions = FakeVersions(state)
        driver = BetweenGameDriver(
            chain,
            state,
            state,
            proposals,
            versions,
            budget=budget,
            clock=lambda: NOW,
        )
        return driver, state, proposals, versions

    def test_cold_root_is_isolated_and_child_is_passed_to_next_game(self) -> None:
        driver, state, proposals, _ = self.make_driver()
        seen: list[tuple[str, int]] = []

        def play(version_id: str, game_index: int) -> RunRecord:
            seen.append((version_id, game_index))
            run = completed_run(f"run-{game_index}", driver.chain, game_index, version_id)
            state.runs[run.run_id] = run
            return run

        cursor = driver.run(2, play)

        root_id = driver.root_version_id
        self.assertEqual(seen[0], (root_id, 0))
        self.assertEqual(seen[1], (f"child_{proposals.calls[0][1]}", 1))
        self.assertEqual(cursor.game_index, 2)
        root = state.versions[root_id]
        self.assertEqual((root.memory_refs, root.rule_ids), ([], []))

        other, _, _, _ = self.make_driver("chain-b")
        self.assertNotEqual(other.root_version_id, root_id)
        self.assertNotEqual(other.experiment_id, driver.experiment_id)

    def test_restart_reuses_persisted_proposal_after_failed_commit(self) -> None:
        driver, state, proposals, versions = self.make_driver()
        root_id = driver.ensure_cold_root()
        run = completed_run("run-0", driver.chain, 0, root_id)
        state.runs[run.run_id] = run
        versions.fail_once = True

        with self.assertRaisesRegex(RuntimeError, "publication interrupted"):
            driver.advance(run.run_id)
        self.assertEqual(len(proposals.calls), 1)

        recovered = driver.recover()

        self.assertEqual(len(proposals.calls), 1)
        self.assertEqual(recovered.version_id, f"child_{proposals.calls[0][1]}")
        self.assertEqual(recovered.game_index, 1)

    def test_restart_after_commit_does_not_reflect_again(self) -> None:
        driver, state, proposals, _ = self.make_driver()
        root_id = driver.ensure_cold_root()
        run = completed_run("run-0", driver.chain, 0, root_id)
        state.runs[run.run_id] = run
        child_id = driver.advance(run.run_id)

        cursor = driver.recover()

        self.assertEqual(cursor.version_id, child_id)
        self.assertEqual(len(proposals.calls), 1)

    def test_persisted_budget_stops_new_reflection_but_chain_continues(self) -> None:
        driver, state, proposals, _ = self.make_driver(
            budget=ReflectionBudget(max_calls=1, max_usd=0.25)
        )
        root_id = driver.ensure_cold_root()
        first = completed_run("run-0", driver.chain, 0, root_id)
        state.runs[first.run_id] = first
        child_id = driver.advance(first.run_id)
        second = completed_run("run-1", driver.chain, 1, child_id)
        state.runs[second.run_id] = second

        next_id = driver.advance(second.run_id)

        self.assertEqual(next_id, child_id)
        self.assertEqual(len(proposals.calls), 1)

    def test_recovery_rejects_cross_chain_version_history(self) -> None:
        driver, state, _, _ = self.make_driver()
        root_id = driver.ensure_cold_root()
        state.runs["run-0"] = completed_run("run-0", driver.chain, 0, root_id)
        state.runs["run-1"] = completed_run("run-1", driver.chain, 1, "foreign")

        with self.assertRaisesRegex(DriverError, "used 'foreign'"):
            driver.recover()


if __name__ == "__main__":
    unittest.main()
