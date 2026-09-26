# zorkinator

A self-improving harness for Zork I. It plays through [Jericho](https://github.com/microsoft/jericho),
learns rules from its own deaths, and promotes a rule to a hard guardrail only when the evidence backs it.
Everything (runs, moves, world facts, rules, harness versions) lives in MongoDB Atlas.

Baseline: frontier LLMs in a bare loop score under 10% of Zork I, best ~75/350
([arXiv 2602.15867](https://arxiv.org/abs/2602.15867)). Same model, same prompt, plus this harness.

Built at the MongoDB x Cerebral Valley Harness Engineering hackathon, 2026-09-26.

## Setup

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

## Docs

Coding agents: start at [`AGENTS.md`](AGENTS.md).

- [`docs/CONTRACTS.md`](docs/CONTRACTS.md): Mongo shapes and function signatures (authority)
- [`docs/DECISIONS.md`](docs/DECISIONS.md): what's decided and what's still proposed
- [`docs/ARCHITECTURE_MAP.md`](docs/ARCHITECTURE_MAP.md): components, data flow, rule format, team split
- [`docs/DESIGN.md`](docs/DESIGN.md): claim, ground rules, results to show
- [`docs/PRIOR_ART.md`](docs/PRIOR_ART.md): ZorkGPT and jev-zork, and what we learned from them
