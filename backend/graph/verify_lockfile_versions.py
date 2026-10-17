
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.main import connect_graph


def main() -> None:
    _, graph, node_count = connect_graph()
    print(f"Connected to FalkorDB ({node_count} nodes).")

    queries = [
        (
            "Resolution counts by lockfile",
            """
            MATCH (c:RepoComponent)-[r:HAS_LOCKFILE_RESOLUTION]->
                  (v:PackageVersion)
            RETURN c.path AS component,
                   r.lockfile_path AS lockfile,
                   count(r) AS resolutions
            ORDER BY component
            """,
        ),
        (
            "Sample @langchain/core version paths",
            """
            MATCH (c:RepoComponent)-[r:HAS_LOCKFILE_RESOLUTION]->
                  (v:PackageVersion {name: '@langchain/core'})
            MATCH (p:Package)-[:HAS_VERSION]->(v)
            RETURN c.path AS component,
                   p.name AS package,
                   v.version AS resolved_version,
                   r.selector AS selector,
                   r.lockfile_path AS lockfile
            ORDER BY component, resolved_version
            LIMIT 20
            """,
        ),
        (
            "Relationship integrity",
            """
            MATCH (c:RepoComponent)-[:HAS_LOCKFILE_RESOLUTION]->
                  (v:PackageVersion)
            OPTIONAL MATCH (p:Package)-[:HAS_VERSION]->(v)
            RETURN count(v) AS resolution_matches,
                   count(p) AS matched_package_versions
            """,
        ),
    ]

    for label, cypher in queries:
        print(f"\n--- {label} ---")
        result = graph.ro_query(cypher)
        for row in result.result_set:
            print(row)


if __name__ == "__main__":
    main()
