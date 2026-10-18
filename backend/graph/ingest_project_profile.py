"""Idempotently add TechCompass goals to the configured FalkorDB graph."""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from graph_connection import connect_graph

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def main():
    profile = json.loads(
        (ROOT / "backend" / "graph" / "project_profile.json").read_text(
            encoding="utf-8"
        )
    )
    db, graph = connect_graph()
    graph.query(
        """MERGE (p:ProductProfile {id: $id})
        SET p.name = $name, p.description = $description,
            p.business_goal = $business_goal,
            p.source_path = 'backend/graph/project_profile.json',
            p.source_type = 'maintainer_project_brief'""",
        {key: profile[key] for key in (
            "id", "name", "description", "business_goal"
        )},
    )
    for objective in profile["objectives"]:
        graph.query(
            """MERGE (o:ProductObjective {id: $id})
            SET o.text = $text, o.source = $source
            WITH o
            MATCH (p:ProductProfile {id: $profile_id})
            MERGE (p)-[:HAS_OBJECTIVE]->(o)""",
            {**objective, "profile_id": profile["id"]},
        )
    print(
        f"Ingested TechCompass profile and "
        f"{len(profile['objectives'])} objectives into "
        f"{os.getenv('FALKORDB_GRAPH', 'techcompass')}"
    )
    db.close()


if __name__ == "__main__":
    main()
