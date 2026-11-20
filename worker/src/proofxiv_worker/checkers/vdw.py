"""``vdw_coloring``: an r-colouring of {1..n} without a monochromatic k-term arithmetic progression.

Each colour class is a bitset B (a Python integer, bit i <-> the integer i+1).  For a common
difference d, the starts a of monochromatic k-APs of that colour are exactly the set bits of
B & (B >> d) & (B >> 2d) & ... & (B >> (k-1)d) restricted to a + (k-1)d <= n.
"""

from __future__ import annotations

from typing import Any

from .common import CheckResult, MalformedCertificate, check_fields, get_int, get_str

MAX_N = 50_000_000
MAX_COLOURS = 10


def check_vdw(data: Any) -> CheckResult:
    ctype = "vdw_coloring"
    check_fields(data, ctype, ["n", "colours", "k", "colouring"])
    n = get_int(data, "n", 1, MAX_N)
    r = get_int(data, "colours", 1, MAX_COLOURS)
    k = get_int(data, "k", 1, MAX_N)
    text = get_str(data, "colouring")
    if len(text) != n:
        raise MalformedCertificate(f"field 'colouring' must have length n = {n}, got {len(text)}")
    allowed = "0123456789"[:r]
    bad = set(text) - set(allowed)
    if bad:
        raise MalformedCertificate(
            f"field 'colouring' contains {sorted(bad)[0]!r}, expected digits 0..{r - 1}"
        )
    counts = [text.count(ch) for ch in allowed]
    stats = {"n": n, "colours": r, "k": k, "colour_counts": counts}

    if k == 1:
        return CheckResult(
            False, "the single integer 1 is a monochromatic 1-term progression", stats
        )

    # bit i of masks[c] is set iff colour(i + 1) == c; built via the reversed string as a binary number
    masks = []
    for ch in allowed:
        binary = text[::-1].translate({ord(x): ord("1") if x == ch else ord("0") for x in allowed})
        masks.append(int(binary, 2))

    max_d = (n - 1) // (k - 1)
    for d in range(1, max_d + 1):
        starts = n - (k - 1) * d  # a in 1..starts  <->  bit a-1
        window = (1 << starts) - 1
        for colour, mask in enumerate(masks):
            acc = mask & window
            j = 1
            while acc and j < k:
                acc &= mask >> (j * d)
                j += 1
            if acc:
                a = (acc & -acc).bit_length()  # lowest set bit index + 1 = the integer a
                terms = [a + j * d for j in range(k)]
                shown = terms if k <= 12 else terms[:6] + ["..."] + terms[-2:]
                return CheckResult(
                    False,
                    f"monochromatic {k}-term progression of colour {colour}: start {a}, "
                    f"difference {d}: {shown}",
                    stats,
                )
    return CheckResult(
        True,
        f"no monochromatic {k}-term arithmetic progression in {{1..{n}}}; shows W({r},{k}) > {n}",
        stats,
    )
