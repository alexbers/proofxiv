"""Time each read endpoint sequentially (single client) to find slow ones."""

from __future__ import annotations

import sys
import time

import httpx


def main() -> None:
    base = sys.argv[1]
    with httpx.Client(base_url=base, timeout=30) as c:
        papers = c.get("/api/v1/papers?limit=20").json()
        problems = c.get("/api/v1/problems?limit=20").json()
        paths = {
            "feed": "/api/v1/papers",
            "problems": "/api/v1/problems",
            "problems_search": "/api/v1/problems?q=Ramsey",
            "stats": "/api/v1/stats",
            "state": "/api/v1/state",
            "feed_json": "/api/v1/feeds/papers.json",
            "trusted": "/api/v1/trusted-operators",
        }
        if papers:
            paths["paper"] = f"/api/v1/papers/{papers[0]['id']}"
        if problems:
            paths["problem"] = f"/api/v1/problems/{problems[0]['slug']}"
        for name, path in paths.items():
            times = []
            for _ in range(10):
                t = time.monotonic()
                c.get(path).raise_for_status()
                times.append((time.monotonic() - t) * 1000)
            times.sort()
            print(f"{name:16s} median {times[5]:6.1f} ms   max {times[-1]:6.1f} ms")


if __name__ == "__main__":
    main()
