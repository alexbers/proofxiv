"""Anonymous reads of public pages are cached briefly; signed-in reads never are."""

from __future__ import annotations

import httpx


async def test_micro_cache(app):
    from proofxiv_server.api.cache import MicroCache

    transport = httpx.ASGITransport(app=MicroCache(app, ttl=30.0))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver/api/v1") as c:
        first = await c.get("/stats")
        second = await c.get("/stats")
        signed = await c.get("/stats", headers={"Authorization": "Bearer pxv_x"})
        bypass = await c.get("/stats", headers={"Cache-Control": "no-cache"})
        await c.post("/auth/logout")  # any successful write empties the cache
        after_write = await c.get("/stats")
    assert first.headers["x-cache"] == "miss" and second.headers["x-cache"] == "hit"
    assert first.json() == second.json()
    assert "x-cache" not in signed.headers
    assert bypass.headers["x-cache"] == "miss" and after_write.headers["x-cache"] == "miss"
