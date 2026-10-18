import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.routing import APIRoute

from backend.api import main


class ApiAccessTests(unittest.TestCase):
    def setUp(self):
        self.graph_patch = patch("backend.api.main.graph", None)
        self.graph_patch.start()

    def tearDown(self):
        self.graph_patch.stop()

    def assert_public_route(self, path):
        route = next(
            route for route in main.app.routes
            if isinstance(route, APIRoute) and route.path == path
        )
        self.assertEqual(route.dependant.dependencies, [])

    def assert_graph_unavailable(self, callback, *args, **kwargs):
        with self.assertRaises(HTTPException) as raised:
            callback(*args, **kwargs)
        self.assertEqual(raised.exception.status_code, 503)

    def test_graph_routes_are_public(self):
        self.assert_public_route("/api/graph/project")
        self.assert_graph_unavailable(main.project_overview)

    def test_component_impact_route_is_public(self):
        self.assert_public_route("/api/graph/component-impact")
        self.assert_graph_unavailable(
            main.component_impact, component="server", depth=4
        )

    def test_external_knowledge_routes_are_public(self):
        cases = (
            ("/api/graph/visualization", main.graph_visualization,
             {"component": "server", "depth": 4}),
            ("/api/knowledge/search", main.external_knowledge_search,
             {"q": "react"}),
            ("/api/knowledge/sync", main.sync_knowledge, {}),
            ("/api/knowledge/preview", main.preview_knowledge, {}),
            ("/api/knowledge/articles", main.submit_article,
             {"request": main.ArticleSubmission(url="https://example.org/article")}),
            ("/api/agent/ask", main.ask_agent,
             {"request": main.AgentQuestion(question="Explain this project")}),
        )
        for path, endpoint, kwargs in cases:
            with self.subTest(path=path):
                self.assert_public_route(path)
                self.assert_graph_unavailable(endpoint, **kwargs)

    def test_login_route_is_removed(self):
        paths = {
            route.path for route in main.app.routes
            if isinstance(route, APIRoute)
        }
        self.assertNotIn("/api/auth/login", paths)

    def test_cors_allowlist_excludes_unconfigured_origin(self):
        cors = next(
            middleware for middleware in main.app.user_middleware
            if middleware.cls.__name__ == "CORSMiddleware"
        )
        self.assertNotIn("https://untrusted.example", cors.kwargs["allow_origins"])


if __name__ == "__main__":
    unittest.main()
