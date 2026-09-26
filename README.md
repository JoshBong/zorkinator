# zorkinator

A self-improving harness for Zork I. It plays through [Jericho](https://github.com/microsoft/jericho),
learns rules from its own deaths, and promotes a rule to a hard guardrail only when the evidence backs it.
Everything (runs, moves, world facts, rules, harness versions) lives in MongoDB Atlas.

Baseline: frontier LLMs in a bare loop score under 10% of Zork I, best ~75/350
([arXiv 2602.15867](https://arxiv.org/abs/2602.15867)). Same model, same prompt, plus this harness.

Built at the MongoDB x Cerebral Valley Harness Engineering hackathon, 2026-09-26.

## Setup

On macOS or Linux, the setup script installs a project-local Python 3.11 runtime with `uv`, all runtime
and development dependencies, the spaCy model, the verified Zork I story file, and the Git hooks:

```bash
./setup.sh
```

Manual setup, if preferred:

```bash
brew install python@3.11            # Jericho does not build on 3.14
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm

mkdir -p games
curl -L -o games/zork1.z5 https://github.com/BYU-PCCL/z-machine-games/raw/master/jericho-game-suite/zork1.z5
md5 games/zork1.z5                  # b732a93a6244ddd92a9b9a3e3a46c687

cp .env.example .env                # then fill in keys
```

Check Jericho works:

```bash
python -c "from jericho import FrotzEnv; print(FrotzEnv('games/zork1.z5').reset()[0][:120])"
```

Play manually in the terminal:

```bash
source .venv/bin/activate
python -m zorkinator manual --seed 0
```

Type `quit` or press Ctrl-C to leave. The benchmark-forbidden commands `SAVE`, `RESTORE`, and `RESTART`
are blocked by the adapter.

## Code quality

Run the same checks used by CI:

```bash
ruff check .
ruff format --check .
mypy
python -m unittest discover -v
```

Pre-commit runs Ruff (including formatting) and strict mypy with the Pydantic plugin. GitHub Actions runs
the checks again on every push and pull request. Configure the repository's branch protection to require
the `lint-type-test` check if merges must be blocked when these checks fail.

Jericho does not ship type information, so its supported API surface is defined in
`typings/jericho/__init__.pyi`. Update that stub in the same change whenever new Jericho APIs are used;
strict mypy deliberately does not ignore missing imports globally.

## Docs

Coding agents: start at [`AGENTS.md`](AGENTS.md).

- [`docs/CONTRACTS.md`](docs/CONTRACTS.md): Mongo shapes and function signatures (authority)
- [`docs/DECISIONS.md`](docs/DECISIONS.md): what's decided and what's still proposed
- [`docs/ARCHITECTURE_MAP.md`](docs/ARCHITECTURE_MAP.md): components, data flow, rule format, team split
- [`docs/INTEGRATION_HANDOFF.md`](docs/INTEGRATION_HANDOFF.md): outer-loop next work and inner-loop integration boundary
- [`docs/DESIGN.md`](docs/DESIGN.md): claim, ground rules, results to show
- [`docs/PRIOR_ART.md`](docs/PRIOR_ART.md): ZorkGPT and jev-zork, and what we learned from them
