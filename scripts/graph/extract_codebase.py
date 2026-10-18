import hashlib
import json
import os
from bisect import bisect_right
from pathlib import Path

from tree_sitter_language_pack import get_parser

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "data" / "source" / "anything-llm"
_index_path = Path(os.getenv(
    "TECHCOMPASS_CODEBASE_INDEX",
    "data/index/codebase.json",
))
OUT = _index_path if _index_path.is_absolute() else ROOT / _index_path

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "coverage",
    ".next", "out", "vendor", "playwright-report",
}

CODE_EXTENSIONS = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
}

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode(
        "utf-8", errors="replace"
    )

def identifier_name(node, source: bytes) -> str | None:
    if node is None:
        return None
    if node.type in {
        "identifier", "property_identifier",
        "type_identifier", "private_property_identifier",
    }:
        return node_text(node, source)
    return None

def walk_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() in CODE_EXTENSIONS:
            yield path

def get_child(node, field_name):
    try:
        return node.child_by_field_name(field_name)
    except Exception:
        return None

def extract_tree(tree, source, relative, file_id, symbols, imports):
    node_types = {
        "function_declaration": "function",
        "generator_function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
        "interface_declaration": "interface",
        "type_alias_declaration": "type",
        "enum_declaration": "enum",
        "abstract_class_declaration": "class",
    }

    line_starts = [0]
    line_starts.extend(
        index + 1 for index, byte in enumerate(source) if byte == 10
    )
    stack = [(tree.root_node, None)]
    while stack:
        node, enclosing = stack.pop()
        kind = node_types.get(node.type)
        name = None
        if kind:
            name = identifier_name(get_child(node, "name"), source)

        if node.type == "variable_declarator":
            value = get_child(node, "value")
            if value is not None and value.type in {
                "arrow_function", "function_expression",
            }:
                kind = "function"
                name = identifier_name(get_child(node, "name"), source)

        if kind and name:
            start_line = bisect_right(line_starts, node.start_byte)
            end_line = bisect_right(line_starts, node.end_byte)
            qualified = f"{enclosing}.{name}" if enclosing else name
            symbol_id = sha256(f"{relative}::{qualified}::{kind}")

            symbols.append({
                "id": symbol_id,
                "file_id": file_id,
                "file_path": relative,
                "name": name,
                "qualified_name": qualified,
                "kind": kind,
                "start_line": start_line,
                "end_line": end_line,
                "signature": node_text(node, source)[:600],
            })

            if kind == "class":
                next_enclosing = qualified
            else:
                next_enclosing = enclosing
        else:
            next_enclosing = enclosing

        if node.type in {"import_statement", "export_statement"}:
            source_node = get_child(node, "source")
            if source_node is not None:
                specifier = node_text(source_node, source).strip("'\"`")
                if specifier:
                    imports.append({
                        "file_id": file_id,
                        "file_path": relative,
                        "specifier": specifier,
                        "line": bisect_right(line_starts, node.start_byte),
                        "kind": node.type,
                    })

        for child in reversed(node.children):
            stack.append((child, next_enclosing))

def index_files(paths):
    files = []
    symbols = []
    imports = []
    parse_errors = 0

    for path in paths:
        relative = path.relative_to(REPO).as_posix()

        try:
            raw = path.read_bytes()
        except OSError:
            continue

        # Avoid enormous generated or bundled files.
        if len(raw) > 1_000_000:
            continue

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue

        language = CODE_EXTENSIONS[path.suffix.lower()]
        parser = get_parser(language)
        tree = parser.parse(raw)
        if tree.root_node.has_error:
            parse_errors += 1

        file_id = sha256(relative)

        files.append({
            "id": file_id,
            "path": relative,
            "language": language,
            "content_hash": sha256(text),
        })

        extract_tree(tree, raw, relative, file_id, symbols, imports)

    return files, symbols, imports, parse_errors

def main():
    if not REPO.exists():
        raise SystemExit(f"Repository not found: {REPO}")

    paths = list(walk_files(REPO))
    files, symbols, imports, parse_errors = index_files(paths)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({
            "repository": "AnythingLLM",
            "files": files,
            "symbols": symbols,
            "imports": imports,
            "parse_errors": parse_errors,
        }, indent=2),
        encoding="utf-8",
    )

    print(f"Files indexed: {len(files)}")
    print(f"Symbols extracted: {len(symbols)}")
    print(f"Import/export references: {len(imports)}")
    print(f"Files with parser errors: {parse_errors}")
    print(f"Output: {OUT}")

if __name__ == "__main__":
    main()
