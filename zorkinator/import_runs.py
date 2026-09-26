"""Load locally logged games (runs/*.jsonl) into Atlas so the dashboard can show them.

    python -m zorkinator.import_runs --chain long      # every chain whose name starts with "long"
    python -m zorkinator.import_runs --chain chats     # the 10 x 10 baseline

Runs already in Atlas are skipped, so it is safe to rerun.
"""

from __future__ import annotations

import argparse

from dotenv import load_dotenv

from . import db
from .analyze import load


def import_chain(prefix: str, out: str = "runs") -> tuple[int, int]:
    runs, moves = load(out)
    sink = db.MongoSink()
    existing = {doc["_id"] for doc in db.get_db().runs.find({}, {"_id": 1})}
    imported = skipped = 0
    for run in runs:
        if not (run.chain or "").startswith(prefix):
            continue
        if run.run_id in existing:
            skipped += 1
            continue
        for move in moves[run.run_id]:
            sink.move(move)
        sink.run(run)
        imported += 1
    return imported, skipped


def main() -> int:
    parser = argparse.ArgumentParser(prog="zorkinator.import_runs")
    parser.add_argument("--chain", required=True, help="chain name prefix to import")
    parser.add_argument("--out", default="runs")
    args = parser.parse_args()
    load_dotenv()
    imported, skipped = import_chain(args.chain, args.out)
    print(f"imported {imported} games, skipped {skipped} already in Atlas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
