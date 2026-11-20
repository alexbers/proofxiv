"""``regular_graph_girth``: a simple d-regular graph with girth exactly g.

The girth is computed exactly by breadth-first search from every vertex, vectorised over blocks
of sources: when the frontier at distance L is expanded, an edge to another vertex at distance L
closes an odd cycle of length 2L+1, and two frontier vertices reaching the same new vertex close an
even cycle of length 2L+2.  The minimum over all sources is the girth.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .common import CheckResult, MalformedCertificate, check_fields, get_int, get_list, is_int

MAX_N = 10_000_000
MAX_EDGES = 50_000_000


def _girth(n: int, indptr: np.ndarray, indices: np.ndarray) -> int | None:
    deg = np.diff(indptr)
    best = 1 << 62
    chunk = max(1, min(n, (1 << 23) // n))
    for s0 in range(0, n, chunk):
        srcs = np.arange(s0, min(n, s0 + chunk), dtype=np.int64)
        size = len(srcs)
        dist = np.full((size, n), -1, dtype=np.int32)
        dist[np.arange(size), srcs] = 0
        f_src = np.arange(size, dtype=np.int64)
        f_v = srcs
        f_par = np.full(size, -1, dtype=np.int64)
        level = 0
        while len(f_v) and 2 * level + 1 < best:
            d = deg[f_v]
            total = int(d.sum())
            if total == 0:
                break
            rep_src = np.repeat(f_src, d)
            rep_par = np.repeat(f_par, d)
            rep_v = np.repeat(f_v, d)
            offsets = np.arange(total, dtype=np.int64) - np.repeat(np.cumsum(d) - d, d)
            nb = indices[np.repeat(indptr[f_v], d) + offsets]
            keep = nb != rep_par
            rep_src, rep_v, nb = rep_src[keep], rep_v[keep], nb[keep]
            dn = dist[rep_src, nb]
            if np.any(dn == level):
                best = min(best, 2 * level + 1)
            new = dn == -1
            keys = rep_src[new] * n + nb[new]
            parents = rep_v[new]
            uniq, first, counts = np.unique(keys, return_index=True, return_counts=True)
            if len(counts) and counts.max() > 1:
                best = min(best, 2 * level + 2)
            f_src = uniq // n
            f_v = uniq % n
            f_par = parents[first]
            dist[f_src, f_v] = level + 1
            level += 1
    return None if best == 1 << 62 else best


def check_regular_graph_girth(data: Any) -> CheckResult:
    ctype = "regular_graph_girth"
    check_fields(data, ctype, ["n", "degree", "girth", "edges"])
    n = get_int(data, "n", 1, MAX_N)
    degree = get_int(data, "degree", 0, None)
    claimed_girth = get_int(data, "girth", 3, None)
    edges = get_list(data, "edges")
    if len(edges) > MAX_EDGES:
        raise MalformedCertificate(f"more than {MAX_EDGES} edges")
    for i, e in enumerate(edges):
        if not (isinstance(e, list) and len(e) == 2 and is_int(e[0]) and is_int(e[1])):
            raise MalformedCertificate(f"edges[{i}] must be a pair of integers")
        if not (0 <= e[0] < n and 0 <= e[1] < n):
            raise MalformedCertificate(f"edges[{i}] has an endpoint outside 0..{n - 1}")
    m = len(edges)
    arr = np.array(edges, dtype=np.int64).reshape(m, 2)
    u, v = arr[:, 0], arr[:, 1]
    degrees = np.bincount(u, minlength=n) + np.bincount(v, minlength=n)
    stats: dict[str, Any] = {
        "n": n,
        "edges": m,
        "simple": False,
        "min_degree": int(degrees.min()),
        "max_degree": int(degrees.max()),
        "girth": None,
    }
    loops = np.nonzero(u == v)[0]
    if len(loops):
        i = int(loops[0])
        return CheckResult(False, f"edges[{i}] is a loop at vertex {int(u[i])}", stats)
    lo, hi = np.minimum(u, v), np.maximum(u, v)
    keys = lo * n + hi
    order = np.argsort(keys, kind="stable")
    dup = np.nonzero(keys[order][1:] == keys[order][:-1])[0]
    if len(dup):
        i, j = sorted((int(order[dup[0]]), int(order[dup[0] + 1])))
        return CheckResult(
            False,
            f"edges[{i}] and edges[{j}] are the same edge {{{int(lo[i])}, {int(hi[i])}}}",
            stats,
        )
    stats["simple"] = True
    src = np.concatenate([u, v])
    dst = np.concatenate([v, u])
    order = np.argsort(src, kind="stable")
    indices = dst[order]
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(src, minlength=n), out=indptr[1:])
    girth = _girth(n, indptr, indices)
    stats["girth"] = girth
    if stats["min_degree"] != degree or stats["max_degree"] != degree:
        bad = int(np.nonzero(degrees != degree)[0][0])
        return CheckResult(
            False, f"vertex {bad} has degree {int(degrees[bad])}, claimed degree {degree}", stats
        )
    if girth is None:
        return CheckResult(False, f"the graph is acyclic, claimed girth {claimed_girth}", stats)
    if girth != claimed_girth:
        return CheckResult(False, f"the girth is {girth}, claimed {claimed_girth}", stats)
    return CheckResult(
        True, f"simple {degree}-regular graph on {n} vertices with girth {girth} verified", stats
    )
