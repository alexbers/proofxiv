#!/usr/bin/env python3
"""Differential fuzzing of the two checker implementations.

Generates random small certificates of every type (valid and invalid ones, plus random
corruptions), runs the Python implementation in-process and the Rust binary as a subprocess, and
reports every case where the exit codes or the ``stats`` objects differ.

usage: fuzz_compare.py [--rust PATH] [--cases N] [--seed S] [--types t1,t2] [--workdir DIR]
The default work directory is <proofxiv>/.dev/tmp/fuzz (never the system temp directory).
"""

from __future__ import annotations

import argparse
import base64
import itertools
import json
import random
import subprocess
import sys
from fractions import Fraction
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # .../proofxiv
sys.path.insert(0, str(ROOT / "worker" / "src"))

from proofxiv_worker.checkers import check  # noqa: E402
from proofxiv_worker.checkers.common import CertificateError  # noqa: E402


def python_verdict(ctype: str, data: dict) -> tuple[int, dict]:
    try:
        res = check(ctype, json.loads(json.dumps(data)))
    except CertificateError:
        return 2, {}
    return (0 if res.ok else 1), res.stats


# ----------------------------------------------------------------------------------------------
# generators
# ----------------------------------------------------------------------------------------------


def g_graph_ramsey(r: random.Random) -> dict:
    n = r.randint(0, 13)
    c = r.randint(1, 3)
    if r.random() < 0.3 and n >= 2:
        p = r.choice([5, 13, 17])
        n = p
        qr = {(x * x) % p for x in range(1, p)}
        s = "".join("1" if (j - i) % p in qr else "0" for j in range(n) for i in range(j))
        c = 2
    else:
        s = "".join(str(r.randrange(c)) for _ in range(n * (n - 1) // 2))
    return {
        "n": n,
        "colours": c,
        "forbidden_clique_sizes": [r.randint(1, 5) for _ in range(c)],
        "edge_colours": s,
    }


def colex_rank(subset) -> int:
    return sum(comb(a, i + 1) for i, a in enumerate(sorted(subset)))


def encode_colouring(n: int, k: int, colour) -> str:
    total = comb(n, k)
    bits = bytearray((total + 7) // 8)
    for subset in itertools.combinations(range(n), k):
        if colour(subset):
            r = colex_rank(subset)
            bits[r >> 3] |= 0x80 >> (r & 7)
    return base64.b64encode(bytes(bits)).decode()


def g_hypergraph(r: random.Random) -> dict:
    k = r.choice([2, 3, 3, 4])
    n = r.randint(k, 11)
    p = r.random()
    table = {s: r.random() < p for s in itertools.combinations(range(n), k)}
    sizes = [r.randint(max(1, k - 1), k + 3) for _ in range(2)]
    return {
        "n": n,
        "k": k,
        "forbidden_sizes": sizes,
        "colouring": encode_colouring(n, k, table.__getitem__),
    }


def g_vdw(r: random.Random) -> dict:
    n = r.randint(1, 60)
    colours = r.randint(1, 3)
    k = r.randint(1, 6)
    if r.random() < 0.3:
        base = "00110011"  # W(2,3) = 9
        n, colours, k = r.randint(1, 8), 2, 3
        s = base[:n]
    else:
        s = "".join(str(r.randrange(colours)) for _ in range(n))
    return {"n": n, "colours": colours, "k": k, "colouring": s}


FIELDS = {
    2: {"p": 2},
    3: {"p": 3},
    4: {"p": 2, "m": 2, "modulus": [1, 1, 1]},
    5: {"p": 5},
    7: {"p": 7},
    8: {"p": 2, "m": 3, "modulus": [1, 1, 0, 1]},
    9: {"p": 3, "m": 2, "modulus": [2, 1, 1]},
}


def g_linear_code(r: random.Random) -> dict:
    q = r.choice(list(FIELDS))
    n = r.randint(1, 10)
    k = r.randint(1, min(n, 4 if q <= 5 else 3))
    G = [[r.randrange(q) if r.random() < 0.8 else 0 for _ in range(n)] for _ in range(k)]
    return {
        "field": FIELDS[q],
        "n": n,
        "k": k,
        "generator_matrix": G,
        "minimum_distance": r.randint(1, n),
    }


def g_covering(r: random.Random) -> dict:
    n = r.randint(1, 11)
    radius = r.randint(0, min(n, 3))
    m = r.randint(1, 1 + 2**n // (1 + n * radius))
    words = ["".join(r.choice("01") for _ in range(n)) for _ in range(m)]
    if r.random() < 0.5:
        words = sorted(set(words))
    data = {"n": n, "radius": radius, "codewords": words}
    if r.random() < 0.3:
        data["size"] = len(set(words)) + r.choice([0, 0, 1])
    return data


def g_girth(r: random.Random) -> dict:
    n = r.randint(1, 16)
    choice = r.random()
    if choice < 0.4 and n >= 3:
        # circulant graph C_n(S)
        steps = r.sample(range(1, n // 2 + 1), r.randint(1, max(1, min(3, n // 2))))
        edges = {tuple(sorted((i, (i + s) % n))) for i in range(n) for s in steps}
        edges = [list(e) for e in edges if e[0] != e[1]]
    else:
        edges = [[r.randrange(n), r.randrange(n)] for _ in range(r.randint(0, 2 * n))]
    degs = [0] * n
    for u, v in edges:
        degs[u] += 1
        degs[v] += 1
    return {
        "n": n,
        "degree": r.choice([degs[0], r.randint(0, 4)]),
        "girth": r.randint(3, 8),
        "edges": edges,
    }


def paley_type_one(q: int) -> list[list[int]]:
    """Paley I Hadamard matrix of order q + 1 for a prime q = 3 mod 4: H = I + S, S skew."""
    qr = {(x * x) % q for x in range(1, q)}

    def chi(x: int) -> int:
        return 0 if x % q == 0 else (1 if x % q in qr else -1)

    S = [[0] + [1] * q] + [[-1] + [chi(j - i) for j in range(q)] for i in range(q)]
    return [[S[i][j] + (1 if i == j else 0) for j in range(q + 1)] for i in range(q + 1)]


def g_hadamard(r: random.Random) -> dict:
    order = r.choice([1, 2, 4, 8, 12, 16])
    if order == 12:
        H = paley_type_one(11)
    else:
        H = [[1]]
        while len(H) < order:
            H = [row + row for row in H] + [row + [-x for x in row] for row in H]
    if r.random() < 0.5:  # random equivalence: negate rows / columns, permute
        for i in range(len(H)):
            if r.random() < 0.5:
                H[i] = [-x for x in H[i]]
        perm = list(range(len(H)))
        r.shuffle(perm)
        H = [[row[p] for p in perm] for row in H]
    if r.random() < 0.4:
        i, j = r.randrange(len(H)), r.randrange(len(H))
        H[i][j] = -H[i][j]
    rows = ["".join("+" if x > 0 else "-" for x in row) for row in H]
    data = {"n": len(H), "rows": rows}
    if r.random() < 0.5:
        data["symmetric"] = r.random() < 0.5
    return data


def g_cw(r: random.Random) -> dict:
    known = [
        (7, 4, [-1, 1, 1, 0, 1, 0, 0]),
        (4, 1, [0, 0, 1, 0]),
        (2, 2, [1, 1]),
        (4, 4, [1, 1, 1, -1]),
    ]
    if r.random() < 0.5:
        n, w, row = r.choice(known)
        row = list(row)
        if r.random() < 0.5:
            s = r.randrange(n)
            row = row[s:] + row[:s]
        if r.random() < 0.3:
            row[r.randrange(n)] *= -1
    else:
        n = r.randint(1, 20)
        row = [r.choice([-1, 0, 0, 1]) for _ in range(n)]
        w = max(1, sum(1 for x in row if x))
    data = {
        "n": len(row),
        "weight": max(1, min(len(row), w + r.choice([0, 0, 0, 1]))),
        "first_row": row,
    }
    if r.random() < 0.5:
        data["proper"] = r.random() < 0.5
    return data


def g_affine(r: random.Random) -> dict:
    p = r.choice([2, 3, 3, 5, 7])
    d = r.randint(1, 3 if p <= 5 else 2)
    space = list(itertools.product(range(p), repeat=d))
    k = r.randint(0, len(space))
    pts = [list(x) for x in r.sample(space, k)]
    if pts and r.random() < 0.1:
        pts.append(list(pts[0]))
    data = {"p": p, "dimension": d, "points": pts}
    if r.random() < 0.3:
        data["size"] = len(pts) + r.choice([0, 0, 1])
    return data


def g_kissing(r: random.Random) -> dict:
    dim = r.randint(1, 5)
    if r.random() < 0.4 and dim >= 2:
        vs = []
        for i, j in itertools.combinations(range(dim), 2):
            for si in (1, -1):
                for sj in (1, -1):
                    v = [0] * dim
                    v[i], v[j] = si, sj
                    vs.append(v)
        r.shuffle(vs)
        vs = vs[: r.randint(1, len(vs))]
        if r.random() < 0.3:
            vs.append([r.randint(-2, 2) for _ in range(dim)])
        data = {"dimension": dim, "vectors": vs}
        if r.random() < 0.5:
            data["squared_norm"] = 2
        return data
    vs = [
        [r.randint(-3, 3) * r.choice([1, 1, 1000003]) for _ in range(dim)]
        for _ in range(r.randint(1, 12))
    ]
    return {"dimension": dim, "vectors": vs}


def rat(r: random.Random, lo: Fraction, hi: Fraction) -> Fraction:
    den = r.randint(1, 12)
    return Fraction(r.randint(int(lo * den), int(hi * den)), den)


def fstr(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


def g_heilbronn(r: random.Random) -> dict:
    region = r.choice(["unit_square", "unit_triangle", "tri"])
    n = r.randint(3, 9)
    pts = []
    for _ in range(n):
        x, y = (
            rat(r, Fraction(-1, 10), Fraction(11, 10)),
            rat(r, Fraction(-1, 10), Fraction(11, 10)),
        )
        if region == "unit_triangle" and r.random() < 0.8 and x + y > 1:
            x, y = 1 - x, 1 - y
        pts.append([fstr(x), fstr(y)])
    reg = region
    if region == "tri":
        reg = {"triangle": [["0", "0"], ["2", "0"], ["1/2", "3/2"]]}
    return {
        "region": reg,
        "points": pts,
        "min_area": fstr(Fraction(r.randint(0, 20), r.randint(1, 400))),
    }


def g_poly(r: random.Random) -> dict:
    coeffs = [Fraction(r.choice([-3, -1, 1, 2]))]
    for _ in range(r.randint(0, 4)):
        root = Fraction(r.randint(-8, 8), r.randint(1, 3))
        for _ in range(r.choice([1, 1, 2, 3])):
            coeffs = (
                [-root * coeffs[0]]
                + [coeffs[i - 1] - root * coeffs[i] for i in range(1, len(coeffs))]
                + [coeffs[-1]]
            )
    if r.random() < 0.4:  # times x^2 + e
        e = Fraction(r.randint(0, 3), r.randint(1, 2))
        coeffs = [e * coeffs[0], e * coeffs[1] if len(coeffs) > 1 else 0] + [
            (e * coeffs[i] if i < len(coeffs) else 0) + coeffs[i - 2]
            for i in range(2, len(coeffs) + 2)
        ]
    if r.random() < 0.3:
        coeffs = [c + Fraction(r.randint(-2, 2), 7) for c in coeffs]
    kind = r.choice(["fin", "fin", "right", "left", "all", "point"])
    a = rat(r, Fraction(-6), Fraction(6))
    b = a + rat(r, Fraction(0), Fraction(8))
    interval = {
        "fin": [fstr(a), fstr(b)],
        "right": [fstr(a), "inf"],
        "left": ["-inf", fstr(b)],
        "all": ["-inf", "inf"],
        "point": [fstr(a), fstr(a)],
    }[kind]
    data = {"coefficients": [fstr(Fraction(c)) for c in coeffs], "interval": interval}
    if r.random() < 0.6:
        data["strict"] = r.random() < 0.5
    return data


GENERATORS = {
    "graph_ramsey_coloring": g_graph_ramsey,
    "hypergraph_ramsey_coloring": g_hypergraph,
    "vdw_coloring": g_vdw,
    "linear_code": g_linear_code,
    "covering_code": g_covering,
    "regular_graph_girth": g_girth,
    "hadamard_matrix": g_hadamard,
    "circulant_weighing_matrix": g_cw,
    "affine_line_free_set": g_affine,
    "kissing_configuration": g_kissing,
    "heilbronn_configuration": g_heilbronn,
    "polynomial_positive_interval": g_poly,
}


def corrupt(r: random.Random, data: dict) -> dict:
    """A random structural corruption (usually makes the certificate malformed)."""
    data = json.loads(json.dumps(data))
    key = r.choice(sorted(data))
    action = r.randrange(6)
    if action == 0:
        del data[key]
    elif action == 1:
        data[key] = r.choice([None, True, "x", 1.5 if False else "1.5", [], {}, -1, 10**6])
    elif action == 2:
        data["unexpected_field"] = 1
    elif action == 3 and isinstance(data[key], list) and data[key]:
        data[key] = data[key][:-1]
    elif action == 4 and isinstance(data[key], str) and data[key]:
        data[key] = data[key][:-1] + "?"
    else:
        data["type"] = "something_else"
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--rust",
        default=str(ROOT / "worker" / "checkers-rs" / "target" / "release" / "proofxiv-check"),
    )
    ap.add_argument("--cases", type=int, default=150)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--types", default=",".join(GENERATORS))
    ap.add_argument("--workdir", default=str(ROOT / ".dev" / "tmp" / "fuzz"))
    args = ap.parse_args()
    work = Path(args.workdir)
    work.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    failures = 0
    for ctype in args.types.split(","):
        gen = GENERATORS[ctype]
        tally = {0: 0, 1: 0, 2: 0}
        for case in range(args.cases):
            data = gen(rng)
            if rng.random() < 0.1:
                data = corrupt(rng, data)
            path = work / f"{ctype}.json"
            path.write_text(json.dumps(data))
            py_code, py_stats = python_verdict(ctype, data)
            proc = subprocess.run([args.rust, ctype, str(path)], capture_output=True, check=False)
            try:
                rs = json.loads(proc.stdout)
            except ValueError:
                rs = {"stats": None}
            tally[py_code] += 1
            if proc.returncode != py_code or (py_code != 2 and rs.get("stats") != py_stats):
                failures += 1
                keep = work / f"mismatch_{ctype}_{case}.json"
                keep.write_text(json.dumps(data))
                print(
                    f"MISMATCH {ctype} case {case}: python exit {py_code} stats {py_stats}; "
                    f"rust exit {proc.returncode} {proc.stdout.decode()[:300]} -> {keep}"
                )
        print(f"{ctype:32s} pass={tally[0]:4d} fail={tally[1]:4d} malformed={tally[2]:4d}")
    print("mismatches:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
