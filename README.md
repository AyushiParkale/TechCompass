# TechCompass

TechCompass is a small, private chat interface for exploring a knowledge graph
built from the AnythingLLM repository. A FastAPI service queries FalkorDB and
uses Groq to answer questions; the web chat shows the graph tools used for each
answer.

The graph contains project components and dependencies, source-file and symbol
index data, documentation sections, and documentation-backed architecture
evidence. It is not a general-purpose code execution agent and does not fetch
article URLs automatically.

## Try the chat

### Requirements

- Python 3.10 or newer and `pip`
- Node.js 18 or newer (only needed to build the static frontend)
- A reachable **deployed FalkorDB instance** containing a TechCompass graph
- A Groq API key

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
graph name, and Groq API key. Set `FALKORDB_TLS` to match the instance
configuration. For a public deployment, enable TLS on FalkorDB and set this to
`true`; do not send database credentials over an unencrypted connection.

Set a strong private chat password and a separate signing secret of at least
32 characters. To generate a signing secret in PowerShell:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Do not commit `.env`, paste secret values into chat, or put database/Groq
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

### 2. Build and open the chat UI

In a second PowerShell terminal, from the project root:

```powershell
$env:TECHCOMPASS_API_URL = "http://127.0.0.1:8000"
Set-Location frontend
npm run build
python -m http.server 5173 --directory dist
```

Open <http://127.0.0.1:5173>, sign in with the value of
`TECHCOMPASS_ACCESS_PASSWORD`, and ask a question. For example:

> What agent-related capabilities are documented in this project?

Expand **Evidence tools used** below a reply to inspect the graph tools
requested. The token is kept in page memory; refreshing the page requires
signing in again. This small interface displays the answer as plain text; it
does not render model output as HTML.

Stop the API and static server with `Ctrl+C` in their respective terminals.

## Test the backend and API protection

Install the full dependencies if you want to run the test suite:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The tests cover login token expiration/signatures, failed-login throttling,
protection of graph endpoints, and rejection of unconfigured browser origins.
They do not require a FalkorDB connection.

With the API running, test login and an authenticated read-only graph endpoint:

```powershell
$login = Invoke-RestMethod `
    -Uri "http://127.0.0.1:8000/api/auth/login" `
    -Method Post `
    -ContentType "application/json" `
    -Body (@{ password = "YOUR_TECHCOMPASS_ACCESS_PASSWORD" } | ConvertTo-Json)

$headers = @{ Authorization = "Bearer $($login.access_token)" }
Invoke-RestMethod `
    -Uri "http://127.0.0.1:8000/api/graph/summary" `
    -Headers $headers
```

Replace the placeholder in the command locally; never commit a real password
into this README or a script.

## Available API routes

The health routes are public. Login issues a short-lived signed bearer token.
All graph and agent routes require that token.

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/health/live` | Process liveness |
| `GET` | `/health/ready` | FalkorDB readiness and node count |
| `POST` | `/api/auth/login` | Exchange the shared password for a token |
| `GET` | `/api/graph/summary` | Counts for indexed graph node types |
| `GET` | `/api/graph/project` | Indexed project components |
| `GET` | `/api/graph/dependencies?component=server` | Declared dependencies for a component |
| `GET` | `/api/graph/package-usage?package=react` | Files directly importing a package |
| `GET` | `/api/graph/architecture?q=agent` | Search documentation-backed architecture evidence |
| `GET` | `/api/graph/package-impact?package=react` | Compare declarations with direct imports |
| `POST` | `/api/agent/ask` | Ask the Groq agent to investigate the graph |

The agent is evidence-grounded but can miss relevant graph records or make
incomplete tool selections. Check the returned `tools_used` trace and evidence;
absence from results is not proof that a capability is missing.

## Refreshing the graph

Graph ingestion scripts are maintainer workflows, not part of the chat startup.
They need the AnythingLLM source/docs under the expected `data/source/` paths
and an extracted code index under `data/index/`. That input data is deliberately
not included in this public repository. Run ingestion scripts from the project
root only after restoring/providing the source corpus and checking the target
FalkorDB instance:

1. Extract and ingest source files and symbols.
2. Ingest components and dependencies, docs, and architecture evidence.
3. Resolve imports and link documentation to code.
4. Parse/ingest lockfile versions and run the lockfile verifier.

All graph scripts use the configured remote FalkorDB connection; loopback
database hosts are rejected. Treat ingestion as writes to the configured
deployed graph, not as a local test operation.

## Deployment

The recommended hosting split is a static frontend on Vercel, the persistent
FastAPI service on Render, and FalkorDB Cloud for the graph. Follow
[`DEPLOYMENT.md`](DEPLOYMENT.md) for Render/Vercel environment variables,
deployment steps, and production checks. Production should use HTTPS for both
the browser/API and API/FalkorDB connections.

## Security notes

- Keep `.env`, FalkorDB credentials, Groq keys, and the shared chat password
  out of Git and out of Vercel frontend configuration.
- `.env` and `data/` are ignored by `.gitignore`; `.env.example` is safe
  placeholder configuration.
- The current chat uses one shared password, not individual accounts.
- Failed logins are rate-limited per API process. For broader public use, add a
  durable rate limiter, individual identity, and usage monitoring.
- The managed FalkorDB free instance has backups/HA disabled according to the
  setup notes; arrange backups before treating it as the sole graph copy.

## Project layout

```text
backend/
  agent/       Groq graph-tool agent
  api/         FastAPI routes and password/token authentication
  graph/       FalkorDB connection, query, extraction, and ingestion scripts
frontend/      Minimal static chat UI and Vercel configuration
tests/         Authentication and API access tests
```
