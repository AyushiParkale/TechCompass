
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.api.main import connect_graph


def main() -> None:
    _, graph, node_count = connect_graph()
    print(f"Connected to FalkorDB ({node_count} nodes).")

    queries = {
        "Package samples": """
            MATCH (p:Package)
            RETURN properties(p) AS props
            LIMIT 3
        """,
        "Component samples": """
            MATCH (c:RepoComponent)
            RETURN properties(c) AS props
            LIMIT 10
        """,
        "Existing dependency edges": """
            MATCH (c:RepoComponent)-[r:DEPENDS_ON]->(p:Package)
            RETURN properties(c) AS component,
                   properties(r) AS relationship,
                   properties(p) AS package
            LIMIT 5
        """,
    }

    for label, cypher in queries.items():
        print(f"\n--- {label} ---")
        result = graph.ro_query(cypher)
        for row in result.result_set:
            print(row)


if __name__ == "__main__":
    main()
