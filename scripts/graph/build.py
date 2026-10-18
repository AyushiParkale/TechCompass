"""Run the canonical AnythingLLM graph build pipeline.

This file is the single source of truth for ingestion order. Each stage remains
an isolated script because several legacy ingesters still perform work at
module scope; invoking them as child processes prevents accidental execution
when they are imported by application code.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Stage:
    key: str
    script: str
    purpose: str
    requires: tuple[str, ...]
    produces: tuple[str, ...]


STAGES = (
    Stage(
        "source_index", "extract_codebase.py",
        "Extract source files, symbols, and import references from the AnythingLLM checkout.",
        ("AnythingLLM checkout",), ("graph-scoped code index",),
    ),
    Stage(
        "project_manifest", "ingest_anythingllm.py",
        "Create the project, repository components, and declared package edges from package.json manifests.",
        ("AnythingLLM checkout", "FalkorDB graph"),
        ("Project", "RepoComponent", "Package", "HAS_COMPONENT", "DEPENDS_ON"),
    ),
    Stage(
        "documentation", "ingest_docs.py",
        "Store official documentation as documents and heading-level sections.",
        ("AnythingLLM docs checkout", "FalkorDB graph"),
        ("Document", "DocSection", "HAS_SECTION"),
    ),
    Stage(
        "code_symbols", "ingest_symbols.py",
        "Create source-file, symbol, and import-reference nodes from the source index.",
        ("graph-scoped code index", "FalkorDB graph"),
        ("SourceFile", "Symbol", "ImportReference", "DEFINES", "HAS_IMPORT", "CONTAINS"),
    ),
    Stage(
        "import_resolution", "resolve_imports.py",
        "Resolve imports to local source files or declared packages.",
        ("graph-scoped code index", "SourceFile", "RepoComponent", "Package"),
        ("IMPORTS", "IMPORTS_PACKAGE"),
    ),
    Stage(
        "project_architecture", "ingest_project_context.py",
        "Add reviewed goals, objectives, capabilities, technologies, patterns, and their source evidence.",
        ("anythingllm_context.json", "Document sections", "Project"),
        ("ProjectGoal", "ProjectObjective", "ProjectCapability", "Technology", "ArchitecturePattern", "Evidence"),
    ),
    Stage(
        "documentation_code_links", "link_docs_to_code.py",
        "Connect documentation sections to likely implementation files with explicit mapping provenance.",
        ("DocSection", "SourceFile"), ("CANDIDATE_LINKS_TO", "CONTAINS"),
    ),
    Stage(
        "lockfile_parse", "parse_yarn_lockfiles.py",
        "Parse Yarn lockfiles into a per-graph resolved-version index.",
        ("server/frontend/collector Yarn lockfiles",),
        ("graph-scoped lockfile index",),
    ),
    Stage(
        "lockfile_resolution", "ingest_lockfile_versions.py",
        "Add exact package-version and transitive lockfile relationships.",
        ("graph-scoped lockfile index", "RepoComponent", "Package"),
        ("PackageVersion", "HAS_VERSION", "HAS_LOCKFILE_RESOLUTION", "DEPENDS_ON_VERSION"),
    ),
    Stage(
        "lockfile_audit", "verify_lockfile_versions.py",
        "Check package/version relationships and report inconsistencies.",
        ("resolved lockfile graph",), ("audit report",),
    ),
    Stage(
        "techcompass_profile", "ingest_project_profile.py",
        "Store TechCompass's own product goals separately from the analyzed AnythingLLM project.",
        ("project_profile.json",), ("ProductProfile", "ProductObjective"),
    ),
)


def _index_path(relative: str) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-existing-index", action="store_true")
    args = parser.parse_args()

    graph_name = os.getenv("FALKORDB_GRAPH", "").strip()
    if not graph_name:
        parser.error("FALKORDB_GRAPH must be set to the target graph name")

    repo = ROOT / "data" / "source" / "anything-llm"
    docs = ROOT / "data" / "source" / "anythingllm-docs" / "pages"
    if not repo.is_dir():
        parser.error(f"AnythingLLM checkout is missing: {repo}")
    if not docs.is_dir():
        parser.error(f"AnythingLLM documentation pages are missing: {docs}")

    index_env = os.getenv("TECHCOMPASS_CODEBASE_INDEX", "data/index/codebase.json")
    code_index = _index_path(index_env)
    if args.reuse_existing_index and not code_index.is_file():
        parser.error(f"Requested reusable code index does not exist: {code_index}")

    python = sys.executable
    graph_dir = ROOT / "scripts" / "graph"
    stages = STAGES[1:] if args.reuse_existing_index else STAGES
    print(f"Target graph: {graph_name}", flush=True)
    print(f"Code index: {code_index}", flush=True)
    print(f"Pipeline stages: {len(stages)}", flush=True)

    for number, stage in enumerate(stages, start=1):
        script_path = graph_dir / stage.script
        if not script_path.is_file():
            print(f"Missing stage script: {script_path}", file=sys.stderr)
            return 2
        print(f"\n=== [{number}/{len(stages)}] {stage.key}: {stage.purpose} ===", flush=True)
        print(f"Requires: {', '.join(stage.requires)}", flush=True)
        print(f"Produces: {', '.join(stage.produces)}", flush=True)
        module = f"scripts.graph.{Path(stage.script).stem}"
        result = subprocess.run(
            [python, "-m", module], cwd=ROOT, env=os.environ.copy()
        )
        if result.returncode:
            print(
                f"Stage '{stage.key}' failed with exit code {result.returncode}. "
                f"Graph '{graph_name}' is incomplete; the previously configured graph was not changed.",
                file=sys.stderr,
                flush=True,
            )
            return result.returncode

    print(f"\nGraph build complete: {graph_name}", flush=True)
    print("Validate /health/ready and run a graph-backed query before activating this graph.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
