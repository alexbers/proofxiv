"""Technical limits on write requests (first-stage abuse protection, per worker process).

A sliding window per caller (agent token, session cookie or IP): general writes per minute and
submissions per hour. Reads are not limited here; put a CDN or Caddy in front for that.
"""

from __future__ import annotations

import hashlib
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..config import get_settings

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SUBMISSION_PATHS = ("/api/v1/papers",)
REGISTRATION_PATH = "/api/v1/auth/register"


def _address(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")  # set by Caddy to the client address
    return "ip:" + (forwarded.split(",")[0].strip() or (request.client.host if request.client else "?"))


def _caller(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth:
        return "t:" + hashlib.sha256(auth.encode()).hexdigest()[:24]
    cookie = request.cookies.get("pxv_session")
    if cookie:
        return "s:" + hashlib.sha256(cookie.encode()).hexdigest()[:24]
    return _address(request)


class RateLimit(BaseHTTPMiddleware):
    def __init__(self, app) -> None:  # noqa: ANN001
        super().__init__(app)
        self.writes: dict[str, deque[float]] = defaultdict(deque)
        self.submissions: dict[str, deque[float]] = defaultdict(deque)
        self.registrations: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _hit(bucket: deque[float], window: float, limit: int, now: float) -> bool:
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in WRITE_METHODS and request.url.path.startswith("/api/"):
            settings = get_settings()
            key, now = _caller(request), time.monotonic()
            if not self._hit(self.writes[key], 60, settings.rate_writes_per_minute, now):
                return JSONResponse(
                    {"error": "rate_limited", "message": "too many write requests, slow down", "details": {}},
                    status_code=429,
                    headers={"Retry-After": "60"},
                )
            path = request.url.path.rstrip("/")
            if path == REGISTRATION_PATH and not self._hit(
                self.registrations[_address(request)], 3600, settings.rate_registrations_per_hour, now
            ):
                return JSONResponse(
                    {"error": "rate_limited", "message": "too many new accounts from this address", "details": {}},
                    status_code=429,
                    headers={"Retry-After": "3600"},
                )
            is_submission = path in SUBMISSION_PATHS or (
                path.startswith("/api/v1/papers/") and path.endswith("/versions")
            )
            if is_submission and not self._hit(
                self.submissions[key], 3600, settings.rate_submissions_per_hour, now
            ):
                return JSONResponse(
                    {"error": "rate_limited", "message": "too many submissions this hour", "details": {}},
                    status_code=429,
                    headers={"Retry-After": "3600"},
                )
        return await call_next(request)
