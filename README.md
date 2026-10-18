# TechCompass

TechCompass is a small chat interface for exploring a knowledge graph
built from the AnythingLLM repository. A FastAPI service queries FalkorDB and
uses Groq to answer questions; the web chat shows the graph tools used for each
answer.

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
python backend\graph\ingest_project_profile.py
```

The agent uses that profile to reason about why external items matter to
TechCompass, while dependency, import, and documentation evidence describes
impact on the analyzed repository. The profile is a maintainer brief; update it
when the product goals change.

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
python -m http.server 5173 --bind 127.0.0.1 --directory dist
```

Open <http://127.0.0.1:5173> and ask a question. For example:

> What agent-related capabilities are documented in this project?

Expand **Evidence tools used** below a reply to inspect the graph tools
requested. This small interface displays the answer as plain text; it does not
render model output as HTML.

Stop the API and static server with `Ctrl+C` in their respective terminals.

## Test the backend and API

Install the full dependencies if you want to run the test suite:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

The tests cover public API access, depth-bounded component traces,
missing-path responses, Yarn lockfile dependency extraction, safe external URL
handling, feed/article parsing, and rejection of unconfigured browser origins.
They do not require a FalkorDB connection.

With the API running, test a public read-only endpoint:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/graph/summary"
```

## Available API routes

All API routes are public and require no password or bearer token.

| Method | Route | Description |
| --- | --- | --- |
| `GET` | `/health/live` | Process liveness |
| `GET` | `/health/ready` | FalkorDB readiness and node count |
| `GET` | `/api/graph/summary` | Counts for indexed graph node types |
| `GET` | `/api/graph/project` | Indexed project components |
| `GET` | `/api/graph/dependencies?component=server` | Declared dependencies for a component |
| `GET` | `/api/graph/package-usage?package=react` | Files directly importing a package |
| `GET` | `/api/graph/architecture?q=agent` | Search documentation-backed architecture evidence |
| `GET` | `/api/graph/package-impact?package=react` | Compare declarations with direct imports |
| `GET` | `/api/graph/component-impact?component=server&depth=4` | Trace declared and transitive package dependencies up to four hops, with lockfile evidence |
| `GET` | `/api/graph/visualization?component=anything-llm-server&depth=4` | Dependency paths and related external evidence for the graph explorer |
| `GET` | `/api/knowledge/search?q=langchain` | Search ingested advisories and articles |
| `POST` | `/api/knowledge/preview` | Fetch a bounded live sample from each connector without writing to FalkorDB |
| `POST` | `/api/knowledge/sync` | Manually sync security, research, release, HN, and configured RSS/Atom sources |
| `POST` | `/api/knowledge/articles` | Fetch and index a manually submitted public HTTPS article URL |
| `POST` | `/api/agent/ask` | Ask the Groq agent to investigate the graph |

The agent is evidence-grounded but can miss relevant graph records or make
incomplete tool selections. Check the returned `tools_used` trace and evidence;
absence from results is not proof that a capability is missing.
Component impact distinguishes an unmodeled component from a modeled component
with no path at the requested depth. Neither result proves the real codebase has
no such dependency. Compare `depth=1` and `depth=4` for the same component to
see direct versus transitive package paths and the lockfile evidence behind
each hop.

### External knowledge sync

Use **Test connectors (read-only)** to inspect live API responses before
syncing. The preview shows the graph-derived package versions, package keyword,
research term, configured feeds and release mappings used for the requests,
plus each source's status, result count, and up to three normalized records.
Choose Small or Medium to adjust the sample: Small checks up to five installed
versions, one package keyword/research term, one GitHub advisory page, and one
release mapping; Medium checks up to 20 versions, three package keywords and
research terms, two advisory pages, and three release mappings. NVD and arXiv
pace requests, so Medium can take longer. This preview does not upsert records
into FalkorDB. `/api/knowledge/preview` uses the same source fetchers as sync,
with reduced input limits.

The Google News controls accept a search query, language, and region. The same
settings are used for preview and the next explicit sync; clear the query to
skip Google News. The feed returns matching headlines and links from Google
News RSS and does not guarantee exhaustive tech coverage.

For project-aware live discovery, ask the chat to find current or upcoming
news relevant to this project. The agent first reads the project, component,
dependency, and documentation context from FalkorDB, then chooses among
source-specific API tools such as Google News, OSV, GitHub Advisories, NVD,
arXiv, Crossref, Hacker News, configured RSS, and configured GitHub releases.
The query terms come from the graph evidence for that request, not a fixed
technology keyword list. Each live-source tool stores returned records in
FalkorDB and reports the sources and links used. If the graph does not contain
an explicit business goal, the agent is instructed to say so rather than infer
one.

Use **Sync external sources** to fetch OSV advisories, recent GitHub npm
advisories, NVD CVE keyword results, arXiv and Crossref research metadata,
Hacker News stories, configured GitHub releases, and any configured RSS/Atom
feeds. Research queries use package names from the graph, ordered by project
usage; no AI/LLM package-name allowlist is applied. Set
`TECHCOMPASS_RESEARCH_TERMS` to explicitly override these with targeted topics. Sync
runs only when explicitly requested; there is no background scheduler. A failed
source is reported individually while successfully fetched sources can still
be ingested.
Before syncing, configure the API process to use the intended graph
(`FALKORDB_GRAPH=techcompass-rebuild-...`) and restart it. Sync writes to that
configured graph; it does not switch graphs or clear existing records.

Optional backend environment settings:

```text
TECHCOMPASS_RSS_FEEDS=https://example.org/feed.xml,https://example.net/rss.xml
NVD_API_KEY=your-server-side-nvd-key
GITHUB_TOKEN=your-optional-github-token
TECHCOMPASS_GITHUB_RELEASE_REPOS=langchain=langchain-ai/langchainjs,openai=openai/openai-node
TECHCOMPASS_RESEARCH_TERMS=langchain,openai,retrieval augmented generation
```

Configure no more than 20 HTTPS RSS/Atom feed URLs. Without an NVD API key, a
sync queries only five package keywords to respect the anonymous service rate
limit. Store keys only in the backend environment, never in frontend settings.
Without a GitHub token, public endpoints use GitHub's unauthenticated rate
limits; the token is optional and must stay server-side. GitHub release targets
must be explicitly mapped as `npm-package=owner/repository` because the
dependency graph does not currently store upstream repository URLs. At most 20
repositories are queried, with up to ten latest stable releases each. arXiv
requests are paced, and Crossref requests are limited to five terms and ten
results per term. The anonymous NVD check is paced and may take about 25
seconds.
The graph records exact OSV package-version matches as `AFFECTS_VERSION`;
GitHub affected ranges and NVD/RSS text matches are evidence to review, not
proof that the indexed lockfile version is vulnerable. Articles, papers, and
stories are linked to packages only when their title or summary mentions a
modeled package. GitHub release links use the explicit package/repository
mapping, not vulnerability evidence. arXiv and Crossref contribute metadata
and abstracts only, not full paper text. Review each source's license and reuse
terms before redistributing its content; RSS publishers also set their own
terms. Hacker News collection is limited to matching story results, not
comments.

The **Add a public article or advisory URL** form accepts HTTPS URLs only. URL
fetching blocks non-public DNS addresses, non-default ports, and redirects,
limits responses to 2 MB, and stores only extracted title/summary text plus
source metadata. It does not bypass paywalls or fetch arbitrary linked pages.
Submitted HTML and feed content are untrusted evidence, not instructions to the
agent.

The graph explorer draws up to eight dependency paths (maximum depth four) and
lists linked external evidence. Select a component/depth and use the source
search to inspect already ingested records. Graph and external-knowledge API
routes are public. The default CORS configuration is a browser policy, not
authentication; direct API clients can still call the endpoints.

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

To rebuild without disturbing the currently configured graph, set
`FALKORDB_GRAPH` in the PowerShell session to a **new, unused graph name**
before running the ingestion workflow. FalkorDB creates the graph on first
write, so this clean namespace prevents stale records from the previous build
while keeping that graph available for comparison or rollback. Do not use the
existing graph name for a parallel rebuild.

Run the complete workflow from the project root in this order:

```powershell
$env:FALKORDB_GRAPH = "techcompass-rebuild-20261017"
python backend\graph\extract_codebase.py
python backend\graph\ingest_anythingllm.py
python backend\graph\ingest_docs.py
python backend\graph\ingest_symbols.py
python backend\graph\resolve_imports.py
python backend\graph\ingest_architecture_knowledge.py
python backend\graph\link_docs_to_code.py
python backend\graph\parse_yarn_lockfiles.py
python backend\graph\ingest_lockfile_versions.py
python backend\graph\verify_lockfile_versions.py
```

Replace the example graph name with a unique name if it already exists.
`parse_yarn_lockfiles.py` preserves package dependency selectors from each
lockfile, and `ingest_lockfile_versions.py` resolves those selectors into
version-specific `DEPENDS_ON_VERSION` edges. These complement component
manifest declarations and retain the lockfile selector/version chain for
multi-hop explanations. To run the API against the new graph, set the same
`FALKORDB_GRAPH` value in the API's PowerShell session before startup. Verify
the graph with `/health/ready` and `/api/graph/summary` before switching any
deployed service to it.

## Deployment

The recommended hosting split is a static frontend on Vercel, the persistent
FastAPI service on Render, and FalkorDB Cloud for the graph. Follow
[`DEPLOYMENT.md`](DEPLOYMENT.md) for Render/Vercel environment variables,
deployment steps, and production checks. Production should use HTTPS for both
the browser/API and API/FalkorDB connections.

## Security notes

- **There is no app authentication.** Anyone who can reach the API can read
  graph data, invoke the Groq agent, trigger external syncs, and submit URLs.
  Keep the API on localhost or restrict access using a private network or
  trusted gateway before exposing it beyond your machine.
- Keep `.env`, FalkorDB credentials, and Groq keys out of Git and out of Vercel
  frontend configuration.
- `.env` and `data/` are ignored by `.gitignore`; `.env.example` is safe
  placeholder configuration.
- The managed FalkorDB free instance has backups/HA disabled according to the
  setup notes; arrange backups before treating it as the sole graph copy.

## Project layout

```text
backend/
  agent/       Groq graph-tool agent
  api/         Public FastAPI routes
  graph/       FalkorDB connection, query, extraction, and ingestion scripts
frontend/      Minimal static chat UI and Vercel configuration
tests/         API access and graph behavior tests
```
