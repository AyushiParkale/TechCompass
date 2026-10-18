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

    def test_update_report_route_is_public_and_requires_graph(self):
        self.assert_public_route("/api/updates/run")
        self.assert_graph_unavailable(
            main.run_project_update,
            request=main.UpdateReportRequest(days=30),
        )

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
