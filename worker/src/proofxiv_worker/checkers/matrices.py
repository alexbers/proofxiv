"""Matrices: ``hadamard_matrix`` and ``circulant_weighing_matrix``."""

from __future__ import annotations

from typing import Any

import numpy as np

from .common import (
    CheckResult,
    MalformedCertificate,
    check_fields,
    get_bool,
    get_int,
    get_list,
    int_list,
)

MAX_ORDER = 4096
MAX_CW_ORDER = 100_000


def check_hadamard(data: Any) -> CheckResult:
    ctype = "hadamard_matrix"
    check_fields(data, ctype, ["n", "rows"], ["symmetric"])
    n = get_int(data, "n", 1, MAX_ORDER)
    want_symmetric = get_bool(data, "symmetric", False)
    rows = get_list(data, "rows", n)
    plus = np.empty((n, n), dtype=bool)
    for i, row in enumerate(rows):
        if not isinstance(row, str) or len(row) != n or not set(row) <= {"+", "-"}:
            raise MalformedCertificate(f"rows[{i}] must be a string of {n} characters '+'/'-'")
        plus[i] = np.frombuffer(row.encode("ascii"), dtype=np.uint8) == ord("+")
    symmetric = bool((plus == plus.T).all())
    stats = {"n": n, "symmetric": symmetric}
    # <row_i, row_j> = n - 2 * (number of positions where the rows differ)
    packed = np.packbits(plus, axis=1)
    width = (packed.shape[1] + 7) // 8 * 8
    padded = np.zeros((n, width), dtype=np.uint8)
    padded[:, : packed.shape[1]] = packed
    words = padded.view(np.uint64)
    block = max(1, (1 << 22) // (n * words.shape[1]))
    for i0 in range(0, n, block):
        diff = np.bitwise_count(words[i0 : i0 + block, None, :] ^ words[None, :, :]).sum(
            axis=2, dtype=np.int64
        )
        gram = n - 2 * diff
        target = np.zeros_like(gram)
        idx = np.arange(gram.shape[0])
        target[idx, i0 + idx] = n
        bad = np.argwhere(gram != target)
        if len(bad):
            a, j = (int(x) for x in bad[0])
            i = i0 + a
            what = "squared norm" if i == j else "inner product"
            expected = n if i == j else 0
            return CheckResult(
                False, f"rows {i} and {j}: {what} {int(gram[a, j])}, expected {expected}", stats
            )
    if want_symmetric and not symmetric:
        i, j = (int(x) for x in np.argwhere(plus != plus.T)[0])
        return CheckResult(
            False, f"H H^T = nI holds but H is not symmetric: H[{i}][{j}] != H[{j}][{i}]", stats
        )
    kind = "symmetric Hadamard matrix" if want_symmetric else "Hadamard matrix"
    return CheckResult(True, f"{kind} of order {n} verified (H H^T = {n} I)", stats)


def _proper(n: int, support: np.ndarray) -> bool:
    """True iff for every divisor e > 1 of n the support meets at least two classes mod e."""
    if len(support) == 0:
        return False
    return not any(n % e == 0 and (support % e == support[0] % e).all() for e in range(2, n + 1))


def check_circulant_weighing(data: Any) -> CheckResult:
    ctype = "circulant_weighing_matrix"
    check_fields(data, ctype, ["n", "weight", "first_row"], ["proper"])
    n = get_int(data, "n", 1, MAX_CW_ORDER)
    weight = get_int(data, "weight", 1, n)
    want_proper = get_bool(data, "proper", False)
    row = int_list(data["first_row"], "field 'first_row'", n, -1, 1)
    a = np.array(row, dtype=np.int64)
    support = np.nonzero(a)[0]
    stats = {
        "n": n,
        "weight": int(len(support)),
        "plus": int((a == 1).sum()),
        "minus": int((a == -1).sum()),
        "proper": _proper(n, support),
    }
    if len(support) != weight:
        return CheckResult(
            False,
            f"the first row has {len(support)} nonzero entries, claimed weight {weight}",
            stats,
        )
    # PAF(s) = sum_i a_i a_{(i+s) mod n}: every ordered pair (i, j) of support positions
    # contributes a_i a_j to the shift s = (j - i) mod n
    paf = np.zeros(n, dtype=np.int64)
    values = a[support]
    chunk = max(1, (1 << 22) // len(support))
    for start in range(0, len(support), chunk):
        shifts = (support[None, :] - support[start : start + chunk, None]) % n
        sign = values[start : start + chunk, None] * values[None, :]
        paf += np.bincount(shifts[sign > 0], minlength=n)
        paf -= np.bincount(shifts[sign < 0], minlength=n)
    if paf[0] != weight:
        return CheckResult(False, f"PAF(0) = {int(paf[0])}, expected {weight}", stats)
    nonzero = np.nonzero(paf[1:])[0]
    if len(nonzero):
        s = int(nonzero[0]) + 1
        return CheckResult(
            False, f"the periodic autocorrelation at shift {s} is {int(paf[s])}, expected 0", stats
        )
    if want_proper and not stats["proper"]:
        return CheckResult(
            False,
            "the autocorrelation condition holds but the matrix is not proper: the support lies in "
            "one residue class modulo a divisor e > 1 of n",
            stats,
        )
    return CheckResult(True, f"circulant weighing matrix CW({n},{weight}) verified", stats)
