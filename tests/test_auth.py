import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.api.auth import (
    LOGIN_ATTEMPT_LIMIT,
    LOGIN_WINDOW_SECONDS,
    _login_attempts,
    check_login_password,
    is_valid_access_token,
    issue_access_token,
    require_access_token,
)


class AccessTokenTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {"TECHCOMPASS_AUTH_SECRET": "test-secret-" + "x" * 32},
        )
        self.environment.start()

    def tearDown(self):
        self.environment.stop()

    def test_token_is_valid_until_expiry(self):
        token = issue_access_token(now=1000)

        self.assertTrue(is_valid_access_token(token, now=1001))
        self.assertFalse(
            is_valid_access_token(
                token,
                now=1000 + 12 * 60 * 60,
            )
        )

    def test_tampered_token_is_invalid(self):
        token = issue_access_token(now=1000)
        payload, signature = token.split(".", maxsplit=1)
        tampered_token = f"{payload[:-1]}A.{signature}"

        self.assertFalse(is_valid_access_token(tampered_token, now=1001))

    def test_auth_dependency_rejects_missing_token(self):
        with self.assertRaises(HTTPException) as raised:
            require_access_token(authorization=None)

        self.assertEqual(raised.exception.status_code, 401)

    def test_login_attempts_are_limited(self):
        client_ip = "test-login-rate-limit"
        _login_attempts.pop(client_ip, None)
        with patch.dict(
            os.environ,
            {"TECHCOMPASS_ACCESS_PASSWORD": "private"},
        ):
            for attempt in range(LOGIN_ATTEMPT_LIMIT):
                check_login_password("wrong", client_ip, now=attempt)

            with self.assertRaises(HTTPException) as raised:
                check_login_password(
                    "wrong",
                    client_ip,
                    now=LOGIN_ATTEMPT_LIMIT,
                )

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(LOGIN_WINDOW_SECONDS, 15 * 60)
        _login_attempts.pop(client_ip, None)


if __name__ == "__main__":
    unittest.main()
