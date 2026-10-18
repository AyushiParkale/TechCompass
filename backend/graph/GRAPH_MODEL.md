# AnythingLLM graph: construction strategy

## Purpose and graph boundary

The graph models **AnythingLLM**, the analyzed product. TechCompass is the tool
that builds and queries this graph. The graph also stores TechCompass's own
`ProductProfile` under a different label so its discovery strategy cannot be
mistaken for AnythingLLM's architecture or goals.

The graph is built to answer connected questions, not to be a flat inventory:

1. What user or business goal does a documented capability support?
2. Which patterns, technologies, docs, components, and source paths explain that
   capability?
3. What is an external development, and which capability path makes it relevant?
4. What evidence supports each claim, and how strong is the connection?

## Strategic layers

| Layer | Main nodes | Purpose |
| --- | --- | --- |
| Product intent | `Project`, `ProjectGoal`, `ProjectObjective`, `ProjectCapability` | Describes what AnythingLLM is for and what outcomes its documented capabilities serve. |
| Architecture concepts | `ArchitecturePattern`, `Technology` | Names approaches and technology categories without making the graph a package-only model. |
| Official knowledge | `Document`, `DocSection`, `Evidence` | Keeps citations and source excerpts close to the claims they support. |
| Implementation | `RepoComponent`, `SourceFile`, `Symbol`, `ImportReference` | Maps concepts to repository areas and traversable code structure. |
| Dependency resolution | `Package`, `PackageVersion` | Separates manifest declarations from exact lockfile resolutions and transitive version edges. |
| External learning | `ExternalItem`, `ImpactAssessment`, `ExternalReference` | Stores discovered items and explicit, explainable assessments against project capabilities. |
| TechCompass self-profile | `ProductProfile`, `ProductObjective` | Stores the analyzer's own goals separately from the analyzed project. |

## Relationship model

```text
(Project)-[:HAS_GOAL]->(ProjectGoal)-[:SUPPORTED_BY]->(Evidence)
(Project)-[:HAS_OBJECTIVE]->(ProjectObjective)-[:ADVANCES]->(ProjectCapability)
(Project)-[:HAS_CAPABILITY]->(ProjectCapability)
(ProjectCapability)-[:USES_PATTERN]->(ArchitecturePattern)
(ProjectCapability)-[:USES_TECHNOLOGY]->(Technology)
(ProjectCapability)-[:SUPPORTED_BY]->(Evidence)
(ProjectCapability)-[:IMPLEMENTED_BY]->(SourceFile)
(ProjectCapability)-[:RELATES_TO_COMPONENT]->(RepoComponent)
(Document)-[:HAS_SECTION]->(DocSection)
(DocSection)-[:CANDIDATE_LINKS_TO]->(SourceFile)
(Project)-[:HAS_COMPONENT]->(RepoComponent)-[:DEPENDS_ON]->(Package)
(RepoComponent)-[:CONTAINS]->(SourceFile)-[:DEFINES]->(Symbol)
(SourceFile)-[:HAS_IMPORT]->(ImportReference)
(SourceFile)-[:IMPORTS]->(SourceFile | Package)
(Package)-[:HAS_VERSION]->(PackageVersion)
(RepoComponent)-[:HAS_LOCKFILE_RESOLUTION]->(PackageVersion)
(PackageVersion)-[:DEPENDS_ON_VERSION]->(PackageVersion)
(ImpactAssessment)-[:ABOUT_ITEM]->(ExternalItem)
(ImpactAssessment)-[:ABOUT_CAPABILITY]->(ProjectCapability)
(ImpactAssessment)-[:ASSESSES_PROJECT]->(Project)
(ImpactAssessment)-[:GROUNDED_IN]->(Evidence)
(ExternalItem)-[:HAS_REFERENCE]->(ExternalReference)
(ExternalItem)-[:MENTIONS_PACKAGE]->(Package)
(ExternalItem)-[:AFFECTS_VERSION]->(PackageVersion)
(ProductProfile)-[:HAS_OBJECTIVE]->(ProductObjective)
```

`IMPLEMENTED_BY`, `RELATES_TO_COMPONENT`, and `CANDIDATE_LINKS_TO` currently
carry heuristic or reviewed-path provenance. They are useful traversal leads,
not proof that a specific runtime code path executes. Lockfile resolution edges
are stronger version evidence than manifest ranges. External relevance and
implementation impact are separate claims: an item can be topically relevant
while its impact on the repository remains a hypothesis.

