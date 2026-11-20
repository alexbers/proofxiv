"""Codes: ``linear_code`` (rank and exact minimum distance over GF(q)) and ``covering_code``."""

from __future__ import annotations

import itertools
from math import comb
from typing import Any

import numpy as np

from .common import (
    CheckResult,
    MalformedCertificate,
    check_fields,
    get_int,
    get_list,
    int_list,
)

MAX_Q = 1024
MAX_N = 100_000
MAX_ENUMERATION = 1 << 34  # bound on q^k * n
MAX_COVER_N = 28


# ---------------------------------------------------------------------------------------------
# Finite fields GF(p^m); the integer sum c_i p^i encodes the polynomial sum c_i x^i
# ---------------------------------------------------------------------------------------------


def _is_prime(p: int) -> bool:
    if p < 2:
        return False
    i = 2
    while i * i <= p:
        if p % i == 0:
            return False
        i += 1
    return True


def _divisible(f: list[int], g: list[int], p: int) -> bool:
    """Is f divisible by the monic polynomial g over F_p?  (coefficients low -> high)"""
    r = list(f)
    dg = len(g) - 1
    for top in range(len(r) - 1, dg - 1, -1):
        c = r[top] % p
        if c:
            for i in range(dg + 1):
                r[top - dg + i] = (r[top - dg + i] - c * g[i]) % p
    return all(x % p == 0 for x in r[:dg])


