"""``polynomial_positive_interval``: P(x) > 0 (or >= 0) for all x in a closed interval.

Method (all integer arithmetic):

1. Clear denominators; P becomes a primitive integer polynomial of degree m.
2. Square-free part s = P / gcd(P, P').  A single prime p with deg gcd(P mod p, P' mod p) = 0
   certifies gcd = 1 (the common case).  Otherwise the gcd is computed by the small-primes
   modular algorithm (Brown/Collins) and verified by exact division.
3. Every piece of the interval is mapped onto y in [0, 1] by a Moebius map x = A(y)/B(y) with
   linear A, B and B > 0 on [0, 1); R(y) = B(y)^m P(A(y)/B(y)) has the sign of P(x(y)).
   Infinite endpoints are allowed: y = 1 then corresponds to x = +-infinity.
4. The roots of the transformed s in [0, 1] are isolated by Vincent-Collins-Akritas bisection
   (Descartes' rule of signs on (t+1)^m Q(1/(t+1))).  Leaves have 0 roots, or exactly one root
   with both endpoints not roots.
5. Every component of the interval minus the roots contains a sample point (the midpoint of a
   root-free leaf or an endpoint of a one-root leaf); P >= 0 holds iff P > 0 at all samples,
   P > 0 holds iff additionally there are no roots.
"""

from __future__ import annotations

from fractions import Fraction
from itertools import accumulate
from math import gcd, lcm
from typing import Any

from .common import (
    CheckResult,
    MalformedCertificate,
    check_fields,
    fraction_str,
    get_bool,
    get_list,
    parse_rational,
)

MAX_DEGREE = 2000


# ---------------------------------------------------------------------------------------------
# Integer polynomial helpers (coefficient lists, lowest degree first)
# ---------------------------------------------------------------------------------------------


def _strip(a: list[int]) -> list[int]:
    while a and a[-1] == 0:
        a.pop()
    return a


def _content(a: list[int]) -> int:
    return gcd(*a) if a else 0


