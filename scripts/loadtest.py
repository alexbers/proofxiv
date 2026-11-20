"""Read-path load test: concurrent clients hitting the feed, paper pages, problems and feeds.

Usage: .venv/bin/python scripts/loadtest.py http://127.0.0.1:8000 --clients 50 --seconds 30
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx


async def worker(client: httpx.AsyncClient, paths: list[str], deadline: float, out: list, errors: list) -> None:
    while time.monotonic() < deadline:
        path = random.choice(paths)
        started = time.monotonic()
        try:
            resp = await client.get(path)
            if resp.status_code >= 500:
                errors.append(resp.status_code)
        except httpx.HTTPError as exc:
            errors.append(type(exc).__name__)
            continue
        out.append(time.monotonic() - started)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("base")
    parser.add_argument("--clients", type=int, default=50)
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()
    limits = httpx.Limits(max_connections=args.clients, max_keepalive_connections=args.clients)
    async with httpx.AsyncClient(base_url=args.base, timeout=30, limits=limits) as client:
        papers = (await client.get("/api/v1/papers?limit=50")).json()
        problems = (await client.get("/api/v1/problems?limit=50")).json()
        paths = ["/api/v1/papers", "/api/v1/problems", "/api/v1/stats", "/api/v1/feeds/papers.json"]
        paths += [f"/api/v1/papers/{p['id']}" for p in papers]
        paths += [f"/api/v1/problems/{p['slug']}" for p in problems]
        latencies: list[float] = []
        errors: list = []
        deadline = time.monotonic() + args.seconds
        await asyncio.gather(
            *(worker(client, paths, deadline, latencies, errors) for _ in range(args.clients))
        )
    latencies.sort()
    n = len(latencies)
    print(f"{n} requests in {args.seconds:.0f}s = {n / args.seconds:.0f} req/s, {len(errors)} errors")
    if n:
        print(
            f"latency p50 {statistics.median(latencies) * 1000:.0f} ms, "
            f"p95 {latencies[int(n * 0.95)] * 1000:.0f} ms, max {latencies[-1] * 1000:.0f} ms"
        )


if __name__ == "__main__":
    asyncio.run(main())
