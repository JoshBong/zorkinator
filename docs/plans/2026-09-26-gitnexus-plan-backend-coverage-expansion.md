# GitNexus Engineering Plan

> Task: Raise backend/library test coverage to 100%, excluding CLI entry points and the separate frontend.
> Evidence initially verified at commit 4c9660d4acb39e71e59183cb9ee89ca948739d3f; rebased onto remote main commit 1a9e32b and refreshed before implementation.
> Evidence provenance schema 2; global dirty digest sha256:0a9c85780067d9afcd0764f307b60891e3cee927ee11eaeb5ec7826d10fd82cd; cited-path manifest has no emitted paths because this planning pass makes no line-level source claims; exact generated plan path excluded.

## Objective (§1)

Reach 100% line and branch coverage for the non-CLI Python backend. Exclude only command entry modules and the separate TypeScript frontend; do not hide reusable backend logic with coverage pragmas.

## Current Behaviour (§2–3)

[verified] The current full suite passes 113 tests at 74% repository-wide branch coverage. The low-coverage backend seams are model transports and game loops (`runner.py`, `harness.py`), persistence (`db.py`), chain/version failure paths (`driver.py`, `versions.py`), and read-model mapping (`view/mapping.py`). [graph] GitNexus reports the staged coverage work affects no runtime processes; `MongoOuterLoopStore` is also consumed by the view mapping and driver.

## Findings (§4–5)

[verified] `runner.play` and `harness.play_game` have explicit cap, blocked-command, give-up, death, win, version, rule-retry, and cost-budget branches. [verified] Mongo store methods centralize persistence and need deterministic mock-client tests for connection/index/transaction/vector failures. [verified] `BetweenGameDriver.run` distinguishes expected learning failures from persistence failures. [verified] version publication validates associations, evidence, memory budgets, and rule promotion before atomic publication.

## Proposed Changes (§6)

- Add `coverage` to development dependencies and a coverage configuration that measures Python backend/library modules while omitting `zorkinator/cli.py`, `zorkinator/__main__.py`, `zorkinator/analyze.py`, `zorkinator/import_runs.py`, and `zorkinator/test_connection.py`.
- Extend existing tests with deterministic fakes/mocks for every runner, harness, driver, version, database, memory, reflector, transport, and view-mapping branch reported as missing.
- Add targeted tests rather than production changes or broad coverage exclusions. Use mock Mongo and mock model clients; preserve Atlas smoke tests as integration coverage.

## Implementation Sequence (§7)

1. Establish the committed coverage command and exact non-CLI scope; regenerate a baseline report.
2. Cover pure helpers and transport branches (`adapter`, `memory`, `openai_chat`, `progress`, `runner.AnthropicChat`, builder, reflector).
3. Cover runner and harness terminal branches with scripted chats and mocked adapters: invalid commands, budgets, give-up, game-over/death/win, warnings/rejections, exact-version validation, and trace/progress callbacks.
4. Cover Mongo store constructor, environment, index/vector, immutable collision, transaction, recall, and aggregation error paths with mocks.
5. Cover driver and version guards, retry/recovery, malformed persisted data, budget accounting, association/evidence/rule/memory limits, and publish failures.
6. Cover backend view mapping/API read paths with mocked database functions; do not change frontend code.
7. Iterate on `coverage report -m` until included backend line and branch coverage are 100%, then run full lint/type/test/coverage verification.

## Test Strategy (§8)

- Existing tests: `tests/test_runner.py`, `tests/test_harness.py`, `tests/test_db.py`, `tests/test_driver.py`, `tests/test_versions.py`, `tests/test_reflector.py`, `tests/test_view.py`.
- New focused test modules only where existing fixtures become unwieldy; each failure-path test must assert both the outcome and the absence of unintended side effects.
- Verification: `.venv/bin/python -m coverage run --branch --source=zorkinator -m unittest discover`; `.venv/bin/python -m coverage report -m`; `ruff check .`; `ruff format --check .`; `mypy`.

## Implementation Context (§11)

```yaml
implementation_context:
  task_summary: "Achieve 100% backend/library branch coverage without testing frontend or CLI entry modules."
  acceptance_criteria:
    - "The configured coverage report is 100% for every included module."
    - "The full test suite, Ruff, formatting, and strict mypy pass."
    - "No production behavior is altered merely to raise coverage."
  evidence_provenance:
    schema_version: 2
    head_commit: "1a9e32b"
    generated_plan_path: "docs/plans/2026-09-26-gitnexus-plan-backend-coverage-expansion.md"
    global_dirty_digest:
      algorithm: "sha256"
      canonicalization: "gitnexus-evidence-provenance-v2 NUL-framed UTF-8 records"
      value: "0a9c85780067d9afcd0764f307b60891e3cee927ee11eaeb5ec7826d10fd82cd"
    cited_path_manifest: []
  files_to_modify:
    - {file: "requirements-dev.txt", symbols: [], intended_change: "Add the coverage reporter."}
    - {file: "pyproject.toml", symbols: [], intended_change: "Configure included backend coverage and explicit CLI omissions."}
    - {file: "tests/", symbols: [], intended_change: "Add deterministic branch and error-path coverage."}
  tests:
    - {file: "tests/test_runner.py", scenarios: ["mock transport and game terminal/error branches"]}
    - {file: "tests/test_harness.py", scenarios: ["mock version, rule, and terminal branches"]}
    - {file: "tests/test_db.py", scenarios: ["mock Mongo constructor/index/immutable failures"]}
    - {file: "tests/test_driver.py", scenarios: ["recover/run failures and budget edge cases"]}
    - {file: "tests/test_versions.py", scenarios: ["validation and publication error branches"]}
    - {file: "tests/test_view.py", scenarios: ["mapping and read API branches"]}
  verification_commands:
    - ".venv/bin/python -m coverage run --branch --source=zorkinator -m unittest discover"
    - ".venv/bin/python -m coverage report -m"
    - ".venv/bin/ruff check ."
    - ".venv/bin/ruff format --check ."
    - ".venv/bin/mypy"
  pdg_constraints: []
  assumptions:
    - "Coverage configuration can omit exactly the requested CLI entry modules while retaining reusable backend code."
  open_questions: []
  avoid:
    - "Do not add broad source omissions or coverage pragmas to executable backend logic."
    - "Do not test or change frontend files."
    - "Do not change runtime behavior solely for coverage."
```

## Assumptions and Open Questions (§12)

The backend FastAPI read model remains in scope because it is Python service code, not the separate frontend. If this is not desired, omit `zorkinator/view/app.py` and `zorkinator/view/mapping.py` explicitly before execution.

## Definition of Done (§13)

The committed report shows 100% line and branch coverage for every included module, the exclusions are limited to the agreed CLI entry points, and all verification commands pass.
