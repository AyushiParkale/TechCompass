
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from falkordb import FalkorDB
from pydantic import BaseModel, Field

from backend.agent.graph_agent import run_graph_agent
from backend.graph.graph_connection import is_local_host
from backend.graph.external_knowledge import (
    ExternalSourceError,
    external_evidence_for_packages,
    ingest_article_url,
    preview_external_knowledge,
    search_external_knowledge,
    sync_external_knowledge,
)
from backend.graph.query_service import (
    find_package_usage,
    get_component_dependencies,
    search_architecture_knowledge,
    search_project,
    trace_component_dependencies,
    trace_package_impact,
)

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("techcompass")

client = None
graph = None


def connect_graph():
    host = os.getenv("FALKORDB_HOST", "").strip()
    password = os.getenv("FALKORDB_PASSWORD", "")
    username = os.getenv("FALKORDB_USERNAME", "").strip() or None
    port = int(os.getenv("FALKORDB_PORT", "6379"))
    graph_name = os.getenv("FALKORDB_GRAPH", "techcompass")
    tls_setting = os.getenv("FALKORDB_TLS")
    if tls_setting is None:
        use_tls = True
    else:
        normalized_tls = tls_setting.strip().lower()
        if normalized_tls not in {"1", "true", "yes", "0", "false", "no"}:
            raise ValueError("FALKORDB_TLS must be set to true or false")
        use_tls = normalized_tls in {"1", "true", "yes"}

    if not host or not password:
        raise RuntimeError(
            "Set FALKORDB_HOST and FALKORDB_PASSWORD in .env"
        )
    if is_local_host(host):
        raise RuntimeError(
            "FALKORDB_HOST must point to the deployed FalkorDB instance; "
            "local database connections are disabled"
        )

    db = FalkorDB(
        host=host,
        port=port,
        username=username,
        password=password,
        ssl=use_tls,
        ssl_cert_reqs="required" if use_tls else None,
        ssl_check_hostname=use_tls,
        socket_connect_timeout=5,
        socket_timeout=10,
    )

    selected_graph = db.select_graph(graph_name)

    # A fixed, read-only query verifies connectivity and graph access.
    result = selected_graph.ro_query(
        "MATCH (n) RETURN count(n) AS node_count"
    )
    node_count = int(result.result_set[0][0])

    return db, selected_graph, node_count


@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, graph

    try:
        client, graph, node_count = connect_graph()
        logger.info(
            "Connected to FalkorDB graph; nodes=%s", node_count
        )
    except Exception:
        # Keep the API alive so readiness reports the failure.
        logger.exception("FalkorDB connection failed")

    yield

    graph = None
    client = None


app = FastAPI(
    title="TechCompass Graph API",
    version="0.1.0",
    lifespan=lifespan,
)

allowed_origins = [
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
if not allowed_origins:
    raise RuntimeError("FRONTEND_ORIGINS must contain at least one origin")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class AgentQuestion(BaseModel):
    question: str = Field(min_length=3, max_length=1500)


class ArticleSubmission(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class GoogleNewsOptions(BaseModel):
    query: str = Field(default="", max_length=250)
    language: str = Field(
        default="en-US",
        pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z]{2})?$",
    )
    country: str = Field(default="US", pattern=r"^[A-Za-z]{2}$")


class KnowledgePreviewRequest(BaseModel):
    size: str = Field(default="small", pattern=r"^(small|medium)$")
    google_news: GoogleNewsOptions = Field(default_factory=GoogleNewsOptions)


class KnowledgeSyncRequest(BaseModel):
    google_news: GoogleNewsOptions = Field(default_factory=GoogleNewsOptions)


@app.get("/health/live")
def liveness():
    return {"status": "alive"}


@app.get("/health/ready")
def readiness():
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        result = graph.ro_query(
            "MATCH (n) RETURN count(n) AS node_count"
        )
        return {
            "status": "ready",
            "graph": os.getenv("FALKORDB_GRAPH", "techcompass"),
            "node_count": int(result.result_set[0][0]),
        }
    except Exception:
        logger.exception("Graph readiness query failed")
        raise HTTPException(
            status_code=503,
            detail="Graph database query failed",
        )


@app.get("/api/graph/summary")
def graph_summary():
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    # Static labels only: no user-provided Cypher or label injection.
    labels = [
        "Project",
        "RepoComponent",
        "Package",
        "PackageVersion",
        "SourceFile",
        "Symbol",
        "Document",
        "DocSection",
        "Evidence",
        "Technology",
        "Approach",
        "Constraint",
        "ExternalItem",
        "ExternalReference",
    ]

    try:
        counts = {}
        for label in labels:
            result = graph.ro_query(
                f"MATCH (n:{label}) RETURN count(n) AS count"
            )
            counts[label] = int(result.result_set[0][0])

        return {
            "graph": os.getenv("FALKORDB_GRAPH", "techcompass"),
            "node_counts": counts,
            "total_indexed_nodes": sum(counts.values()),
        }
    except Exception:
        logger.exception("Graph summary query failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to query graph summary",
        )


