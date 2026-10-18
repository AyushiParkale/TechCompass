import unittest

from backend.agent.graph_agent import TOOLS, _validate_arguments
from backend.graph.query_service import trace_component_dependencies


class FakeGraph:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.queries = []

    def ro_query(self, cypher, params):
        self.queries.append((cypher, params))
        return next(self.responses)


class QueryResult:
    def __init__(self, columns, rows):
        self.header = list(enumerate(columns))
        self.result_set = rows


class ComponentDependencyTraceTests(unittest.TestCase):
    def test_returns_component_path_and_import_evidence(self):
        graph = FakeGraph([
            QueryResult(
                ["component", "manifest_path"],
                [["server", "server/package.json"]],
            ),
            QueryResult(
                [
                    "source_component",
                    "direct_package",
                    "declared_range",
                    "dependency_type",
                    "resolved_version",
                    "hops",
                    "dependency_path",
                    "versions",
                    "lockfile",
                    "lockfile_selector",
                    "dependency_evidence",
                ],
                [[
                    "server",
                    "@langchain/core",
                    "^0.2.0",
                    "runtime",
                    "0.2.36",
                    4,
                    [
                        "server",
                        "@langchain/core",
                        "@langchain/community",
                        "langchain",
                    ],
                    ["0.2.36", "0.2.36", "0.1.36"],
                    "data/source/anything-llm/server/yarn.lock",
                    "@langchain/core@^0.2.0",
                    [
                        {
                            "dependency_selector": (
                                "@langchain/community@^0.2.23"
                            ),
                            "optional": False,
                        }
                    ],
                ]],
            ),
        ])

        result = trace_component_dependencies(graph, "server", depth=4)

        self.assertEqual(result["status"], "paths_found")
        self.assertEqual(result["depth"], 4)
        self.assertEqual(result["paths"][0]["hops"], 4)
        self.assertEqual(
            result["paths"][0]["dependency_path"][-1],
            "langchain",
        )
        self.assertIn(
            "resolution.selector",
            graph.queries[1][0],
        )
        self.assertIn(
            "edge.lockfile_path = resolution.lockfile_path",
            graph.queries[1][0],
        )
        self.assertEqual(graph.queries[1][1]["depth"], 4)

    def test_returns_explicit_message_when_component_is_not_modeled(self):
        graph = FakeGraph([
            QueryResult(["component", "manifest_path"], []),
        ])

        result = trace_component_dependencies(graph, "unknown-service", 1)

        self.assertEqual(result["status"], "component_not_found")
        self.assertIn("not modeled", result["message"])
        self.assertEqual(len(graph.queries), 1)

    def test_returns_explicit_message_when_no_path_exists_at_depth(self):
        graph = FakeGraph([
            QueryResult(
                ["component", "manifest_path"],
                [["server", "server/package.json"]],
            ),
            QueryResult(
                [
                    "source_component",
                    "target_component",
                    "hops",
                    "components",
                    "evidence",
                ],
                [],
            ),
        ])

        result = trace_component_dependencies(graph, "server", 1)

        self.assertEqual(result["status"], "no_paths")
        self.assertIn("may mean the dependency is not modeled", result["message"])

    def test_rejects_depth_outside_supported_range(self):
        graph = FakeGraph([])

        with self.assertRaises(ValueError):
            trace_component_dependencies(graph, "server", 5)

        self.assertEqual(graph.queries, [])

    def test_agent_accepts_bounded_integer_depth(self):
        validated = _validate_arguments(
            "trace_component_dependencies",
            {"component": "server", "depth": 4},
        )

        self.assertEqual(validated, {"component": "server", "depth": 4})

    def test_agent_rejects_string_depth(self):
        with self.assertRaises(ValueError):
            _validate_arguments(
                "trace_component_dependencies",
                {"component": "server", "depth": "4"},
            )

    def test_impact_assessment_tool_does_not_accept_confidence(self):
        tool = next(
            tool["function"]
            for tool in TOOLS
            if tool["function"]["name"] == "record_external_impact_assessment"
        )
        self.assertNotIn("confidence", tool["parameters"]["properties"])
        arguments = {
            "item_id": "a" * 64,
            "capability_id": "retrieval",
            "relevance": "hypothesis",
            "rationale": "A source-backed connection.",
            "potential_impact": "It may improve retrieval.",
            "next_step": "Compare the documented interfaces.",
        }
        self.assertEqual(
            _validate_arguments("record_external_impact_assessment", arguments),
            arguments,
        )
        with self.assertRaises(ValueError):
            _validate_arguments(
                "record_external_impact_assessment",
                {**arguments, "confidence": "low"},
            )


if __name__ == "__main__":
    unittest.main()
