
import hashlib
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB
from graph_connection import is_local_host

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

# Curated concepts: the text must actually occur in a section.
# These are discovery rules, not proof of implementation in source code.
RULES = [
    ("Technology", "Node.js", ["nodejs", "node.js"]),
    ("Technology", "JavaScript", ["javascript"]),
    ("Technology", "Docker", ["docker"]),
    ("Technology", "Google Apps Script", ["google apps script"]),
    ("Technology", "OAuth 2.0", ["oauth2", "oauth 2.0"]),
    ("Technology", "Large Language Model", ["llm", "language model"]),
    ("Approach", "Agent Skills", ["agent skill", "agent skills"]),
    ("Approach", "Scheduled Jobs", ["scheduled job", "scheduled jobs"]),
    ("Approach", "Document Generation Agent",
     ["document generation agent"]),
    ("Approach", "Filesystem Agent", ["file system agent"]),
    ("Approach", "Tool-Using Agent", ["tools", "tool calling"]),
    ("Constraint", "Single-User Mode",
     ["single-user mode", "single user mode"]),
    ("Constraint", "Approval-Gated Actions",
     ["ask for approval", "explicit approval", "approval"]),
    ("Constraint", "Filesystem Permissions",
     ["read-only", "read only", "permissions"]),
]

ALLOWED_LABELS = {"Technology", "Approach", "Constraint"}


def connect():
    host = os.getenv("FALKORDB_HOST", "").strip()
    password = os.getenv("FALKORDB_PASSWORD", "")
    if not host or not password:
        raise RuntimeError("Set FALKORDB_HOST and FALKORDB_PASSWORD in .env")
    if is_local_host(host):
        raise RuntimeError(
            "FALKORDB_HOST must point to the deployed FalkorDB instance; "
            "local database connections are disabled"
        )

    username = os.getenv("FALKORDB_USERNAME", "").strip() or None
    port = int(os.getenv("FALKORDB_PORT", "6379"))
    tls_setting = os.getenv("FALKORDB_TLS")
    if tls_setting is None:
        tls = True
    else:
        normalized_tls = tls_setting.strip().lower()
        if normalized_tls not in {"1", "true", "yes", "0", "false", "no"}:
            raise ValueError("FALKORDB_TLS must be set to true or false")
        tls = normalized_tls in {"1", "true", "yes"}

    db = FalkorDB(
        host=host,
        port=port,
        username=username,
        password=password,
        ssl=tls,
        ssl_cert_reqs="required" if tls else None,
        ssl_check_hostname=tls,
        socket_connect_timeout=10,
        socket_timeout=30,
    )
    return db.select_graph(os.getenv("FALKORDB_GRAPH", "techcompass"))


def stable_id(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main():
    graph = connect()

    result = graph.ro_query("""
        MATCH (d:Document)-[:HAS_SECTION]->(s:DocSection)
        RETURN
            s.id AS id,
            d.title AS document,
            s.heading AS heading,
            s.content AS content,
            s.source_path AS source_path,
            s.source_url AS source_url
    """)

    sections = result.result_set
    if not sections:
        raise RuntimeError(
            "No linked documentation sections found. Check the cloud graph."
        )

    evidence_count = 0
    links_count = 0
    seen_evidence = set()

    for row in sections:
        section_id, document, heading, content, source_path, source_url = row
        if not section_id:
            continue

        document = document or "Untitled document"
        heading = heading or "Untitled section"
        content = content or ""
        searchable = (heading + "\n" + content).lower()

        matched = [
            (label, name)
            for label, name, keywords in RULES
            if any(keyword in searchable for keyword in keywords)
        ]
        if not matched:
            continue

        evidence_id = "docsection:" + str(section_id)
        if evidence_id not in seen_evidence:
            graph.query("""
                MATCH (s:DocSection {id: $section_id})
                MERGE (e:Evidence {id: $evidence_id})
                SET e.source_type = 'official_documentation',
                    e.document_title = $document,
                    e.section_heading = $heading,
                    e.source_path = $source_path,
                    e.source_url = $source_url,
                    e.excerpt = $excerpt,
                    e.verification_status = 'documented',
                    e.extraction_method = 'curated_keyword_rules'
                MERGE (s)-[:HAS_EVIDENCE]->(e)
            """, {
                "section_id": section_id,
                "evidence_id": evidence_id,
                "document": document,
                "heading": heading,
                "source_path": source_path,
                "source_url": source_url,
                "excerpt": content[:1500],
            })
            seen_evidence.add(evidence_id)
            evidence_count += 1

        for label, name in matched:
            if label not in ALLOWED_LABELS:
                continue

            graph.query(f"""
                MATCH (e:Evidence {{id: $evidence_id}})
                MERGE (c:{label} {{name: $name}})
                ON CREATE SET c.created_by = 'documentation_ingestion'
                MERGE (c)-[:SUPPORTED_BY]->(e)
            """, {
                "evidence_id": evidence_id,
                "name": name,
            })
            links_count += 1

    print(f"Documentation sections scanned: {len(sections)}")
    print(f"Evidence records processed: {evidence_count}")
    print(f"Concept-to-evidence links processed: {links_count}")
    print("Existing graph nodes were preserved.")


if __name__ == "__main__":
    main()
