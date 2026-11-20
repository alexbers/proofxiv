"""A tiny response cache for anonymous reads of public pages (a few seconds of staleness).

Readers and crawlers mostly hit the same feed, catalog and paper pages; caching them for a few
seconds per process multiplies throughput on a small machine. Signed-in requests are never cached.
"""

from __future__ import annotations

import time
from collections import OrderedDict

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
CACHEABLE_PREFIXES = (
    "/api/v1/home",
    "/api/v1/papers",
    "/api/v1/problems",
    "/api/v1/stats",
    "/api/v1/state",
    "/api/v1/feeds/",
    "/api/v1/digests",
    "/api/v1/discussion",
    "/api/v1/trusted-operators",
    "/api/v1/moderation-log",
    "/api/v1/operators/",
    "/api/v1/oai",
)


class MicroCache(BaseHTTPMiddleware):
    def __init__(self, app, ttl: float = 10.0, max_entries: int = 2000) -> None:  # noqa: ANN001
        super().__init__(app)
        self.ttl = ttl
        self.max_entries = max_entries
        self.entries: OrderedDict[str, tuple[float, int, bytes, dict[str, str]]] = OrderedDict()

    def _cacheable(self, request: Request) -> bool:
        return (
            request.method == "GET"
            and request.url.path.startswith(CACHEABLE_PREFIXES)
            and "authorization" not in request.headers
            and "pxv_session" not in request.cookies
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in READ_METHODS:
            response = await call_next(request)
            if response.status_code < 400:
                # a write can change any public page; on a small site the cache refills at once
                self.entries.clear()
            return response
        if not self._cacheable(request):
            return await call_next(request)
        key = str(request.url)
        now = time.monotonic()
        fresh = "no-cache" in request.headers.get("cache-control", "")
        hit = None if fresh else self.entries.get(key)
        if hit is not None and hit[0] > now:
            self.entries.move_to_end(key)
            return Response(hit[2], status_code=hit[1], headers={**hit[3], "X-Cache": "hit"})
        response = await call_next(request)
        if response.status_code != 200:
            return response
        body = b"".join([chunk async for chunk in response.body_iterator])  # type: ignore[attr-defined]
        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        self.entries[key] = (now + self.ttl, response.status_code, body, headers)
        if len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)
        return Response(body, status_code=response.status_code, headers={**headers, "X-Cache": "miss"})
