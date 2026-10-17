import re
from typing import Any


def _rows(
    graph: Any,
    cypher: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a read-only query and map result columns to values."""
    result = graph.ro_query(cypher, params or {})
    columns = [column[1] for column in result.header]
    return [
        dict(zip(columns, row))
        for row in result.result_set
    ]


def search_project(graph: Any) -> list[dict[str, Any]]:
    """Return the project's indexed components."""
    return _rows(
        graph,
        """
        MATCH (p:Project)-[:HAS_COMPONENT]->(c:RepoComponent)
        RETURN p.name AS project,
               c.name AS component,
               c.path AS manifest_path
        ORDER BY c.name
        LIMIT 50
        """,
    )


def get_component_dependencies(
    graph: Any,
    component: str,
) -> list[dict[str, Any]]:
    """Return declared packages and version ranges for a component."""
    return _rows(
        graph,
        """
        MATCH (c:RepoComponent)-[d:DEPENDS_ON]->(p:Package)
        WHERE toLower(c.name) CONTAINS toLower($component)
        RETURN c.name AS component,
               p.name AS package,
               p.ecosystem AS ecosystem,
               d.declared_version AS declared_version,
               d.dependency_type AS dependency_type,
               d.source_file AS manifest
        ORDER BY p.name
        LIMIT 200
        """,
        {"component": component},
    )


def find_package_usage(
    graph: Any,
    package: str,
) -> list[dict[str, Any]]:
    """Find source files directly connected to a package import."""
    return _rows(
        graph,
        """
        MATCH (f:SourceFile)-[u:IMPORTS_PACKAGE]->(p:Package)
        WHERE toLower(p.name) = toLower($package)
        OPTIONAL MATCH (c:RepoComponent)-[:CONTAINS]->(f)
        RETURN p.name AS package,
               f.path AS source_file,
               u.specifier AS import_specifier,
               u.line AS line,
               c.name AS component
        ORDER BY f.path
        LIMIT 500
        """,
        {"package": package},
    )


def search_architecture_knowledge(
    graph: Any,
    query: str,
) -> list[dict[str, Any]]:
    """Search concept names and their supporting documentation evidence."""
    stop_words = {
        "the", "and", "for", "from", "what", "how", "does",
        "anythingllm", "explain", "documented", "approach",
        "show", "tell", "about",
    }
    terms = [
        word.lower()
        for word in re.findall(r"[a-zA-Z0-9.+#-]+", query)
        if len(word) > 1 and word.lower() not in stop_words
    ][:8]
    if not terms:
        return []

    rows = _rows(
        graph,
        """
        MATCH (c)-[:SUPPORTED_BY]->(e:Evidence)
        WHERE (c:Technology OR c:Approach OR c:Constraint)
        WITH c, e, [
            term IN $terms
            WHERE toLower(coalesce(e.section_heading, '')) CONTAINS term
        ] AS section_terms, [
            term IN $terms
            WHERE toLower(coalesce(e.document_title, '')) CONTAINS term
        ] AS document_terms, [
            term IN $terms
            WHERE toLower(coalesce(c.name, '')) CONTAINS term
        ] AS concept_terms, [
            term IN $terms
            WHERE toLower(coalesce(c.name, '')) CONTAINS term
                OR toLower(coalesce(e.document_title, '')) CONTAINS term
                OR toLower(coalesce(e.section_heading, '')) CONTAINS term
                OR toLower(coalesce(e.excerpt, '')) CONTAINS term
                OR toLower(coalesce(e.source_path, '')) CONTAINS term
        ] AS matched_terms
        WHERE size(matched_terms) > 0
        RETURN
            labels(c) AS concept_types,
            c.name AS concept,
            e.document_title AS document,
            e.section_heading AS section,
            e.source_path AS source_path,
            e.source_url AS source_url,
            e.excerpt AS evidence_excerpt,
            e.verification_status AS evidence_status,
            matched_terms
        ORDER BY size(section_terms) DESC,
                 size(document_terms) DESC,
                 size(concept_terms) DESC,
                 size(matched_terms) DESC,
                 e.document_title,
                 e.section_heading
        LIMIT 100
        """,
        {"terms": terms},
    )
    unique_rows = {}
    for row in rows:
        key = (
            row.get("document"),
            row.get("section"),
            row.get("source_url"),
        )
        unique_rows.setdefault(key, row)
    return list(unique_rows.values())[:15]


def trace_package_impact(
    graph: Any,
    package: str,
) -> dict[str, list[dict[str, Any]]]:
    """Separate declared dependency edges from direct package imports."""
    declared_dependencies = _rows(
        graph,
        """
        MATCH (c:RepoComponent)-[d:DEPENDS_ON]->(p:Package)
        WHERE toLower(p.name) = toLower($package)
        RETURN c.name AS component,
               c.path AS manifest_path,
               p.name AS package,
               p.ecosystem AS ecosystem,
               d.declared_version AS declared_version,
               d.dependency_type AS dependency_type,
               d.source_file AS manifest
        ORDER BY c.name
        LIMIT 200
        """,
        {"package": package},
    )
    direct_imports = find_package_usage(graph, package)
    return {
        "declared_dependencies": declared_dependencies,
        "direct_imports": direct_imports,
    }
