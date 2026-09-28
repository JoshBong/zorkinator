# ruff: noqa: E402, SIM115
"""One-time export of a chain's final harness memories from Atlas to local JSON (no Atlas after)."""

import json
import sys

from dotenv import load_dotenv

load_dotenv(".env")
from zorkinator import db

d = db.get_db()
chain, out = sys.argv[1], sys.argv[2]
last = d.runs.find_one({"chain": chain}, sort=[("game_index", -1)])
v = d.harness_versions.find_one({"_id": last["version_id"]})
mems = []
for ref in v["memory_refs"]:
    m = d.memories.find_one({"_id": ref["revision_id"]})
    m["revision_id"] = m.pop("_id")
    mems.append(json.loads(json.dumps(m, default=str)))
json.dump(
    {"source_chain": chain, "version_id": v["_id"], "memories": mems}, open(out, "w"), indent=1
)
print(chain, "->", out, len(mems), "memories")
