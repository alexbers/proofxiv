#!/usr/bin/env python3
"""Generate the hand-made test vectors in ``worker/checkers/testdata/<type>/{pass,fail,malformed}``.

Every vector is built from a classical object whose properties are known independently of the
checkers (Paley and Clebsch colourings, Golay codes, Petersen/Heawood/Tutte-Coxeter/Hoffman-Singleton
graphs, Sylvester and Paley Hadamard matrices, root systems, ...), or from such an object with a
deliberate defect.  The expected verdict is given by the directory: pass = valid (exit 0),
fail = invalid (exit 1), malformed = violates the format (exit 2).  Running the script again
reproduces the same files (all searches use fixed seeds).
"""

from __future__ import annotations

import base64
import itertools
import json
import random
import sys
from fractions import Fraction
from math import comb
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "testdata"
WRITTEN: list[Path] = []


def emit(ctype: str, verdict: str, name: str, data, comment: str, raw: str | None = None) -> None:
    path = OUT / ctype / verdict / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:  # deliberately broken JSON text (malformed vectors only)
        path.write_text(raw)
    else:
        doc = {"type": ctype, "comment": comment}
        doc.update(data)
        path.write_text(json.dumps(doc) + "\n")
    WRITTEN.append(path)


def fstr(x) -> str:
    x = Fraction(x)
    return str(x.numerator) if x.denominator == 1 else f"{x.numerator}/{x.denominator}"


# =============================================================================================
# graph_ramsey_coloring
# =============================================================================================


def colours_from(n: int, colour) -> str:
    """edge_colours string: pairs {i<j} in colex order (rank j(j-1)/2 + i)."""
    return "".join(str(colour(i, j)) for j in range(n) for i in range(j))


def circulant_colour(n: int, steps: set[int]):
    return lambda i, j: 0 if min((j - i) % n, (i - j) % n) in steps else 1


def gf16_log():
    """discrete logarithm in GF(16) = F2[x]/(x^4 + x + 1), generator x."""
    log, e = {}, 1
    for k in range(15):
        log[e] = k
        e <<= 1
        if e & 16:
            e ^= 0b10011
    return log


def graph_ramsey() -> None:
    t = "graph_ramsey_coloring"
    pentagon = circulant_colour(5, {1})
    emit(
        t,
        "pass",
        "pentagon_R33_gt_5",
        {
            "n": 5,
            "colours": 2,
            "forbidden_clique_sizes": [3, 3],
            "edge_colours": colours_from(5, pentagon),
        },
        "pentagon / pentagram: R(3,3) > 5",
    )
    qr17 = {(x * x) % 17 for x in range(1, 17)}
    paley17 = lambda i, j: 0 if (j - i) % 17 in qr17 else 1  # noqa: E731
    emit(
        t,
        "pass",
        "paley17_R44_gt_17",
        {
            "n": 17,
            "colours": 2,
            "forbidden_clique_sizes": [4, 4],
            "edge_colours": colours_from(17, paley17),
        },
        "Paley graph of order 17 and its complement: R(4,4) > 17",
    )
    emit(
        t,
        "pass",
        "wagner8_R34_gt_8",
        {
            "n": 8,
            "colours": 2,
            "forbidden_clique_sizes": [3, 4],
            "edge_colours": colours_from(8, circulant_colour(8, {1, 4})),
        },
        "circulant C8(1,4) is triangle-free with independence number 3: R(3,4) > 8",
    )
    emit(
        t,
        "pass",
        "cubic_residues13_R35_gt_13",
        {
            "n": 13,
            "colours": 2,
            "forbidden_clique_sizes": [3, 5],
            "edge_colours": colours_from(13, circulant_colour(13, {1, 5})),
        },
        "circulant C13(1,5) (cubic residues mod 13): R(3,5) > 13",
    )
    log = gf16_log()
    clebsch = lambda i, j: log[i ^ j] % 3  # noqa: E731
    emit(
        t,
        "pass",
        "clebsch_R333_gt_16",
        {
            "n": 16,
            "colours": 3,
            "forbidden_clique_sizes": [3, 3, 3],
            "edge_colours": colours_from(16, clebsch),
        },
        "Greenwood-Gleason colouring of K16 by cubic classes of GF(16)*: R(3,3,3) > 16",
    )
    emit(
        t,
        "pass",
        "single_vertex",
        {"n": 1, "colours": 2, "forbidden_clique_sizes": [2, 2], "edge_colours": ""},
        "K1 has no edge: R(2,2) > 1",
    )
    emit(
        t,
        "pass",
        "asymmetric_sizes_paley17",
        {
            "n": 17,
            "colours": 2,
            "forbidden_clique_sizes": [4, 5],
            "edge_colours": colours_from(17, paley17),
        },
        "a valid certificate for a weaker claim, R(4,5) > 17",
    )
    for p, s, verdict in ((37, 5, "pass"), (101, 6, "real")):
        qr = {(x * x) % p for x in range(1, p)}
        emit(
            t,
            verdict,
            f"paley{p}_R{s}{s}_gt_{p}",
            {
                "n": p,
                "colours": 2,
                "forbidden_clique_sizes": [s, s],
                "edge_colours": colours_from(
                    p, lambda i, j, qr=qr, p=p: 0 if (j - i) % p in qr else 1
                ),
            },
            f"Paley graph of order {p} (clique number {s - 1}): R({s},{s}) > {p}"
            + (" -- the classical bound R(6,6) >= 102 from the literature" if p == 101 else ""),
        )

    emit(
        t,
        "fail",
        "paley17_has_triangles",
        {
            "n": 17,
            "colours": 2,
            "forbidden_clique_sizes": [3, 4],
            "edge_colours": colours_from(17, paley17),
        },
        "Paley(17) contains triangles, so it does not show R(3,4) > 17",
    )
    flipped = colours_from(
        5, lambda i, j: 1 - pentagon(i, j) if (i, j) == (0, 1) else pentagon(i, j)
    )
    emit(
        t,
        "fail",
        "pentagon_one_edge_flipped",
        {"n": 5, "colours": 2, "forbidden_clique_sizes": [3, 3], "edge_colours": flipped},
        "pentagon colouring with the single edge {0,1} recoloured: a monochromatic triangle appears",
    )
    rng = random.Random(18)
    extra = {i: rng.randrange(2) for i in range(17)}
    paley18 = lambda i, j: extra[i] if j == 17 else paley17(i, j)  # noqa: E731
    emit(
        t,
        "fail",
        "K18_R44_impossible",
        {
            "n": 18,
            "colours": 2,
            "forbidden_clique_sizes": [4, 4],
            "edge_colours": colours_from(18, paley18),
        },
        "Paley(17) plus one vertex: R(4,4) = 18, so every 2-colouring of K18 has a monochromatic K4",
    )
    emit(
        t,
        "fail",
        "K6_R33_impossible",
        {
            "n": 6,
            "colours": 2,
            "forbidden_clique_sizes": [3, 3],
            "edge_colours": colours_from(6, circulant_colour(6, {1, 3})),
        },
        "R(3,3) = 6: the colouring C6(1,3) / complement has a monochromatic triangle",
    )
    e0 = next((i, j) for j in range(16) for i in range(j) if clebsch(i, j) != 0)
    clebsch_bad = colours_from(16, lambda i, j: 0 if (i, j) == e0 else clebsch(i, j))
    emit(
        t,
        "fail",
        "clebsch_one_edge_recoloured",
        {"n": 16, "colours": 3, "forbidden_clique_sizes": [3, 3, 3], "edge_colours": clebsch_bad},
        f"Clebsch colouring with edge {{{e0[0]},{e0[1]}}} moved to colour 0 (non-adjacent vertices of the "
        "Clebsch graph have common neighbours, so a colour-0 triangle appears)",
    )
    emit(
        t,
        "fail",
        "forbidden_size_one",
        {"n": 3, "colours": 2, "forbidden_clique_sizes": [1, 3], "edge_colours": "010"},
        "a single vertex is a clique of size 1 in every colour",
    )

    good = {
        "n": 5,
        "colours": 2,
        "forbidden_clique_sizes": [3, 3],
        "edge_colours": colours_from(5, pentagon),
    }
    emit(
        t,
        "malformed",
        "edge_string_too_short",
        dict(good, edge_colours=good["edge_colours"][:-1]),
        "length 9 instead of 10",
    )
    emit(
        t,
        "malformed",
        "colour_digit_out_of_range",
        dict(good, edge_colours="2" + good["edge_colours"][1:]),
        "digit 2 with 2 colours",
    )
    emit(
        t,
        "malformed",
        "sizes_length_mismatch",
        dict(good, forbidden_clique_sizes=[3, 3, 3]),
        "three sizes for two colours",
    )
    emit(t, "malformed", "unknown_field", dict(good, colors=2), "misspelled extra field")
    emit(
        t,
        "malformed",
        "size_zero",
        dict(good, forbidden_clique_sizes=[0, 3]),
        "clique size must be >= 1",
    )
    emit(t, "malformed", "n_is_string", dict(good, n="5"), "n must be a JSON integer")
    emit(
        t,
        "malformed",
        "wrong_type_field",
        dict(good, type="vdw_coloring"),
        "field 'type' disagrees with the CLI type",
    )


