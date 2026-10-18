
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB
from backend.graph.graph_connection import connection_options

ROOT = Path(__file__).resolve().parents[2]
_index_path = Path(os.getenv(
    "TECHCOMPASS_CODEBASE_INDEX",
    "data/index/codebase.json",
))
DATA = _index_path if _index_path.is_absolute() else ROOT / _index_path
BATCH_SIZE = 250

load_dotenv(ROOT / ".env")

if not DATA.exists():
    raise SystemExit(f"Extraction file not found: {DATA}")

db = FalkorDB(
    host=os.getenv("FALKORDB_HOST", ""),
    port=int(os.getenv("FALKORDB_PORT", "6379")),
    **connection_options(),
    password=os.getenv("FALKORDB_PASSWORD"),
)
graph = db.select_graph(os.getenv("FALKORDB_GRAPH", "techcompass"))

data = json.loads(DATA.read_text(encoding="utf-8"))

# Upsert source files in batches to avoid one cloud round trip per record.
for start in range(0, len(data["files"]), BATCH_SIZE):
    batch = data["files"][start:start + BATCH_SIZE]
    graph.query(
        """
        UNWIND $rows AS row
        MERGE (f:SourceFile {id: row.id})
        SET f.path = row.path,
            f.language = row.language,
            f.content_hash = row.content_hash,
            f.repository = 'AnythingLLM'
        """,
        {"rows": batch},
    )

# Upsert symbols and their definitions.
for start in range(0, len(data["symbols"]), BATCH_SIZE):
    batch = data["symbols"][start:start + BATCH_SIZE]
    graph.query(
        """
        UNWIND $rows AS row
        MERGE (s:Symbol {id: row.id})
        SET s.name = row.name,
            s.qualified_name = row.qualified_name,
            s.kind = row.kind,
            s.start_line = row.start_line,
            s.end_line = row.end_line,
            s.signature = row.signature,
            s.source_path = row.file_path
        WITH row, s
        MATCH (f:SourceFile {id: row.file_id})
        MERGE (f)-[:DEFINES]->(s)
        """,
        {"rows": batch},
    )

# Store static imports/exports. We'll resolve these into actual file/package
# relationships in the next step.
for start in range(0, len(data["imports"]), BATCH_SIZE):
    batch = data["imports"][start:start + BATCH_SIZE]
    graph.query(
        """
        UNWIND $rows AS row
        MATCH (f:SourceFile {id: row.file_id})
        MERGE (i:ImportReference {
            source_file: row.file_path,
            specifier: row.specifier,
            line: row.line
        })
        SET i.kind = row.kind
        MERGE (f)-[:HAS_IMPORT]->(i)
        """,
        {"rows": batch},
    )

# Read existing components; preserve the current dependency graph.
result = graph.query(
    "MATCH (c:RepoComponent) RETURN c.path, c.name"
)

components = []
for row in result.result_set:
    manifest_path, name = row[0], row[1]
    if not manifest_path or not name:
        continue

    prefix = Path(manifest_path).parent.as_posix()
    if prefix == ".":
        prefix = ""

    components.append({
        "prefix": prefix,
        "name": name,
        "manifest_path": manifest_path,
    })

# Link files to the most specific matching component directory.
component_links = []
for f in data["files"]:
    candidates = [
        c for c in components
        if c["prefix"] and (
            f["path"] == c["prefix"]
            or f["path"].startswith(c["prefix"] + "/")
        )
    ]

    if candidates:
        component = max(candidates, key=lambda c: len(c["prefix"]))
    else:
        # Root-level component association, if your graph has one.
        candidates = [c for c in components if not c["prefix"]]
        if not candidates:
            continue
        component = candidates[0]

    component_links.append({
        "component_path": component["manifest_path"],
        "file_id": f["id"],
    })

for start in range(0, len(component_links), BATCH_SIZE):
    batch = component_links[start:start + BATCH_SIZE]
    graph.query(
        """
        UNWIND $rows AS row
        MATCH (c:RepoComponent {path: row.component_path})
        MATCH (f:SourceFile {id: row.file_id})
        MERGE (c)-[:CONTAINS]->(f)
        """,
        {"rows": batch},
    )

print(f"Files ingested: {len(data['files'])}")
print(f"Symbols ingested: {len(data['symbols'])}")
print(f"Import references ingested: {len(data['imports'])}")
print("FalkorDB ingestion complete.")
