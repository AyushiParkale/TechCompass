"""Ingest reviewed, source-grounded project goals and architecture context."""

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.graph.graph_connection import connect_graph

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "backend" / "graph" / "anythingllm_context.json"
DOCS_ROOT = "data/source/anythingllm-docs/"
REPO_ROOT = ROOT / "data" / "source" / "anything-llm"
ALLOWED_RELATIONS = {"USES_PATTERN", "USES_TECHNOLOGY"}

load_dotenv(ROOT / ".env")


def stable_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def path_url(path: str) -> str:
    relative = path.removeprefix("pages/")
    relative = relative.removesuffix(".md").removesuffix(".mdx")
    return "https://docs.anythingllm.com/" + relative


def _sections_for_path(graph: Any, source_path: str) -> list[dict[str, Any]]:
    result = graph.ro_query(
        """MATCH (s:DocSection)
        WHERE s.source_path = $source_path
        RETURN s.id, s.heading, s.content, s.source_url
        LIMIT 100""",
        {"source_path": source_path},
    )
    return [
        {"id": row[0], "heading": row[1] or "", "content": row[2] or "",
         "url": row[3] or path_url(source_path)}
        for row in result.result_set if row and row[0]
    ]


def _choose_evidence_sections(
    sections: list[dict[str, Any]], query: str, limit: int = 2
) -> list[dict[str, Any]]:
    query_terms = {
        token.casefold() for token in query.split() if len(token) > 3
    }
    ranked = []
    for section in sections:
        text = f"{section['heading']} {section['content']}".casefold()
        score = sum(1 for term in query_terms if term in text)
        if score:
            ranked.append((score, section))
    ranked.sort(key=lambda row: (row[0], len(row[1]["content"])), reverse=True)
    return [section for _, section in ranked[:limit]] or sections[:1]


def _read_repository_section(relative_path: str, heading: str) -> str:
    path = (REPO_ROOT / relative_path).resolve()
    if REPO_ROOT.resolve() not in path.parents:
        raise ValueError(f"Repository evidence path escapes source root: {relative_path}")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    heading_level = None
    start = None
    for index, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.strip())
        if match and match.group(2).casefold() == heading.casefold():
            heading_level = len(match.group(1))
            start = index
            break
    if start is None or heading_level is None:
        raise ValueError(f"Markdown heading {heading!r} was not found in {relative_path}")
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#{1,6})\s+", lines[index].strip())
        if match and len(match.group(1)) <= heading_level:
            end = index
            break
    return "\n".join(lines[start:end])[:1800]


def _write_evidence(
    graph: Any, *, source_id: str, title: str, heading: str,
    path: str, url: str, excerpt: str, source_type: str,
) -> str:
    evidence_id = stable_id(f"{source_type}:{path}:{heading}:{source_id}")
    graph.query(
        """MERGE (e:Evidence {id: $id})
        SET e.source_type = $source_type,
            e.document_title = $title,
            e.section_heading = $heading,
            e.source_path = $path,
            e.source_url = $url,
            e.excerpt = $excerpt,
            e.verification_status = 'source_backed',
            e.extraction_method = 'reviewed_project_context'""",
        {"id": evidence_id, "source_type": source_type, "title": title,
         "heading": heading, "path": path, "url": url,
         "excerpt": excerpt[:1800]},
    )
    return evidence_id


def _link_code_evidence(graph: Any, capability_id: str, paths: list[str]) -> int:
    links = 0
    for configured_path in paths:
        result = graph.ro_query(
            """MATCH (f:SourceFile)
            WHERE f.path = $path OR f.path STARTS WITH $prefix
            RETURN f.path, f.id
            ORDER BY f.path LIMIT 25""",
            {"path": configured_path, "prefix": configured_path.rstrip("/") + "/"},
        )
        rows = [{"path": row[0], "id": row[1]} for row in result.result_set if row and row[0] and row[1]]
        for row in rows:
            graph.query(
                """MATCH (cap:ProjectCapability {id: $capability_id})
                MATCH (f:SourceFile {id: $file_id})
                MERGE (cap)-[r:IMPLEMENTED_BY]->(f)
                SET r.verification_status = 'candidate_path_mapping',
                    r.mapping_source = 'reviewed_project_context'""",
                {"capability_id": capability_id, "file_id": row["id"]},
            )
            graph.query(
                """MATCH (cap:ProjectCapability {id: $capability_id})
                MATCH (f:SourceFile {id: $file_id})
                MATCH (component:RepoComponent)-[:CONTAINS]->(f)
                MERGE (cap)-[r:RELATES_TO_COMPONENT]->(component)
                SET r.verification_status = 'candidate_path_mapping',
                    r.mapping_source = 'reviewed_project_context'""",
                {"capability_id": capability_id, "file_id": row["id"]},
            )
            links += 1
    return links