# =============================================================================================
# hypergraph_ramsey_coloring
# =============================================================================================


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


def mono_sets(n: int, k: int, s: int, col: dict) -> list:
    out = []
    for S in itertools.combinations(range(n), s):
        cs = {col[K] for K in itertools.combinations(S, k)}
        if len(cs) == 1:
            out.append(S)
    return out


def search_hyper(n: int, k: int, s: int, seed: int, max_steps: int = 200000) -> dict:
    """Local search for a 2-colouring of the k-subsets of [n] with no monochromatic s-set."""
    rng = random.Random(seed)
    ksets = list(itertools.combinations(range(n), k))
    col = {K: rng.randrange(2) for K in ksets}
    ssets = list(itertools.combinations(range(n), s))
    contains = {K: [] for K in ksets}
    for S in ssets:
        for K in itertools.combinations(S, k):
            contains[K].append(S)

    def is_mono(S):
        first = None
        for K in itertools.combinations(S, k):
            if first is None:
                first = col[K]
            elif col[K] != first:
                return False
        return True

    bad = {S for S in ssets if is_mono(S)}
    for _ in range(max_steps):
        if not bad:
            return col
        S = rng.choice(sorted(bad))
        best, best_delta = None, None
        for K in itertools.combinations(S, k):
            before = sum(1 for T in contains[K] if T in bad)
            col[K] ^= 1
            after = sum(1 for T in contains[K] if is_mono(T))
            col[K] ^= 1
            delta = after - before
            if best is None or delta < best_delta or (delta == best_delta and rng.random() < 0.5):
                best, best_delta = K, delta
        if best_delta > 0 and rng.random() < 0.7:
            best = rng.choice(list(itertools.combinations(S, k)))
        col[best] ^= 1
        for T in contains[best]:
            if is_mono(T):
                bad.add(T)
            else:
                bad.discard(T)
    raise RuntimeError("local search failed")


def hypergraph() -> None:
    t = "hypergraph_ramsey_coloring"
    col11 = search_hyper(11, 3, 4, seed=1)
    assert not mono_sets(11, 3, 4, col11)
    enc = lambda n, k, col: encode_colouring(n, k, lambda K: col[K])  # noqa: E731
    emit(
        t,
        "pass",
        "R443_gt_11",
        {"n": 11, "k": 3, "forbidden_sizes": [4, 4], "colouring": enc(11, 3, col11)},
        "2-colouring of the triples of [11] with no monochromatic 4-set: R(4,4;3) > 11",
    )
    pent = {
        K: int(min((K[1] - K[0]) % 5, (K[0] - K[1]) % 5) != 1)
        for K in itertools.combinations(range(5), 2)
    }
    emit(
        t,
        "pass",
        "graph_case_k2_pentagon",
        {"n": 5, "k": 2, "forbidden_sizes": [3, 3], "colouring": enc(5, 2, pent)},
        "k = 2 is the graph case: pentagon colouring, R(3,3;2) > 5",
    )
    col6 = search_hyper(7, 4, 5, seed=3)
    emit(
        t,
        "pass",
        "four_uniform_n7_s55",
        {"n": 7, "k": 4, "forbidden_sizes": [5, 5], "colouring": enc(7, 4, col6)},
        "2-colouring of the 4-subsets of [7] with no monochromatic 5-set: R(5,5;4) > 7",
    )
    emit(
        t,
        "pass",
        "sizes_above_n",
        {
            "n": 6,
            "k": 3,
            "forbidden_sizes": [7, 8],
            "colouring": encode_colouring(6, 3, lambda K: 0),
        },
        "forbidden sizes larger than n are vacuously avoided",
    )
    one4 = {K: int(K == (0, 1, 2)) for K in itertools.combinations(range(4), 3)}
    emit(
        t,
        "pass",
        "n4_single_split",
        {"n": 4, "k": 3, "forbidden_sizes": [4, 4], "colouring": enc(4, 3, one4)},
        "the only 4-set has triples of both colours",
    )

    flip = None
    for K in itertools.combinations(range(11), 3):
        col11[K] ^= 1
        if mono_sets(11, 3, 4, col11):
            flip = K
            break
        col11[K] ^= 1
    assert flip is not None
    emit(
        t,
        "fail",
        "R443_one_triple_flipped",
        {"n": 11, "k": 3, "forbidden_sizes": [4, 4], "colouring": enc(11, 3, col11)},
        f"the R(4,4;3) > 11 colouring with the triple {list(flip)} recoloured",
    )
    rng = random.Random(13)
    col13 = {K: rng.randrange(2) for K in itertools.combinations(range(13), 3)}
    emit(
        t,
        "fail",
        "R443_n13_impossible",
        {"n": 13, "k": 3, "forbidden_sizes": [4, 4], "colouring": enc(13, 3, col13)},
        "R(4,4;3) = 13 (McKay-Radziszowski), so every colouring of the triples of [13] fails",
    )
    emit(
        t,
        "fail",
        "size_below_k_vacuous",
        {
            "n": 6,
            "k": 3,
            "forbidden_sizes": [2, 6],
            "colouring": encode_colouring(6, 3, lambda K: 1),
        },
        "a 2-set has no 3-subsets, so it is vacuously monochromatic in colour 0",
    )
    emit(
        t,
        "fail",
        "all_one_colour",
        {
            "n": 6,
            "k": 4,
            "forbidden_sizes": [5, 6],
            "colouring": encode_colouring(6, 4, lambda K: 1),
        },
        "every 4-subset has colour 1, so [6] itself is monochromatic",
    )

    good = {"n": 5, "k": 2, "forbidden_sizes": [3, 3], "colouring": enc(5, 2, pent)}
    raw = base64.b64decode(good["colouring"])
    emit(
        t,
        "malformed",
        "padding_bit_set",
        dict(good, colouring=base64.b64encode(raw[:-1] + bytes([raw[-1] | 1])).decode()),
        "C(5,2) = 10 bits; a padding bit of the last byte is 1",
    )
    emit(
        t,
        "malformed",
        "too_many_bytes",
        dict(good, colouring=base64.b64encode(raw + b"\0").decode()),
        "one byte too many",
    )
    emit(
        t,
        "malformed",
        "non_canonical_base64",
        dict(good, colouring=good["colouring"][:-2] + "B="),
        "nonzero bits in the base64 padding position",
    )
    emit(
        t,
        "malformed",
        "base64_with_newline",
        dict(good, colouring=good["colouring"][:2] + "\n" + good["colouring"][2:]),
        "whitespace inside base64",
    )
    emit(t, "malformed", "k_greater_than_n", dict(good, n=3, k=4, colouring=""), "k > n")
    emit(
        t,
        "malformed",
        "three_sizes",
        dict(good, forbidden_sizes=[3, 3, 3]),
        "exactly two sizes are required",
    )
    emit(
        t,
        "malformed",
        "too_many_subsets",
        dict(good, n=1024, k=4, colouring=""),
        "C(1024,4) exceeds 2^27",
    )


# =============================================================================================
# vdw_coloring
# =============================================================================================


