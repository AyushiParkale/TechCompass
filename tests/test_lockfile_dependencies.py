import tempfile
import unittest
from pathlib import Path

from backend.graph.ingest_lockfile_versions import build_dependency_rows
from backend.graph.parse_yarn_lockfiles import parse_yarn_v1


class YarnLockfileDependencyTests(unittest.TestCase):
    def test_parser_extracts_required_and_optional_dependency_selectors(self):
        lockfile = """\
package-a@^1.0.0:
  version "1.2.3"
  dependencies:
    "@scope/required" "^2.0.0"
  optionalDependencies:
    optional-package "3.0.0"
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "yarn.lock"
            path.write_text(lockfile, encoding="utf-8")

            entries = parse_yarn_v1(path)

        self.assertEqual(
            entries[0]["dependencies"],
            [
                {
                    "name": "@scope/required",
                    "selector": "@scope/required@^2.0.0",
                    "optional": False,
                },
                {
                    "name": "optional-package",
                    "selector": "optional-package@3.0.0",
                    "optional": True,
                },
            ],
        )

    def test_builds_version_edges_for_lockfile_selectors(self):
        rows = [
            {
                "name": "source",
                "version": "1.0.0",
                "selector": "source@^1.0.0",
                "lockfile": "server/yarn.lock",
                "dependencies": [
                    {
                        "name": "target",
                        "selector": "target@^2.0.0",
                        "optional": False,
                    }
                ],
            },
            {
                "name": "target",
                "version": "2.1.0",
                "selector": "target@^2.0.0",
                "lockfile": "server/yarn.lock",
                "dependencies": [],
            },
        ]

        edges, unresolved = build_dependency_rows(rows)

        self.assertEqual(unresolved, 0)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["target_version"], "2.1.0")
        self.assertFalse(edges[0]["optional"])

    def test_counts_unresolved_selectors_instead_of_guessing(self):
        rows = [
            {
                "name": "source",
                "version": "1.0.0",
                "selector": "source@^1.0.0",
                "lockfile": "server/yarn.lock",
                "dependencies": [
                    {
                        "name": "missing",
                        "selector": "missing@^2.0.0",
                        "optional": False,
                    }
                ],
            }
        ]

        edges, unresolved = build_dependency_rows(rows)

        self.assertEqual(edges, [])
        self.assertEqual(unresolved, 1)


if __name__ == "__main__":
    unittest.main()
