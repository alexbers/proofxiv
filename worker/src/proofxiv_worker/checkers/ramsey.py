"""Ramsey colourings: ``graph_ramsey_coloring`` and ``hypergraph_ramsey_coloring``.

Both searches are exact branch-and-bound clique searches over bitsets (Python integers).  A set is
only ever extended by vertices that keep every k-subset monochromatic in the colour being searched,
so the work is proportional to the number of monochromatic partial cliques, not to C(n, s).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .common import (
    CheckResult,
    MalformedCertificate,
    binom,
    check_fields,
    decode_base64_strict,
    get_int,
    get_list,
    get_str,
    int_list,
)

MAX_GRAPH_N = 4096
MAX_COLOURS = 10
MAX_HYPER_N = 1024
MAX_SUBSETS = 1 << 27


# ---------------------------------------------------------------------------------------------
# graph_ramsey_coloring
# ---------------------------------------------------------------------------------------------


def _find_clique(adj: list[int], n: int, size: int) -> list[int] | None:
    """Return the vertices of a clique with exactly ``size`` vertices, or None."""
    if size <= 0:
        return []
    if n < size:
        return None
    # iterative depth-first search: cands[d] = vertices that may extend clique[:d]
    clique: list[int] = []
    cands = [(1 << n) - 1]
    while cands:
        cand = cands[-1]
        need = size - len(clique)
        if cand.bit_count() < need:  # also covers cand == 0
            cands.pop()
            if clique:
                clique.pop()
            continue
        v = cand.bit_length() - 1  # highest remaining vertex
        cand ^= 1 << v
        cands[-1] = cand
        clique.append(v)
        if len(clique) == size:
            return clique
        cands.append(cand & adj[v])
    return None


def check_graph_ramsey(data: Any) -> CheckResult:
    ctype = "graph_ramsey_coloring"
    check_fields(data, ctype, ["n", "colours", "forbidden_clique_sizes", "edge_colours"])
    n = get_int(data, "n", 0, MAX_GRAPH_N)
    c = get_int(data, "colours", 1, MAX_COLOURS)
    sizes = int_list(data["forbidden_clique_sizes"], "field 'forbidden_clique_sizes'", c, 1, None)
    text = get_str(data, "edge_colours")
    m = n * (n - 1) // 2
    if len(text) != m:
        raise MalformedCertificate(f"field 'edge_colours' must have length n(n-1)/2 = {m}")
    raw = (
        np.frombuffer(text.encode("ascii", errors="replace"), dtype=np.uint8)
        if m
        else np.zeros(0, np.uint8)
    )
    if m and (raw.min() < ord("0") or raw.max() >= ord("0") + c):
        bad = next(ch for ch in text if not ("0" <= ch < chr(ord("0") + c)))
        raise MalformedCertificate(
            f"field 'edge_colours' contains {bad!r}, expected digits 0..{c - 1}"
        )
    colours = raw.astype(np.int16) - ord("0")
    # matrix[i][j] = colour of edge {i, j}; edge {i<j} has colex rank j(j-1)/2 + i
    matrix = np.full((n, n), -1, dtype=np.int16)
    for j in range(1, n):
        start = j * (j - 1) // 2
        matrix[j, :j] = colours[start : start + j]
    matrix = np.maximum(matrix, matrix.T)
    counts = [int(x) for x in np.bincount(colours, minlength=c)[:c]] if m else [0] * c
    stats = {"n": n, "colours": c, "edges_per_colour": counts}
    for col in range(c):
        s = sizes[col]
        if s > n:
            continue
        mask = matrix == col
        packed = np.packbits(mask, axis=1, bitorder="little") if n else np.zeros((0, 0), np.uint8)
        adj = [int.from_bytes(packed[v].tobytes(), "little") for v in range(n)]
        clique = _find_clique(adj, n, s)
        if clique is not None:
            clique.sort()
            return CheckResult(
                False,
                f"colour {col} contains a clique of size {s}: vertices {clique}",
                stats,
            )
    return CheckResult(
        True,
        f"no colour-i clique of size s_i for any i; the colouring of K_{n} shows "
        f"R({', '.join(map(str, sizes))}) > {n}",
        stats,
    )


# ---------------------------------------------------------------------------------------------
# hypergraph_ramsey_coloring
# ---------------------------------------------------------------------------------------------


def _colex_subsets(n: int, t: int) -> np.ndarray:
    """All t-subsets of {0..n-1} as rows (sorted ascending), in colex order."""
    if t == 0:
        return np.zeros((1, 0), dtype=np.int32)
    prev = _colex_subsets(n - 1, t - 1) if n >= 1 else np.zeros((0, t - 1), dtype=np.int32)
    blocks = []
    for top in range(t - 1, n):
        count = binom(top, t - 1)
        block = np.empty((count, t), dtype=np.int32)
        block[:, : t - 1] = prev[:count]
        block[:, t - 1] = top
        blocks.append(block)
    if not blocks:
        return np.zeros((0, t), dtype=np.int32)
    return np.concatenate(blocks, axis=0)


def _link_bitsets(n: int, k: int, bits: np.ndarray) -> tuple[list[int], list[int]]:
    """For every (k-1)-subset T (by colex rank): bitsets of the u with colour(T+u) = 0 resp. 1."""
    binom_table = np.zeros((n + 1, k + 2), dtype=np.int64)
    for x in range(n + 1):
        for j in range(k + 2):
            binom_table[x, j] = binom(x, j)
    all_subsets = _colex_subsets(n, k - 1)  # shape (C(n,k-1), k-1)
    link0: list[int] = []
    link1: list[int] = []
    step = max(1, (1 << 24) // n)  # rows per chunk: at most ~16M booleans per temporary array
    for start in range(0, all_subsets.shape[0], step):
        subsets = all_subsets[start : start + step]
        rows = subsets.shape[0]
        colour = np.zeros((rows, n), dtype=bool)
        valid = np.ones((rows, n), dtype=bool)
        for u in range(n):
            larger = subsets > u  # elements of T above u move one position up in T + {u}
            less_count = (~larger).sum(axis=1)
            member = (subsets == u).any(axis=1)
            rank = np.zeros(rows, dtype=np.int64)
            for i in range(k - 1):
                rank += binom_table[subsets[:, i], i + 1 + larger[:, i]]
            rank += binom_table[u, less_count + 1]
            rank[member] = 0
            colour[:, u] = bits[rank]
            valid[member, u] = False
        red = np.packbits(colour & valid, axis=1, bitorder="little")
        blue = np.packbits(~colour & valid, axis=1, bitorder="little")
        link0.extend(int.from_bytes(blue[r].tobytes(), "little") for r in range(rows))
        link1.extend(int.from_bytes(red[r].tobytes(), "little") for r in range(rows))
    return link0, link1


def _find_mono_set(n: int, k: int, size: int, link: list[int]) -> list[int] | None:
    """A ``size``-subset of {0..n-1} all of whose k-subsets lie in the colour described by
    ``link`` (link[rank(T)] = bitset of u with T+u of that colour), or None."""
    if size > n:
        return None
    if size < k:
        return list(range(size))
    binoms = [[binom(x, j) for j in range(k + 1)] for x in range(n + 1)]
    # Iterative depth-first search over increasing sets `current`.  For every level d:
    # cands[d] = vertices above max(current[:d]) that keep all k-subsets in the colour, and
    # partials[d][t] = partial colex ranks sum_i C(a_i, i+1) of the t-subsets of current[:d].
    initial: list[list[int]] = [[] for _ in range(max(k - 1, 1))]
    initial[0] = [0]
    current: list[int] = []
    cands = [(1 << n) - 1]
    partials = [initial]
    while cands:
        cand = cands[-1]
        partial = partials[-1]
        need = size - len(current)
        if cand.bit_count() < need:  # also covers cand == 0
            cands.pop()
            partials.pop()
            if current:
                current.pop()
            continue
        low = cand & -cand
        v = low.bit_length() - 1
        cand ^= low
        cands[-1] = cand
        depth = len(current)
        if depth + 1 == size:
            return current + [v]
        new_cand = cand
        if depth >= k - 2:  # new (k-1)-subsets T' + {v}, T' a (k-2)-subset of current
            bv = binoms[v][k - 1]
            for pr in partial[k - 2]:
                new_cand &= link[pr + bv]
                if not new_cand:
                    break
        if new_cand.bit_count() < need - 1:
            continue
        new_partial = [list(p) for p in partial]
        for t in range(min(depth + 1, k - 2), 0, -1):
            bvt = binoms[v][t]
            new_partial[t].extend(r + bvt for r in partial[t - 1])
        current.append(v)
        cands.append(new_cand)
        partials.append(new_partial)
    return None


def check_hypergraph_ramsey(data: Any) -> CheckResult:
    ctype = "hypergraph_ramsey_coloring"
    check_fields(data, ctype, ["n", "k", "forbidden_sizes", "colouring"])
    n = get_int(data, "n", 1, MAX_HYPER_N)
    k = get_int(data, "k", 2, 8)
    if k > n:
        raise MalformedCertificate("field 'k' must not exceed n")
    sizes = int_list(get_list(data, "forbidden_sizes"), "field 'forbidden_sizes'", 2, 1, None)
    total = binom(n, k)
    if total > MAX_SUBSETS:
        raise MalformedCertificate(f"C(n, k) = {total} exceeds the limit {MAX_SUBSETS}")
    raw = decode_base64_strict(data["colouring"], "field 'colouring'")
    if len(raw) != (total + 7) // 8:
        raise MalformedCertificate(
            f"field 'colouring' must decode to ceil(C(n,k)/8) = {(total + 7) // 8} bytes, got {len(raw)}"
        )
    bits = np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="big")
    if bits[total:].any():
        raise MalformedCertificate("padding bits after the last k-subset must be zero")
    bits = bits[:total].astype(bool)
    ones = int(bits.sum())
    stats = {"n": n, "k": k, "colour_counts": [total - ones, ones]}
    links = None
    for colour in (0, 1):
        s = sizes[colour]
        if s > n:
            continue
        if s < k:
            found = list(range(s))
        else:
            if links is None:
                links = _link_bitsets(n, k, bits)
            found = _find_mono_set(n, k, s, links[colour])
        if found is not None:
            return CheckResult(
                False,
                f"the {s}-set {sorted(found)} has all its {k}-subsets of colour {colour}",
                stats,
            )
    return CheckResult(
        True,
        f"no {sizes[0]}-set with all {k}-subsets of colour 0 and no {sizes[1]}-set with all "
        f"{k}-subsets of colour 1; shows R({sizes[0]}, {sizes[1]}; {k}) > {n}",
        stats,
    )
