import os
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.main import app


@asynccontextmanager
async def no_database_lifespan(_app):
    yield


class ApiAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "TECHCOMPASS_ACCESS_PASSWORD": "test-workspace-password",
                "TECHCOMPASS_AUTH_SECRET": "test-secret-" + "x" * 32,
            },
        )
        self.environment.start()
        self.original_lifespan = app.router.lifespan_context
        app.router.lifespan_context = no_database_lifespan
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        app.router.lifespan_context = self.original_lifespan
        self.environment.stop()

    def test_graph_endpoint_requires_login(self):
        response = self.client.get("/api/graph/project")

        self.assertEqual(response.status_code, 401)

    def test_login_token_unlocks_protected_endpoint(self):
        login = self.client.post(
            "/api/auth/login",
            json={"password": "test-workspace-password"},
        )
        self.assertEqual(login.status_code, 200)

        response = self.client.get(
            "/api/graph/project",
            headers={
                "Authorization": f"Bearer {login.json()['access_token']}"
            },
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "Graph database is not connected",
        )

    def test_cors_rejects_unconfigured_origin(self):
        response = self.client.options(
            "/api/agent/ask",
            headers={
                "Origin": "https://untrusted.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
