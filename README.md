# TechCompass

TechCompass creates graph-backed technology update reports for AnythingLLM.
A FastAPI service queries FalkorDB, searches current external sources, and uses
Gemini to explain how findings may relate to project goals and architecture.
The web page streams the checks in progress and renders the completed report.

The graph contains project components and dependencies, source-file and symbol
index data, documentation sections, documentation-backed architecture
evidence, and manually synced external articles and vulnerability advisories.
The web app includes a dependency path visualization, source search, manual
article intake, and an on-demand sync action. The app has no authentication;
keep it on localhost or put it behind a trusted network boundary.

TechCompass's own purpose and discovery objectives are stored separately from
the analyzed AnythingLLM `Project` node. Review or edit
`backend/graph/project_profile.json`, then ingest the profile into the selected
FalkorDB graph with:

```powershell
python -m scripts.graph.ingest_project_profile
```

The agent uses that profile to reason about why external items matter to
TechCompass, while dependency, import, and documentation evidence describes
impact on the analyzed repository. The profile is a maintainer brief; update it
when the product goals change.

## Run an update report

### Requirements

- Python 3.10 or newer and `pip`
- Node.js 18 or newer (only needed to build the static frontend)
- A reachable **deployed FalkorDB instance** containing a TechCompass graph
- A Gemini API key

The repository intentionally excludes `.env`, the downloaded AnythingLLM
source and docs, and generated index data. A fresh clone can connect to an
already-populated FalkorDB graph; it does not include the source corpus needed
to rebuild that graph.

### 1. Configure the backend

From the project root in PowerShell:

```powershell
cd D:\Hackathon\TechCompass
Copy-Item .env.example .env
```

Edit `.env` and set the FalkorDB Cloud endpoint, port, username, password,
graph name, and Gemini API key. Set `FALKORDB_TLS` to match the instance
configuration. For a public deployment, enable TLS on FalkorDB and set this to
`true`; do not send database credentials over an unencrypted connection. The
default model is `gemini-3.8-flash`; set `GEMINI_MODEL` to another model
available to your API key if needed.

Do not commit `.env`, paste secret values into chat, or put database/Gemini
credentials in frontend variables. `.env.example` contains placeholders only.

Create an environment and install the API dependencies:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-api.txt
```

Start the API:

```powershell
python -m uvicorn backend.api.main:app --host 127.0.0.1 --port 8000 --reload
```

The API loads `.env` from the project root. Check that the graph connection
worked at <http://127.0.0.1:8000/health/ready>; a successful response includes
`"status": "ready"` and the node count. The liveness endpoint is
<http://127.0.0.1:8000/health/live>.

### 2. Build and open the report UI

In a second PowerShell terminal, from the project root:

```powershell
$env:TECHCOMPASS_API_URL = "http://127.0.0.1:8000"
Set-Location frontend
npm run build
python -m http.server 5173 --bind 127.0.0.1 --directory dist
```

Open <http://127.0.0.1:5173>, choose a lookback window from 1 to 90 days, and
select **Check for updates**. The page streams graph loading, search planning,
source checks, graph impact mapping, and report generation. The report groups
findings by project objectives and includes external links and graph evidence.

Stop the API and static server with `Ctrl+C` in their respective terminals.

## Test the backend and API

Install the full dependencies if you want to run the test suite:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

With the API running, check readiness:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health/ready"
```

## Available API routes