def _primitive(a: list[int]) -> list[int]:
    c = _content(a)
    if c > 1:
        a = [x // c for x in a]
    return a


def _derivative(a: list[int]) -> list[int]:
    return [i * a[i] for i in range(1, len(a))]


def _exact_div(a: list[int], d: list[int]) -> list[int] | None:
    """a / d if d divides a in Z[x] (d primitive), else None."""
    a = list(a)
    dd = len(d) - 1
    lead = d[-1]
    if len(a) - 1 < dd:
        return None if any(a) else []
    quo = [0] * (len(a) - dd)
    for top in range(len(a) - 1, dd - 1, -1):
        c = a[top]
        if c == 0:
            continue
        if c % lead:
            return None
        f = c // lead
        quo[top - dd] = f
        base = top - dd
        for i in range(dd + 1):
            a[base + i] -= f * d[i]
    if any(a[:dd]):
        return None
    return quo


def _is_probable_prime(n: int) -> bool:
    if n < 2:
        return False
    small = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)
    for p in small:
        if n % p == 0:
            return n == p
    d, s = n - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in small:  # deterministic for n < 3.3e24
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(s - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _primes_below(start: int):
    p = start
    while True:
        p -= 1
        if _is_probable_prime(p):
            yield p


def _rem_mod(a: list[int], b: list[int], p: int) -> list[int]:
    a = list(a)
    db = len(b) - 1
    inv = pow(b[-1], -1, p)
    while len(a) - 1 >= db:
        c = a[-1] * inv % p
        if c:
            shift = len(a) - 1 - db
            for i in range(db + 1):
                a[shift + i] = (a[shift + i] - c * b[i]) % p
        a.pop()
        _strip(a)
    return a


def _gcd_mod(a: list[int], b: list[int], p: int) -> list[int]:
    a = _strip([x % p for x in a])
    b = _strip([x % p for x in b])
    while b:
        a, b = b, _rem_mod(a, b, p)
    inv = pow(a[-1], -1, p)
    return [x * inv % p for x in a]


def _int_gcd(a: list[int], b: list[int]) -> list[int]:
    """gcd of two nonzero integer polynomials: primitive, positive leading coefficient."""
    a, b = _primitive(a), _primitive(b)
    gamma = gcd(a[-1], b[-1])
    best_deg = None
    residues: list[int] = []
    modulus = 1
    previous = None
    for p in _primes_below(1 << 61):
        if a[-1] % p == 0 or b[-1] % p == 0:
            continue
        g = _gcd_mod(a, b, p)
        deg = len(g) - 1
        if deg == 0:
            return [1]
        if best_deg is not None and deg > best_deg:
            continue  # unlucky prime
        scaled = [gamma % p * x % p for x in g]
        if best_deg is None or deg < best_deg:
            best_deg, residues, modulus, previous = deg, scaled, p, None
        else:  # Chinese remaindering
            inv = pow(modulus % p, -1, p)
            residues = [
                r + modulus * (((s - r) % p) * inv % p)
                for r, s in zip(residues, scaled, strict=True)
            ]
            modulus *= p
        half = modulus // 2
        candidate = _primitive([r - modulus if r > half else r for r in residues])
        if candidate[-1] < 0:
            candidate = [-x for x in candidate]
        if (
            candidate == previous
            and _exact_div(a, candidate) is not None
            and _exact_div(b, candidate) is not None
        ):
            return candidate
        previous = candidate
    raise AssertionError("unreachable")


def _squarefree_part(a: list[int]) -> tuple[list[int], bool]:
    """(s, squarefree) with s = a / gcd(a, a')."""
    if len(a) <= 2:
        return a, True
    da = _derivative(a)
    for p in _primes_below(1 << 61):
        if a[-1] % p and da[-1] % p:
            if len(_gcd_mod(a, da, p)) == 1:
                return a, True
            break
    g = _int_gcd(a, da)
    if len(g) == 1:
        return a, True
    s = _exact_div(a, g)
    assert s is not None
    return s, False


def _taylor_shift_1(a: list[int]) -> list[int]:
    """Coefficients of a(t + 1)."""
    a = list(a)
    n = len(a) - 1
    for i in range(n):
        tail = list(accumulate(reversed(a[i:])))
        tail.reverse()
        a[i:] = tail
    return a


def _taylor_shift(a: list[int], c: int) -> list[int]:
    """Coefficients of a(t + c) for an integer c."""
    if c == 0:
        return list(a)
    if c == 1:
        return _taylor_shift_1(a)
    a = list(a)
    n = len(a) - 1
    for i in range(n):
        for j in range(n - 1, i - 1, -1):
            a[j] += c * a[j + 1]
    return a


def _sign_variations(a: list[int]) -> int:
    count = 0
    last = 0
    for x in a:
        if x:
            if last and (x > 0) != (last > 0):
                count += 1
            last = x
    return count


def _descartes_01(q: list[int]) -> int:
    """Descartes' bound V for the number of roots of q in the open interval (0, 1), i.e. the sign
    variations of (t+1)^m q(1/(t+1)); returns 2 as soon as V >= 2 is certain."""
    if _sign_variations(q) == 0:
        return 0
    a = q[::-1]
    n = len(a) - 1
    count = 0
    last = 0
    for i in range(n + 1):
        if i < n:  # after this pass a[i] is the final coefficient of t^i
            tail = list(accumulate(reversed(a[i:])))
            tail.reverse()
            a[i:] = tail
        x = a[i]
        if x:
            if last and (x > 0) != (last > 0):
                count += 1
                if count >= 2:
                    return 2
            last = x
    return count


def _drop_twos(a: list[int]) -> list[int]:
    shift = min(((x & -x).bit_length() - 1 for x in a if x), default=0)
    return [x >> shift for x in a] if shift else a


def _scale_variable(a: list[int], c: int) -> list[int]:
    """Coefficients of a(c t)."""
    out = []
    power = 1
    for x in a:
        out.append(x * power)
        power *= c
    return out


def _transform(c: list[int], start: Fraction, kind: str, end: Fraction | None = None) -> list[int]:
    """A positive multiple of B(y)^m c(A(y)/B(y)) for the Moebius map of the piece.

    kind "finite":    x = start + (end - start) y            (y in [0, 1])
    kind "right":     x = start + y / (1 - y)                  ([start, +inf))
    kind "left":      x = start - y / (1 - y)                  ((-inf, start])
    Built from elementary steps: Q(t) = den^m c((num + sigma den t)/den), then for the
    infinite kinds (1 - y)^m Q(y / (1 - y)) = shift_{-1}(negate_odd(reverse(shift_{-1}(Q)))).
    """
    m = len(c) - 1
    if kind == "finite":
        assert end is not None
        den = lcm(start.denominator, end.denominator)
        num, width = int(start * den), int((end - start) * den)
    else:
        den, num = start.denominator, start.numerator
        width = den if kind == "right" else -den
    scaled = [x * den ** (m - i) for i, x in enumerate(c)]  # den^m c(x / den)
    q = _scale_variable(_taylor_shift(scaled, num), width)  # den^m c((num + width t)/den)
    if kind == "finite":
        return q
    q = _taylor_shift(q, -1)
    q = q[::-1]
    q = [-x if i % 2 else x for i, x in enumerate(q)]
    return _taylor_shift(q, -1)


def _eval_dyadic_sign(r: list[int], num: int, k: int) -> int:
    """sign of r(num / 2^k), via Horner on 2^(k*m) r(num / 2^k) = sum_j r_j num^j 2^(k(m-j))."""
    acc = 0
    m = len(r) - 1
    for j in range(m, -1, -1):
        acc = acc * num + (r[j] << (k * (m - j)))
    return (acc > 0) - (acc < 0)


# ---------------------------------------------------------------------------------------------
# Root isolation on [0, 1]
# ---------------------------------------------------------------------------------------------


class _Isolation:
    def __init__(self, r: list[int]):
        self.zero_leaves: list[tuple[int, int]] = []  # (num, k): interval [num/2^k, (num+1)/2^k]
        self.one_leaves: list[tuple[int, int]] = []
        self.dyadic_roots: list[tuple[int, int]] = []  # num/2^k
        self.root_at_0 = r[0] == 0
        self.root_at_1 = sum(r) == 0
        m = len(r) - 1
        stack = [(_primitive(_drop_twos(r)), 0, 0)]
        while stack:
            q, num, k = stack.pop()
            v = _descartes_01(q)
            if v == 0:
                self.zero_leaves.append((num, k))
                continue
            if v == 1 and q[0] != 0 and sum(q) != 0:
                self.one_leaves.append((num, k))
                continue
            left = [x << (m - i) for i, x in enumerate(q)]  # 2^m q(t/2)
            right = _taylor_shift_1(left)  # 2^m q((t+1)/2)
            if right[0] == 0:
                self.dyadic_roots.append((2 * num + 1, k + 1))
            stack.append((_primitive(_drop_twos(right)), 2 * num + 1, k + 1))
            stack.append((_primitive(_drop_twos(left)), 2 * num, k + 1))

    def root_count(self) -> int:
        return len(self.one_leaves) + len(self.dyadic_roots) + self.root_at_0 + self.root_at_1

    def samples(self) -> list[tuple[int, int]]:
        pts = [(2 * num + 1, k + 1) for num, k in self.zero_leaves]
        for num, k in self.one_leaves:
            pts.append((num, k))
            pts.append((num + 1, k))
        return pts


# ---------------------------------------------------------------------------------------------
# The checker
# ---------------------------------------------------------------------------------------------


def _parse_endpoint(value: Any, what: str, infinite: str) -> Fraction | None:
    if value == infinite:
        return None
    return parse_rational(value, what)


def _poly_value(c: list[Fraction], x: Fraction) -> Fraction:
    acc = Fraction(0)
    for coef in reversed(c):
        acc = acc * x + coef
    return acc


def check_polynomial(data: Any) -> CheckResult:
    ctype = "polynomial_positive_interval"
    check_fields(data, ctype, ["coefficients", "interval"], ["strict"])
    raw = get_list(data, "coefficients")
    if not 1 <= len(raw) <= MAX_DEGREE + 1:
        raise MalformedCertificate(f"field 'coefficients' must have 1..{MAX_DEGREE + 1} entries")
    coeffs = [parse_rational(c, f"coefficients[{i}]") for i, c in enumerate(raw)]
    interval = get_list(data, "interval", 2)
    a = _parse_endpoint(interval[0], "interval[0]", "-inf")
    b = _parse_endpoint(interval[1], "interval[1]", "inf")
    if a is not None and b is not None and a > b:
        raise MalformedCertificate("interval[0] must not exceed interval[1]")
    strict = get_bool(data, "strict", True)
    relation = ">" if strict else ">="
    shown = f"[{fraction_str(a) if a is not None else '-inf'}, {fraction_str(b) if b is not None else 'inf'}]"

    denominator = lcm(*(c.denominator for c in coeffs))
    P = _strip([int(c * denominator) for c in coeffs])
    stats: dict[str, Any] = {"degree": None, "squarefree": None, "roots_in_interval": None}
    if not P:
        if strict:
            return CheckResult(False, "P is the zero polynomial", stats)
        return CheckResult(True, f"P is the zero polynomial, so P >= 0 on {shown}", stats)
    m = len(P) - 1
    stats["degree"] = m
    P = _primitive(P)

    if a is not None and a == b:
        value = _poly_value(coeffs, a)
        stats["squarefree"] = _squarefree_part(P)[1]
        stats["roots_in_interval"] = int(value == 0)
        ok = value > 0 if strict else value >= 0
        sign = "zero" if value == 0 else ("positive" if value > 0 else "negative")
        return CheckResult(
            ok, f"the interval is the single point {fraction_str(a)}, where P is {sign}", stats
        )

    s, squarefree = _squarefree_part(P)
    stats["squarefree"] = squarefree

    # pieces: x = A(y)/B(y) for y in [0, 1] (A, B linear), and the transform parameters
    pieces = []
    if a is not None and b is not None:
        den = lcm(a.denominator, b.denominator)
        alpha, beta = int(a * den), int(b * den)
        pieces.append(((alpha, beta - alpha), (den, 0), (a, "finite", b)))
    elif a is not None:
        alpha, den = a.numerator, a.denominator
        pieces.append(((alpha, den - alpha), (den, -den), (a, "right", None)))
    elif b is not None:
        beta, den = b.numerator, b.denominator
        pieces.append(((beta, -beta - den), (den, -den), (b, "left", None)))
    else:
        zero = Fraction(0)
        pieces.append(((0, -1), (1, -1), (zero, "left", None)))  # (-inf, 0]
        pieces.append(((0, 1), (1, -1), (zero, "right", None)))  # [0, inf)

    roots = 0
    witness_root = None
    negative_at = None
    for A, B, params in pieces:
        rs = _transform(s, *params)
        rp = rs if squarefree else _transform(P, *params)
        iso = _Isolation(rs)
        roots += iso.root_count()
        if witness_root is None:
            if iso.root_at_0:
                witness_root = ("point", (0, 0), A, B)
            elif iso.dyadic_roots:
                witness_root = ("point", iso.dyadic_roots[0], A, B)
            elif iso.one_leaves:
                witness_root = ("interval", iso.one_leaves[0], A, B)
            elif iso.root_at_1:
                witness_root = ("point", (1, 0), A, B)
        if negative_at is None:
            for num, k in iso.samples():
                if _eval_dyadic_sign(rp, num, k) < 0:
                    negative_at = ((num, k), A, B)
                    break
    if len(pieces) == 2 and P[0] == 0:
        roots -= 1  # the root x = 0 belongs to both halves
    stats["roots_in_interval"] = roots

    def x_of(num: int, k: int, A, B) -> str:
        y = Fraction(num, 1 << k)
        bv = B[0] + B[1] * y
        if bv == 0:
            return "inf" if A[0] + A[1] * y > 0 else "-inf"
        return fraction_str((A[0] + A[1] * y) / bv)

    if negative_at is not None:
        (num, k), A, B = negative_at
        return CheckResult(
            False,
            f"P is negative at x = {x_of(num, k, A, B)}, so P {relation} 0 fails on {shown}",
            stats,
        )
    if strict and roots:
        assert witness_root is not None
        kind, (num, k), A, B = witness_root
        if kind == "point":
            where = f"x = {x_of(num, k, A, B)}"
        else:
            where = f"the open interval between {x_of(num, k, A, B)} and {x_of(num + 1, k, A, B)}"
        return CheckResult(
            False,
            f"P has {roots} distinct real root(s) in {shown}, e.g. at {where}; P > 0 fails",
            stats,
        )
    if roots:
        return CheckResult(
            True,
            f"P >= 0 on {shown}: P touches zero at {roots} point(s) and is positive elsewhere",
            stats,
        )
    return CheckResult(True, f"P > 0 on {shown} (degree {m}, no real roots in the interval)", stats)
