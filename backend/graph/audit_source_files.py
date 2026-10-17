
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB
from graph_connection import connection_options

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "data" / "index" / "codebase.json"

load_dotenv(ROOT / ".env")

db = FalkorDB(
    host=os.getenv("FALKORDB_HOST", ""),
    port=int(os.getenv("FALKORDB_PORT", "6379")),
    **connection_options(),
    password=os.getenv("FALKORDB_PASSWORD"),
)
graph = db.select_graph(os.getenv("FALKORDB_GRAPH", "techcompass"))

data = json.loads(INDEX.read_text(encoding="utf-8"))
indexed = {f["id"]: f["path"] for f in data["files"]}

result = graph.query(
    "MATCH (f:SourceFile) RETURN f.id, f.path"
)
stored = {
    row[0]: row[1]
    for row in result.result_set
}

only_in_index = set(indexed) - set(stored)
only_in_graph = set(stored) - set(indexed)

print("Files in current JSON index:", len(indexed))
print("Files in FalkorDB:", len(stored))
print("Only in JSON index:", len(only_in_index))
print("Only in FalkorDB:", len(only_in_graph))

print("\n--- Files only in FalkorDB (first 30) ---")
for file_id in sorted(only_in_graph)[:30]:
    print(stored[file_id])

print("\n--- Files only in JSON index (first 30) ---")
for file_id in sorted(only_in_index)[:30]:
    print(indexed[file_id])
