
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import jwt

from app.core.config import get_settings


def create_access_token(subject: str) -> str:
    """Create a short-lived JWT access token."""

    settings = get_settings()

    now = datetime.now(timezone.utc)

    payload = {
        "sub": subject,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(
            minutes=settings.access_token_expire_minutes
        ),
        "jti": str(uuid4()),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def create_refresh_token(subject: str) -> str:
    """Create a longer-lived JWT refresh token."""

    settings = get_settings()

    now = datetime.now(timezone.utc)

    payload = {
        "sub": subject,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(
            days=settings.refresh_token_expire_days
        ),
        "jti": str(uuid4()),
    }

    return jwt.encode(
        payload,
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str) -> dict[str, Any]:
    """Verify a JWT signature and expiration, then return its claims."""

    settings = get_settings()

    return jwt.decode(
        token,
        settings.jwt_secret_key.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "iat", "sub", "type", "jti"]},
    )

def verify_token_type(token: str, expected_type: str) -> dict[str, Any]:
    """Decode a token and verify that it has the expected type."""

    payload = decode_token(token)

    if payload.get("type") != expected_type:
        raise ValueError("Invalid token type")

    return payload
