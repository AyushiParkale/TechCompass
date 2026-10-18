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


def trace_component_dependencies(
    graph: Any,
    component: str,
    depth: int = 1,
) -> dict[str, Any]:
    """Trace resolved package dependencies declared by a component."""
    if (
        isinstance(depth, bool)
        or not isinstance(depth, int)
        or not 1 <= depth <= 4
    ):
        raise ValueError("depth must be an integer from 1 to 4")

    matches = _rows(
        graph,
        """
        MATCH (c:RepoComponent)
        WHERE toLower(coalesce(c.name, '')) CONTAINS toLower($component)
           OR toLower(coalesce(c.path, '')) CONTAINS toLower($component)
        RETURN c.name AS component,
               c.path AS manifest_path
        ORDER BY c.name, c.path
        LIMIT 20
        """,
        {"component": component},
    )
    exact_matches = [
        row for row in matches
        if (row.get("component") or "").casefold() == component.casefold()
        or (row.get("manifest_path") or "").casefold() == component.casefold()
    ]
    candidates = exact_matches or matches
    if not candidates:
        return {
            "status": "component_not_found",
            "component": component,
            "depth": depth,
            "paths": [],
            "message": (
                "This component is not modeled in the graph. Its absence "
                "does not establish that it has no dependencies."
            ),
        }
    if len(candidates) > 1:
        return {
            "status": "ambiguous_component",
            "component": component,
            "depth": depth,
            "matches": candidates,
            "paths": [],
            "message": "Specify a more distinctive component name or path.",
        }

    selected = candidates[0]
    paths = _rows(
        graph,
        """
        MATCH (source:RepoComponent {path: $manifest_path})
              -[declared:DEPENDS_ON]->(package:Package)
              -[:HAS_VERSION]->(version:PackageVersion)
              <-[resolution:HAS_LOCKFILE_RESOLUTION]-(source)
        MATCH path=(version)-[:DEPENDS_ON_VERSION*0..3]->
                   (target:PackageVersion)
        WHERE length(path) <= $depth - 1
          AND resolution.selector =
              package.name + '@' + declared.declared_version
          AND all(
              edge IN relationships(path)
              WHERE edge.lockfile_path = resolution.lockfile_path
          )
        RETURN source.name AS source_component,
               package.name AS direct_package,
               declared.declared_version AS declared_range,
               declared.dependency_type AS dependency_type,
               version.version AS resolved_version,
               length(path) + 1 AS hops,
               [source.name] + [n IN nodes(path) | n.name]
                   AS dependency_path,
               [n IN nodes(path) | n.version] AS versions,
               resolution.lockfile_path AS lockfile,
               resolution.selector AS lockfile_selector,
               [r IN relationships(path) | {
                   dependency_selector: r.dependency_selector,
                   lockfile: r.lockfile_path,
                   optional: r.optional
               }] AS dependency_evidence
        ORDER BY hops DESC, direct_package, target.name
        LIMIT 100
        """,
        {
            "manifest_path": selected["manifest_path"],
            "depth": depth,
        },
    )
    if not paths:
        return {
            "status": "no_paths",
            "component": selected["component"],
            "manifest_path": selected["manifest_path"],
            "depth": depth,
            "paths": [],
            "message": (
                f"No modeled dependency path starts at "
                f"{selected['component']} within {depth} hop(s). This may "
                "mean the dependency is not modeled; it is not proof that "
                "no real dependency exists."
            ),
        }
    return {
        "status": "paths_found",
        "component": selected["component"],
        "manifest_path": selected["manifest_path"],
        "depth": depth,
        "paths": paths,
    }


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
