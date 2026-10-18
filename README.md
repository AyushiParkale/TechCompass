# TechCompass — Continuous Project Intelligence Dashboard

**TechCompass** is a continuous, graph-native technology intelligence dashboard designed to discover external signals (release notes, research papers, engineering blogs, advisories) and assess their relevance against a target codebase's internal architecture, dependencies, and constraints.

The primary target project for this intelligence dashboard is **AnythingLLM**. Its codebase, dependencies, architecture concepts, documentation, and operational constraints are indexed in a **FalkorDB** knowledge graph. A **FastAPI** service coupled with a **Groq-powered LLM agent** performs graph-backed analysis to generate actionable, explainable engineering recommendations.


## Table of Contents
1. [Product Overview](#-product-overview)
2. [Key Architecture & Data Flow](#-key-architecture--data-flow)
3. [Core Capabilities & Views](#-core-capabilities--views)
4. [File Structure](#-file-structure)
5. [API Specification & Integration Status](#-api-specification--integration-status)
6. [Getting Started & Local Setup](#-getting-started--local-setup)
7. [Environment Variables](#-environment-variables)
8. [Security & Design Principles](#-security--design-principles)


## Product Overview

TechCompass transforms how engineering teams evaluate external software changes and technological advancements. Instead of manually copying and pasting articles into generic chatbot interfaces, TechCompass continuously ingests ecosystem signals, correlates them with a deep knowledge graph of your project, and produces structured recommendations.

### Key Value Drivers
- **Automated Discovery**: Sources are monitored automatically for relevant updates.
- **Graph-Backed Reasoning**: Recommendations are grounded in actual codebase components, dependencies, and documented constraints—not hallucinated assertions.
- **Explainable Assessments**: Every signal includes specific supporting graph evidence, identified capability gaps, and concrete adoption steps.
- **Non-Destructive Action Plans**: Structured blueprints help teams evaluate, prototype, and adopt technologies safely.


## Key Architecture & Data Flow


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


### Ingestion & Analysis Workflow
1. **Signal Discovery**: The scanner periodically checks configured technical feeds for new items.
2. **Graph Context Retrieval**: The backend retrieves subgraphs from FalkorDB matching technologies, packages, or architectural concepts mentioned in the signal.
3. **Groq Reasoning**: The LLM agent evaluates the signal against the project subgraph to determine relevance, potential impact, capability gaps, and recommended next actions.
4. **Dashboard Presentation**: The findings are delivered to the frontend, categorized by urgency and impact.


## Core Capabilities & Views

### 1. Signal Feed (Landing Page)
- **Compact Metric Cards**: Live tracking of newly discovered signals, high-relevance findings, opportunities awaiting review, and active implementation plans.
- **Filterable List View**: Search and filter by relevance score, source type, topic tags, and analysis status.
- **Quick Action Actions**: View full reports, open external sources, save items for review, dismiss irrelevant signals, or convert recommendations into action plans.

### 2. Signal Analysis Detail
- **Signal Summary**: Extracted claims and direct links to original external publications.
- **Project Relevance**: High-level impact assessment, confidence score, and justification.
- **Graph Evidence**: Linked nodes representing source files, internal symbols, dependencies, and architecture concepts.
- **Opportunity Analysis**: Differentiation between existing capabilities, potential improvements, and unverified gaps.
- **Action Recommendation**: Structured decision path (*Prototype*, *Adopt*, *Monitor*, or *Reject*) accompanied by risk analysis and test criteria.

### 3. Opportunities
- Aggregates related individual signals into unified engineering opportunities to avoid duplicate review work.
- Groups affected architectural components and provides unified effort estimates and priority scores.

### 4. Architecture Explorer
- Interactive node visualization backed directly by FalkorDB graph data.
- Interactive filtering by node type: `Component`, `Source File`, `Symbol`, `Package`, `Document`, `Technology`, `Approach`, and `Constraint`.
- Inspect relationship paths directly between external signals and internal codebase elements.

### 5. Action Plans
- Track adoption blueprints across standard lifecycles: `Proposed`, `Under Review`, `Approved`, `In Progress`, and `Completed`.
- Breaks down complex recommendations into discrete tasks, validation steps, prerequisite checks, and risk mitigations.

### 6. Sources & Monitoring
- Admin configuration view to manage feed sources (Engineering Blogs, Release Notes, Research Papers, Advisories).
- Controls for scan frequencies, relevance thresholds, topic filters, and source status toggles.


## 🔌 API Specification & Integration Status

TechCompass maintains a strict distinction between **live connected endpoints** and **proposed endpoints** currently utilizing demo adapters (`isDemo: true`).

### Active Verified Endpoints (`isDemo: false`)

| Method | Route | Description |
| :--- | :--- | :--- |
| `GET` | `/health/ready` | Verifies connection readiness to FastAPI and database services |
| `GET` | `/api/graph/summary` | Returns graph metadata, node counts, and relationship metrics |
| `GET` | `/api/graph/project` | Fetches core project architecture hierarchy for AnythingLLM |
| `GET` | `/api/graph/architecture?q={query}` | Searches architectural elements matching a query term |
| `GET` | `/api/graph/dependencies?component={id}`| Returns dependency subgraphs for a specific component |
| `GET` | `/api/graph/package-usage?package={pkg}`| Identifies files and modules consuming a specified package |
| `GET` | `/api/graph/package-impact?package={pkg}`| Computes impact analysis for upgrading or replacing a package |
| `POST`| `/api/agent/ask` | Queries the Groq LLM agent with graph context |

### Proposed Endpoints (`isDemo: true` Fixtures)

The following endpoints are specified in `api-contract.yaml` and provide temporary local persistence until implemented in FastAPI:

- `GET /api/signals` — Fetch signal list with filter parameters.
- `GET /api/signals/{id}` — Fetch detailed report for a single signal.
- `GET /api/opportunities` — Fetch aggregated opportunities.
- `GET /api/plans` — List implementation plans.
- `POST /api/plans` — Create or update an implementation plan.
- `GET /api/sources` — Fetch scanner source status and parameters.


## 🚀 Getting Started & Local Setup

### Frontend Setup

1. **Clone or Download** the project files into a single workspace directory:
   bash
   git clone https://github.com/your-org/techcompass.git
   cd techcompass
   

2. **Serve the Static Application**:
   Since TechCompass uses standard Web standards, you can serve it with any HTTP server:

   *Using Python:*
   bash
   python3 -m http.server 8000
   

   *Using Node.js:*
   bash
   npx serve .
   

3. **Access the Application**:
   Open `http://localhost:8000` in your browser.

### Backend Setup (FastAPI + FalkorDB)

1. **Start FalkorDB Instance**:
   bash
   docker run -p 6379:6379 -it --rm falkordb/falkordb
   

2. **Launch FastAPI Backend**:
   Ensure your backend application is configured to listen on port `8000` or configure the frontend API base URL accordingly.


## 🔑 Environment Variables

For backend setup, ensure the following environment keys are populated in your `.env` file. **Never expose these keys to the frontend browser application.**

env
# Server Configuration
PORT=8000
HOST=0.0.0.0
CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

# Graph Database Connection
FALKORDB_HOST=localhost
FALKORDB_PORT=6379
FALKORDB_GRAPH_NAME=anything_llm

# Groq LLM API Configuration
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=llama-3.3-70b-versatile


## 🛡 Security & Design Principles

1. **Credential Safety**: All LLM keys (Groq) and graph database credentials remain strictly encapsulated within the FastAPI backend layer.
2. **Honest Data Presentation**: Frontend metric cards and connection badges dynamically reflect real backend responses. Mock items are explicitly tagged with `isDemo: true`.
3. **Non-Destructive Workflow**: Action plans generated by TechCompass represent execution proposals only. Creating or updating a plan will **never** automatically alter codebases, open pull requests, or trigger infrastructure deployments.
