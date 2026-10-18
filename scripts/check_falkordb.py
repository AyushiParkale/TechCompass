"""Print the node count for the configured FalkorDB graph."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.graph.graph_connection import connect_graph


def main() -> None:
    db, graph = connect_graph()
    try:
        result = graph.ro_query("MATCH (n) RETURN count(n) AS node_count")
        print("Connected to deployed FalkorDB; nodes:", result.result_set[0][0])
    finally:
        db.close()


if __name__ == "__main__":
    main()
