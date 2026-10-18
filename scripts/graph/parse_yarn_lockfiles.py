
import json
import os
import re
from pathlib import Path

LOCKFILES = [
    Path("data/source/anything-llm/server/yarn.lock"),
    Path("data/source/anything-llm/frontend/yarn.lock"),
    Path("data/source/anything-llm/collector/yarn.lock"),
]

def _dependency_entry(line, optional):
    match = re.match(
        r"""^\s{4}("?[^"]+"?|\S+)\s+(?:"([^"]+)"|'([^']+)'|(\S+))\s*$""",
        line,
    )
    if not match:
        return None

    name = match.group(1).strip('"')
    version_range = next(
        value for value in match.groups()[1:] if value is not None
    )
    return {
        "name": name,
        "selector": f"{name}@{version_range}",
        "optional": optional,
    }


def parse_yarn_v1(path):
    entries = []
    selector = None
    version = None
    resolved = None
    dependencies = []
    dependency_section = None

    def save():
        if selector and version:
            # A Yarn v1 header may contain multiple comma-separated selectors.
            selectors = re.findall(r'"([^"]+)"|([^,]+)', selector)
            for quoted, unquoted in selectors:
                item = (quoted or unquoted).strip().strip('"')
                # Split name from range, supporting scoped npm packages.
                if item.startswith("@"):
                    split_at = item.find("@", 1)
                    if split_at < 0:
                        continue
                else:
                    split_at = item.rfind("@")
                if split_at <= 0:
                    continue

                name = item[:split_at]
                entries.append({
                    "name": name,
                    "selector": item,
                    "version": version,
                    "resolved": resolved,
                    "lockfile": path.as_posix(),
                    "dependencies": dependencies,
                })

    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line[0].isspace() and not line.startswith("#"):
            save()
            selector = line[:-1] if line.endswith(":") else line
            version = None
            resolved = None
            dependencies = []
            dependency_section = None
        elif line.startswith("  dependencies:"):
            dependency_section = "dependencies"
        elif line.startswith("  optionalDependencies:"):
            dependency_section = "optionalDependencies"
        elif line.startswith("    ") and dependency_section:
            dependency = _dependency_entry(
                line,
                optional=dependency_section == "optionalDependencies",
            )
            if dependency:
                dependencies.append(dependency)
        elif line.startswith("  ") and not line.startswith("    "):
            dependency_section = None
            if line.startswith("  version "):
                match = re.search(r'"([^"]+)"', line)
                version = match.group(1) if match else None
            elif line.startswith("  resolved "):
                match = re.search(r'"([^"]+)"', line)
                resolved = match.group(1) if match else None

    save()
    return entries


def main():
    all_entries = []
    for lockfile in LOCKFILES:
        if not lockfile.exists():
            print(f"MISSING: {lockfile}")
            continue
        entries = parse_yarn_v1(lockfile)
        all_entries.extend(entries)
        print(f"{lockfile}: {len(entries)} package-selector entries")

    output = Path(os.getenv(
        "TECHCOMPASS_LOCKFILE_INDEX",
        "data/index/resolved_versions.json",
    ))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(all_entries, indent=2),
        encoding="utf-8",
    )

    print(f"\nTotal entries: {len(all_entries)}")
    print(f"Unique package/version pairs: {len({
        (e['name'], e['version']) for e in all_entries
    })}")
    print(f"Saved to: {output}")
    print("\nSample:")
    for entry in all_entries[:5]:
        print(entry["name"], entry["version"], "<-", entry["selector"])


if __name__ == "__main__":
    main()
