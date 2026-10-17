
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB
from graph_connection import connection_options

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "data" / "source" / "anything-llm"
INDEX = ROOT / "data" / "index" / "codebase.json"
BATCH_SIZE = 250

load_dotenv(ROOT / ".env")

db = FalkorDB(
    host=os.getenv("FALKORDB_HOST", ""),
    port=int(os.getenv("FALKORDB_PORT", "6379")),
    **connection_options(),
    password=os.getenv("FALKORDB_PASSWORD"),
)
graph = db.select_graph(os.getenv("FALKORDB_GRAPH", "techcompass"))

data = json.loads(INDEX.read_text(encoding="utf-8"))
files = {f["path"]: f for f in data["files"]}

# Build path candidates for TypeScript/JavaScript imports.
def resolve_local(source_path, specifier):
    if not specifier.startswith("."):
        return None

    base = (Path(source_path).parent / specifier).as_posix()
    candidates = [base]

    for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        candidates.append(base + ext)

    for ext in (".ts", ".tsx", ".js", ".jsx"):
        candidates.append(f"{base}/index{ext}")

    for candidate in candidates:
        normalized = Path(candidate).as_posix()
        if normalized in files:
            return files[normalized]

    # Some imports omit extensions or use directory aliases.
    # This fallback checks known paths with the same base.
    for path, item in files.items():
        if path.startswith(base + "/") and Path(path).stem in {
            "index", "main"
        }:
            return item

    return None


def package_name(specifier):
    if specifier.startswith(".") or specifier.startswith("/"):
        return None

    # Ignore URL imports and Node built-ins for now.
    if "://" in specifier:
        return None

    if specifier.startswith("@"):
        parts = specifier.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else specifier

    return specifier.split("/")[0]


# Retrieve existing Package nodes by canonical name.
result = graph.query(
    "MATCH (p:Package) RETURN p.name, p.ecosystem"
)

packages = {
    (row[0], row[1]): True
    for row in result.result_set
}

local_count = 0
external_count = 0
unresolved_count = 0
local_rows = []
package_rows = []

for imp in data["imports"]:
    specifier = imp["specifier"]
    source = files.get(imp["file_path"])

    if not source:
        continue

    target = resolve_local(imp["file_path"], specifier)

    if target:
        local_rows.append({
            "source_id": source["id"],
            "target_id": target["id"],
            "specifier": specifier,
            "line": imp["line"],
        })
        local_count += 1
        continue

    name = package_name(specifier)
    if name is None:
        unresolved_count += 1
        continue

    if (name, "npm") in packages:
        package_rows.append({
            "source_id": source["id"],
            "package_name": name,
            "specifier": specifier,
            "line": imp["line"],
        })
        external_count += 1
    else:
        unresolved_count += 1

local_query = """
    UNWIND $rows AS row
    MATCH (src:SourceFile {id: row.source_id})
    MATCH (dst:SourceFile {id: row.target_id})
    MERGE (src)-[r:IMPORTS]->(dst)
    SET r.specifier = row.specifier,
        r.line = row.line,
        r.resolution_status = 'resolved'
"""
for start in range(0, len(local_rows), BATCH_SIZE):
    graph.query(
        local_query,
        {"rows": local_rows[start:start + BATCH_SIZE]},
    )

package_query = """
    UNWIND $rows AS row
    MATCH (src:SourceFile {id: row.source_id})
    MATCH (p:Package {name: row.package_name, ecosystem: 'npm'})
    MERGE (src)-[r:IMPORTS_PACKAGE]->(p)
    SET r.specifier = row.specifier,
        r.line = row.line,
        r.resolution_status = 'matched_package'
"""
for start in range(0, len(package_rows), BATCH_SIZE):
    graph.query(
        package_query,
        {"rows": package_rows[start:start + BATCH_SIZE]},
    )

print(f"Resolved local imports: {local_count}")
print(f"Imports linked to existing npm packages: {external_count}")
print(f"Unresolved or unmatched references: {unresolved_count}")
