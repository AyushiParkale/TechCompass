
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB
from backend.graph.graph_connection import connection_options

# Project root: D:\Hackathon\TechCompass
ROOT = Path(__file__).resolve().parents[2]

# Load the .env file from the project root
load_dotenv(ROOT / ".env")

REPO = ROOT / "data" / "source" / "anything-llm"
GRAPH_NAME = "techcompass"

password = os.getenv("FALKORDB_PASSWORD")

if not password:
    raise RuntimeError(
        f"FALKORDB_PASSWORD not found in {ROOT / '.env'}"
    )

db = FalkorDB(
    host=os.getenv("FALKORDB_HOST", ""),
    port=int(os.getenv("FALKORDB_PORT", "6379")),
    **connection_options(),
    password=password,
)

graph = db.select_graph(os.getenv("FALKORDB_GRAPH", GRAPH_NAME))


# Create or update the project node.
graph.query("""
    MERGE (p:Project {name: $name})
    SET p.repository_url = $url,
        p.source_type = 'real_project'
""", {
    "name": "AnythingLLM",
    "url": "https://github.com/Mintplex-Labs/anything-llm",
})

manifest_count = 0
dependency_count = 0

# Find manifests, excluding generated dependency directories.
for manifest in REPO.rglob("package.json"):
    if any(part in {"node_modules", ".git"} for part in manifest.parts):
        continue

    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        continue

    relative_path = manifest.relative_to(REPO).as_posix()
    component_name = data.get("name") or relative_path

    graph.query("""
        MERGE (c:RepoComponent {path: $path})
        SET c.name = $name,
            c.source_file = $source_file,
            c.source_type = 'repository_manifest'
        WITH c
        MATCH (p:Project {name: 'AnythingLLM'})
        MERGE (p)-[:HAS_COMPONENT]->(c)
    """, {
        "path": relative_path,
        "name": component_name,
        "source_file": relative_path,
    })

    manifest_count += 1

    dependency_groups = {
        "dependencies": "runtime",
        "devDependencies": "development",
        "optionalDependencies": "optional",
        "peerDependencies": "peer",
    }

    for field, dependency_type in dependency_groups.items():
        for package_name, declared_version in (
            data.get(field, {}) or {}
        ).items():
            graph.query("""
                MATCH (c:RepoComponent {path: $path})
                MERGE (p:Package {
                    name: $package,
                    ecosystem: 'npm'
                })
                MERGE (c)-[r:DEPENDS_ON]->(p)
                SET r.declared_version = $version,
                    r.dependency_type = $dependency_type,
                    r.source_file = $source_file
            """, {
                "path": relative_path,
                "package": package_name,
                "version": str(declared_version),
                "dependency_type": dependency_type,
                "source_file": relative_path,
            })
            dependency_count += 1

print(f"Manifests processed: {manifest_count}")
print(f"Dependency declarations processed: {dependency_count}")

result = graph.query("""
    MATCH (p:Project {name: 'AnythingLLM'})
          -[:HAS_COMPONENT]->(c:RepoComponent)
          -[r:DEPENDS_ON]->(d:Package)
    RETURN c.name, d.name, r.declared_version
    LIMIT 20
""")

for row in result.result_set:
    print(row)

db.close()
