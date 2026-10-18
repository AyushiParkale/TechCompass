import hashlib
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from falkordb import FalkorDB
from backend.graph.graph_connection import connection_options

ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = ROOT / "data" / "source" / "anythingllm-docs"
PAGES_ROOT = DOCS_ROOT / "pages"

load_dotenv(ROOT / ".env")

db = FalkorDB(
    host=os.getenv("FALKORDB_HOST", ""),
    port=int(os.getenv("FALKORDB_PORT", "6379")),
    **connection_options(),
    password=os.getenv("FALKORDB_PASSWORD"),
)
graph = db.select_graph(os.getenv("FALKORDB_GRAPH", "techcompass"))


def make_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def split_sections(text: str):
    # Split at Markdown headings, retaining the heading text.
    parts = re.split(r"(?m)^(#{1,6})\s+(.+?)\s*$", text)
    sections = []

    preamble = parts[0].strip()
    if preamble:
        sections.append(("Introduction", preamble))

    for i in range(1, len(parts), 3):
        heading = parts[i + 1].strip()
        body = parts[i + 2].strip() if i + 2 < len(parts) else ""
        if body:
            sections.append((heading, body))

    return sections


if not PAGES_ROOT.exists():
    raise SystemExit(f"Docs pages directory not found: {PAGES_ROOT}")

files = [
    *PAGES_ROOT.rglob("*.md"),
    *PAGES_ROOT.rglob("*.mdx"),
]

# Avoid duplicate processing if the same path appears twice.
files = sorted(set(files))

document_count = 0
section_count = 0

for path in files:
    relative_path = path.relative_to(DOCS_ROOT).as_posix()
    raw = path.read_text(encoding="utf-8")
    content_hash = make_id(raw)

    # Basic frontmatter extraction; the full raw text is preserved in sections.
    title_match = re.search(
        r'(?m)^title:\s*["\']?(.+?)["\']?\s*$',
        raw[:4000],
    )
    title = title_match.group(1) if title_match else path.stem

    document_id = make_id(relative_path)
    url_path = relative_path.removeprefix("pages/")
    url_path = re.sub(r"\.mdx?$", "", url_path)
    url_path = "" if url_path == "index" else url_path

    source_url = "https://docs.anythingllm.com/" + url_path

    graph.query(
        """
        MERGE (d:Document {id: $id})
        SET d.path = $path,
            d.title = $title,
            d.url = $url,
            d.content_hash = $content_hash,
            d.source_type = 'official_documentation'
        """,
        {
            "id": document_id,
            "path": relative_path,
            "title": title,
            "url": source_url,
            "content_hash": content_hash,
        },
    )

    for order, (heading, body) in enumerate(split_sections(raw)):
        section_id = make_id(f"{relative_path}#{heading}:{order}")

        graph.query(
            """
            MERGE (s:DocSection {id: $section_id})
            SET s.heading = $heading,
                s.content = $content,
                s.source_path = $path,
                s.source_url = $url,
                s.order = $order
            WITH s
            MATCH (d:Document {id: $document_id})
            MERGE (d)-[:HAS_SECTION]->(s)
            """,
            {
                "section_id": section_id,
                "heading": heading,
                "content": body,
                "path": relative_path,
                "url": source_url,
                "order": order,
                "document_id": document_id,
            },
        )
        section_count += 1

    document_count += 1

print(f"Documentation files processed: {document_count}")
print(f"Sections indexed: {section_count}")
