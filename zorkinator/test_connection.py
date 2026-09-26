"""Standalone Atlas connectivity check.

Run this after setting MONGODB_URI in your .env to confirm your own connection
works before wiring up builder/player/scribe/reflector/verifier against db.py:

    source .venv/bin/activate
    python -m zorkinator.test_connection
"""

from __future__ import annotations

import sys

from . import db


def main() -> int:
    try:
        client = db.get_client()
        client.admin.command("ping")
    except Exception as exc:
        print(f"Could not reach Atlas: {exc}")
        print("Check MONGODB_URI in your .env (see README.md 'Setup').")
        return 1

    print(f"Connected. Server version: {client.server_info()['version']}")

    collections = (
        "runs",
        "moves",
        "world_facts",
        "rules",
        "memories",
        "memory_events",
        "harness_versions",
    )

    db.ensure_indexes()
    db.MongoOuterLoopStore(db.get_db()).ensure_indexes()
    print(f"Indexes ready on database {db.DB_NAME!r}:")
    for name in collections:
        indexes = sorted(db.get_db()[name].index_information().keys())
        print(f"  {name}: {indexes}")

    print("\nCollection counts (documents already logged by the team):")
    for name in collections:
        count = db.get_db()[name].count_documents({})
        print(f"  {name}: {count}")

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
