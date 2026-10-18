import logging
import os
import json
from contextlib import asynccontextmanager
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from falkordb import FalkorDB
from pydantic import BaseModel, Field

from backend.agent.graph_agent import run_graph_agent
from backend.graph.graph_connection import connection_options, is_local_host

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("techcompass")

client = None
graph = None


def connect_graph():
    host = os.getenv("FALKORDB_HOST", "").strip()
    password = os.getenv("FALKORDB_PASSWORD", "")
    port = int(os.getenv("FALKORDB_PORT", "6379"))
    graph_name = os.getenv("FALKORDB_GRAPH", "techcompass")
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
        password=password,
        **connection_options(),
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
    title="TechCompass Project Update API",
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


class UpdateReportRequest(BaseModel):
    days: int = Field(default=30, ge=1, le=90)


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


@app.post("/api/updates/run")
def run_project_update(request: UpdateReportRequest):
    if graph is None:
        raise HTTPException(
            status_code=503,
            detail="Graph database is not connected",
        )

    days = request.days
    question = (
        "Find external technology launches, releases, and capability updates "
        f"from the last {days} days that could materially benefit or affect "
        "AnythingLLM. Use the project graph to cover its objectives and "
        "architecture. Segment the report by project objective/capability, "
        "explain what each external technology does and the plausible impact "
        "on AnythingLLM's implementation, cite external sources and graph "
        "evidence, and distinguish article publication dates from verified "
        "launch dates."
    )
    events: Queue = Queue()

    def publish_progress(progress: dict):
        events.put({"type": "progress", **progress})

    def run_report():
        try:
            report = run_graph_agent(
                graph, question, progress_callback=publish_progress,
            )
            events.put({"type": "report", "report": report, "days": days})
        except Exception:
            logger.exception("TechCompass project update failed")
            events.put({
                "type": "error",
                "message": "The update check failed. See the backend log for details.",
            })
        finally:
            events.put(None)

    def stream_events():
        Thread(target=run_report, daemon=True).start()
        yield "data: " + json.dumps({
            "type": "progress",
            "stage": "starting",
            "status": "running",
            "detail": f"Starting the {days}-day AnythingLLM update check.",
        }) + "\n\n"
        while True:
            try:
                event = events.get(timeout=15)
            except Empty:
                yield ": keep-alive\n\n"
                continue
            if event is None:
                break
            yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
