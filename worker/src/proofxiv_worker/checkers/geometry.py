"""Point sets: ``affine_line_free_set``, ``kissing_configuration``, ``heilbronn_configuration``."""

from __future__ import annotations

from fractions import Fraction
from math import isqrt, lcm
from typing import Any

import numpy as np

from .common import (
    CheckResult,
    MalformedCertificate,
    check_fields,
    fraction_str,
    get_int,
    get_list,
    int_list,
    is_int,
    parse_rational,
)

# ---------------------------------------------------------------------------------------------
# affine_line_free_set
# ---------------------------------------------------------------------------------------------

MAX_POINTS = 1 << 20
MAX_LINE_WORK = 1 << 36


def _is_prime(p: int) -> bool:
    return p >= 2 and all(p % i for i in range(2, isqrt(p) + 1))


def check_affine_line_free(data: Any) -> CheckResult:
    ctype = "affine_line_free_set"
    check_fields(data, ctype, ["p", "dimension", "points"], ["size"])
    p = get_int(data, "p", 2, 1 << 20)
    if not _is_prime(p):
        raise MalformedCertificate(f"field 'p' = {p} is not prime")
    d = get_int(data, "dimension", 1, 64)
    if p**d >= 1 << 62:
        raise MalformedCertificate("p^dimension must be below 2^62")
    pts = get_list(data, "points")
    if len(pts) > MAX_POINTS:
        raise MalformedCertificate(f"more than {MAX_POINTS} points")
    for i, pt in enumerate(pts):
        int_list(pt, f"points[{i}]", d, 0, p - 1)
    claimed = get_int(data, "size", 0, None) if "size" in data else None
    count = len(pts)
    directions = (p**d - 1) // (p - 1)
    if min(directions, count) * count > MAX_LINE_WORK:
        raise MalformedCertificate("instance too large: min(#directions, |S|) * |S| exceeds 2^36")
    P = np.array(pts, dtype=np.int64).reshape(count, d)
    powers = np.array([p**i for i in range(d)], dtype=np.int64)
    codes = P @ powers
    order = np.argsort(codes, kind="stable")
    sorted_codes = codes[order]
    stats: dict[str, Any] = {"p": p, "dimension": d, "size": count, "full_lines": None}
    dup = np.nonzero(sorted_codes[1:] == sorted_codes[:-1])[0]
    if len(dup):
        i, j = sorted((int(order[dup[0]]), int(order[dup[0] + 1])))
        return CheckResult(False, f"points[{i}] and points[{j}] are equal", stats)
    if claimed is not None and claimed != count:
        return CheckResult(False, f"claimed size {claimed} but {count} points are given", stats)

    def member(Q: np.ndarray) -> np.ndarray:
        c = Q @ powers
        pos = np.searchsorted(sorted_codes, c)
        pos[pos == count] = 0
        return sorted_codes[pos] == c if count else np.zeros(len(c), dtype=bool)

    witness = None
    full = 0
    if count >= 2 and directions <= count:
        # every normalised direction v (first nonzero coordinate 1) and every base point a in S
        for idx in range(directions):
            v = np.zeros(d, dtype=np.int64)
            rest, lead = idx, 0
            # decode idx -> (leading position, free coordinates after it)
            for lead in range(d - 1, -1, -1):
                block = p ** (d - 1 - lead)
                if rest < block:
                    break
                rest -= block
            v[lead] = 1
            for i in range(lead + 1, d):
                v[i] = rest % p
                rest //= p
            inside = np.ones(count, dtype=bool)
            for t in range(1, p):
                inside &= member((P + t * v) % p)
                if not inside.any():
                    break
            hits = int(inside.sum())
            if hits:
                full += hits
                if witness is None:
                    witness = (P[int(np.nonzero(inside)[0][0])].tolist(), v.tolist())
        full //= p  # each full line is counted once per point on it
    elif count >= 2:
        # every unordered pair {a, b}: the line a + t (b - a) lies in S?
        for i in range(count - 1):
            a = P[i]
            v = (P[i + 1 :] - a) % p
            inside = np.ones(len(v), dtype=bool)
            for t in range(2, p):
                inside &= member((a + t * v) % p)
                if not inside.any():
                    break
            hits = int(inside.sum())
            if hits:
                full += hits
                if witness is None:
                    witness = (a.tolist(), v[int(np.nonzero(inside)[0][0])].tolist())
        full //= p * (p - 1) // 2  # each full line is counted once per pair of its points
    stats["full_lines"] = full
    if full:
        assert witness is not None
        base, direction = witness
        return CheckResult(
            False,
            f"S contains {full} full affine line(s), e.g. {{{base} + t*{direction}}}",
            stats,
        )
    return CheckResult(True, f"no affine line of F_{p}^{d} lies in S; |S| = {count}", stats)


