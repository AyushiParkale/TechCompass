# TechCompass — Real-Time Technology Updates for Your Project

**TechCompass** finds recent technology updates that could affect a project. It searches for new releases, research papers, engineering announcements, and advisories, then checks them against the project’s goals, architecture, and dependencies. Instead of presenting headlines alone, TechCompass explains why an update may matter and shows the graph paths connecting it to the project.

The current target project is **AnythingLLM**. Its objectives, capabilities, architecture concepts, documentation evidence, and dependency information are stored in a **FalkorDB** knowledge graph. A **FastAPI** backend uses this graph to guide web searches. **Tavily** is the primary search provider, with DuckDuckGo and Bing as fallbacks. **Gemini** assesses potential impact, and validated findings are saved to the graph for future reports.

The browser dashboard presents findings with source links, potential impact, recommended next steps, and the graph hops behind each match. Candidate connections are labeled so they are not mistaken for confirmed relationships.

Update checks currently run when requested from the dashboard. Background scheduled monitoring is not part of the current workflow.


## Product Overview

TechCompass transforms how engineering teams evaluate external software changes and technological advancements. Instead of manually copying and pasting articles into generic chatbot interfaces, TechCompass continuously ingests ecosystem signals, correlates them with a deep knowledge graph of your project, and produces structured recommendations.

### Key Value Drivers
- **Automated Discovery**: Sources are monitored automatically for relevant updates.
- **Graph-Backed Reasoning**: Recommendations are grounded in actual codebase components, dependencies, and documented constraints—not hallucinated assertions.
- **Explainable Assessments**: Every signal includes specific supporting graph evidence, identified capability gaps, and concrete adoption steps.
- **Non-Destructive Action Plans**: Structured blueprints help teams evaluate, prototype, and adopt technologies safely.


## Key Architecture & Data Flow


<img width="1672" height="941" alt="image" src="https://github.com/user-attachments/assets/c40da0a8-f968-44d5-a78e-6b62a93de163" />

## How TechCompass works

TechCompass checks recent technology developments against a project’s goals and architecture. It uses the project graph to guide web searches, assess possible impact, and show the evidence and graph paths behind its findings.

### Update workflow

1. **Read project context**  
   The agent retrieves project objectives, capabilities, architecture concepts, documentation evidence, and previous impact assessments from FalkorDB.

2. **Plan searches from the graph**  
   The agent uses project objectives and related capabilities to create search queries for the selected look-back period.

3. **Search and check sources**  
   Tavily is the primary search provider. DuckDuckGo and Bing are available as fallbacks. Results are filtered by date, deduplicated, and checked for useful source information.

4. **Trace possible impact**  
   A read-only graph traversal connects search findings to relevant capabilities, evidence, and possible code locations. The report can show the returned path and hop count for each match. Candidate paths are presented as candidates, not confirmed relationships.

5. **Assess and validate findings**  
   Gemini assesses potential impact and recommends a next step. The backend validates source URLs and capability references against the search results and project context before saving an assessment.

6. **Save and report**  
   Validated assessments and their evidence links are saved to FalkorDB. The report is streamed to the browser with source links, potential impact, and graph paths. Saved assessments can provide context for later runs.

### Dashboard

The update report is grouped by project objective. Depending on the available search results and graph data, a finding can include:

- External source and publication date
- Summary of what is new
- Potential impact on the project
- Recommended next step
- Supporting graph path and evidence
- Search and graph traversal details

Graph paths show which nodes and relationships the traversal returned, including the number of hops. Some links represent candidate matches and should be treated accordingly.

### Graph and dependency views

TechCompass also provides graph and dependency queries for exploring project context stored in FalkorDB. The available information depends on which project data has been ingested, such as architecture concepts, components, packages, documentation, and source files.

### API

The FastAPI backend provides readiness checks, graph and dependency queries, and graph-backed agent workflows. The update workflow is available through `POST /api/updates/run`; progress and results are streamed to the browser using server-sent events.

> **Implementation note:** A route listed in `api-contract.yaml` is not necessarily implemented by the backend. Features such as scheduled feed monitoring, a persistent signal feed, opportunity aggregation, and action-plan management should be documented as planned until they are available in the running application.

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
FALKORDB_HOST=r-6jissuruar.instance-pnn3pjksr.hc-7up0crkyn.ap-south-1.aws.f2e0a955bb84.cloud
FALKORDB_USERNAME=falkordb
FALKORDB_PORT=52327
FALKORDB_GRAPH=anythingllm-context-v2-20261018-132947

# Gemini API Configuration
GEMINI_API_KEY=your_gemini_api_key_here


## 🛡 Security & Design Principles

1. **Credential Safety**: All LLM keys (Gemini) and graph database credentials remain strictly encapsulated within the FastAPI backend layer.
2. **Honest Data Presentation**: Frontend metric cards and connection badges dynamically reflect real backend responses. Mock items are explicitly tagged with `isDemo: true`.
3. **Non-Destructive Workflow**: Action plans generated by TechCompass represent execution proposals only. Creating or updating a plan will **never** automatically alter codebases, open pull requests, or trigger infrastructure deployments.
