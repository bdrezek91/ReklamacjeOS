from __future__ import annotations

import base64
import hashlib
import hmac
import time

import bcrypt

from .config import settings

COOKIE_NAME = "reklamacjeos_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


def auth_enabled() -> bool:
    return bool(settings.panel_user and settings.panel_password_hash_b64 and settings.auth_session_secret)


def verify_credentials(username: str, password: str) -> bool:
    if not auth_enabled():
        return False
    if not hmac.compare_digest(username.strip(), settings.panel_user):
        return False
    try:
        password_hash = base64.b64decode(settings.panel_password_hash_b64.encode("ascii"))
        return bcrypt.checkpw(password.encode("utf-8"), password_hash)
    except (ValueError, TypeError):
        return False


def make_session(username: str) -> str:
    expires = int(time.time()) + SESSION_TTL_SECONDS
    payload = f"{username}:{expires}"
    signature = hmac.new(
        settings.auth_session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    raw = f"{payload}:{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def valid_session(token: str | None) -> bool:
    if not auth_enabled() or not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, expires_text, signature = raw.rsplit(":", 2)
        expires = int(expires_text)
    except (ValueError, UnicodeDecodeError):
        return False
    if expires < int(time.time()):
        return False
    if not hmac.compare_digest(username, settings.panel_user):
        return False
    payload = f"{username}:{expires}"
    expected = hmac.new(
        settings.auth_session_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