## Build order and why it matters

`scripts/graph/build.py` is the canonical ordered pipeline. Its stage manifest
lists each stage's prerequisites and the graph/index artifacts it produces.
`scripts/graph/rebuild_context_graph.ps1` only validates the target graph,
isolates its index files, and calls that runner.
The sequence is intentionally staged because later stages depend on earlier
nodes:

1. **Source index**: extracts files, symbols, and imports to a graph-specific
   JSON index. Skipped only when `-ReuseExistingIndex` copies the existing index
   into the new graph's own directory.
2. **Project manifest**: creates the AnythingLLM project/component/package
   declaration layer from `package.json` files.
3. **Documentation**: creates official docs and section nodes.
4. **Code structure**: ingests source files, symbols, and import references,
   then resolves local imports and package imports against known components
   and packages.
5. **Product architecture**: reads `anythingllm_context.json`, creates goals,
   objectives, capabilities, technologies, patterns, and links official docs
   and implementation paths as evidence-backed relationships.
6. **Documentation-to-code mapping**: adds candidate links from documentation
   sections to source files.
7. **Resolved dependencies**: parses the server, frontend, and collector Yarn
   lockfiles, ingests exact versions and transitive edges, then audits them.
8. **Analyzer profile**: adds TechCompass goals in separate profile labels.

Each stage runs as a package module in a child process. This is deliberate: several existing
ingesters still execute work at module import time, so importing them into the
API or a shared runner would accidentally connect to FalkorDB and write data.
The next safe refactor is to turn those scripts into `main()` functions so the
stage modules can also be imported safely by maintenance tools.

## Inputs and ownership

- `data/source/anything-llm/`: upstream application code and package manifests.
- `data/source/anythingllm-docs/`: official documentation used for product
  capabilities and citations.
- `backend/graph/anythingllm_context.json`: reviewed goals/objectives/capability
  map, documentation sources, architecture vocabulary, and candidate code
  paths. This is the semantic architecture layer; keep it source-backed and
  revise it when upstream architecture changes.
- `backend/graph/project_profile.json`: TechCompass product goal and external
  source strategy, not AnythingLLM's purpose.
- `data/index/<graph-name>/`: generated code and lockfile indexes isolated for
  each graph build.

## Impact traversal

External search records are stored as `ExternalItem` nodes. An impact
assessment should connect the item to a graph-resolved capability, capture the
rationale/next step, and connect that assessment to official or
repository `Evidence`. A useful explanation path is:

```text
Project -> ProjectObjective -> ProjectCapability <- ImpactAssessment -> ExternalItem
                                                  -> Evidence
```

The project-to-item relationship is therefore not guessed from the project
name alone. It is explained through a matching capability and its evidence.
When only lexical overlap or a candidate code path exists, describe the
relationship cautiously in the impact explanation. Absence of a path means
“not established in this graph,” not “the external item cannot matter.”

## Build and check

From the repository root, build a fresh isolated graph:

```powershell
.\scripts\graph\setup_graph.ps1 -GraphName anythingllm-context-v3
```

`setup_graph.ps1` clones the two sources under `data/source`, installs the
repository requirements, invokes the rebuild pipeline, and activates the new
graph in `.env` only after the build succeeds. To reuse the existing code index
while still making a fresh graph:

```powershell
.\scripts\graph\rebuild_context_graph.ps1 -GraphName anythingllm-context-v3 -ReuseExistingIndex
```

The build never targets the currently configured graph. Validate the new
graph with the API's readiness endpoint and a few graph-backed questions before
switching application traffic or cleaning old graph data.

## Known boundaries

- Architecture concepts come from the reviewed context map and its cited docs;
  broad keyword-based concept extraction is not part of the build pipeline.
- The current concept-to-code links include reviewed directory/path mappings;
  they are not call-graph or runtime tracing proofs.
- Schema/index creation is not centralized yet. The graph currently relies on
  `MERGE` keys and targeted query patterns; indexes should be added only after
  confirming FalkorDB version support and measuring actual query plans.
- Old graph snapshots are preserved deliberately. No destructive cleanup is
  part of rebuild or activation.