The backend exposes health checks and a streaming update-report endpoint.

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/health/live` | Process liveness |
| `GET` | `/health/ready` | FalkorDB readiness and node count |
| `POST` | `/api/updates/run` | Run the fixed AnythingLLM update job with a 1–90 day lookback |

Send a JSON request and read the server-sent event stream:

```json
{"days":30}
```

The stream includes progress events while checks run, then a report event with
the Markdown report and a `tools_used` search audit.

### External discovery

The agent derives its web searches from the analyzed project's graph context,
then searches with Tavily and falls back to DuckDuckGo or Bing via DDGS if
Tavily fails or returns no dated matches. It verifies launch dates against
official sources, explains relevance and potential project impact separately,
and keeps source links in the response. Security tools for OSV, GitHub
Advisories, and NVD remain available for explicit security questions.

Backend search configuration is server-side:

```text
TAVILY_API_KEY=your-server-side-tavily-key
GITHUB_TOKEN=your-optional-github-token
NVD_API_KEY=your-server-side-nvd-key
```

Keep keys in the backend environment, never in frontend configuration. The
graph-aware web search requires a Tavily API key. Public GitHub requests
use the unauthenticated rate limit when no token is configured.

## Building the AnythingLLM context graph

The context graph is designed for discovery and impact analysis across goals,
capabilities, workflows, architecture patterns, technologies, components,
code, and cited documentation. Package nodes remain available for exact
dependency and security checks, but they are supporting evidence rather than
the default way to discover technology updates.

The reviewed project context lives in
[`backend/graph/anythingllm_context.json`](backend/graph/anythingllm_context.json).
It is grounded in the checked-out AnythingLLM README and official docs. The
ingester links goals and capabilities to documentation evidence and candidate
source files. Code-file links are explicitly marked as candidate mappings; a
directory match does not prove that every linked file implements the capability.
Edit the context profile when the upstream repository or docs establish a new
capability, and retain its source paths so the graph can show why it believes
the claim.

The node/relationship model, impact-assessment guidance, and ordered build
stages are documented in
[`backend/graph/GRAPH_MODEL.md`](backend/graph/GRAPH_MODEL.md). The canonical
pipeline order is defined in `scripts/graph/build.py`; the PowerShell rebuild
script handles graph isolation and delegates the ordered stages to that runner.

The reviewed AnythingLLM map currently spans eight objectives and seventeen
capabilities, including document ingestion and sync, retrieval and vector
storage, agents and MCP, model routing, local deployment, memory, meeting and
media workflows, access controls, APIs, and operational telemetry. External
discovery reserves a search for each objective before adding extra query angles,
balances results across those searches, and reports result counts by query and
project area. It corroborates leading candidates with follow-up searches and
filters AnythingLLM's own domains and repository from external findings. The
search window filters publication dates; a launch is called confirmed only
when an official source establishes its release or availability date.

Optional manual connectivity checks live in `scripts/` and are not part of the
application startup:

```powershell
.\.venv\Scripts\python.exe scripts\check_falkordb.py
.\.venv\Scripts\python.exe scripts\check_gemini.py
```

To refresh the reviewed context in the currently configured graph without
rebuilding repository indexes, run:

```powershell
.\.venv\Scripts\python.exe -m scripts.graph.ingest_project_context
```

### One-command setup from a fresh TechCompass checkout

Configure `.env` first with reachable FalkorDB credentials and graph settings.
From the TechCompass root, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\graph\setup_graph.ps1
```

The script creates `.venv` if needed and ensures `requirements.txt` is
installed, then clones the analyzed source and its official docs into
`data/source/anything-llm/` and `data/source/anythingllm-docs/`. Existing Git
checkouts at those paths are reused without pulling or overwriting local work;
non-Git folders and mismatched remotes cause a clear stop. It then runs the
full clean graph build and, only after success, points `FALKORDB_GRAPH` in
`.env` at the new graph. Restart the API and verify `/health/ready` afterward.

To build a new graph while leaving the graph configured in `.env` untouched,
add `-KeepCurrentGraph` to the command. The new graph name is printed during
the run and can be activated later by setting `FALKORDB_GRAPH` in `.env`.

Graph ingestion scripts are maintainer workflows, not part of report startup.
They require the AnythingLLM repository and docs under `data/source/`, plus
FalkorDB connection settings in `.env`. The complete rebuild uses a new,
timestamped graph name by default and writes extraction indexes under a
graph-specific directory. It refuses to use the graph currently configured in
`.env`, preserving the existing graph and index files for comparison/rollback.

From the project root, run:

```powershell
.\scripts\graph\rebuild_context_graph.ps1
```

To pick a graph name explicitly:

```powershell
.\scripts\graph\rebuild_context_graph.ps1 -GraphName anythingllm-context-v2-demo
```

The script ingests repository structure, manifests, docs, symbols, resolved
imports, reviewed project context, candidate doc/code links, and lockfile
versions. It also ingests TechCompass's own product profile separately from
the analyzed AnythingLLM project. The API continues using the graph in `.env`
until you deliberately set `FALKORDB_GRAPH` to the new graph and restart it.
After the rebuild, validate `/health/ready` and run an update report to confirm
the new context is available.

## Deployment

The recommended hosting split is a static frontend on Vercel, the persistent
FastAPI service on Render, and FalkorDB Cloud for the graph. Follow
[`DEPLOYMENT.md`](DEPLOYMENT.md) for Render/Vercel environment variables,
deployment steps, and production checks. Production should use HTTPS for both
the browser/API and API/FalkorDB connections.

## Security notes

- **There is no app authentication.** Anyone who can reach the API can read
  graph data and invoke the agent, including its external search tools.
  Keep the API on localhost or restrict access using a private network or
  trusted gateway before exposing it beyond your machine.
- Keep `.env`, FalkorDB credentials, and Gemini keys out of Git and out of Vercel
  frontend configuration.
- `.env` and `data/` are ignored by `.gitignore`; `.env.example` is safe
  placeholder configuration.
- The managed FalkorDB free instance has backups/HA disabled according to the
  setup notes; arrange backups before treating it as the sole graph copy.

## Project layout

```text
backend/
  agent/       Gemini graph-tool agent
  api/         Public FastAPI routes
  graph/       FalkorDB runtime access, reviewed profiles, and graph model docs
frontend/      Static update-report UI and Vercel configuration
scripts/graph/ Graph creation and ingestion pipeline
tests/         API access and graph behavior tests
```