@app.get("/api/graph/project")
def project_overview():
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return {"components": search_project(graph)}
    except Exception:
        logger.exception("Project query failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to query project graph",
        )


@app.get("/api/graph/dependencies")
def component_dependencies(
    component: str = Query(min_length=1, max_length=100),
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return {
            "component": component,
            "dependencies": get_component_dependencies(graph, component),
        }
    except Exception:
        logger.exception("Dependency query failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to query dependencies",
        )


@app.get("/api/graph/package-usage")
def package_usage(
    package: str = Query(min_length=1, max_length=200),
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return {
            "package": package,
            "usage": find_package_usage(graph, package),
        }
    except Exception:
        logger.exception("Package usage query failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to query package usage",
        )


@app.get("/api/graph/architecture")
def architecture_search(
    q: str = Query(min_length=1, max_length=200),
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return {
            "query": q,
            "results": search_architecture_knowledge(graph, q),
        }
    except Exception:
        logger.exception("Architecture query failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to query architecture knowledge",
        )


@app.get("/api/graph/package-impact")
def package_impact(
    package: str = Query(min_length=1, max_length=200),
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return {
            "package": package,
            "impact": trace_package_impact(graph, package),
        }
    except Exception:
        logger.exception("Package impact query failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to query package impact",
        )


@app.get("/api/graph/component-impact")
def component_impact(
    component: str = Query(min_length=1, max_length=100),
    depth: int = Query(default=1, ge=1, le=4),
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return trace_component_dependencies(graph, component, depth)
    except Exception:
        logger.exception("Component impact query failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to trace component dependencies",
        )


@app.get("/api/graph/visualization")
def graph_visualization(
    component: str = Query(min_length=1, max_length=100),
    depth: int = Query(default=4, ge=1, le=4),
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        trace = trace_component_dependencies(graph, component, depth)
        package_versions: dict[str, set[str]] = {}
        for path in trace.get("paths", []):
            for name, version in zip(
                path.get("dependency_path", [])[1:],
                path.get("versions", []),
            ):
                if name and version:
                    package_versions.setdefault(name, set()).add(version)
        return {
            "trace": trace,
            "external_evidence": external_evidence_for_packages(
                graph,
                package_versions,
            ),
        }
    except Exception:
        logger.exception("Graph visualization query failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to load graph visualization",
        )


@app.get("/api/knowledge/search")
def external_knowledge_search(
    q: str = Query(min_length=2, max_length=200),
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return {
            "query": q,
            "results": search_external_knowledge(graph, q),
        }
    except Exception:
        logger.exception("External knowledge search failed")
        raise HTTPException(
            status_code=503,
            detail="Unable to search external knowledge",
        )


@app.post("/api/knowledge/sync")
def sync_knowledge(request: KnowledgeSyncRequest | None = Body(default=None)):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return sync_external_knowledge(
            graph,
            google_news=(request.google_news.model_dump() if request else None),
        )
    except Exception:
        logger.exception("External knowledge sync failed")
        raise HTTPException(
            status_code=502,
            detail="External knowledge sync failed. Check server logs.",
        )


@app.post("/api/knowledge/preview")
def preview_knowledge(request: KnowledgePreviewRequest | None = Body(default=None)):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return preview_external_knowledge(
            graph,
            google_news=(
                request.google_news.model_dump() if request else None
            ),
            size=request.size if request else "small",
        )
    except Exception:
        logger.exception("External knowledge preview failed")
        raise HTTPException(
            status_code=502,
            detail="External knowledge preview failed. Check server logs.",
        )

@app.post("/api/knowledge/articles")
def submit_article(
    request: ArticleSubmission,
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return ingest_article_url(graph, request.url)
    except ExternalSourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        logger.exception("Article ingestion failed")
        raise HTTPException(
            status_code=502,
            detail="Article ingestion failed. Check server logs.",
        )


@app.post("/api/agent/ask")
def ask_agent(
    request: AgentQuestion,
):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    try:
        return run_graph_agent(graph, request.question)
    except Exception:
        logger.exception("TechCompass agent request failed")
        raise HTTPException(
            status_code=502,
            detail="Agent request failed. Check server logs.",
        )