# ---------------------------------------------------------------------------------------------
# kissing_configuration
# ---------------------------------------------------------------------------------------------

MAX_VECTORS = 100_000
MAX_SQUARED_NORM = 1 << 61
_M32 = np.uint64(0xFFFFFFFF)
_S32 = np.uint64(32)


def _wide_square_vs_product(two_d: np.ndarray, ni: np.ndarray, nj: np.ndarray) -> np.ndarray:
    """sign((two_d)^2 - ni*nj) for nonnegative int64 arrays < 2^63, as int8 (exact)."""

    def wide(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = x.astype(np.uint64)
        y = y.astype(np.uint64)
        x0, x1 = x & _M32, x >> _S32
        y0, y1 = y & _M32, y >> _S32
        p00, p01, p10, p11 = x0 * y0, x0 * y1, x1 * y0, x1 * y1
        mid = (p00 >> _S32) + (p01 & _M32) + (p10 & _M32)
        lo = (p00 & _M32) | ((mid & _M32) << _S32)
        hi = p11 + (p01 >> _S32) + (p10 >> _S32) + (mid >> _S32)
        return hi, lo

    h1, l1 = wide(two_d, two_d)
    h2, l2 = wide(ni, nj)
    greater = (h1 > h2) | ((h1 == h2) & (l1 > l2))
    equal = (h1 == h2) & (l1 == l2)
    return greater.astype(np.int8) - (~greater & ~equal).astype(np.int8)


def check_kissing(data: Any) -> CheckResult:
    ctype = "kissing_configuration"
    check_fields(data, ctype, ["dimension", "vectors"], ["squared_norm"])
    dim = get_int(data, "dimension", 1, 1024)
    vectors = get_list(data, "vectors")
    if not vectors:
        raise MalformedCertificate("field 'vectors' must not be empty")
    if len(vectors) > MAX_VECTORS:
        raise MalformedCertificate(f"more than {MAX_VECTORS} vectors")
    norms = []
    for i, v in enumerate(vectors):
        if not isinstance(v, list) or len(v) != dim:
            raise MalformedCertificate(f"vectors[{i}] must be an array of {dim} integers")
        total = 0
        for x in v:
            if not is_int(x):
                raise MalformedCertificate(f"vectors[{i}] must contain integers only")
            total += x * x
        if total > MAX_SQUARED_NORM:
            raise MalformedCertificate(f"vectors[{i}] has squared norm above 2^61")
        norms.append(total)
    claimed = get_int(data, "squared_norm", 1, MAX_SQUARED_NORM) if "squared_norm" in data else None
    count = len(vectors)
    stats: dict[str, Any] = {
        "dimension": dim,
        "count": count,
        "touching_pairs": None,
        "violating_pairs": None,
    }
    zero = [i for i, t in enumerate(norms) if t == 0]
    if zero:
        return CheckResult(False, f"vectors[{zero[0]}] is the zero vector", stats)
    if claimed is not None:
        off = [i for i, t in enumerate(norms) if t != claimed]
        if off:
            i = off[0]
            return CheckResult(
                False, f"vectors[{i}] has squared norm {norms[i]}, claimed {claimed}", stats
            )
    X = np.array(vectors, dtype=np.int64).reshape(count, dim)
    N = np.array(norms, dtype=np.int64)
    touching = 0
    violating = 0
    first_bad = None
    block = max(1, min(count, (1 << 22) // count))
    for i0 in range(0, count, block):
        i1 = min(count, i0 + block)
        G = X[i0:i1] @ X[i0:].T  # exact: |entries| <= sqrt(N_i N_j) <= 2^61
        rows = np.arange(i0, i1)[:, None]
        cols = np.arange(i0, count)[None, :]
        positive = (G > 0) & (cols > rows)
        a_idx, b_idx = np.nonzero(positive)
        if len(a_idx) == 0:
            continue
        two_d = 2 * G[a_idx, b_idx]
        sign = _wide_square_vs_product(two_d, N[i0 + a_idx], N[i0 + b_idx])
        touching += int((sign == 0).sum())
        bad = np.nonzero(sign > 0)[0]
        violating += len(bad)
        if len(bad) and first_bad is None:
            first_bad = (int(i0 + a_idx[bad[0]]), int(i0 + b_idx[bad[0]]))
    stats["touching_pairs"] = touching
    stats["violating_pairs"] = violating
    if violating:
        assert first_bad is not None
        i, j = first_bad
        what = "are equal" if vectors[i] == vectors[j] else "make an angle below 60 degrees"
        return CheckResult(
            False,
            f"{violating} pair(s) violate the 60-degree condition, e.g. vectors[{i}] and "
            f"vectors[{j}] {what} (inner product {int(np.dot(X[i], X[j]))}, squared norms "
            f"{norms[i]} and {norms[j]})",
            stats,
        )
    return CheckResult(
        True,
        f"{count} nonzero vectors in Z^{dim} with pairwise angles >= 60 degrees "
        f"({touching} pairs at exactly 60 degrees): a kissing configuration of size {count}",
        stats,
    )


# ---------------------------------------------------------------------------------------------
# heilbronn_configuration
# ---------------------------------------------------------------------------------------------

MAX_HEILBRONN_POINTS = 500


def _point(value: Any, what: str) -> tuple[Fraction, Fraction]:
    if not isinstance(value, list) or len(value) != 2:
        raise MalformedCertificate(f"{what} must be a pair [x, y]")
    return parse_rational(value[0], f"{what}[0]"), parse_rational(value[1], f"{what}[1]")


def _cross(o, a, b) -> Fraction:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def check_heilbronn(data: Any) -> CheckResult:
    ctype = "heilbronn_configuration"
    check_fields(data, ctype, ["region", "points", "min_area"])
    region = data["region"]
    if region == "unit_square":
        square, tri = True, None
        region_area = Fraction(1)
    elif region == "unit_triangle":
        square, tri = (
            False,
            [(Fraction(0), Fraction(0)), (Fraction(1), Fraction(0)), (Fraction(0), Fraction(1))],
        )
        region_area = Fraction(1, 2)
    elif isinstance(region, dict):
        if set(region) != {"triangle"}:
            raise MalformedCertificate(
                'an explicit region must be {"triangle": [[x,y],[x,y],[x,y]]}'
            )
        verts = region["triangle"]
        if not isinstance(verts, list) or len(verts) != 3:
            raise MalformedCertificate("region.triangle must list exactly 3 vertices")
        square = False
        tri = [_point(v, f"region.triangle[{i}]") for i, v in enumerate(verts)]
        region_area = abs(_cross(*tri)) / 2
        if region_area == 0:
            raise MalformedCertificate("region.triangle is degenerate (zero area)")
    else:
        raise MalformedCertificate(
            "field 'region' must be 'unit_square', 'unit_triangle' or a triangle object"
        )
    raw = get_list(data, "points")
    if not 3 <= len(raw) <= MAX_HEILBRONN_POINTS:
        raise MalformedCertificate(f"between 3 and {MAX_HEILBRONN_POINTS} points are required")
    pts = [_point(v, f"points[{i}]") for i, v in enumerate(raw)]
    claimed = parse_rational(data["min_area"], "field 'min_area'")
    if claimed < 0:
        raise MalformedCertificate("field 'min_area' must be nonnegative")
    n = len(pts)
    stats: dict[str, Any] = {"points": n, "min_area": None, "normalized_min_area": None}
    for i, (x, y) in enumerate(pts):
        if square or tri is None:
            inside = 0 <= x <= 1 and 0 <= y <= 1
        else:
            s = [_cross(tri[e], tri[(e + 1) % 3], (x, y)) for e in range(3)]
            inside = all(v >= 0 for v in s) or all(v <= 0 for v in s)
        if not inside:
            return CheckResult(
                False,
                f"points[{i}] = ({fraction_str(x)}, {fraction_str(y)}) lies outside the region",
                stats,
            )
    # exact integer arithmetic on a common denominator D: twice the area of a triangle is
    # |cross| / D^2 where cross is computed from the integer numerators
    D = 1
    for x, y in pts:
        D = lcm(D, x.denominator, y.denominator)
    X = [int(x * D) for x, _ in pts]
    Y = [int(y * D) for _, y in pts]
    best = None
    best_triple = None
    for i in range(n - 2):
        xi, yi = X[i], Y[i]
        for j in range(i + 1, n - 1):
            dx, dy = X[j] - xi, Y[j] - yi
            for k in range(j + 1, n):
                c = dx * (Y[k] - yi) - dy * (X[k] - xi)
                if c < 0:
                    c = -c
                if best is None or c < best:
                    best = c
                    best_triple = (i, j, k)
    assert best is not None and best_triple is not None
    min_area = Fraction(best, 2 * D * D)
    stats["min_area"] = fraction_str(min_area)
    stats["normalized_min_area"] = fraction_str(min_area / region_area)
    i, j, k = best_triple
    if min_area < claimed:
        return CheckResult(
            False,
            f"the triangle on points {i}, {j}, {k} has area {fraction_str(min_area)} < claimed "
            f"{fraction_str(claimed)}",
            stats,
        )
    return CheckResult(
        True,
        f"all {n} points lie in the region and every triangle has area >= {fraction_str(claimed)}; "
        f"exact minimum area {fraction_str(min_area)} (triangle {i}, {j}, {k})",
        stats,
    )
