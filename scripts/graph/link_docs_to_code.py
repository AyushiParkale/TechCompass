
import hashlib
import math
import os
import re
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB
from backend.graph.graph_connection import connection_options

ROOT = Path(__file__).resolve().parents[2]
CODE_ROOT = ROOT / "data" / "source" / "anything-llm"
BATCH_SIZE = 250

load_dotenv(ROOT / ".env")

db = FalkorDB(
    host=os.getenv("FALKORDB_HOST", ""),
    port=int(os.getenv("FALKORDB_PORT", "6379")),
    **connection_options(),
    password=os.getenv("FALKORDB_PASSWORD"),
)
graph = db.select_graph(os.getenv("FALKORDB_GRAPH", "techcompass"))

CODE_EXTENSIONS = {
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".py", ".go", ".rs", ".java", ".php",
    ".sql", ".html", ".css", ".scss", ".vue", ".svelte",
}

EXCLUDED_DIRS = {
    ".git", "node_modules", "dist", "build",
    "coverage", ".next", "out", "vendor",
}

STOPWORDS = set("""
a an the and or but if then else for from to of in on at by with
is are was were be been being as it its this that these those
we you they them their our your not no can could should would
will may might have has had do does did into over under more most
than also only very use used using based file files code source
page pages documentation docs project anythingllm
""".split())


def stable_id(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def tokens(text):
    # Split camelCase and PascalCase before normalizing.
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return [
        word for word in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{1,}", text.lower())
        if word not in STOPWORDS
    ]


def vectorize(text, idf):
    counts = Counter(tokens(text))
    return {
        term: (1 + math.log(count)) * idf.get(term, 1.0)
        for term, count in counts.items()
    }


def cosine(a, b):
    if not a or not b:
        return 0.0

    common = a.keys() & b.keys()
    dot = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))

    if not norm_a or not norm_b:
        return 0.0

    return dot / (norm_a * norm_b)


if not CODE_ROOT.exists():
    raise SystemExit(f"Code repository not found: {CODE_ROOT}")

# 1. Collect source files, excluding generated and third-party code.
source_files = []

for path in CODE_ROOT.rglob("*"):
    if not path.is_file() or path.suffix.lower() not in CODE_EXTENSIONS:
        continue

    relative = path.relative_to(CODE_ROOT)
    if any(part in EXCLUDED_DIRS for part in relative.parts):
        continue

    try:
        if path.stat().st_size > 500_000:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue

    source_files.append({
        "id": stable_id(relative.as_posix()),
        "path": relative.as_posix(),
        "language": path.suffix.lower().lstrip("."),
        "content_hash": stable_id(content),
        "content": content,
    })

if not source_files:
    raise SystemExit("No source files found.")

print(f"Source files discovered: {len(source_files)}")

# 2. Upsert source-file nodes.
for item in source_files:
    graph.query(
        """
        MERGE (f:SourceFile {id: $id})
        SET f.path = $path,
            f.name = $name,
            f.language = $language,
            f.content_hash = $content_hash
        """,
        {
            "id": item["id"],
            "path": item["path"],
            "name": Path(item["path"]).name,
            "language": item["language"],
            "content_hash": item["content_hash"],
        },
    )

# 3. Attach source files to existing RepoComponent nodes by directory.
result = graph.query(
    "MATCH (c:RepoComponent) RETURN c.path, c.name"
)

components = []
for row in result.result_set:
    manifest_path = row[0]
    if not manifest_path:
        continue

    prefix = Path(manifest_path).parent.as_posix()
    if prefix == ".":
        prefix = ""

    components.append({
        "path": prefix,
        "name": row[1],
    })

for item in source_files:
    file_path = item["path"]

    for component in components:
        prefix = component["path"]

        belongs = (
            file_path.startswith(prefix + "/")
            if prefix
            else "/" not in file_path
        )

        if not belongs:
            continue

        graph.query(
            """
            MATCH (c:RepoComponent {name: $component_name})
            MATCH (f:SourceFile {id: $file_id})
            MERGE (c)-[:CONTAINS]->(f)
            """,
            {
                "component_name": component["name"],
                "file_id": item["id"],
            },
        )

# 4. Load documentation sections created by the docs ingester.
result = graph.query(
    """
    MATCH (s:DocSection)
    RETURN s.id, s.heading, s.content, s.source_url
    """
)

sections = [
    {
        "id": row[0],
        "heading": row[1] or "",
        "content": row[2] or "",
        "url": row[3] or "",
    }
    for row in result.result_set
]

if not sections:
    raise SystemExit(
        "No DocSection nodes found. Run ingest_docs.py successfully first."
    )

print(f"Documentation sections discovered: {len(sections)}")

# 5. Build an IDF vocabulary across source files and documentation.
all_texts = [
    item["path"] + " " + item["content"]
    for item in source_files
] + [
    section["heading"] + " " + section["content"] + " " + section["url"]
    for section in sections
]

document_frequency = Counter()
for text in all_texts:
    document_frequency.update(set(tokens(text)))

total = len(all_texts)
idf = {
    term: math.log(1 + total / (1 + frequency)) + 1
    for term, frequency in document_frequency.items()
}

code_vectors = [
    vectorize(item["path"] + " " + item["content"], idf)
    for item in source_files
]

# 6. Rebuild only the candidate links produced by this script.
graph.query(
    """
    MATCH (:DocSection)-[r:CANDIDATE_LINKS_TO]->(:SourceFile)
    DELETE r
    """
)

link_rows = []

for section in sections:
    query_text = section["heading"] + " " + section["content"] + " " + section["url"]
    query_vector = vectorize(query_text, idf)

    ranked = []
    for item, code_vector in zip(source_files, code_vectors):
        score = cosine(query_vector, code_vector)
        if score >= 0.08:
            ranked.append((score, item, query_vector, code_vector))

    ranked.sort(key=lambda item: item[0], reverse=True)

    for score, item, query_vector, code_vector in ranked[:3]:
        shared_terms = sorted(
            query_vector.keys() & code_vector.keys(),
            key=lambda term: query_vector[term] * code_vector[term],
            reverse=True,
        )[:10]

        link_rows.append({
            "section_id": section["id"],
            "file_id": item["id"],
            "score": score,
            "shared_terms": shared_terms,
        })

link_query = """
    UNWIND $rows AS row
    MATCH (s:DocSection {id: row.section_id})
    MATCH (f:SourceFile {id: row.file_id})
    MERGE (s)-[r:CANDIDATE_LINKS_TO]->(f)
    SET r.score = row.score,
        r.method = 'tfidf_lexical',
        r.verification_status = 'candidate',
        r.shared_terms = row.shared_terms
"""
for start in range(0, len(link_rows), BATCH_SIZE):
    graph.query(
        link_query,
        {"rows": link_rows[start:start + BATCH_SIZE]},
    )

print(f"Candidate documentation-to-code links: {len(link_rows)}")
print("Links are candidates, not verified semantic relationships.")
