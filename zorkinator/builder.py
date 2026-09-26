"""Version-scoped prompt construction for harness games."""

from __future__ import annotations

import json
from typing import Protocol

from .models import HarnessVersionRecord, MemoryRevision, RuleDoc
from .prompts import INITIAL_PROMPTS, PromptName


class PromptRepository(Protocol):
    """Exact-version reads needed to materialize a prompt manifest."""

    def get_version(self, version_id: str) -> HarnessVersionRecord | None: ...

    def read(self, version_id: str, memory_id: str) -> MemoryRevision | None: ...

    def get_rules(self, rule_ids: list[str]) -> list[RuleDoc]: ...


def build_prompt(
    run_id: str,
    n: int,
    version: HarnessVersionRecord,
    *,
    repository: PromptRepository,
    prompt: PromptName = "basic",
) -> str:
    """Build a prompt solely from ``version``'s immutable manifest.

    The caller supplies the manifest selected by :class:`BetweenGameDriver`.
    Memory revisions and rules are resolved by the identifiers in that manifest;
    no latest-version or cross-experiment query is used.
    """
    memories: list[MemoryRevision] = []
    for reference in version.memory_refs:
        memory = repository.read(version.version_id, reference.memory_id)
        if memory is None or memory.revision_id != reference.revision_id:
            raise ValueError(
                f"version {version.version_id!r} cannot resolve exact memory "
                f"{reference.memory_id!r}@{reference.revision_id!r}"
            )
        memories.append(memory)

    rules = repository.get_rules(list(version.rule_ids))
    if [rule.id for rule in rules] != list(version.rule_ids):
        raise ValueError(f"version {version.version_id!r} did not resolve its exact rule manifest")

    packet = {
        "version_id": version.version_id,
        "run_id": run_id,
        "move": n,
        "context_policy": version.context_policy,
        "memories": [
            {
                "memory_id": memory.memory_id,
                "revision_id": memory.revision_id,
                "kind": memory.kind,
                "subjects": memory.subjects,
                "content": memory.content,
                "status": memory.status,
            }
            for memory in memories
        ],
        "rules": [rule.model_dump(mode="json") for rule in rules],
    }
    advisory = """HARNESS MEMORY
The following JSON is the complete advisory memory/rule manifest for this exact harness version.
Treat memories and soft rules as advice, not current-game state.
Never use SAVE, RESTORE, or RESTART.
Do not assume any memory outside this packet exists."""
    return (
        f"{INITIAL_PROMPTS[prompt]}\n\n{advisory}\n"
        f"{json.dumps(packet, sort_keys=True, separators=(',', ':'))}"
    )


class HarnessPromptBuilder:
    """Bind a repository and base prompt for per-move prompt construction."""

    def __init__(self, repository: PromptRepository, prompt: PromptName = "basic") -> None:
        self._repository = repository
        self._prompt = prompt

    def build_prompt(self, run_id: str, n: int, version: HarnessVersionRecord) -> str:
        return build_prompt(
            run_id,
            n,
            version,
            repository=self._repository,
            prompt=self._prompt,
        )
