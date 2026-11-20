"""Bearer tokens for agents and session cookies for people."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt

TOKEN_PREFIX = "pxv_"

# Scopes a token can carry. Trusted work additionally needs a role and a signing key.
SCOPE_AGENT = "agent"  # research, submissions, exchange reviews, reports, sync
SCOPE_RUNNER = "runner"  # run jobs
SCOPE_TRUSTED = "trusted"  # weekly trusted run
ALL_SCOPES = frozenset({SCOPE_AGENT, SCOPE_RUNNER, SCOPE_TRUSTED})


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_user_code() -> str:
    alphabet = "BCDFGHJKLMNPQRSTVWXZ"
    raw = "".join(secrets.choice(alphabet) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def encode_session(operator_id: uuid.UUID, secret: str, days: int) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(operator_id), "iat": now, "exp": now + timedelta(days=days)}
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_session(token: str, secret: str) -> uuid.UUID | None:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        return uuid.UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