def _irreducible(modulus: list[int], p: int) -> bool:
    """Trial division by every monic polynomial of degree 1..m//2."""
    m = len(modulus) - 1
    for deg in range(1, m // 2 + 1):
        for low in itertools.product(range(p), repeat=deg):
            if _divisible(modulus, list(low) + [1], p):
                return False
    return True


class Field:
    def __init__(self, p: int, m: int, modulus: list[int] | None):
        self.p, self.m, self.q = p, m, p**m
        q = self.q
        digits = np.array([[(a // p**i) % p for i in range(m)] for a in range(q)], dtype=np.int64)
        weights = np.array([p**i for i in range(m)], dtype=np.int64)
        self.add = (((digits[:, None, :] + digits[None, :, :]) % p) @ weights).astype(np.int16)
        self.neg = (((-digits) % p) @ weights).astype(np.int16)
        # xpow[j][a] = digits of a * x^j  (multiplication by x: shift up, reduce x^m)
        xpow = [digits]
        low = np.array(modulus[:m] if modulus is not None else [0], dtype=np.int64)
        for _ in range(1, m):
            prev = xpow[-1]
            shifted = np.zeros_like(prev)
            shifted[:, 1:] = prev[:, :-1]
            xpow.append((shifted - prev[:, m - 1 : m] * low[None, :]) % p)
        prod = np.zeros((q, q, m), dtype=np.int64)
        for j in range(m):  # a * b = sum_j b_j * (a x^j)
            prod += digits[None, :, j, None] * xpow[j][:, None, :]
        self.mul = ((prod % p) @ weights).astype(np.int16)
        is_one = self.mul[1:] == 1
        if not (is_one.sum(axis=1) == 1).all():
            raise MalformedCertificate("the field modulus does not define a field")
        self.inv = np.zeros(q, dtype=np.int16)
        self.inv[1:] = np.argmax(is_one, axis=1)


def _parse_field(data: dict[str, Any]) -> Field:
    spec = data["field"]
    if not isinstance(spec, dict):
        raise MalformedCertificate("field 'field' must be an object")
    for key in spec:
        if key not in ("p", "m", "modulus"):
            raise MalformedCertificate(f"unknown key {key!r} in 'field'")
    if "p" not in spec:
        raise MalformedCertificate("missing key 'p' in 'field'")
    p = get_int(spec, "p", 2, MAX_Q)
    if not _is_prime(p):
        raise MalformedCertificate(f"field.p = {p} is not prime")
    m = get_int(spec, "m", 1, 10) if "m" in spec else 1
    if p**m > MAX_Q:
        raise MalformedCertificate(f"q = p^m = {p**m} exceeds {MAX_Q}")
    if m == 1:
        if "modulus" in spec:
            raise MalformedCertificate("field.modulus must be omitted for a prime field (m = 1)")
        return Field(p, 1, None)
    if "modulus" not in spec:
        raise MalformedCertificate("field.modulus is required when m > 1")
    modulus = int_list(spec["modulus"], "field.modulus", m + 1, 0, p - 1)
    if modulus[m] != 1:
        raise MalformedCertificate("field.modulus must be monic (last coefficient 1)")
    if not _irreducible(modulus, p):
        raise MalformedCertificate("field.modulus is not irreducible over GF(p)")
    return Field(p, m, modulus)


# ---------------------------------------------------------------------------------------------
# linear_code
# ---------------------------------------------------------------------------------------------


def _rref(F: Field, G: np.ndarray) -> np.ndarray:
    """Row-reduced echelon form over GF(q); returns the nonzero rows."""
    A = G.copy()
    rows, cols = A.shape
    r = 0
    for c in range(cols):
        if r == rows:
            break
        piv = np.nonzero(A[r:, c])[0]
        if len(piv) == 0:
            continue
        i = r + int(piv[0])
        if i != r:
            A[[r, i]] = A[[i, r]]
        A[r] = F.mul[F.inv[A[r, c]], A[r]]
        for i2 in range(rows):
            if i2 != r and A[i2, c]:
                A[i2] = F.add[A[i2], F.mul[F.neg[A[i2, c]], A[r]]]
        r += 1
    return A[:r]


def _weight_distribution(F: Field, B: np.ndarray, n: int) -> list[int]:
    """hist[w] = number of codewords of weight w in the row space of B (zero word included)."""
    r = B.shape[0]
    q = F.q
    hist = np.zeros(n + 1, dtype=np.int64)
    low = 0  # rows enumerated as a table; the others in a Python loop
    while low < r and q ** (low + 1) * n <= 1 << 24:
        low += 1
    table = np.zeros((1, n), dtype=np.int16)
    elements = np.arange(q)
    for row in B[r - low :]:
        multiples = F.mul[elements[:, None], row[None, :]]  # (q, n)
        table = F.add[table[None, :, :], multiples[:, None, :]].reshape(-1, n)
    high_rows = B[: r - low]
    for coeffs in itertools.product(range(q), repeat=r - low):
        base = np.zeros(n, dtype=np.int16)
        for a, row in zip(coeffs, high_rows, strict=True):
            if a:
                base = F.add[base, F.mul[a, row]]
        weights = np.count_nonzero(F.add[table, base[None, :]], axis=1)
        hist += np.bincount(weights, minlength=n + 1)
    return [int(x) for x in hist]


def check_linear_code(data: Any) -> CheckResult:
    ctype = "linear_code"
    check_fields(data, ctype, ["field", "n", "k", "generator_matrix", "minimum_distance"])
    F = _parse_field(data)
    n = get_int(data, "n", 1, MAX_N)
    k = get_int(data, "k", 1, n)
    d = get_int(data, "minimum_distance", 1, n)
    rows = get_list(data, "generator_matrix", k)
    for i, row in enumerate(rows):
        int_list(row, f"generator_matrix[{i}]", n, 0, F.q - 1)
    if F.q**k * n > MAX_ENUMERATION:
        raise MalformedCertificate(f"q^k * n = {F.q**k * n} exceeds the enumeration limit 2^34")
    G = np.array(rows, dtype=np.int16).reshape(k, n)
    B = _rref(F, G)
    rank = int(B.shape[0])
    hist = _weight_distribution(F, B, n)
    nonzero = [[w, hist[w]] for w in range(1, n + 1) if hist[w]]
    true_d = nonzero[0][0] if nonzero else None
    stats = {
        "q": F.q,
        "n": n,
        "k": k,
        "rank": rank,
        "minimum_distance": true_d,
        "weight_distribution": nonzero,
    }
    if rank < k:
        return CheckResult(
            False, f"the generator matrix has rank {rank} < k = {k} over GF({F.q})", stats
        )
    if true_d != d:
        return CheckResult(
            False, f"claimed minimum distance {d}, but the true minimum distance is {true_d}", stats
        )
    return CheckResult(True, f"[{n},{k},{d}] linear code over GF({F.q}) verified", stats)


# ---------------------------------------------------------------------------------------------
# covering_code
# ---------------------------------------------------------------------------------------------

# _BYTE_FLIP[i][b]: the byte b with bit j moved to bit j ^ (1 << i), for i = 0, 1, 2
_BYTE_FLIP = [
    np.array(
        [sum(((b >> j) & 1) << (j ^ (1 << i)) for j in range(8)) for b in range(256)],
        dtype=np.uint8,
    )
    for i in range(3)
]


def _ball_size(n: int, radius: int) -> int:
    """Number of words of weight <= radius."""
    return sum(comb(n, w) for w in range(radius + 1))


def _ball(n: int, radius: int) -> np.ndarray:
    words = [np.zeros(1, dtype=np.int64)]
    for w in range(1, radius + 1):
        combos = np.fromiter(
            itertools.chain.from_iterable(itertools.combinations(range(n), w)),
            dtype=np.int64,
            count=comb(n, w) * w,
        ).reshape(-1, w)
        words.append((np.int64(1) << combos).sum(axis=1))
    return np.concatenate(words)


def _covered_bitmap(n: int, radius: int, code: np.ndarray) -> np.ndarray:
    """Little-endian bitmap (uint8 array) of the words within distance `radius` of the code."""
    nbits = 1 << n
    if radius >= n:
        marks = np.zeros(max(nbits, 8), dtype=bool)
        marks[:nbits] = True
        return np.packbits(marks, bitorder="little")
    ball_size = _ball_size(n, radius)
    if n <= 24 and ball_size <= 1 << 22 and len(code) * ball_size <= 1 << (n + 5):
        # sumset C + B(R): mark c XOR e for every codeword c and error pattern e of weight <= R
        ball = _ball(n, radius)
        marks = np.zeros(max(nbits, 8), dtype=bool)
        chunk = max(1, (1 << 22) // len(ball))
        for start in range(0, len(code), chunk):
            marks[(code[start : start + chunk, None] ^ ball[None, :]).ravel()] = True
        return np.packbits(marks, bitorder="little")
    # dilation: `radius` rounds of adding all neighbours (one coordinate flipped)
    bitmap = np.zeros(max(1, nbits // 8), dtype=np.uint8)
    np.bitwise_or.at(bitmap, code >> 3, (np.int64(1) << (code & 7)).astype(np.uint8))
    for _ in range(radius):
        grown = bitmap.copy()
        for i in range(n):
            if i < 3:
                grown |= _BYTE_FLIP[i][bitmap]
            else:
                block = 1 << (i - 3)
                grown.reshape(-1, 2, block)[:] |= bitmap.reshape(-1, 2, block)[:, ::-1, :]
        bitmap = grown
    return bitmap


def check_covering_code(data: Any) -> CheckResult:
    ctype = "covering_code"
    check_fields(data, ctype, ["n", "radius", "codewords"], ["size"])
    n = get_int(data, "n", 1, MAX_COVER_N)
    radius = get_int(data, "radius", 0, n)
    words = get_list(data, "codewords")
    if not words:
        raise MalformedCertificate("field 'codewords' must not be empty")
    values = []
    for i, w in enumerate(words):
        if not isinstance(w, str) or len(w) != n or not set(w) <= {"0", "1"}:
            raise MalformedCertificate(f"codewords[{i}] must be a string of {n} characters 0/1")
        values.append(int(w[::-1], 2))  # character j <-> bit j
    claimed_size = get_int(data, "size", 1, None) if "size" in data else None
    distinct = len(set(values))
    stats: dict[str, Any] = {"n": n, "radius": radius, "size": distinct, "uncovered": None}
    if distinct != len(values):
        seen: dict[int, int] = {}
        for i, v in enumerate(values):
            if v in seen:
                return CheckResult(
                    False, f"codewords[{seen[v]}] and codewords[{i}] are equal", stats
                )
            seen[v] = i
    if claimed_size is not None and claimed_size != distinct:
        return CheckResult(
            False, f"claimed size {claimed_size} but the code has {distinct} codewords", stats
        )
    bitmap = _covered_bitmap(n, radius, np.array(values, dtype=np.int64))
    if n < 3:
        bits = np.unpackbits(bitmap, bitorder="little")[: 1 << n]
        uncovered_words = np.nonzero(bits == 0)[0]
        uncovered = len(uncovered_words)
        first = int(uncovered_words[0]) if uncovered else None
    else:
        uncovered = (1 << n) - int(np.bitwise_count(bitmap).sum(dtype=np.int64))
        first = None
        if uncovered:
            b = int(np.nonzero(bitmap != 0xFF)[0][0])
            byte = int(bitmap[b])
            first = 8 * b + next(j for j in range(8) if not (byte >> j) & 1)
    stats["uncovered"] = uncovered
    if uncovered:
        word = format(first, f"0{n}b")[::-1]
        return CheckResult(
            False, f"{uncovered} words are at distance > {radius} from the code, e.g. {word}", stats
        )
    return CheckResult(
        True,
        f"every word of F_2^{n} is within distance {radius} of one of the {distinct} codewords; "
        f"shows K({n},{radius}) <= {distinct}",
        stats,
    )