def main() -> None:
    profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    project = profile["project"]
    db, graph = connect_graph()
    project_source = project["business_goal_source"]
    project_source_path = ROOT / project_source
    if not project_source_path.exists():
        raise FileNotFoundError(f"Project source evidence is missing: {project_source}")
    readme = project_source_path.read_text(encoding="utf-8")
    overview_start = readme.find("### Product Overview")
    readme_excerpt = (
        readme[overview_start:overview_start + 1800]
        if overview_start >= 0 else readme[:1800]
    )

    graph.query(
        """MERGE (p:Project {name: $name})
        SET p.repository_url = $repository_url,
            p.official_domains = $official_domains,
            p.description = $description,
            p.business_goal = $business_goal,
            p.objectives = $objectives,
            p.context_version = '2',
            p.context_source = $source_path""",
        {"name": project["name"], "repository_url": project["repository_url"],
         "official_domains": project.get("official_domains", []),
         "description": project["description"],
         "business_goal": project["business_goal"],
         "objectives": [item["text"] for item in project["objectives"]],
         "source_path": project_source},
    )
    project_evidence_id = _write_evidence(
        graph, source_id="anythingllm-readme", title="AnythingLLM README",
        heading="Product overview", path=project_source,
        url=project["repository_url"] + "/blob/master/README.md",
        excerpt=readme_excerpt, source_type="repository_readme",
    )
    graph.query(
        """MATCH (p:Project {name: $name})
        MATCH (e:Evidence {id: $evidence_id})
        MERGE (g:ProjectGoal {id: $goal_id})
        SET g.text = $goal, g.source_type = 'inferred_from_readme'
        MERGE (p)-[:HAS_GOAL]->(g)
        MERGE (g)-[:SUPPORTED_BY]->(e)""",
        {"name": project["name"], "evidence_id": project_evidence_id,
         "goal_id": "anythingllm-core-goal", "goal": project["business_goal"]},
    )

    capabilities = {item["id"]: item for item in profile["capabilities"]}
    for capability in profile["capabilities"]:
        graph.query(
            """MATCH (p:Project {name: $project})
            MERGE (c:ProjectCapability {id: $id})
            SET c.name = $name, c.description = $description,
                c.context_source = 'reviewed_project_context'
            MERGE (p)-[:HAS_CAPABILITY]->(c)""",
            {"project": project["name"], "id": capability["id"],
             "name": capability["name"], "description": capability["description"]},
        )
    objective_by_id = {item["id"]: item for item in project["objectives"]}
    evidence_links = 0
    code_links = 0
    for objective_id, objective in objective_by_id.items():
        objective_evidence = []
        for source_path in objective["sources"]:
            sections = _sections_for_path(graph, source_path)
            if not sections:
                print(f"Warning: no ingested documentation sections for {source_path}")
                continue
            for section in _choose_evidence_sections(
                sections, objective["text"], limit=2
            ):
                evidence_id = _write_evidence(
                    graph, source_id=str(section["id"]),
                    title=Path(source_path).stem.replace("-", " ").title(),
                    heading=section["heading"],
                    path=DOCS_ROOT + source_path, url=section["url"],
                    excerpt=section["content"],
                    source_type="official_project_documentation",
                )
                objective_evidence.append(evidence_id)
                evidence_links += 1
        graph.query(
            """MATCH (p:Project {name: $project})
            MERGE (o:ProjectObjective {id: $id})
            SET o.text = $text, o.source_type = 'documented_product_objective'
            MERGE (p)-[:HAS_OBJECTIVE]->(o)""",
            {"project": project["name"], "id": objective_id,
             "text": objective["text"]},
        )
        for evidence_id in objective_evidence:
            graph.query(
                """MATCH (o:ProjectObjective {id: $objective_id})
                MATCH (e:Evidence {id: $evidence_id})
                MERGE (o)-[:SUPPORTED_BY]->(e)""",
                {"objective_id": objective_id, "evidence_id": evidence_id},
            )

        for capability_id in objective["capability_ids"]:
            if capability_id not in capabilities:
                raise ValueError(f"Unknown capability id in objective: {capability_id}")
            graph.query(
                """MATCH (o:ProjectObjective {id: $objective_id})
                MATCH (c:ProjectCapability {id: $capability_id})
                MERGE (o)-[:ADVANCES]->(c)""",
                {"objective_id": objective_id, "capability_id": capability_id},
            )

    for capability in profile["capabilities"]:
        for label, relation, values in (
            ("ArchitecturePattern", "USES_PATTERN", capability.get("patterns", [])),
            ("Technology", "USES_TECHNOLOGY", capability.get("technologies", [])),
        ):
            if relation not in ALLOWED_RELATIONS:
                raise ValueError(f"Unsupported relationship: {relation}")
            for value in values:
                graph.query(
                    f"""MATCH (c:ProjectCapability {{id: $capability_id}})
                    MERGE (n:{label} {{name: $name}})
                    SET n.context_source = 'reviewed_project_context'
                    MERGE (c)-[:{relation}]->(n)""",
                    {"capability_id": capability["id"], "name": value},
                )

        for source_path in capability.get("documentation", []):
            sections = _sections_for_path(graph, source_path)
            if not sections:
                print(f"Warning: no ingested documentation sections for {source_path}")
                continue
            for section in _choose_evidence_sections(
                sections, capability["name"] + " " + capability["description"], limit=2
            ):
                evidence_id = _write_evidence(
                    graph, source_id=str(section["id"]),
                    title=Path(source_path).stem.replace("-", " ").title(),
                    heading=section["heading"], path=DOCS_ROOT + source_path,
                    url=section["url"], excerpt=section["content"],
                    source_type="official_project_documentation",
                )
                graph.query(
                    """MATCH (c:ProjectCapability {id: $capability_id})
                    MATCH (e:Evidence {id: $evidence_id})
                    MERGE (c)-[:SUPPORTED_BY]->(e)""",
                    {"capability_id": capability["id"], "evidence_id": evidence_id},
                )
                evidence_links += 1
        for source in capability.get("repository_sections", []):
            relative_path = source["path"]
            excerpt = _read_repository_section(relative_path, source["heading"])
            source_url = (
                project["repository_url"] + "/blob/master/" + relative_path
            )
            evidence_id = _write_evidence(
                graph, source_id=f"{relative_path}#{source['heading']}",
                title="AnythingLLM README", heading=source["heading"],
                path=f"data/source/anything-llm/{relative_path}",
                url=source_url, excerpt=excerpt,
                source_type="repository_readme",
            )
            graph.query(
                """MATCH (c:ProjectCapability {id: $capability_id})
                MATCH (e:Evidence {id: $evidence_id})
                MERGE (c)-[:SUPPORTED_BY]->(e)""",
                {"capability_id": capability["id"], "evidence_id": evidence_id},
            )
            evidence_links += 1
        code_links += _link_code_evidence(
            graph, capability["id"],
            capability.get("implementation_paths", []),
        )

    summary = graph.ro_query(
        """MATCH (p:Project {name: $name})-[:HAS_CAPABILITY]->(c:ProjectCapability)
        OPTIONAL MATCH (c)-[:SUPPORTED_BY]->(e:Evidence)
        OPTIONAL MATCH (c)-[r:IMPLEMENTED_BY]->(f:SourceFile)
        RETURN count(DISTINCT c), count(DISTINCT e), count(DISTINCT f)""",
        {"name": project["name"]},
    )
    print(f"Project: {project['name']}")
    print(f"Reviewed capabilities: {summary.result_set[0][0] if summary.result_set else 0}")
    print(f"Capability/document evidence links processed: {evidence_links}")
    print(f"Candidate capability/source-file links processed: {code_links}")
    print("Source-file links are candidates; they are not proof of exact runtime impact.")
    db.close()


if __name__ == "__main__":
    main()