def vdw_search(n: int, r: int, k: int) -> str:
    col = [0] * n

    def ok(pos):
        c = col[pos]
        for d in range(1, pos // (k - 1) + 1):
            if all(col[pos - j * d] == c for j in range(1, k)):
                return False
        return True

    def rec(pos, used):
        if pos == n:
            return True
        for c in range(min(r, used + 1)):  # symmetry: colours appear in order
            col[pos] = c
            if ok(pos) and rec(pos + 1, max(used, c + 1)):
                return True
        return False

    assert rec(0, 0)
    return "".join(map(str, col))


def vdw() -> None:
    t = "vdw_coloring"
    emit(
        t, "pass", "W23_gt_8", {"n": 8, "colours": 2, "k": 3, "colouring": "00110011"}, "W(2,3) = 9"
    )
    w24 = vdw_search(34, 2, 4)
    emit(t, "pass", "W24_gt_34", {"n": 34, "colours": 2, "k": 4, "colouring": w24}, "W(2,4) = 35")
    w33 = vdw_search(26, 3, 3)
    emit(t, "pass", "W33_gt_26", {"n": 26, "colours": 3, "k": 3, "colouring": w33}, "W(3,3) = 27")
    emit(
        t,
        "pass",
        "k_larger_than_n",
        {"n": 4, "colours": 1, "k": 5, "colouring": "0000"},
        "no 5-term progression fits in {1..4}",
    )
    emit(
        t,
        "pass",
        "k2_distinct_colours",
        {"n": 3, "colours": 3, "k": 2, "colouring": "012"},
        "2-term progressions are pairs: all colours distinct",
    )

    emit(
        t,
        "fail",
        "W23_n9_impossible",
        {"n": 9, "colours": 2, "k": 3, "colouring": "001100110"},
        "W(2,3) = 9: {1,5,9} is monochromatic",
    )
    for pos in range(34):
        bad = w24[:pos] + str(1 - int(w24[pos])) + w24[pos + 1 :]
        if any(
            all(bad[a + j * d] == bad[a] for j in range(4))
            for d in range(1, 12)
            for a in range(34 - 3 * d)
        ):
            break
    emit(
        t,
        "fail",
        "W24_one_flip",
        {"n": 34, "colours": 2, "k": 4, "colouring": bad},
        f"the W(2,4) > 34 colouring with position {pos + 1} recoloured",
    )
    emit(
        t,
        "fail",
        "W24_extended_to_35",
        {"n": 35, "colours": 2, "k": 4, "colouring": w24 + "0"},
        "W(2,4) = 35: any colouring of {1..35} fails",
    )
    emit(
        t,
        "fail",
        "k1_always_fails",
        {"n": 3, "colours": 2, "k": 1, "colouring": "010"},
        "a single integer is a 1-term progression",
    )
    emit(
        t,
        "fail",
        "w33_extended_with_run",
        {"n": 30, "colours": 3, "k": 3, "colouring": w33 + "1111"},
        "the W(3,3) > 26 colouring followed by a run 27..30 of one colour",
    )

    good = {"n": 8, "colours": 2, "k": 3, "colouring": "00110011"}
    emit(t, "malformed", "length_mismatch", dict(good, n=9), "n = 9 but 8 colours")
    emit(
        t,
        "malformed",
        "digit_out_of_range",
        dict(good, colouring="00110012"),
        "digit 2 with 2 colours",
    )
    emit(t, "malformed", "letters", dict(good, colouring="aabbaabb"), "colours must be digits")
    emit(t, "malformed", "eleven_colours", dict(good, colours=11), "at most 10 colours")
    emit(
        t,
        "malformed",
        "float_k",
        None,
        "",
        raw='{"n": 8, "colours": 2, "k": 3.0, "colouring": "00110011"}\n',
    )
    emit(
        t,
        "malformed",
        "negative_zero",
        None,
        "",
        raw='{"n": 8, "colours": 2, "k": 3, "colouring": "00110011", "comment": "x", "extra": -0}\n',
    )
    emit(
        t,
        "malformed",
        "duplicate_key",
        None,
        "",
        raw='{"n": 8, "n": 8, "colours": 2, "k": 3, "colouring": "00110011"}\n',
    )
    emit(
        t,
        "malformed",
        "not_json",
        None,
        "",
        raw='{"n": 8, "colours": 2, "k": 3, "colouring": "00110011"\n',
    )


# =============================================================================================
# linear_code
# =============================================================================================


class GF:
    def __init__(self, p: int, modulus: list[int]):
        self.p, self.m = p, len(modulus) - 1
        self.q = p**self.m
        self.mod = modulus

    def digits(self, a):
        return [(a // self.p**i) % self.p for i in range(self.m)]

    def enc(self, d):
        return sum(c * self.p**i for i, c in enumerate(d))

    def add(self, a, b):
        return self.enc(
            [(x + y) % self.p for x, y in zip(self.digits(a), self.digits(b), strict=True)]
        )

    def mul(self, a, b):
        da, db = self.digits(a), self.digits(b)
        prod = [0] * (2 * self.m)
        for i, x in enumerate(da):
            for j, y in enumerate(db):
                prod[i + j] += x * y
        for top in range(2 * self.m - 1, self.m - 1, -1):
            c = prod[top] % self.p
            if c:
                for i in range(self.m + 1):
                    prod[top - self.m + i] -= c * self.mod[i]
        return self.enc([x % self.p for x in prod[: self.m]])

    def power(self, a, e):
        r = 1
        for _ in range(e):
            r = self.mul(r, a)
        return r


def cyclic_generator(g: list[int], n: int, p: int) -> list[list[int]]:
    """rows x^i g(x), i < n - deg g (coefficients low -> high, mod p)."""
    k = n - (len(g) - 1)
    return [[(g[j - i] % p) if 0 <= j - i < len(g) else 0 for j in range(n)] for i in range(k)]


def linear_codes() -> None:
    t = "linear_code"
    hamming = [
        [1, 0, 0, 0, 0, 1, 1],
        [0, 1, 0, 0, 1, 0, 1],
        [0, 0, 1, 0, 1, 1, 0],
        [0, 0, 0, 1, 1, 1, 1],
    ]
    F2 = {"p": 2}
    emit(
        t,
        "pass",
        "hamming_7_4_3",
        {"field": F2, "n": 7, "k": 4, "generator_matrix": hamming, "minimum_distance": 3},
        "binary Hamming code",
    )
    golay23 = cyclic_generator(
        [1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1], 23, 2
    )  # x^11+x^9+x^7+x^6+x^5+x+1
    golay24 = [row + [sum(row) % 2] for row in golay23]
    emit(
        t,
        "pass",
        "golay_24_12_8",
        {"field": F2, "n": 24, "k": 12, "generator_matrix": golay24, "minimum_distance": 8},
        "extended binary Golay code (4096 codewords)",
    )
    tern = cyclic_generator([2, 0, 1, 2, 1, 1], 11, 3)  # x^5 + x^4 - x^3 + x^2 - 1
    emit(
        t,
        "pass",
        "ternary_golay_11_6_5",
        {"field": {"p": 3}, "n": 11, "k": 6, "generator_matrix": tern, "minimum_distance": 5},
        "ternary Golay code",
    )
    emit(
        t,
        "pass",
        "tetracode_4_2_3",
        {
            "field": {"p": 3},
            "n": 4,
            "k": 2,
            "generator_matrix": [[1, 0, 1, 1], [0, 1, 1, 2]],
            "minimum_distance": 3,
        },
        "ternary tetracode",
    )
    w = 2  # GF(4) = F2[x]/(x^2+x+1): 0, 1, w = x (2), w^2 = x+1 (3)
    hexa = [[1, 0, 0, 1, w, w], [0, 1, 0, w, 1, w], [0, 0, 1, w, w, 1]]
    F4 = {"p": 2, "m": 2, "modulus": [1, 1, 1]}
    emit(
        t,
        "pass",
        "hexacode_6_3_4",
        {"field": F4, "n": 6, "k": 3, "generator_matrix": hexa, "minimum_distance": 4},
        "hexacode over GF(4)",
    )
    gf8 = GF(2, [1, 1, 0, 1])
    pts8 = [gf8.power(2, i) for i in range(7)]  # the nonzero elements x^i
    rs8 = [[gf8.power(a, e) for a in pts8] for e in range(3)]
    F8 = {"p": 2, "m": 3, "modulus": [1, 1, 0, 1]}
    emit(
        t,
        "pass",
        "reed_solomon_gf8_7_3_5",
        {"field": F8, "n": 7, "k": 3, "generator_matrix": rs8, "minimum_distance": 5},
        "Reed-Solomon code over GF(8): evaluations of polynomials of degree < 3 at the 7 nonzero elements",
    )
    gf9 = GF(3, [2, 1, 1])
    rs9 = [[gf9.power(a, e) for a in range(1, 9)] for e in range(2)]
    F9 = {"p": 3, "m": 2, "modulus": [2, 1, 1]}
    emit(
        t,
        "pass",
        "reed_solomon_gf9_8_2_7",
        {"field": F9, "n": 8, "k": 2, "generator_matrix": rs9, "minimum_distance": 7},
        "Reed-Solomon code over GF(9) = F3[x]/(x^2+x+2)",
    )
    emit(
        t,
        "pass",
        "repetition_gf5",
        {
            "field": {"p": 5},
            "n": 5,
            "k": 1,
            "generator_matrix": [[1, 2, 3, 4, 1]],
            "minimum_distance": 5,
        },
        "a single row without zero entries over GF(5)",
    )
    emit(
        t,
        "pass",
        "gf9_is_not_integers_mod_9",
        {"field": F9, "n": 2, "k": 1, "generator_matrix": [[1, 3]], "minimum_distance": 2},
        "3 encodes x in GF(9), so 3*[1,3] = [x, x^2] has weight 2 (integers mod 9 would give [3, 0])",
    )

    emit(
        t,
        "fail",
        "hamming_claims_4",
        {"field": F2, "n": 7, "k": 4, "generator_matrix": hamming, "minimum_distance": 4},
        "claimed distance too large",
    )
    emit(
        t,
        "fail",
        "hamming_claims_2",
        {"field": F2, "n": 7, "k": 4, "generator_matrix": hamming, "minimum_distance": 2},
        "claimed distance too small: the distance must be exact",
    )
    emit(
        t,
        "fail",
        "rank_deficient",
        {
            "field": F2,
            "n": 7,
            "k": 4,
            "generator_matrix": hamming[:3] + [[1, 1, 0, 0, 1, 1, 0]],
            "minimum_distance": 3,
        },
        "row 4 = row 1 + row 2, so the rank is 3",
    )
    bad_golay = [list(r) for r in golay24]
    bad_golay[5][17] ^= 1
    emit(
        t,
        "fail",
        "golay_one_bit_flipped",
        {"field": F2, "n": 24, "k": 12, "generator_matrix": bad_golay, "minimum_distance": 8},
        "extended Golay generator with one entry changed",
    )
    rs9_bad = [list(r) for r in rs9]
    rs9_bad[1][0] = rs9_bad[1][1]
    emit(
        t,
        "fail",
        "reed_solomon_gf9_repeated_point",
        {"field": F9, "n": 8, "k": 2, "generator_matrix": rs9_bad, "minimum_distance": 7},
        "two evaluation columns coincide, so a codeword of weight 6 exists",
    )
    emit(
        t,
        "fail",
        "reed_solomon_gf9_claims_8",
        {"field": F9, "n": 8, "k": 2, "generator_matrix": rs9, "minimum_distance": 8},
        "the [8,2] Reed-Solomon code has distance 7, not 8",
    )

    good = {"field": F2, "n": 7, "k": 4, "generator_matrix": hamming, "minimum_distance": 3}
    emit(
        t,
        "malformed",
        "reducible_modulus",
        dict(
            good,
            field={"p": 3, "m": 2, "modulus": [2, 0, 1]},
            generator_matrix=[[1, 2, 3, 4, 5, 6, 7]] * 4,
        ),
        "x^2 + 2 = (x - 1)(x + 1) over GF(3) is reducible",
    )
    emit(
        t,
        "malformed",
        "not_monic",
        dict(good, field={"p": 3, "m": 2, "modulus": [2, 1, 2]}),
        "modulus must be monic",
    )
    emit(t, "malformed", "p_not_prime", dict(good, field={"p": 4}), "p = 4 is not prime")
    emit(
        t,
        "malformed",
        "modulus_for_prime_field",
        dict(good, field={"p": 2, "modulus": [1, 1]}),
        "m = 1 takes no modulus",
    )
    emit(
        t,
        "malformed",
        "entry_out_of_field",
        dict(good, generator_matrix=hamming[:3] + [[0, 0, 0, 1, 1, 1, 2]]),
        "entry 2 over GF(2)",
    )
    emit(
        t,
        "malformed",
        "row_length",
        dict(good, generator_matrix=hamming[:3] + [[0, 0, 0, 1, 1, 1]]),
        "a row of length 6",
    )
    emit(t, "malformed", "k_rows_mismatch", dict(good, k=5), "k = 5 but 4 rows")
    emit(t, "malformed", "distance_zero", dict(good, minimum_distance=0), "distance must be >= 1")
    emit(
        t,
        "malformed",
        "enumeration_too_large",
        dict(good, n=40, k=36, generator_matrix=[[1] * 40] * 36),
        "2^36 * 40 exceeds the limit",
    )


# =============================================================================================
# covering_code
# =============================================================================================


def span_gf2(rows: list[list[int]]) -> list[str]:
    words = set()
    for coeffs in itertools.product([0, 1], repeat=len(rows)):
        words.add(
            "".join(
                str(sum(c * r[j] for c, r in zip(coeffs, rows, strict=True)) % 2)
                for j in range(len(rows[0]))
            )
        )
    return sorted(words)


def covering() -> None:
    t = "covering_code"
    hamming = [
        [1, 0, 0, 0, 0, 1, 1],
        [0, 1, 0, 0, 1, 0, 1],
        [0, 0, 1, 0, 1, 1, 0],
        [0, 0, 0, 1, 1, 1, 1],
    ]
    ham = span_gf2(hamming)
    emit(
        t,
        "pass",
        "hamming7_R1",
        {"n": 7, "radius": 1, "size": 16, "codewords": ham},
        "perfect Hamming code: K(7,1) <= 16",
    )
    emit(
        t,
        "pass",
        "repetition3_R1",
        {"n": 3, "radius": 1, "codewords": ["000", "111"]},
        "K(3,1) = 2",
    )
    golay23 = cyclic_generator([1, 1, 0, 0, 0, 1, 1, 1, 0, 1, 0, 1], 23, 2)
    emit(
        t,
        "pass",
        "golay23_R3",
        {"n": 23, "radius": 3, "size": 4096, "codewords": span_gf2(golay23)},
        "perfect binary Golay code: covering radius 3",
    )
    emit(
        t,
        "pass",
        "radius_n_single_word",
        {"n": 6, "radius": 6, "codewords": ["010101"]},
        "one word covers F_2^6 at radius 6",
    )
    emit(
        t,
        "pass",
        "radius0_full_space",
        {"n": 3, "radius": 0, "codewords": ["".join(b) for b in itertools.product("01", repeat=3)]},
        "radius 0 requires every word",
    )
    balls = [sum(1 << (w ^ e) for e in (0, 1, 2, 4, 8, 16)) for w in range(32)]
    k51 = next(
        c
        for c in itertools.combinations(range(1, 32), 6)
        if (
            balls[0]
            | balls[c[0]]
            | balls[c[1]]
            | balls[c[2]]
            | balls[c[3]]
            | balls[c[4]]
            | balls[c[5]]
        )
        == (1 << 32) - 1
    )
    k51 = [format(w, "05b")[::-1] for w in (0,) + k51]
    emit(
        t,
        "pass",
        "K51_seven_words",
        {"n": 5, "radius": 1, "codewords": k51},
        "seven words covering F_2^5 at radius 1 (found by exhaustive search; K(5,1) = 7)",
    )

    emit(
        t,
        "fail",
        "hamming_minus_one_word",
        {"n": 7, "radius": 1, "codewords": ham[1:]},
        "removing a codeword of a perfect code leaves 8 words uncovered",
    )
    emit(
        t,
        "fail",
        "duplicate_codeword",
        {"n": 7, "radius": 1, "codewords": ham + [ham[3]]},
        "a codeword listed twice",
    )
    emit(
        t,
        "fail",
        "wrong_size_claim",
        {"n": 7, "radius": 1, "size": 15, "codewords": ham},
        "claimed size 15 for 16 codewords",
    )
    emit(
        t,
        "fail",
        "repetition4_R1",
        {"n": 4, "radius": 1, "codewords": ["0000", "1111"]},
        "0011 is at distance 2 from both codewords",
    )
    emit(
        t,
        "fail",
        "golay23_R2",
        {"n": 23, "radius": 2, "codewords": span_gf2(golay23)},
        "the Golay code has covering radius 3, not 2",
    )
    emit(
        t,
        "fail",
        "K51_six_words",
        {"n": 5, "radius": 1, "codewords": k51[:6]},
        "K(5,1) = 7: six words cannot cover",
    )

    good = {"n": 3, "radius": 1, "codewords": ["000", "111"]}
    emit(t, "malformed", "word_length", dict(good, codewords=["000", "11"]), "a word of length 2")
    emit(t, "malformed", "bad_character", dict(good, codewords=["000", "121"]), "character 2")
    emit(
        t,
        "malformed",
        "integers_not_strings",
        dict(good, codewords=[0, 7]),
        "codewords are strings",
    )
    emit(t, "malformed", "empty_code", dict(good, codewords=[]), "at least one codeword")
    emit(t, "malformed", "n_too_large", dict(good, n=29, codewords=["0" * 29]), "n <= 28")
    emit(t, "malformed", "radius_above_n", dict(good, radius=4), "radius <= n")


# =============================================================================================
# regular_graph_girth
# =============================================================================================


def petersen():
    return (
        [[i, (i + 1) % 5] for i in range(5)]
        + [[i, i + 5] for i in range(5)]
        + [[5 + i, 5 + (i + 2) % 5] for i in range(5)]
    )


def heawood():
    lines = [{(i + d) % 7 for d in (0, 1, 3)} for i in range(7)]
    return [[p, 7 + li] for li, line in enumerate(lines) for p in sorted(line)]


def tutte_coxeter():
    duads = list(itertools.combinations(range(6), 2))
    synthemes = []
    for a in itertools.combinations(duads, 3):
        if len(set(itertools.chain(*a))) == 6:
            synthemes.append(a)
    return [[duads.index(d), 15 + si] for si, s in enumerate(synthemes) for d in s]


def hoffman_singleton():
    P = lambda h, j: 5 * h + j  # noqa: E731
    Q = lambda i, k: 25 + 5 * i + k  # noqa: E731
    edges = []
    for h in range(5):
        for j in range(5):
            edges.append([P(h, j), P(h, (j + 1) % 5)])
            edges.append([Q(h, j), Q(h, (j + 2) % 5)])
    for h, i, k in itertools.product(range(5), repeat=3):
        edges.append([P(h, (h * i + k) % 5), Q(i, k)])
    return edges


def girth() -> None:
    t = "regular_graph_girth"
    emit(
        t,
        "pass",
        "petersen",
        {"n": 10, "degree": 3, "girth": 5, "edges": petersen()},
        "Petersen graph, the (3,5)-cage",
    )
    emit(
        t,
        "pass",
        "heawood",
        {"n": 14, "degree": 3, "girth": 6, "edges": heawood()},
        "Heawood graph (Fano incidence graph), the (3,6)-cage",
    )
    emit(
        t,
        "pass",
        "tutte_coxeter",
        {"n": 30, "degree": 3, "girth": 8, "edges": tutte_coxeter()},
        "Tutte-Coxeter graph (duads/synthemes), the (3,8)-cage",
    )
    emit(
        t,
        "pass",
        "hoffman_singleton",
        {"n": 50, "degree": 7, "girth": 5, "edges": hoffman_singleton()},
        "Hoffman-Singleton graph, the (7,5)-cage",
    )
    emit(
        t,
        "pass",
        "K4",
        {
            "n": 4,
            "degree": 3,
            "girth": 3,
            "edges": [list(e) for e in itertools.combinations(range(4), 2)],
        },
        "complete graph K4",
    )
    emit(
        t,
        "pass",
        "K33",
        {"n": 6, "degree": 3, "girth": 4, "edges": [[a, b] for a in range(3) for b in range(3, 6)]},
        "K_{3,3}",
    )
    emit(
        t,
        "pass",
        "cycle7",
        {"n": 7, "degree": 2, "girth": 7, "edges": [[i, (i + 1) % 7] for i in range(7)]},
        "the 7-cycle",
    )

    P = petersen()
    emit(
        t,
        "fail",
        "petersen_girth_claim_6",
        {"n": 10, "degree": 3, "girth": 6, "edges": P},
        "girth is 5, not 6",
    )
    emit(
        t,
        "fail",
        "petersen_girth_claim_4",
        {"n": 10, "degree": 3, "girth": 4, "edges": P},
        "girth is 5, not 4 (exact girth required)",
    )
    emit(
        t,
        "fail",
        "petersen_missing_edge",
        {"n": 10, "degree": 3, "girth": 5, "edges": P[:-1]},
        "one edge removed: two vertices of degree 2",
    )
    emit(
        t,
        "fail",
        "petersen_multi_edge",
        {"n": 10, "degree": 3, "girth": 5, "edges": P + [[P[3][1], P[3][0]]]},
        "an edge repeated in reverse orientation",
    )
    emit(
        t,
        "fail",
        "loop",
        {"n": 3, "degree": 2, "girth": 3, "edges": [[0, 1], [1, 2], [2, 2]]},
        "a loop is not allowed",
    )
    emit(
        t,
        "fail",
        "petersen_plus_K4",
        {
            "n": 14,
            "degree": 3,
            "girth": 5,
            "edges": P + [[a + 10, b + 10] for a, b in itertools.combinations(range(4), 2)],
        },
        "disjoint union with K4: the girth is the global minimum 3",
    )
    emit(
        t,
        "fail",
        "matching_acyclic",
        {"n": 4, "degree": 1, "girth": 3, "edges": [[0, 1], [2, 3]]},
        "a perfect matching has no cycle",
    )
    emit(
        t,
        "fail",
        "wrong_degree",
        {"n": 7, "degree": 3, "girth": 7, "edges": [[i, (i + 1) % 7] for i in range(7)]},
        "2-regular, claimed 3",
    )
    hs = hoffman_singleton()
    hs_bad = hs[:-1] + [[hs[-1][0], hs[-2][1]]]
    emit(
        t,
        "fail",
        "hoffman_singleton_rewired",
        {"n": 50, "degree": 7, "girth": 5, "edges": hs_bad},
        "one edge moved to another endpoint",
    )

    good = {
        "n": 4,
        "degree": 3,
        "girth": 3,
        "edges": [list(e) for e in itertools.combinations(range(4), 2)],
    }
    emit(
        t,
        "malformed",
        "endpoint_out_of_range",
        dict(good, edges=good["edges"] + [[0, 4]]),
        "vertex 4 with n = 4",
    )
    emit(t, "malformed", "triple_edge", dict(good, edges=[[0, 1, 2]]), "an edge must be a pair")
    emit(t, "malformed", "girth_two", dict(good, girth=2), "girth >= 3")
    emit(t, "malformed", "n_zero", dict(good, n=0, edges=[]), "n >= 1")
    emit(t, "malformed", "negative_vertex", dict(good, edges=[[-1, 0]]), "negative vertex")


# =============================================================================================
# hadamard_matrix
# =============================================================================================


def sylvester(order: int) -> list[list[int]]:
    H = [[1]]
    while len(H) < order:
        H = [r + r for r in H] + [r + [-x for x in r] for r in H]
    return H


def legendre(q: int):
    qr = {(x * x) % q for x in range(1, q)}
    return lambda x: 0 if x % q == 0 else (1 if x % q in qr else -1)


def paley1(q: int) -> list[list[int]]:
    chi = legendre(q)
    S = [[0] + [1] * q] + [[-1] + [chi(j - i) for j in range(q)] for i in range(q)]
    return [[S[i][j] + (1 if i == j else 0) for j in range(q + 1)] for i in range(q + 1)]


def paley2(q: int) -> list[list[int]]:
    """symmetric Paley II matrix of order 2(q+1) for a prime q = 1 mod 4."""
    chi = legendre(q)
    C = [[0] + [1] * q] + [[1] + [chi(j - i) for j in range(q)] for i in range(q)]
    out = []
    for i in range(q + 1):
        for a in range(2):
            row = []
            for j in range(q + 1):
                c = C[i][j]
                block = [[1, -1], [-1, -1]] if c == 0 else [[c, c], [c, -c]]
                row += block[a]
            out.append(row)
    return out


def rows_of(H) -> list[str]:
    return ["".join("+" if x > 0 else "-" for x in r) for r in H]


def hadamard() -> None:
    t = "hadamard_matrix"
    for order in (1, 2, 4, 16):
        emit(
            t,
            "pass",
            f"sylvester_{order}",
            {"n": order, "symmetric": True, "rows": rows_of(sylvester(order))},
            f"Sylvester matrix of order {order} (symmetric)",
        )
    emit(t, "pass", "paley1_12", {"n": 12, "rows": rows_of(paley1(11))}, "Paley I, q = 11")
    emit(
        t,
        "pass",
        "paley1_20",
        {"n": 20, "symmetric": False, "rows": rows_of(paley1(19))},
        "Paley I, q = 19 (not symmetric, not claimed)",
    )
    emit(
        t,
        "pass",
        "paley2_12_symmetric",
        {"n": 12, "symmetric": True, "rows": rows_of(paley2(5))},
        "Paley II, q = 5: symmetric",
    )
    emit(
        t,
        "pass",
        "paley2_28_symmetric",
        {"n": 28, "symmetric": True, "rows": rows_of(paley2(13))},
        "Paley II, q = 13: symmetric",
    )

    H = sylvester(8)
    H[3][5] = -H[3][5]
    emit(t, "fail", "sylvester8_one_entry", {"n": 8, "rows": rows_of(H)}, "one entry negated")
    H = paley1(11)
    H[7] = list(H[2])
    emit(t, "fail", "paley12_repeated_row", {"n": 12, "rows": rows_of(H)}, "row 7 equals row 2")
    emit(
        t,
        "fail",
        "paley1_claimed_symmetric",
        {"n": 12, "symmetric": True, "rows": rows_of(paley1(11))},
        "Hadamard but not symmetric, symmetry claimed",
    )
    emit(
        t,
        "fail",
        "order6_impossible",
        {
            "n": 6,
            "rows": rows_of(
                [
                    [1, 1, 1, 1, 1, 1],
                    [1, -1, 1, -1, 1, -1],
                    [1, 1, -1, -1, 1, 1],
                    [1, -1, -1, 1, 1, -1],
                    [1, 1, 1, 1, -1, -1],
                    [1, -1, 1, -1, -1, 1],
                ]
            ),
        },
        "no Hadamard matrix of order 6 exists",
    )
    emit(t, "fail", "order3_odd", {"n": 3, "rows": ["++-", "+-+", "-++"]}, "odd order > 2")

    good = {"n": 2, "rows": ["++", "+-"]}
    emit(t, "malformed", "row_too_long", dict(good, rows=["++", "+-+"]), "row of length 3")
    emit(
        t,
        "malformed",
        "digits_instead_of_signs",
        dict(good, rows=["11", "1-"]),
        "only + and - are allowed",
    )
    emit(t, "malformed", "missing_row", dict(good, rows=["++"]), "n rows required")
    emit(
        t,
        "malformed",
        "symmetric_not_bool",
        dict(good, symmetric="yes"),
        "symmetric must be a boolean",
    )
    emit(t, "malformed", "integer_rows", dict(good, rows=[[1, 1], [1, -1]]), "rows are strings")


# =============================================================================================
# circulant_weighing_matrix
# =============================================================================================


def paf_ok(a: list[int]) -> bool:
    n = len(a)
    return all(sum(a[i] * a[(i + s) % n] for i in range(n)) == 0 for s in range(1, n))


def search_cw(n: int, zeros: set[int]) -> list[int] | None:
    support = [i for i in range(n) if i not in zeros]
    for signs in itertools.product([1, -1], repeat=len(support) - 1):
        a = [0] * n
        a[support[0]] = 1
        for i, s in zip(support[1:], signs, strict=True):
            a[i] = s
        if paf_ok(a):
            return a
    return None


def cw() -> None:
    t = "circulant_weighing_matrix"
    cw7 = [-1, 1, 1, 0, 1, 0, 0]
    emit(
        t,
        "pass",
        "cw_7_4",
        {"n": 7, "weight": 4, "first_row": cw7, "proper": True},
        "CW(7,4) from the Fano difference set",
    )
    cw13 = None
    for shift in range(13):
        cw13 = search_cw(13, {(d + shift) % 13 for d in (0, 1, 3, 9)})
        if cw13:
            break
    assert cw13
    emit(
        t,
        "pass",
        "cw_13_9",
        {"n": 13, "weight": 9, "first_row": cw13, "proper": True},
        "CW(13,9): zeros on a (13,4,1) difference set",
    )
    emit(
        t,
        "pass",
        "cw_4_4_circulant_hadamard",
        {"n": 4, "weight": 4, "first_row": [1, 1, 1, -1]},
        "the circulant Hadamard matrix of order 4",
    )
    emit(
        t,
        "pass",
        "cw_5_1_trivial",
        {"n": 5, "weight": 1, "first_row": [0, 0, -1, 0, 0]},
        "weight 1 is a CW (improper, properness not claimed)",
    )
    cw14 = [x for v in cw7 for x in (v, 0)]
    emit(
        t,
        "pass",
        "cw_14_4_interleaved",
        {"n": 14, "weight": 4, "first_row": cw14, "proper": False},
        "CW(7,4) spread over even positions",
    )

    bad = list(cw7)
    bad[2] = -bad[2]
    emit(
        t,
        "fail",
        "cw_7_4_sign_flipped",
        {"n": 7, "weight": 4, "first_row": bad},
        "one sign changed",
    )
    emit(
        t,
        "fail",
        "cw_7_4_wrong_weight",
        {"n": 7, "weight": 3, "first_row": cw7},
        "claimed weight 3, actual weight 4",
    )
    emit(
        t,
        "fail",
        "cw_14_4_claimed_proper",
        {"n": 14, "weight": 4, "first_row": cw14, "proper": True},
        "support inside the even residues: improper",
    )
    emit(
        t,
        "fail",
        "cw_5_1_claimed_proper",
        {"n": 5, "weight": 1, "first_row": [0, 0, -1, 0, 0], "proper": True},
        "a single nonzero entry is improper",
    )
    emit(t, "fail", "all_ones", {"n": 4, "weight": 4, "first_row": [1, 1, 1, 1]}, "PAF(1) = 4")
    bad13 = list(cw13)
    i = bad13.index(0)
    j = next(k for k in range(13) if bad13[k] != 0)
    bad13[i], bad13[j] = bad13[j], 0
    emit(
        t,
        "fail",
        "cw_13_9_moved_entry",
        {"n": 13, "weight": 9, "first_row": bad13},
        "one nonzero entry moved to a zero position",
    )

    good = {"n": 7, "weight": 4, "first_row": cw7}
    emit(
        t,
        "malformed",
        "entry_two",
        dict(good, first_row=[-1, 2, 1, 0, 1, 0, 0]),
        "entries must be -1, 0, 1",
    )
    emit(t, "malformed", "length_mismatch", dict(good, n=8), "n = 8 with 7 entries")
    emit(t, "malformed", "weight_zero", dict(good, weight=0), "weight >= 1")
    emit(
        t,
        "malformed",
        "string_entries",
        dict(good, first_row="-++0+00"),
        "first_row is an array of integers",
    )


# =============================================================================================
# affine_line_free_set
# =============================================================================================


def affine() -> None:
    t = "affine_line_free_set"
    cap4 = [[0, 0], [0, 1], [1, 0], [1, 1]]
    emit(
        t,
        "pass",
        "cap_F3_2_size4",
        {"p": 3, "dimension": 2, "size": 4, "points": cap4},
        "maximum cap in F_3^2",
    )
    rng = random.Random(4)
    space = list(itertools.product(range(3), repeat=4))
    best = []
    for _ in range(200):
        order = space[:]
        rng.shuffle(order)
        cap = []
        for x in order:
            if all(
                tuple((-a - b) % 3 for a, b in zip(x, y, strict=True)) not in set(map(tuple, cap))
                for y in cap
            ):
                cap.append(x)
        if len(cap) > len(best):
            best = cap
    emit(
        t,
        "pass",
        f"cap_F3_4_size{len(best)}",
        {"p": 3, "dimension": 4, "size": len(best), "points": [list(x) for x in best]},
        "greedy cap set in F_3^4 (third points of all pairs avoided)",
    )
    p = 5
    axes_free = [[x, y] for x in range(1, p) for y in range(1, p)]
    emit(
        t,
        "pass",
        "F5_2_complement_of_axes",
        {"p": 5, "dimension": 2, "points": axes_free},
        "every line of F_5^2 meets the two axes",
    )
    emit(
        t,
        "pass",
        "F2_3_single_point",
        {"p": 2, "dimension": 3, "points": [[1, 0, 1]]},
        "over F_2 every pair is a line; one point is line-free",
    )
    emit(t, "pass", "empty_set", {"p": 7, "dimension": 2, "points": []}, "the empty set")
    cube = [
        [x, y, z]
        for x in range(1, 7)
        for y in range(1, 7)
        for z in range(1, 7)
        if (x + y + z) % 7 != 0
    ]
    emit(
        t,
        "pass",
        "F7_3_cube_minus_plane",
        {"p": 7, "dimension": 3, "points": cube[:]},
        "points with all coordinates nonzero and x+y+z != 0 in F_7^3",
    )

    emit(
        t,
        "fail",
        "cap_plus_point",
        {"p": 3, "dimension": 2, "points": cap4 + [[2, 2]]},
        "(0,0),(1,1),(2,2) is a line",
    )
    emit(
        t,
        "fail",
        "axes_complement_plus_point",
        {"p": 5, "dimension": 2, "points": axes_free + [[0, 1]]},
        "the line y = 1 becomes full",
    )
    emit(
        t,
        "fail",
        "F2_two_points",
        {"p": 2, "dimension": 2, "points": [[0, 0], [1, 1]]},
        "over F_2 any two points form a line",
    )
    emit(
        t,
        "fail",
        "duplicate_point",
        {"p": 3, "dimension": 2, "points": cap4 + [[1, 0]]},
        "a point listed twice",
    )
    emit(
        t,
        "fail",
        "wrong_size_claim",
        {"p": 3, "dimension": 2, "size": 5, "points": cap4},
        "claimed size 5 for 4 points",
    )
    emit(
        t,
        "fail",
        "whole_plane_F3",
        {
            "p": 3,
            "dimension": 2,
            "points": [list(x) for x in itertools.product(range(3), repeat=2)],
        },
        "F_3^2 contains all of its 12 lines",
    )
    emit(
        t,
        "fail",
        "origin_completes_lines_F7",
        {"p": 7, "dimension": 3, "points": cube + [[0, 0, 0]]},
        "adding the origin completes lines such as t(1,2,3), whose other points all lie in the set",
    )

    good = {"p": 3, "dimension": 2, "points": cap4}
    emit(
        t,
        "malformed",
        "coordinate_out_of_range",
        dict(good, points=cap4 + [[3, 0]]),
        "coordinate 3 in F_3",
    )
    emit(t, "malformed", "p_not_prime", dict(good, p=9), "p = 9 is not prime (use a prime field)")
    emit(
        t,
        "malformed",
        "point_dimension",
        dict(good, points=cap4 + [[1, 2, 0]]),
        "a point with 3 coordinates",
    )
    emit(
        t,
        "malformed",
        "space_too_large",
        dict(good, p=1048573, dimension=4, points=[]),
        "p^dimension >= 2^62",
    )


# =============================================================================================
# kissing_configuration
# =============================================================================================


def roots_d(n: int) -> list[list[int]]:
    out = []
    for i, j in itertools.combinations(range(n), 2):
        for si in (1, -1):
            for sj in (1, -1):
                v = [0] * n
                v[i], v[j] = si, sj
                out.append(v)
    return out


def e8_scaled() -> list[list[int]]:
    vs = [[2 * x for x in v] for v in roots_d(8)]
    for signs in itertools.product([1, -1], repeat=8):
        if signs.count(-1) % 2 == 0:
            vs.append(list(signs))
    return vs


def kissing() -> None:
    t = "kissing_configuration"
    emit(
        t,
        "pass",
        "D3_12",
        {"dimension": 3, "squared_norm": 2, "vectors": roots_d(3)},
        "kissing number 12 in dimension 3",
    )
    emit(
        t,
        "pass",
        "D4_24",
        {"dimension": 4, "squared_norm": 2, "vectors": roots_d(4)},
        "kissing number 24 in dimension 4",
    )
    emit(
        t,
        "pass",
        "E8_240",
        {"dimension": 8, "squared_norm": 8, "vectors": e8_scaled()},
        "E8 root system scaled by 2: kissing number 240",
    )
    emit(
        t,
        "pass",
        "touching_exactly_60",
        {"dimension": 3, "vectors": [[1, 1, 0], [1, 0, 1], [0, 1, 1]]},
        "three vectors at exactly 60 degrees",
    )
    emit(
        t,
        "pass",
        "mixed_norms",
        {"dimension": 2, "vectors": [[1, 0], [1, 2], [-1, 1], [0, -3]]},
        "rays of different lengths, all angles >= 60 degrees",
    )
    emit(t, "pass", "single_vector", {"dimension": 5, "vectors": [[0, 0, 7, 0, 0]]}, "one vector")

    D4 = roots_d(4)
    emit(
        t,
        "fail",
        "D4_duplicate",
        {"dimension": 4, "vectors": D4 + [D4[5]]},
        "a vector listed twice",
    )
    emit(
        t,
        "fail",
        "D4_plus_unit_vector",
        {"dimension": 4, "vectors": D4 + [[1, 0, 0, 0]]},
        "(1,0,0,0) is at 45 degrees from (1,1,0,0)",
    )
    emit(
        t,
        "fail",
        "D3_plus_one_13",
        {"dimension": 3, "vectors": roots_d(3) + [[1, 1, 1]]},
        "13 > kissing number of dimension 3",
    )
    emit(
        t,
        "fail",
        "zero_vector",
        {"dimension": 2, "vectors": [[1, 0], [0, 0]]},
        "the zero vector has no direction",
    )
    emit(
        t,
        "fail",
        "norm_claim_mismatch",
        {"dimension": 4, "squared_norm": 2, "vectors": D4 + [[2, 0, 0, 0]]},
        "one vector of squared norm 4",
    )
    emit(
        t,
        "fail",
        "just_below_60",
        {"dimension": 2, "vectors": [[1000, 0], [500, 866]]},
        "angle slightly below 60 degrees (cos = 0.5000...)",
    )
    emit(
        t,
        "fail",
        "parallel_rays",
        {"dimension": 2, "vectors": [[1, 2], [3, 6]]},
        "two rays in the same direction",
    )

    good = {"dimension": 2, "vectors": [[1, 0], [0, 1]]}
    emit(
        t,
        "malformed",
        "wrong_length",
        dict(good, vectors=[[1, 0], [0, 1, 0]]),
        "a vector of length 3",
    )
    emit(
        t,
        "malformed",
        "string_coordinate",
        dict(good, vectors=[[1, 0], ["0", 1]]),
        "coordinates are JSON integers",
    )
    emit(
        t,
        "malformed",
        "norm_too_large",
        dict(good, vectors=[[1, 0], [2**31, 2**31]]),
        "squared norm above 2^61",
    )
    emit(t, "malformed", "empty", dict(good, vectors=[]), "at least one vector")
    emit(
        t,
        "malformed",
        "float_coordinate",
        None,
        "",
        raw='{"dimension": 2, "vectors": [[1, 0], [0.5, 1]]}\n',
    )


# =============================================================================================
# heilbronn_configuration
# =============================================================================================


def heilbronn() -> None:
    t = "heilbronn_configuration"
    sq = [["0", "0"], ["1", "0"], ["0", "1"], ["1", "1"]]
    emit(
        t,
        "pass",
        "square_corners",
        {"region": "unit_square", "points": sq, "min_area": "1/2"},
        "the four corners: every triangle has area 1/2",
    )
    tri = [["0", "0"], ["1", "0"], ["0", "1"], ["1/3", "1/3"]]
    emit(
        t,
        "pass",
        "triangle_with_centroid",
        {"region": "unit_triangle", "points": tri, "min_area": "1/6"},
        "vertices and centroid",
    )
    five = [["0", "0"], ["1", "0"], ["1", "1"], ["0", "1"], ["1/2", "1/3"]]
    emit(
        t,
        "pass",
        "square_five_points_weaker_claim",
        {"region": "unit_square", "points": five, "min_area": "1/13"},
        "corners and (1/2, 1/3): minimum area 1/12 >= claimed 1/13",
    )
    reg = {"triangle": [["0", "0"], ["4", "0"], ["0", "3"]]}
    emit(
        t,
        "pass",
        "explicit_triangle",
        {
            "region": reg,
            "points": [["0", "0"], ["4", "0"], ["0", "3"], ["1", "1"]],
            "min_area": "3/2",
        },
        "explicit right triangle (0,0),(4,0),(0,3)",
    )
    emit(
        t,
        "pass",
        "zero_claim",
        {
            "region": "unit_square",
            "points": [["0", "0"], ["1/2", "1/2"], ["1", "1"]],
            "min_area": 0,
        },
        "collinear points with the trivial claim 0",
    )
    emit(
        t,
        "pass",
        "boundary_points",
        {
            "region": "unit_triangle",
            "points": [["1/2", "1/2"], ["0", "1/2"], ["1/2", "0"]],
            "min_area": "1/8",
        },
        "points on the boundary belong to the closed region",
    )

    emit(
        t,
        "fail",
        "point_outside_square",
        {
            "region": "unit_square",
            "points": sq[:3]
            + [["1", "1000000000000000000000000000001/1000000000000000000000000000000"]],
            "min_area": "1/3",
        },
        "y = 1 + 10^-30",
    )
    emit(
        t,
        "fail",
        "point_outside_triangle",
        {
            "region": "unit_triangle",
            "points": [["0", "0"], ["1", "0"], ["0", "1"], ["1/2", "51/100"]],
            "min_area": "1/1000",
        },
        "x + y = 101/100 > 1",
    )
    emit(
        t,
        "fail",
        "collinear",
        {
            "region": "unit_square",
            "points": [["0", "0"], ["1/2", "1/2"], ["1", "1"], ["1", "0"]],
            "min_area": "1/100",
        },
        "three collinear points",
    )
    emit(
        t,
        "fail",
        "claim_slightly_too_large",
        {
            "region": "unit_triangle",
            "points": tri,
            "min_area": "100000000000000000000000000000000000000000000000001/600000000000000000000000000000000000000000000000000",
        },
        "claimed 1/6 + 10^-50 / 6",
    )
    emit(
        t,
        "fail",
        "duplicate_points",
        {"region": "unit_square", "points": sq + [["1", "1"]], "min_area": "1/2"},
        "a repeated point gives area 0",
    )
    emit(
        t,
        "fail",
        "outside_explicit_triangle",
        {
            "region": reg,
            "points": [["0", "0"], ["4", "0"], ["0", "3"], ["3", "1"]],
            "min_area": "1/2",
        },
        "(3,1) is outside the triangle (0,0),(4,0),(0,3)",
    )

    good = {"region": "unit_square", "points": sq, "min_area": "1/2"}
    emit(t, "malformed", "unknown_region", dict(good, region="unit_disc"), "unsupported region")
    emit(
        t,
        "malformed",
        "degenerate_triangle",
        dict(good, region={"triangle": [["0", "0"], ["1", "1"], ["2", "2"]]}),
        "zero-area region",
    )
    emit(t, "malformed", "two_points", dict(good, points=sq[:2]), "at least 3 points")
    emit(
        t,
        "malformed",
        "decimal_coordinate",
        dict(good, points=sq[:3] + [["0.5", "1"]]),
        "decimals are not rationals in this format",
    )
    emit(
        t,
        "malformed",
        "zero_denominator",
        dict(good, points=sq[:3] + [["1/0", "1"]]),
        "zero denominator",
    )
    emit(
        t,
        "malformed",
        "negative_claim",
        dict(good, min_area="-1/2"),
        "min_area must be nonnegative",
    )
    emit(
        t,
        "malformed",
        "float_point",
        None,
        "",
        raw='{"region": "unit_square", "points": [[0, 0], [1, 0], [0.5, 1]], "min_area": "1/4"}\n',
    )


# =============================================================================================
# polynomial_positive_interval
# =============================================================================================


def poly_from_roots(roots, lead=1) -> list[Fraction]:
    c = [Fraction(lead)]
    for r in roots:
        r = Fraction(r)
        c = [-r * c[0]] + [c[i - 1] - r * c[i] for i in range(1, len(c))] + [c[-1]]
    return c


def chebyshev(n: int) -> list[int]:
    T0, T1 = [1], [0, 1]
    for _ in range(n - 1):
        T0, T1 = (
            T1,
            [
                (-T0[i] if i < len(T0) else 0) + (2 * T1[i - 1] if i >= 1 else 0)
                for i in range(len(T1) + 1)
            ],
        )
    return T1


def polynomials() -> None:
    t = "polynomial_positive_interval"
    S = lambda c: [fstr(x) for x in c]  # noqa: E731
    emit(
        t,
        "pass",
        "x2_plus_1_everywhere",
        {"coefficients": ["1", "0", "1"], "interval": ["-inf", "inf"]},
        "x^2 + 1 > 0 on R",
    )
    emit(
        t,
        "pass",
        "touching_double_root",
        {
            "coefficients": S(poly_from_roots(["1/2", "1/2"])),
            "interval": ["0", "1"],
            "strict": False,
        },
        "(x - 1/2)^2 >= 0 on [0, 1]",
    )
    cheb = chebyshev(20)
    emit(
        t,
        "pass",
        "chebyshev20_plus_1_nonstrict",
        {
            "coefficients": [str(cheb[0] + 1)] + [str(x) for x in cheb[1:]],
            "interval": ["-1", "1"],
            "strict": False,
        },
        "T_20(x) + 1 >= 0 on [-1, 1] with 10 double roots",
    )
    emit(
        t,
        "pass",
        "x2_minus_2_tight",
        {"coefficients": ["-2", "0", "1"], "interval": ["1415/1000", "2"]},
        "sqrt(2) = 1.41421... < 1.415",
    )
    emit(
        t,
        "pass",
        "constant",
        {"coefficients": ["5/3"], "interval": ["-inf", "inf"]},
        "a positive constant",
    )
    wilk = poly_from_roots(range(1, 11))
    emit(
        t,
        "pass",
        "wilkinson10_beyond_roots",
        {"coefficients": S(wilk), "interval": ["10001/1000", "inf"]},
        "(x-1)...(x-10) > 0 for x > 10",
    )
    emit(
        t,
        "pass",
        "wilkinson10_between_roots",
        {"coefficients": S(wilk), "interval": ["21/10", "29/10"], "strict": True},
        "between the roots 2 and 3 the sign is (-1)^8 = +",
    )
    close = poly_from_roots(["1/3", Fraction(1, 3) + Fraction(1, 10**30)])
    emit(
        t,
        "pass",
        "close_roots_outside",
        {"coefficients": S(close), "interval": ["34/100", "1"]},
        "two roots 10^-30 apart just left of the interval",
    )
    emit(
        t,
        "pass",
        "left_infinite",
        {"coefficients": ["1", "-1"], "interval": ["-inf", "999/1000"]},
        "1 - x > 0 for x <= 0.999",
    )
    emit(
        t,
        "pass",
        "zero_polynomial_nonstrict",
        {"coefficients": ["0", "0"], "interval": ["0", "1"], "strict": False},
        "0 >= 0",
    )
    emit(
        t,
        "pass",
        "single_point_interval",
        {"coefficients": ["-1", "0", "1"], "interval": ["2", "2"]},
        "P(2) = 3 > 0",
    )
    big = [str(10**80 + 7)] + ["0"] * 59 + ["-1"] + ["0"] * 19 + ["1"]
    emit(
        t,
        "pass",
        "big_coefficients_degree80",
        {"coefficients": big, "interval": ["-3/2", "3/2"]},
        "10^80 + 7 - x^60 + x^80 on [-1.5, 1.5]",
    )

    emit(
        t,
        "fail",
        "root_at_endpoint",
        {"coefficients": ["0", "1"], "interval": ["0", "1"]},
        "P(0) = 0, strict positivity fails",
    )
    emit(
        t,
        "fail",
        "double_root_strict",
        {"coefficients": S(poly_from_roots(["1/2", "1/2"])), "interval": ["0", "1"]},
        "(x - 1/2)^2 vanishes at 1/2",
    )
    emit(
        t,
        "fail",
        "chebyshev20_plus_1_strict",
        {"coefficients": [str(cheb[0] + 1)] + [str(x) for x in cheb[1:]], "interval": ["-1", "1"]},
        "T_20 + 1 touches zero",
    )
    emit(
        t,
        "fail",
        "negative_leading_infinite",
        {"coefficients": ["100", "0", "-1"], "interval": ["0", "inf"]},
        "100 - x^2 is eventually negative",
    )
    emit(
        t,
        "fail",
        "sign_change_nonstrict",
        {"coefficients": ["0", "-1", "0", "1"], "interval": ["-2", "2"], "strict": False},
        "x^3 - x changes sign",
    )
    emit(
        t,
        "fail",
        "zero_polynomial_strict",
        {"coefficients": ["0"], "interval": ["0", "1"]},
        "the zero polynomial is not positive",
    )
    emit(
        t,
        "fail",
        "double_root_and_sign_change",
        {"coefficients": S(poly_from_roots([1, 1, 2])), "interval": ["0", "3"], "strict": False},
        "(x-1)^2 (x-2) < 0 on (1, 2)",
    )
    emit(
        t,
        "fail",
        "x2_minus_2_just_inside",
        {"coefficients": ["-2", "0", "1"], "interval": ["1414/1000", "2"]},
        "sqrt(2) = 1.41421... > 1.414",
    )
    emit(
        t,
        "fail",
        "close_roots_inside",
        {"coefficients": S(close), "interval": ["1/4", "1"], "strict": False},
        "negative between two roots 10^-30 apart",
    )
    emit(
        t,
        "fail",
        "wilkinson10_root_at_endpoint",
        {"coefficients": S(wilk), "interval": ["10", "inf"]},
        "P(10) = 0",
    )
    emit(
        t,
        "fail",
        "single_point_negative",
        {"coefficients": ["-1", "0", "1"], "interval": ["1/2", "1/2"], "strict": False},
        "P(1/2) = -3/4",
    )
    emit(
        t,
        "fail",
        "odd_degree_everywhere",
        {"coefficients": ["1", "1", "0", "1"], "interval": ["-inf", "inf"], "strict": False},
        "x^3 + x + 1 -> -inf",
    )

    good = {"coefficients": ["1", "0", "1"], "interval": ["0", "1"]}
    emit(t, "malformed", "reversed_interval", dict(good, interval=["1", "0"]), "a > b")
    emit(
        t,
        "malformed",
        "inf_on_the_left",
        dict(good, interval=["inf", "1"]),
        "the left endpoint may only be -inf",
    )
    emit(
        t,
        "malformed",
        "decimal_coefficient",
        dict(good, coefficients=["1.5", "1"]),
        "decimals are not allowed",
    )
    emit(t, "malformed", "zero_denominator", dict(good, coefficients=["1/0"]), "zero denominator")
    emit(
        t,
        "malformed",
        "empty_coefficients",
        dict(good, coefficients=[]),
        "at least one coefficient",
    )
    emit(
        t, "malformed", "plus_sign", dict(good, coefficients=["+1"]), "a leading '+' is not allowed"
    )
    emit(
        t,
        "malformed",
        "huge_json_integer",
        None,
        "",
        raw='{"coefficients": [123456789012345678901234567890, 1], "interval": ["0", "1"]}\n',
    )
    emit(t, "malformed", "strict_as_string", dict(good, strict="false"), "strict is a boolean")


def main() -> int:
    for gen in (
        graph_ramsey,
        hypergraph,
        vdw,
        linear_codes,
        covering,
        girth,
        hadamard,
        cw,
        affine,
        kissing,
        heilbronn,
        polynomials,
    ):
        gen()
    counts: dict[tuple[str, str], int] = {}
    for p in WRITTEN:
        key = (p.parent.parent.name, p.parent.name)
        counts[key] = counts.get(key, 0) + 1
    for (ctype, verdict), c in sorted(counts.items()):
        print(f"{ctype:32s} {verdict:10s} {c}")
    print("total", len(WRITTEN))
    return 0


if __name__ == "__main__":
    sys.exit(main())
