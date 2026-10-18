
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.graph.graph_connection import connect_graph

_index_path = Path(os.getenv(
    "TECHCOMPASS_LOCKFILE_INDEX",
    "data/index/resolved_versions.json",
))
INPUT = _index_path if _index_path.is_absolute() else ROOT / _index_path
BATCH_SIZE = 250

# Only map lockfiles whose component identities we have verified.
COMPONENT_BY_LOCKFILE = {
    "data/source/anything-llm/server/yarn.lock": "server/package.json",
    "data/source/anything-llm/frontend/yarn.lock": "frontend/package.json",
    "data/source/anything-llm/collector/yarn.lock": "collector/package.json",
}

def main():
    entries = json.loads(INPUT.read_text(encoding="utf-8"))

    rows = []
    skipped = Counter()

    for entry in entries:
        lockfile = entry["lockfile"].replace("\\", "/")
        component_path = COMPONENT_BY_LOCKFILE.get(lockfile)

        if not component_path:
            skipped["unmapped_lockfile"] += 1
            continue

        if not all(entry.get(k) for k in ("name", "version", "selector")):
            skipped["incomplete_entry"] += 1
            continue

        rows.append({
            "name": entry["name"],
            "ecosystem": "npm",
            "version": entry["version"],
            "selector": entry["selector"],
            "lockfile": lockfile,
            "component_path": component_path,
            "resolved": entry.get("resolved"),
            "dependencies": entry.get("dependencies", []),
        })

    # Remove duplicate selector mappings without collapsing distinct versions.
    unique = {
        (
            r["name"], r["version"], r["selector"],
            r["lockfile"], r["component_path"]
        ): r
        for r in rows
    }
    rows = list(unique.values())

    dependency_rows, unresolved_dependencies = build_dependency_rows(rows)

    print(f"Input entries: {len(entries)}")
    print(f"Mapped lockfile entries: {len(rows)}")
    print(f"Resolved package-version dependencies: {len(dependency_rows)}")
    print(f"Unresolved package-version dependencies: {unresolved_dependencies}")
    print(f"Skipped: {dict(skipped)}")

    db, graph = connect_graph()
    print("Connected to FalkorDB.")

    # Validate all component identities before making any changes.
    result = graph.ro_query("""
        MATCH (c:RepoComponent)
        RETURN c.path AS path
    """)
    existing_components = {
        row[0] for row in result.result_set
    }

    missing = sorted({
        r["component_path"] for r in rows
        if r["component_path"] not in existing_components
    })

    if missing:
        raise RuntimeError(
            "Component paths not found; no ingestion performed: "
            + ", ".join(missing)
        )

    # Ensure package identity exists, including transitive packages.
    # Keep existing package properties and dependency edges intact.
    package_query = """
        UNWIND $rows AS row
        MERGE (p:Package {
            name: row.name,
            ecosystem: row.ecosystem
        })
    """

    version_query = """
        UNWIND $rows AS row
        MATCH (p:Package {
            name: row.name,
            ecosystem: row.ecosystem
        })
        MERGE (v:PackageVersion {
            name: row.name,
            ecosystem: row.ecosystem,
            version: row.version
        })
        MERGE (p)-[:HAS_VERSION]->(v)
    """

    resolution_query = """
        UNWIND $rows AS row
        MATCH (c:RepoComponent {path: row.component_path})
        MATCH (v:PackageVersion {
            name: row.name,
            ecosystem: row.ecosystem,
            version: row.version
        })
        MERGE (c)-[r:HAS_LOCKFILE_RESOLUTION {
            lockfile_path: row.lockfile,
            selector: row.selector
        }]->(v)
        SET r.resolved_url = row.resolved
    """

    # Run in manageable batches.
    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]
        params = {"rows": batch}

        graph.query(package_query, params)
        graph.query(version_query, params)
        graph.query(resolution_query, params)

        print(
            f"Processed {min(start + len(batch), len(rows))}"
            f"/{len(rows)} selector mappings"
        )

    dependency_query = """
        UNWIND $rows AS row
        MATCH (source:PackageVersion {
            name: row.source_name,
            ecosystem: 'npm',
            version: row.source_version
        })
        MATCH (target:PackageVersion {
            name: row.target_name,
            ecosystem: 'npm',
            version: row.target_version
        })
        MERGE (source)-[r:DEPENDS_ON_VERSION {
            lockfile_path: row.lockfile,
            dependency_selector: row.dependency_selector
        }]->(target)
        SET r.optional = row.optional
    """
    for start in range(0, len(dependency_rows), BATCH_SIZE):
        graph.query(
            dependency_query,
            {"rows": dependency_rows[start:start + BATCH_SIZE]},
        )

    print("\nIngestion complete. Verifying graph counts...")

    checks = {
        "PackageVersion nodes": """
            MATCH (v:PackageVersion)
            RETURN count(v) AS n
        """,
        "Lockfile resolution edges": """
            MATCH (:RepoComponent)-[r:HAS_LOCKFILE_RESOLUTION]->(:PackageVersion)
            RETURN count(r) AS n
        """,
        "Package version edges": """
            MATCH (:Package)-[r:HAS_VERSION]->(:PackageVersion)
            RETURN count(r) AS n
        """,
        "Package-version dependency edges": """
            MATCH (:PackageVersion)-[r:DEPENDS_ON_VERSION]->
                  (:PackageVersion)
            RETURN count(r) AS n
        """,
    }

    for label, query in checks.items():
        result = graph.ro_query(query)
        print(f"{label}: {result.result_set[0][0]}")

    print("Existing DEPENDS_ON relationships were not modified.")


def build_dependency_rows(rows):
    versions_by_selector = {
        (row["lockfile"], row["selector"]): row
        for row in rows
    }
    dependency_rows = []
    unresolved = 0

    for row in rows:
        for dependency in row["dependencies"]:
            target = versions_by_selector.get(
                (row["lockfile"], dependency["selector"])
            )
            if target is None:
                unresolved += 1
                continue
            dependency_rows.append({
                "source_name": row["name"],
                "source_version": row["version"],
                "target_name": target["name"],
                "target_version": target["version"],
                "lockfile": row["lockfile"],
                "dependency_selector": dependency["selector"],
                "optional": dependency["optional"],
            })

    unique_dependencies = {
        (
            row["source_name"], row["source_version"],
            row["target_name"], row["target_version"],
            row["lockfile"], row["dependency_selector"],
        ): row
        for row in dependency_rows
    }
    return list(unique_dependencies.values()), unresolved


if __name__ == "__main__":
    main()
