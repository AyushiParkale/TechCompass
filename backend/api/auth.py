import base64
import hashlib
import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException

TOKEN_LIFETIME_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_ATTEMPT_LIMIT = 8

_login_attempts: dict[str, deque[float]] = defaultdict(deque)
_login_attempts_lock = threading.Lock()


def _auth_secret() -> str:
    secret = os.getenv("TECHCOMPASS_AUTH_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError(
            "TECHCOMPASS_AUTH_SECRET must contain at least 32 characters"
        )
    return secret


def _encode_payload(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_payload(payload: str) -> bytes:
    padded_payload = payload + "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(padded_payload.encode("ascii"))


def issue_access_token(now: int | None = None) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = json.dumps(
        {"exp": issued_at + TOKEN_LIFETIME_SECONDS},
        separators=(",", ":"),
    ).encode("utf-8")
    encoded_payload = _encode_payload(payload)
    signature = hmac.new(
        _auth_secret().encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{encoded_payload}.{_encode_payload(signature)}"


def is_valid_access_token(
    token: str,
    now: int | None = None,
) -> bool:
    try:
        encoded_payload, encoded_signature = token.split(".", maxsplit=1)
        supplied_signature = _decode_payload(encoded_signature)
        expected_signature = hmac.new(
            _auth_secret().encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            return False

        payload = json.loads(_decode_payload(encoded_payload))
        expires_at = payload["exp"]
        current_time = int(time.time()) if now is None else now
        return (
            isinstance(expires_at, int)
            and not isinstance(expires_at, bool)
            and current_time < expires_at
        )
    except (
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
    ):
        return False


def check_login_password(
    password: str,
    client_ip: str,
    now: float | None = None,
) -> bool:
    current_time = time.time() if now is None else now

    with _login_attempts_lock:
        attempts = _login_attempts[client_ip]
        while attempts and current_time - attempts[0] >= LOGIN_WINDOW_SECONDS:
            attempts.popleft()
        if len(attempts) >= LOGIN_ATTEMPT_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="Too many login attempts. Try again later.",
            )
        attempts.append(current_time)

    expected_password = os.getenv("TECHCOMPASS_ACCESS_PASSWORD", "")
    if not expected_password:
        raise HTTPException(
            status_code=503,
            detail="Chat access is not configured",
        )
    try:
        _auth_secret()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Chat access is not configured",
        ) from exc
    matches = hmac.compare_digest(
        password.encode("utf-8"),
        expected_password.encode("utf-8"),
    )

    if matches:
        with _login_attempts_lock:
            _login_attempts.pop(client_ip, None)
    return matches


def require_access_token(
    authorization: str | None = Header(default=None),
) -> None:
    try:
        _auth_secret()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Chat access is not configured",
        ) from exc
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Sign in to use TechCompass",
            headers={"WWW-Authenticate": "Bearer"},
        )

    scheme, separator, token = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or not is_valid_access_token(token)
    ):
        raise HTTPException(
            status_code=401,
            detail="Session expired or invalid. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
