#!/usr/bin/env python3
"""Convert certificates produced in our earlier research rounds into the checker formats.

Sources (read-only): ``<math>/release/papers/round{1,2}/*/data`` (and ``<math>/attempts*`` for
one cross-check).  Output: ``worker/checkers/testdata/<type>/real/*.json`` (expected to pass)
and ``.../real_fail/*.json`` (real data with a false claim, expected to fail).

usage: convert_real_data.py [--only name1,name2] [--list]

Every converter is a function returning ``[(relative_path, certificate), ...]``; the formats are
documented in ``worker/checkers/FORMATS.md``.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import itertools
import json
import re
import sys
from fractions import Fraction
from math import comb
from pathlib import Path

PROOFXIV = Path(__file__).resolve().parents[3]
MATH = PROOFXIV.parent
PAPERS = MATH / "release" / "papers"
OUT = PROOFXIV / "worker" / "checkers" / "testdata"


def paper(prefix: str) -> Path:
    hits = sorted(PAPERS.glob(f"round*/{prefix}*"))
    if len(hits) != 1:
        raise FileNotFoundError(f"paper directory {prefix!r}: {hits}")
    return hits[0] / "data"


def fstr(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


# ---------------------------------------------------------------------------------------------
# B03: symmetric Hadamard matrices of orders 532 and 732 (propus construction)
# ---------------------------------------------------------------------------------------------


def _propus_matrix(v: int, X1: list[int], X2: list[int], X4: list[int]) -> list[list[int]]:
    """The 4v x 4v propus matrix of data/verify.py (A(i,k) = x(k-i), AR = x(-i-k), RA = x(i+k))."""
    seq = lambda X: [-1 if i in set(X) else 1 for i in range(v)]  # noqa: E731
    a, b, d = seq(X1), seq(X2), seq(X4)
    circ = lambda x: [[x[(k - i) % v] for k in range(v)] for i in range(v)]  # noqa: E731
    AR = lambda x: [[x[(-i - k) % v] for k in range(v)] for i in range(v)]  # noqa: E731
    RA = lambda x: [[x[(i + k) % v] for k in range(v)] for i in range(v)]  # noqa: E731
    neg = lambda M: [[-y for y in row] for row in M]  # noqa: E731
    blocks = [
        [neg(circ(a)), AR(b), AR(b), AR(d)],
        [AR(b), RA(d), circ(a), neg(RA(b))],
        [AR(b), circ(a), neg(RA(d)), RA(b)],
        [AR(d), neg(RA(b)), RA(b), circ(a)],
    ]
    return [sum((blk[i] for blk in brow), []) for brow in blocks for i in range(v)]


def _propus_first(path: Path) -> tuple[int, dict[str, list[int]]]:
    cur: dict[str, list[int]] = {}
    header = None
    for line in path.read_text().splitlines():
        if line.startswith("SOLUTION"):
            if header is not None:
                break
            header = line
        elif ":" in line and header is not None:
            key, vals = line.split(":", 1)
            cur[key.strip()] = [int(t) for t in vals.split()]
    v = int(header.split("v=")[1].split()[0])
    return v, cur


def hadamard_b03():
    data = paper("Symmetric-Hadamard-matrices-of-orders-532-and-732")
    out = []
    for fname in ("propus_families_v133_all72.txt", "propus_families_v183_all432.txt"):
        v, sol = _propus_first(data / fname)
        H = _propus_matrix(v, sol["X1(sym)"], sol["X2=X3"], sol["X4"])
        n = 4 * v
        explicit = MATH / "attempts2" / "B03-hadamard" / "results" / f"H{n}.txt"
        if explicit.exists():  # cross-check with the matrix written by the original search
            M = [
                [int(t) for t in line.split()]
                for line in explicit.read_text().splitlines()
                if line.strip()
            ]
            assert M == H, f"propus construction differs from {explicit}"
        rows = ["".join("+" if x > 0 else "-" for x in row) for row in H]
        out.append(
            (
                f"hadamard_matrix/real/b03_symmetric_hadamard_{n}.json",
                {
                    "type": "hadamard_matrix",
                    "comment": f"B03: symmetric propus Hadamard matrix of order {n} (v = {v}, first family in {fname})",
                    "n": n,
                    "symmetric": True,
                    "rows": rows,
                },
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# B07: Heilbronn configurations in the triangle (0,0),(1,0),(0,1), n = 23 and 35
# ---------------------------------------------------------------------------------------------

_EXACT_RE = re.compile(r"exact value of these literals = ([0-9]+)/([0-9]+)")


def heilbronn_b07():
    data = paper("New-best-known-configurations-for-Heilbronns-triangle-problem")
    out = []
    for n in (23, 35):
        text = (data / f"triangle_n{n}_record.txt").read_text()
        m = _EXACT_RE.search(text)
        normalized = Fraction(
            int(m.group(1)), int(m.group(2))
        )  # = min area / area(T), area(T) = 1/2
        pts = []
        for line in text.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            x, y = line.split()[:2]
            pts.append([fstr(Fraction(x)), fstr(Fraction(y))])
        assert len(pts) == n
        cert = {
            "type": "heilbronn_configuration",
            "comment": f"B07: record Heilbronn configuration of {n} points in the unit triangle "
            f"(decimal literals of triangle_n{n}_record.txt converted exactly)",
            "region": "unit_triangle",
            "points": pts,
            "min_area": fstr(normalized / 2),
        }
        out.append((f"heilbronn_configuration/real/b07_triangle_n{n}.json", cert))
        if n == 23:
            bad = dict(cert, min_area=fstr(normalized / 2 + Fraction(1, 10**40)))
            bad["comment"] = (
                "B07 n = 23 with the claimed minimum area increased by 10^-40 (must fail)"
            )
            out.append(
                ("heilbronn_configuration/fail/real_b07_triangle_n23_claim_too_large.json", bad)
            )
    return out


# ---------------------------------------------------------------------------------------------
# A11: 6-regular graph of girth 9 on 4368 vertices (voltage lift over SL(2,13))
# ---------------------------------------------------------------------------------------------


def girth_a11():
    data = paper("An-explicit-6-regular-graph-of-girth-9-on-4368-vertices")
    p = 13
    edges = []
    for line in (data / "voltages_SL2_13.txt").read_text().splitlines():
        if line.startswith("edge"):
            left, right = line.split(":")
            t = left.split()
            edges.append((int(t[1]), int(t[2]), tuple(int(x) for x in right.split())))

    def mul(x, y):
        a, b, c, d = x
        e, f, g, h = y
        return ((a * e + b * g) % p, (a * f + b * h) % p, (c * e + d * g) % p, (c * f + d * h) % p)

    group = [
        (a, b, c, d)
        for a in range(p)
        for b in range(p)
        for c in range(p)
        for d in range(p)
        if (a * d - b * c) % p == 1
    ]
    index = {g: i for i, g in enumerate(group)}
    base = 1 + max(max(u, v) for u, v, _ in edges)
    vid = lambda u, g: u * len(group) + index[g]  # noqa: E731
    lifted = []
    for u, v, A in edges:  # edge u -> v with voltage A lifts to (u, M) -- (v, M A) for all M
        for M in group:
            lifted.append([vid(u, M), vid(v, mul(M, A))])
    n = base * len(group)
    return [
        (
            "regular_graph_girth/real/a11_6regular_girth9_4368.json",
            {
                "type": "regular_graph_girth",
                "comment": "A11: SL(2,13) voltage lift of the 2-vertex base graph in voltages_SL2_13.txt",
                "n": n,
                "degree": 6,
                "girth": 9,
                "edges": lifted,
            },
        )
    ]


# ---------------------------------------------------------------------------------------------
# A06: four-dimensional linear codes over GF(8) and GF(9)
# ---------------------------------------------------------------------------------------------

_FIELDS = {
    8: {"p": 2, "m": 3, "modulus": [1, 1, 0, 1]},  # x^3 + x + 1, element c0 + 2 c1 + 4 c2
    9: {"p": 3, "m": 2, "modulus": [2, 1, 1]},  # x^2 + x + 2, element c0 + 3 c1
}


def codes_a06():
    data = paper("New-four-dimensional-linear-codes-over-GF8-and-GF9")
    out = []
    for path in sorted((data / "codes").glob("q*_n*_d*.txt")):
        lines = path.read_text().splitlines()
        hdr = dict(kv.split("=") for kv in lines[0].split())
        q, k, n, d = (int(hdr[x]) for x in ("q", "k", "n", "d"))
        cols = []
        for line in lines[1:]:
            if line.strip():
                vals = [int(t) for t in line.split()]
                cols += [vals[1:]] * vals[0]
        assert len(cols) == n
        G = [[col[i] for col in cols] for i in range(k)]
        out.append(
            (
                f"linear_code/real/a06_{path.stem}.json",
                {
                    "type": "linear_code",
                    "comment": f"A06: [{n},{k},{d}] code over GF({q}) from {path.name}",
                    "field": _FIELDS[q],
                    "n": n,
                    "k": k,
                    "generator_matrix": G,
                    "minimum_distance": d,
                },
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# A15: proper circulant weighing matrices
# ---------------------------------------------------------------------------------------------


def cw_a15():
    data = paper("New-proper-circulant-weighing-matrices")
    certs = json.loads((data / "certificates.json").read_text())
    out = []
    for name, c in certs.items():
        out.append(
            (
                f"circulant_weighing_matrix/real/a15_{name}.json",
                {
                    "type": "circulant_weighing_matrix",
                    "comment": f"A15: {c.get('note', name)}",
                    "n": c["n"],
                    "weight": c["k"],
                    "proper": True,
                    "first_row": c["seq"],
                },
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# A10: line-free sets in F_7^3, F_11^3 and F_13^3
# ---------------------------------------------------------------------------------------------


def linefree_a10():
    data = paper("New-lower-bounds-for-line-free-sets-in-F7-3-and-F11-3")
    out = []
    for fname, p in (
        ("linefree_F7_227.txt", 7),
        ("linefree_F11_1010.txt", 11),
        ("linefree_F13_1740_cone.txt", 13),
    ):
        idx = [int(x) for x in (data / fname).read_text().split()]
        pts = [[x % p, (x // p) % p, x // (p * p)] for x in idx]  # index = x + p y + p^2 z
        out.append(
            (
                f"affine_line_free_set/real/a10_{Path(fname).stem}.json",
                {
                    "type": "affine_line_free_set",
                    "comment": f"A10: line-free set of size {len(pts)} in F_{p}^3 ({fname})",
                    "p": p,
                    "dimension": 3,
                    "size": len(pts),
                    "points": pts,
                },
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# A05: van der Waerden colourings (Heule's certificates and unrolled cyclic colourings)
# ---------------------------------------------------------------------------------------------


def vdw_a05():
    data = paper("New-lower-bounds-for-multicolour-van-der-Waerden-numbers")
    out = []
    for path in sorted((data / "heule_certs").glob("W_*.cert")):
        r, k, _ = (int(t) for t in path.stem.split("_")[1:])
        text = path.read_text().strip()
        colouring = text.translate(str.maketrans("abc", "012"))
        out.append(
            (
                f"vdw_coloring/real/a05_heule_{path.stem}.json",
                {
                    "type": "vdw_coloring",
                    "comment": f"A05: {path.name} from Heule's repository; shows W({r},{k}) > {len(text)}",
                    "n": len(text),
                    "colours": r,
                    "k": k,
                    "colouring": colouring,
                },
            )
        )
    for path in sorted(data.glob("chi*.txt")):
        m = re.fullmatch(r"chi(\d*)_(\d+)_(\d+)", path.stem)
        r = int(m.group(1)) if m.group(1) else 2
        k, period = int(m.group(2)), int(m.group(3))
        chi = path.read_text().strip()
        assert len(chi) == period
        unrolled = chi * (k - 1)  # Lemma (unrolling): k-1 periods, no monochromatic k-AP
        out.append(
            (
                f"vdw_coloring/real/a05_unrolled_{path.stem}.json",
                {
                    "type": "vdw_coloring",
                    "comment": f"A05: {k - 1} periods of the cyclic colouring {path.name}; shows W({r},{k}) > {len(unrolled)}",
                    "n": len(unrolled),
                    "colours": r,
                    "k": k,
                    "colouring": unrolled,
                },
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# B02: hypergraph Ramsey colourings (red = colour 0, blue = colour 1)
# ---------------------------------------------------------------------------------------------


def _colex_rank(subset) -> int:
    return sum(comb(a, i + 1) for i, a in enumerate(sorted(subset)))


def _colex_order(n: int, k: int):
    """k-subsets of range(n) as sorted tuples in colex order: by largest element, then recursively."""
    if k == 0:
        yield ()
        return
    for top in range(k - 1, n):
        for rest in _colex_order(top, k - 1):
            yield rest + (top,)


def hypergraph_b02():
    data = paper("New-lower-bounds-for-hypergraph-Ramsey-numbers")
    specs = [
        ("R45_37_red.txt.gz", 37, 3, (4, 5)),
        ("R46_67_red.txt.gz", 67, 3, (4, 6)),
        ("R55_97_red.txt.gz", 97, 3, (5, 5)),
        ("R554_37_red.txt.gz", 37, 4, (5, 5)),
    ]
    out = []
    for fname, n, k, sizes in specs:
        with gzip.open(data / fname, "rt") as fh:
            red = {tuple(sorted(int(t) for t in line.split())) for line in fh if line.strip()}
        assert all(len(s) == k for s in red)
        out.append(
            _hyper_cert(
                n,
                k,
                sizes,
                lambda s, red=red: s not in red,
                f"B02: {fname} (red = colour 0); shows R({sizes[0]},{sizes[1]};{k}) > {n}",
                f"b02_{fname.split('_red')[0]}",
            )
        )
    # R(5,6;4) >= 98: AGL(1,97)-invariant colouring of the 4-subsets of Z_97 (describe_R564_97.txt)
    text = (data / "describe_R564_97.txt").read_text()
    pairs = {tuple(int(x) for x in m) for m in re.findall(r"\{(\d+),(\d+)\}", text)}
    q = 97

    def is_blue(s):
        a, b, c, d = s
        inv = pow(b - a, -1, q)
        t, u = sorted(((c - a) * inv % q, (d - a) * inv % q))
        return (t, u) not in pairs

    out.append(
        _hyper_cert(
            q,
            4,
            (5, 6),
            is_blue,
            "B02: AGL(1,97)-invariant colouring expanded from describe_R564_97.txt "
            "(red = colour 0); shows R(5,6;4) > 97",
            "b02_R564_97",
        )
    )
    return out


def _hyper_cert(n, k, sizes, is_blue, comment, stem):
    total = comb(n, k)
    bits = bytearray((total + 7) // 8)
    for rank, subset in enumerate(_colex_order(n, k)):
        if rank % 9973 == 0:  # spot check of the enumeration order against the rank formula
            assert _colex_rank(subset) == rank
        if is_blue(subset):
            bits[rank >> 3] |= 0x80 >> (rank & 7)
    return (
        f"hypergraph_ramsey_coloring/real/{stem}.json",
        {
            "type": "hypergraph_ramsey_coloring",
            "comment": comment,
            "n": n,
            "k": k,
            "forbidden_sizes": list(sizes),
            "colouring": base64.b64encode(bytes(bits)).decode("ascii"),
        },
    )


# ---------------------------------------------------------------------------------------------
# B09: covering codes K(20,3) <= 1984 and the normal code K(14,2) <= 248
# ---------------------------------------------------------------------------------------------


def covering_b09():
    data = paper("An-improved-upper-bound-for-binary-covering-codes")
    out = []
    for fname, n, radius in (("C_n20_R3_M1984.txt", 20, 3), ("B_n14_R2_M248.txt", 14, 2)):
        words = [int(t) for t in (data / fname).read_text().split()]
        strings = ["".join(str((w >> i) & 1) for i in range(n)) for w in words]  # bit i = x_i
        out.append(
            (
                f"covering_code/real/b09_{Path(fname).stem}.json",
                {
                    "type": "covering_code",
                    "comment": f"B09: {fname}; shows K({n},{radius}) <= {len(words)}",
                    "n": n,
                    "radius": radius,
                    "size": len(words),
                    "codewords": strings,
                },
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# B04: kissing configuration of 12277 points in dimension 19
# ---------------------------------------------------------------------------------------------


def kissing_b04():
    data = paper("A-kissing-configuration-of-12277-points-in-dimension-19")
    rows = [
        [int(t) for t in line.split()]
        for line in (data / "RECORD12277_R19.txt").read_text().splitlines()
        if line.strip()
    ]
    return [
        (
            "kissing_configuration/real/b04_kissing_12277_dim19.json",
            {
                "type": "kissing_configuration",
                "comment": "B04: RECORD12277_R19.txt, 12277 integer vectors in Z^19 with pairwise angles >= 60 degrees",
                "dimension": 19,
                "vectors": rows,
            },
        )
    ]


# ---------------------------------------------------------------------------------------------
# B06: degree-600 polynomial positive on [48/25, infinity)
# ---------------------------------------------------------------------------------------------


def polynomial_b06():
    data = paper("New-certified-upper-bounds-for-the-sign-uncertainty-constant")
    meta = json.loads((data / "su_certificate.json").read_text())
    coeffs = [
        line.strip()
        for line in (data / "su_P_monomial.txt").read_text().splitlines()
        if line.strip()
    ]
    r = Fraction(meta["r_num"], meta["r_den"])
    assert len(coeffs) - 1 == meta["degree"]
    good = {
        "type": "polynomial_positive_interval",
        "comment": f"B06: P(u) = sum_j gamma_j Lam_2j(u) of degree {meta['degree']}; P > 0 on [{fstr(r)}, inf)",
        "coefficients": coeffs,
        "interval": [fstr(r), "inf"],
        "strict": True,
    }
    bad = dict(good, interval=["0", fstr(r)])
    bad["comment"] = "B06 polynomial on [0, 48/25]: P(0) < 0, so positivity must fail"
    return [
        ("polynomial_positive_interval/real/b06_sign_uncertainty_degree600.json", good),
        ("polynomial_positive_interval/real_fail/b06_degree600_on_0_r.json", bad),
    ]


# ---------------------------------------------------------------------------------------------
# Real certificates with a false claim or a deliberate defect.  Each failure is guaranteed by
# construction or by a theorem; fast ones go to fail/ (prefix real_), slow ones to real_fail/.
# ---------------------------------------------------------------------------------------------


def _load(rel: str) -> dict:
    return json.loads((OUT / rel).read_text())


def mutated_real():
    out = []
    H = _load("hadamard_matrix/real/b03_symmetric_hadamard_732.json")
    rows = list(H["rows"])
    rows[100] = rows[100][:417] + ("-" if rows[100][417] == "+" else "+") + rows[100][418:]
    out.append(
        (
            "hadamard_matrix/fail/real_b03_732_one_entry_flipped.json",
            dict(
                H,
                rows=rows,
                comment="B03 order 732 with H[100][417] negated: row 100 is no longer orthogonal to the others",
            ),
        )
    )

    C = _load("linear_code/real/a06_q9_n579_d513.json")
    out.append(
        (
            "linear_code/fail/real_a06_q9_n579_claims_514.json",
            dict(
                C,
                minimum_distance=514,
                comment="A06 [579,4,513] code over GF(9) claimed with distance 514",
            ),
        )
    )

    cw = _load("circulant_weighing_matrix/real/a15_cwFam762.json")
    row = list(cw["first_row"])
    j = next(i for i, x in enumerate(row) if x)
    row[j] = -row[j]
    n = len(row)
    assert any(sum(row[i] * row[(i + s) % n] for i in range(n)) for s in range(1, n))
    out.append(
        (
            "circulant_weighing_matrix/fail/real_a15_cw762_sign_flipped.json",
            dict(
                cw, first_row=row, comment=f"A15 CW(762,361) with the sign at position {j} flipped"
            ),
        )
    )

    lf = _load("affine_line_free_set/real/a10_linefree_F7_227.json")
    p = lf["p"]
    pts = {tuple(x) for x in lf["points"]}
    missing = None
    for v in itertools.product(range(p), repeat=3):
        if not any(v) or next(x for x in v if x) != 1:
            continue
        for a in itertools.product(range(p), repeat=3):
            line = [tuple((a[i] + t * v[i]) % p for i in range(3)) for t in range(p)]
            outside = [q for q in line if q not in pts]
            if len(outside) == 1:
                missing = outside[0]
                break
        if missing:
            break
    out.append(
        (
            "affine_line_free_set/fail/real_a10_F7_plus_one_point.json",
            dict(
                lf,
                points=lf["points"] + [list(missing)],
                size=len(pts) + 1,
                comment=f"A10 line-free set in F_7^3 plus the point {list(missing)}, the last missing point of a line",
            ),
        )
    )

    cov = _load("covering_code/real/b09_C_n20_R3_M1984.json")
    assert 1984 * sum(comb(20, w) for w in range(3)) < 2**20  # sphere-covering bound
    out.append(
        (
            "covering_code/fail/real_b09_n20_radius2.json",
            dict(
                cov,
                radius=2,
                comment="B09 code of size 1984 claimed with covering radius 2: impossible by the sphere-covering bound",
            ),
        )
    )

    hy = _load("hypergraph_ramsey_coloring/real/b02_R45_37.json")
    out.append(
        (
            "hypergraph_ramsey_coloring/fail/real_b02_R45_37_claimed_R44.json",
            dict(
                hy,
                forbidden_sizes=[4, 4],
                comment="B02 R(4,5;3) colouring of [37] claimed for R(4,4;3) > 37, false since R(4,4;3) = 13",
            ),
        )
    )

    vd = _load("vdw_coloring/real/a05_heule_W_2_6_226.json")
    out.append(
        (
            "vdw_coloring/fail/real_a05_W26_claimed_k5.json",
            dict(
                vd,
                k=5,
                comment="A05 W(2,6) > 1130 colouring claimed for 5-term progressions, false since W(2,5) = 178",
            ),
        )
    )

    g = _load("regular_graph_girth/real/a11_6regular_girth9_4368.json")
    out.append(
        (
            "regular_graph_girth/real_fail/a11_claimed_girth10.json",
            dict(g, girth=10, comment="A11 graph (girth 9) claimed with girth 10"),
        )
    )

    k = _load("kissing_configuration/real/b04_kissing_12277_dim19.json")
    out.append(
        (
            "kissing_configuration/real_fail/b04_with_duplicate_vector.json",
            dict(
                k,
                vectors=k["vectors"] + [k["vectors"][5000]],
                comment="B04 configuration with vector 5000 repeated",
            ),
        )
    )
    return out


CONVERTERS = {
    "hadamard_b03": hadamard_b03,
    "heilbronn_b07": heilbronn_b07,
    "girth_a11": girth_a11,
    "codes_a06": codes_a06,
    "cw_a15": cw_a15,
    "linefree_a10": linefree_a10,
    "vdw_a05": vdw_a05,
    "hypergraph_b02": hypergraph_b02,
    "covering_b09": covering_b09,
    "kissing_b04": kissing_b04,
    "polynomial_b06": polynomial_b06,
    "mutated_real": mutated_real,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    names = [x for x in args.only.split(",") if x] or list(CONVERTERS)
    for name in names:
        for rel, cert in CONVERTERS[name]():
            path = OUT / rel
            if args.list:
                print(rel)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cert, separators=(",", ":")) + "\n")
            print(f"{name}: wrote {rel} ({path.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
