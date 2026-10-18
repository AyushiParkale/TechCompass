# TechCompass — Continuous Project Intelligence Dashboard

**TechCompass** is a continuous, graph-native technology intelligence platform designed to monitor external technical signals (release notes, research papers, engineering blogs, security advisories) and analyze their specific relevance, architectural impact, and integration paths against an internal software codebase.

The primary codebase indexed by this instance is **AnythingLLM**. Its complete architecture—including source files, external packages, functional capabilities, operational constraints, and strategic goals—is structured as a knowledge graph inside **FalkorDB**. A **FastAPI** backend integrated with a **Groq-powered LLM agent** runs graph-backed reasoning pipelines to generate explainable, actionable engineering recommendations.


## 📋 Table of Contents
1. [Product Overview & Architecture](#-product-overview--architecture)
2. [Key Value Drivers & Ingestion Workflow](#-key-value-drivers--ingestion-workflow)
3. [Core Capabilities & Dashboard Views](#-core-capabilities--dashboard-views)
4. [Graph Data Model & Visualizations](#-graph-data-model--visualizations)
5. [Cypher Queries & Graph Algorithms](#-cypher-queries--graph-algorithms)
6. [File Structure](#-file-structure)
7. [API Specification & Endpoint Contract](#-api-specification--endpoint-contract)
8. [Getting Started & Local Setup](#-getting-started--local-setup)
9. [Environment Variables](#-environment-variables)
10. [Security & Design Principles](#-security--design-principles)


## 💡 Product Overview & Architecture

Modern engineering teams struggle with context switching when evaluating new software libraries, security advisories, or AI paper releases. Manual copy-pasting of articles into generic LLMs lacks project-specific context and produces hallucinated or non-actionable suggestions.

TechCompass bridges this gap by grounding external intelligence directly in your project's architectural graph topology.

```
                      +-----------------------------+
                      |   External Signal Sources   |
                      |  (RSS, GitHub Releases,     |
                      |   ArXiv, Engineering Blogs) |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      |  TechCompass Ingestion      |
                      |  & Auto Scanner Engine      |
                      +--------------+--------------+
                                     |
                                     v
+-----------------------+     +-----------------------------+
| AnythingLLM Codebase  | --> | FalkorDB Knowledge Graph    |
| (Nodes & Constraints) |     | (Entities & Relationships)  |
+-----------------------+     +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      | FastAPI Backend +           |
                      | Groq Reasoning Agent        |
                      +--------------+--------------+
                                     |
                                     v
                      +-----------------------------+
                      | TechCompass Web App         |
                      | (Single-Page Dashboard)     |
                      +-----------------------------+
```

## ⚡ Key Value Drivers & Ingestion Workflow

### Key Value Drivers
- **Continuous Ecosystem Scanning**: Ingests technology updates automatically across configured developer feeds.
- **Graph-Native Reasoning**: Contextualizes every external signal against actual codebase nodes, packages, and architecture constraints.
- **Explainable Assessments**: Replaces vague AI suggestions with concrete evidence trees, affected file paths, and dependency graphs.
- **Structured Action Blueprints**: Translates high-impact signals into step-by-step adoption proposals without executing destructive codebase changes.

### Ingestion Workflow
1. **Discovery**: Scanner engine periodically scans configured feeds for new articles, release notes, or advisories.
2. **Entity Context Retrieval**: The backend extracts entity mentions (e.g., packages, frameworks, patterns) and queries FalkorDB for matching subgraphs.
3. **Groq Agent Evaluation**: The LLM agent analyzes the signal against retrieved subgraphs to calculate relevance scores, capability gaps, and risk levels.
4. **Dashboard Synchronization**: Results are saved and surfaced to the UI with urgency badges, graph evidence paths, and suggested next steps.


## 🖥 Core Capabilities & Dashboard Views

### 1. Signal Feed (Landing Page)
- **Live Metrics**: Real-time counter cards tracking new signals, high-relevance discoveries, unassigned opportunities, and active action plans.
- **Multi-Filter Explorer**: Instant filtering by relevance score, source type (`Blog`, `Release`, `ArXiv`, `Advisory`), topic tag, and processing state.
- **Direct Actions**: Convert signals into formal Action Plans, mark for review, or dismiss irrelevant entries.

### 2. Signal Analysis Detail
- **Extracted Claims**: Key claims synthesized from original technical publications.
- **Project Relevance Score**: Calculated relevance percentage, confidence level, and detailed justification.
- **Graph Evidence Panel**: Explicit list of connected graph entities (`Source File`, `Package`, `Constraint`, `Symbol`).
- **Opportunity Assessment**: Identifies current system capabilities vs. missing features unlocked by the signal.
- **Action Recommendation**: Clear decision paths (*Adopt*, *Prototype*, *Monitor*, *Reject*) with testing guidelines and risk assessments.

### 3. Opportunities Engine
- Consolidates related signals into unified engineering initiatives to prevent fragmented refactoring efforts.
- Quantifies effort estimates, impacted components, and strategic value across signal clusters.

### 4. Interactive Architecture Explorer
- Visual node explorer linked directly to FalkorDB queries.
- Filter by entity types: `Project`, `RepoComponent`, `ProjectObjective`, `ProjectCapability`, `Package`, `Evidence`, and `ImpactAssessment`.
- Drill down into relationship links to trace how external packages affect core application files.

### 5. Action Plans
- Complete lifecycle tracking: `Proposed` ➔ `Under Review` ➔ `Approved` ➔ `In Progress` ➔ `Completed`.
- Breaks complex upgrades into milestone tasks, prerequisite checks, test plans, and rollback criteria.

### 6. Sources & Scanner Management
- Admin control panel for managing RSS feeds, GitHub release channels, and advisory endpoints.
- Configure scan intervals, relevance thresholds, and source health checks.


## 🌐 Graph Data Model & Visualizations

TechCompass indexes project metadata inside **FalkorDB** using standard graph primitives.

### Primary Node Schema

| Node Label | Description | Example Properties |
| :--- | :--- | :--- |
| `Project` | Root project entity | `name: "AnythingLLM"`, `version: "1.0.0"` |
| `RepoComponent` | Subsystems or modules | `name: "Collector"`, `path: "server/utils/collector"` |
| `ProjectObjective` | Strategic technical goals | `name: "Multi-modal RAG support"` |
| `ProjectCapability` | Functional capability | `name: "Vector Search Ingestion"` |
| `Package` | External library / dependency | `name: "@langchain/community"`, `version: "^0.0.10"` |
| `Evidence` | Supporting documentation/files | `path: "docs/ARCHITECTURE.md"` |
| `ImpactAssessment` | Signal evaluation result node | `score: 0.88`, `action: "Prototype"` |

### Core Relationship Schema
- `(:Project)-[:HAS_OBJECTIVE]->(:ProjectObjective)`
- `(:ProjectObjective)-[:ADVANCES]->(:ProjectCapability)`
- `(:ProjectCapability)-[:SUPPORTED_BY]->(:Evidence)`
- `(:Project)-[:HAS_COMPONENT]->(:RepoComponent)`
- `(:RepoComponent)-[:DEPENDS_ON]->(:Package)`
- `(:ImpactAssessment)-[:ASSESSES_PROJECT]->(:Project)`


### Knowledge Graph Visualizations

> **Image Embed Instructions:**
> Ensure your graph screenshot images are placed in an `assets/` folder at the root of this project repository with the names `image_d96bf8.jpg` and `image_d96ea2.jpg`.

#### Visualization 1: Project Objectives & Capabilities Tree
This graph snippet illustrates the root `AnythingLLM` project node connected to its strategic objectives, capabilities, and supporting evidence nodes:

<img width="1024" height="498" alt="image" src="https://github.com/user-attachments/assets/4bf16f52-a615-473c-b287-890178af59d7" />


* **Embedded Path**: `assets/image_d96bf8.jpg`
* **Graph Query**: `MATCH p=(project:Project {name: "AnythingLLM"}) RETURN p`
* **Description**: Shows the hierarchy originating from the main project node outward to `ProjectObjective`, `ProjectCapability`, and `Evidence` leaf nodes.


#### Visualization 2: Full Graph Entity & Dependency Network
This view displays the interconnected cluster topology between project components, packages, capabilities, and impact assessments:

<img width="1024" height="536" alt="image" src="https://github.com/user-attachments/assets/add07dbe-b62e-4c1a-aa79-99efc63cfb8a" />


* **Embedded Path**: `assets/image_d96ea2.jpg`
* **Graph Query**: `MATCH (n)-[e]-(m) RETURN n, e, m`
* **Description**: Visualizes package dependency groupings linked across core repository modules and signal impact assessment clusters.


## 🔍 Cypher Queries & Graph Algorithms

TechCompass utilizes FalkorDB Cypher queries to extract contextual subgraphs prior to sending prompts to the Groq reasoning agent.

### 1. Root Hierarchy Traversal
Retrieves main project objectives and supporting capabilities:
```cypher
MATCH (p:Project {name: "AnythingLLM"})-[r1:HAS_OBJECTIVE]->(obj:ProjectObjective)-[r2:ADVANCES]->(cap:ProjectCapability)
RETURN p, obj, cap
```

### 2. Full Topology Neighborhood Inspection
Retrieves node relationships across the overall architecture model (used in visual explorer views):
```cypher
MATCH (n)-[e]-(m)
RETURN n, e, m
LIMIT 100
```

### 3. Package Impact Analysis
Identifies codebase modules dependent on a specific package when assessing upgrade signals:
```cypher
MATCH (pkg:Package {name: $packageName})<-[dep:DEPENDS_ON]-(comp:RepoComponent)
OPTIONAL MATCH (comp)-[:CONTAINS]->(file:Evidence)
RETURN pkg, comp, file
```

### 4. Graph Context Ingestion for LLM Agent
Gathers capabilities, goals, and constraints to form context windows for Groq analysis:
```cypher
MATCH (p:Project {name: "AnythingLLM"})-[:HAS_OBJECTIVE]->(obj:ProjectObjective)
MATCH (obj)-[:ADVANCES]->(cap:ProjectCapability)
OPTIONAL MATCH (cap)-[:SUPPORTED_BY]->(ev:Evidence)
RETURN obj.name AS Objective, cap.name AS Capability, collect(ev.path) AS EvidenceFiles
```


## 📂 File Structure

```
.
├── index.html          # Core single-page interface and presentation markup
├── style.css           # Premium dark/light layout styling and design system
├── techcompass.json    # Application configuration, navigation routes, and schema fixtures
├── api-contract.yaml   # OpenAPI 3.1 contract specification for the FastAPI backend
├── README.md           # Project documentation and architecture guide
└── assets/             # Diagram and graph visual artifacts
    ├── image_d96bf8.jpg # Project hierarchy graph screenshot
    └── image_d96ea2.jpg # Full graph dependency cluster screenshot
```


## 🔌 API Specification & Endpoint Contract

TechCompass separates **verified live endpoints** from **proposed demo endpoints** (`isDemo: true`).

### Active Verified Endpoints (`isDemo: false`)

| Method | Route | Description |
| :--- | :--- | :--- |
| `GET` | `/health/ready` | Verifies readiness of FastAPI and database connection |
| `GET` | `/api/graph/summary` | Returns graph metadata, node counts, and relationship metrics |
| `GET` | `/api/graph/project` | Fetches core project architecture hierarchy for AnythingLLM |
| `GET` | `/api/graph/architecture?q={query}` | Searches architectural elements matching search string |
| `GET` | `/api/graph/dependencies?component={id}` | Returns dependency subgraph for a given component |
| `GET` | `/api/graph/package-usage?package={pkg}` | Lists files and modules consuming a specified package |
| `GET` | `/api/graph/package-impact?package={pkg}` | Performs impact analysis for upgrading/replacing a package |
| `POST` | `/api/agent/ask` | Queries the Groq LLM agent with graph context |

### Proposed Endpoints (`isDemo: true` Fixtures)

The following endpoints are defined in `api-contract.yaml` and currently served via local JSON fixtures:

- `GET /api/signals` — Fetch filterable list of external intelligence signals.
- `GET /api/signals/{id}` — Fetch detailed signal evaluation report.
- `GET /api/opportunities` — Fetch consolidated technical opportunities.
- `GET /api/plans` — Retrieve list of action plans.
- `POST /api/plans` — Create or update an action plan blueprint.
- `GET /api/sources` — Fetch feed scanner source list and health status.

---

## 🚀 Getting Started & Local Setup

### 1. Prerequisites
- Docker & Docker Compose
- Python 3.10+
- Node.js 18+ (optional, for static serving)

### 2. Launch Graph Database (FalkorDB)
Start FalkorDB in Docker:
```bash
docker run -p 6379:6379 -it --rm falkordb/falkordb
```

### 3. Backend Setup (FastAPI)
1. Clone repository and navigate to root directory:
   ```bash
   git clone https://github.com/your-org/techcompass.git
   cd techcompass
   ```
2. Set up virtual environment and install dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install fastapi uvicorn falkordb groq python-dotenv
   ```
3. Create `.env` file (refer to [Environment Variables](#-environment-variables)).
4. Launch FastAPI service:
   ```bash
   uvicorn main:app --reload --port 8000
   ```

### 4. Frontend Setup
Serve static frontend files using Python or Node:

*Using Python:*
```bash
python3 -m http.server 3000
```

*Using Node.js:*
```bash
npx serve . -l 3000
```

Open `http://localhost:3000` in your web browser.


## 🔑 Environment Variables

Create a `.env` file in the root directory. **Do not expose API keys or credentials on the frontend.**

```env
# Server Configuration
PORT=8000
HOST=0.0.0.0
CORS_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

# FalkorDB Connection
FALKORDB_HOST=localhost
FALKORDB_PORT=6379
FALKORDB_GRAPH_NAME=anything_llm

# Groq LLM API Configuration
GROQ_API_KEY=gsk_your_actual_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

## 🛡 Security & Design Principles

1. **Backend Decoupling & Key Safety**: LLM tokens (Groq) and FalkorDB credentials reside strictly within the FastAPI service environment.
2. **Deterministic Graph Evidence**: Reasoning outputs must link back to verifiable graph paths in FalkorDB to prevent AI hallucination.
3. **Transparent Demo Tagging**: Frontend elements backed by static mock data are explicitly marked with `isDemo: true` badges to maintain accurate data visibility.
4. **Non-Destructive Proposal Model**: Action Plans serve as decision frameworks. Generating an Action Plan will never execute direct code changes or trigger automated commits without human approval.
