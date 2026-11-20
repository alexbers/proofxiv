"""Technical limits: too many writes from one caller get HTTP 429."""

from __future__ import annotations

from .conftest import login


async def test_write_rate_limit(transport, monkeypatch):
    from proofxiv_server.config import get_settings

    erin = await login(transport, "erin")  # a fresh session cookie = a fresh bucket
    monkeypatch.setattr(get_settings(), "rate_writes_per_minute", 5)
    statuses = []
    for _ in range(7):
        resp = await erin.client.post(
            "/papers/00000000-0000-0000-0000-000000000000/signals", json={"kind": "like"}
        )
        statuses.append(resp.status_code)
    assert statuses.index(429) == 5, statuses  # five writes per minute for this session
