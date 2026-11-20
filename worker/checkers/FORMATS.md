# ProofXiv reference checkers: certificate formats

This document is the specification shared by the two independent implementations of the platform
reference checkers (design document, section 6.2):

* **Python**: package `proofxiv_worker.checkers` (`worker/src/proofxiv_worker/checkers/`);
* **Rust**: crate `worker/checkers-rs`, binary `proofxiv-check`.

A certificate is accepted only when **both** implementations accept it. Both implementations are
run on the shared test vectors in `worker/checkers/testdata/` (see the end of this document); they
must return the expected verdict and identical `stats`.

## 1. Interfaces

### Command line (identical for both implementations)

```
proofxiv-check <certificate_type> <file.json>
python -m proofxiv_worker.checkers <certificate_type> <file.json>
```

Both print exactly one JSON line on stdout:

```json
{"ok": true, "type": "hadamard_matrix", "details": "human-readable reason", "stats": {"n": 4, "symmetric": true}}
```

| exit code | meaning | `ok` | `stats` |
|---|---|---|---|
| 0 | the certificate is valid | `true` | type-specific, see below |
| 1 | the certificate is well formed but invalid | `false` | type-specific, see below |
| 2 | malformed input (violates this document), unknown type, unreadable file, wrong number of arguments, internal error | `false` | `{}` |

`details` is free text (a witness of the failure, or a summary). It is **not** compared between the
implementations. `stats` is deterministic and must be identical in both implementations; the test
suite asserts this for every vector with exit code 0 or 1. Key order in `stats` is irrelevant.

### Python API

```python
from proofxiv_worker.checkers import check, CHECKERS, CheckResult, run_both
result = check("vdw_coloring", data)        # data: the parsed JSON object (dict)
result.ok, result.details, result.stats      # CheckResult dataclass
```

`check` raises `UnknownCertificateType` or `MalformedCertificate` (both subclasses of
`CertificateError`, a `ValueError`) where the command line exits with code 2.
`CHECKERS` maps the twelve type names to their checker functions.

`run_both(type, path, rust_binary=None, timeout=None)` runs both command-line implementations
as subprocesses and returns

```
{"implementations": {"python": bool, "rust": bool},   # exit code 0 and "ok": true
 "agree": bool,                                       # equal exit codes and identical stats
 "ok": bool,                                          # agree and both accepted
 "details": "python: exit 0: ... | rust: exit 0: ...",
 "results": {...}, "exit_codes": {...}, "seconds": {...}, "type": ..., "rust_binary": ...}
```

The Rust binary is the explicit argument, else `$PROOFXIV_CHECK_BIN`, else `proofxiv-check` on `$PATH`,
else `worker/checkers-rs/target/{release,debug}/proofxiv-check`. If it cannot be found, `rust` is
`false` and `details` says so. The runner fills `CheckerReport` from this dictionary.

## 2. Common conventions

These rules apply to every certificate type. Any violation is **malformed** (exit code 2).

1. **File.** UTF-8 (no byte order mark), one JSON value, which must be an object.
2. **Fields.** Every type lists its required and optional fields. Unknown fields are rejected
   (a misspelled optional flag must not be silently ignored). Two generic optional fields are
   allowed everywhere: `"type"` (a string that must equal the certificate type given on the
   command line) and `"comment"` (any string, ignored).
3. **Numbers.** JSON numbers must be integer literals in the signed 64-bit range
   [-2^63, 2^63 - 1]. Fractions (`1.0`), exponents (`1e3`), the literal `-0`, `NaN`/`Infinity`
   and larger literals are rejected. Booleans are not integers. Arbitrary-size integers and
   rationals are written as strings (rule 6).
4. **Objects.** Duplicate keys are rejected. Nesting depth (number of nested arrays/objects) is at
   most 64. Strings must be valid Unicode (no unpaired surrogate escapes).
5. **Ranges.** Every integer field has the range given in its type section; values outside the
   range, and instances above the documented size limits, are malformed. The limits exist so that
   both implementations reject exactly the same inputs; they are generous for the intended use.
6. **Rationals.** A rational field is either a JSON integer or a string matching
   `-?[0-9]+` or `-?[0-9]+/[0-9]+` (ASCII digits; no `+`, spaces, decimal points or exponents;
   at most 10 000 digits in the numerator and in the denominator; the denominator must not be 0).
   The value need not be in lowest terms. Rationals in `stats`/`details` are printed in lowest
   terms as `"p"` or `"p/q"` with `q > 0`.
7. **Base64.** Standard alphabet of RFC 4648 (`A-Z a-z 0-9 + /`), `=` padding required, no
   whitespace, and canonical: re-encoding the decoded bytes must give the input string exactly
   (unused bits in the last character must be zero).
8. **Colex order.** The t-subsets `{a_1 < ... < a_t}` of `{0, ..., n-1}` are ranked by
   `rank = C(a_1, 1) + C(a_2, 2) + ... + C(a_t, t)`, which enumerates them in colexicographic order
   (by largest element, then the next largest, ...). For pairs `{i < j}` the rank is
   `j(j-1)/2 + i`, i.e. the order is (0,1), (0,2), (1,2), (0,3), (1,3), (2,3), ...
9. **Exactness.** All decisions use exact integer or rational arithmetic. No floating-point value
   influences a verdict in either implementation.

## 3. Certificate types

Notation: "invalid" means exit code 1 (well formed, but the claimed property fails).

### 3.1 `graph_ramsey_coloring`

An edge colouring of the complete graph K_n with c colours. Claim: R(s_1, ..., s_c) > n.

| field | type | constraint |
|---|---|---|
| `n` | integer | 0 ≤ n ≤ 4096 (vertices 0..n-1) |
| `colours` | integer | 1 ≤ c ≤ 10 |
| `forbidden_clique_sizes` | array of c integers | each s_i ≥ 1 |
| `edge_colours` | string | length n(n-1)/2; character number `j(j-1)/2 + i` is the colour (`"0"`..`"9"`, less than c) of the edge {i, j}, i < j (colex order of pairs) |

**Valid** iff for every colour i there is no set of s_i vertices all of whose pairs have colour i.
(A single vertex is a clique of size 1, so s_i = 1 makes every colouring with n ≥ 1 invalid.)

**stats**: `n`, `colours`, `edges_per_colour` (array: number of edges of each colour).

```json
{"type": "graph_ramsey_coloring", "n": 5, "colours": 2, "forbidden_clique_sizes": [3, 3], "edge_colours": "0101100110"}
```

(the pentagon 0-1-2-3-4-0 in colour 0, the pentagram in colour 1: R(3,3) > 5).

### 3.2 `hypergraph_ramsey_coloring`

A 2-colouring of the k-subsets of {0, ..., n-1}. Claim: R(s_0, s_1; k) > n.

| field | type | constraint |
|---|---|---|
| `n` | integer | 1 ≤ n ≤ 1024 |
| `k` | integer | 2 ≤ k ≤ 8, k ≤ n; C(n, k) ≤ 2^27 |
| `forbidden_sizes` | array of 2 integers | [s_0, s_1], each ≥ 1 |
| `colouring` | base64 string | decodes to exactly ceil(C(n,k)/8) bytes |

The colour of the k-subset with colex rank r (rule 8) is bit r of the decoded byte string, where
bit r is bit `7 - (r mod 8)` (most significant bit first) of byte `floor(r / 8)`. Bit value 0 means
colour 0, value 1 means colour 1. The padding bits after bit C(n,k) - 1 must be zero.

**Valid** iff for j = 0, 1 there is no s_j-subset of {0..n-1} all of whose k-subsets have colour
j. If s_j < k the condition is vacuous for every s_j-subset, so such a certificate is invalid
whenever s_j ≤ n.

**stats**: `n`, `k`, `colour_counts` ([number of k-subsets of colour 0, of colour 1]).

```json
{"type": "hypergraph_ramsey_coloring", "n": 4, "k": 3, "forbidden_sizes": [4, 4], "colouring": "gA=="}
```

(the triple {0,1,2} has rank 0 and colour 1, the other three triples colour 0: R(4,4;3) > 4).

### 3.3 `vdw_coloring`

An r-colouring of {1, ..., n}. Claim: W(r, k) > n.

| field | type | constraint |
|---|---|---|
| `n` | integer | 1 ≤ n ≤ 50 000 000 |
| `colours` | integer | 1 ≤ r ≤ 10 |
| `k` | integer | 1 ≤ k ≤ 50 000 000 |
| `colouring` | string | length n; character number i (0-based) is the colour (`"0"`..`"9"`, less than r) of the integer i + 1 |

**Valid** iff there are no integers a ≥ 1, d ≥ 1 with a + (k-1)d ≤ n such that a, a+d, ...,
a+(k-1)d all have the same colour. (k = 1 is always invalid; k > n is always valid.)

**stats**: `n`, `colours`, `k`, `colour_counts` (array of r counts).

```json
{"type": "vdw_coloring", "n": 8, "colours": 2, "k": 3, "colouring": "00110011"}
```

### 3.4 `linear_code`

A linear [n, k] code over GF(q) given by a generator matrix, with a claimed minimum distance.

| field | type | constraint |
|---|---|---|
| `field` | object | `{"p": p, "m": m, "modulus": [...]}` (see below) |
| `n` | integer | 1 ≤ n ≤ 100 000 |
| `k` | integer | 1 ≤ k ≤ n |
| `generator_matrix` | array of k arrays of n integers | entries are field elements 0 ≤ e < q |
| `minimum_distance` | integer | 1 ≤ d ≤ n |

The field object has exactly the keys `p` (a prime, 2 ≤ p ≤ 1024), optional `m` (1 ≤ m ≤ 10,
default 1) and `modulus`; q = p^m ≤ 1024. For m = 1 the key `modulus` must be absent and GF(q) is
the integers modulo p. For m > 1, `modulus` is required: m + 1 integers in [0, p), the
coefficients of a polynomial f from the constant term upwards; f must be monic (last entry 1) and
irreducible over GF(p), and GF(q) = GF(p)[x]/(f). A field element is encoded by the integer
e = c_0 + c_1 p + ... + c_{m-1} p^(m-1) (0 ≤ c_i < p), representing c_0 + c_1 x + ... + c_{m-1} x^(m-1).
Example: GF(9) = F_3[x]/(x^2 + x + 2) is `{"p": 3, "m": 2, "modulus": [2, 1, 1]}` and the integer
5 = 2 + 1·3 is the element 2 + x.

Size limit: q^k · n ≤ 2^34 (the minimum distance is computed by enumerating all codewords).

**Valid** iff the generator matrix has rank k over GF(q) and the minimum Hamming weight of a
nonzero codeword equals `minimum_distance` exactly (a too small claim is also invalid).

**stats**: `q`, `n`, `k`, `rank`, `minimum_distance` (the true minimum weight of a nonzero
codeword of the row space; `null` if the rank is 0), `weight_distribution` (array of
[w, A_w] for every w ≥ 1 with A_w > 0, where A_w counts the codewords of weight w in the row space).

```json
{"type": "linear_code", "field": {"p": 2}, "n": 7, "k": 4, "minimum_distance": 3,
 "generator_matrix": [[1,0,0,0,0,1,1], [0,1,0,0,1,0,1], [0,0,1,0,1,1,0], [0,0,0,1,1,1,1]]}
```

```json
{"type": "linear_code", "field": {"p": 3, "m": 2, "modulus": [2, 1, 1]}, "n": 2, "k": 1,
 "generator_matrix": [[1, 3]], "minimum_distance": 2}
```

(in GF(9) the integer 3 is the element x, so 3·[1, 3] = [x, x²] has weight 2).

### 3.5 `covering_code`

A binary code of length n with covering radius at most R. Claim: K(n, R) ≤ |C|.

| field | type | constraint |
|---|---|---|
| `n` | integer | 1 ≤ n ≤ 28 |
| `radius` | integer | 0 ≤ R ≤ n |
| `codewords` | non-empty array of strings | each of length n over `"0"`/`"1"` (character i is coordinate i) |
| `size` | integer, optional | ≥ 1 |

**Valid** iff the codewords are pairwise distinct, `size` (if present) equals their number, and
every word of F_2^n is within Hamming distance R of some codeword.

**stats**: `n`, `radius`, `size` (number of distinct codewords), `uncovered` (number of words at
distance > R from the code; `null` if the distinctness or size check already failed).

```json
{"type": "covering_code", "n": 3, "radius": 1, "size": 2, "codewords": ["000", "111"]}
```

### 3.6 `regular_graph_girth`

A simple d-regular graph with girth exactly g.

| field | type | constraint |
|---|---|---|
| `n` | integer | 1 ≤ n ≤ 10 000 000 (vertices 0..n-1) |
| `degree` | integer | ≥ 0 |
| `girth` | integer | ≥ 3 |
| `edges` | array of at most 50 000 000 pairs | each `[u, v]` with integers 0 ≤ u, v < n |

**Valid** iff the graph is simple (no loop `[v, v]`, no edge listed twice in either orientation),
every vertex has degree `degree`, and the girth (length of a shortest cycle) equals `girth`. An
acyclic graph is invalid.

**stats**: `n`, `edges` (number of listed edges), `simple` (bool), `min_degree`, `max_degree`
(computed from the list; a loop counts 2), `girth` (the true girth; `null` if the graph is not
simple or acyclic).

```json
{"type": "regular_graph_girth", "n": 4, "degree": 3, "girth": 3,
 "edges": [[0, 1], [0, 2], [0, 3], [1, 2], [1, 3], [2, 3]]}
```

### 3.7 `hadamard_matrix`

| field | type | constraint |
|---|---|---|
| `n` | integer | 1 ≤ n ≤ 4096 |
| `rows` | array of n strings | each of length n over `"+"` (entry +1) and `"-"` (entry -1) |
| `symmetric` | boolean, optional | default `false` |

**Valid** iff H Hᵀ = n I, and additionally H = Hᵀ when `symmetric` is `true`.

**stats**: `n`, `symmetric` (whether H = Hᵀ holds, whatever was claimed).

```json
{"type": "hadamard_matrix", "n": 4, "symmetric": true, "rows": ["++++", "+-+-", "++--", "+--+"]}
```

### 3.8 `circulant_weighing_matrix`

The circulant matrix W with W[i][j] = a[(j - i) mod n] for a first row a over {-1, 0, 1}.

| field | type | constraint |
|---|---|---|
| `n` | integer | 1 ≤ n ≤ 100 000 |
| `weight` | integer | 1 ≤ k ≤ n |
| `first_row` | array of n integers | each -1, 0 or 1 |
| `proper` | boolean, optional | default `false` |

**Valid** iff a has exactly k nonzero entries and the periodic autocorrelation
PAF(s) = sum_i a_i a_{(i+s) mod n} is 0 for s = 1, ..., n-1 (then W Wᵀ = k I). If `proper` is
`true`, additionally for every divisor e > 1 of n the support of a meets at least two residue
classes modulo e (otherwise W is a blown-up smaller weighing matrix).

**stats**: `n`, `weight` (number of nonzero entries), `plus`, `minus` (numbers of +1 and -1),
`proper` (whether the properness condition holds).

```json
{"type": "circulant_weighing_matrix", "n": 7, "weight": 4, "proper": true, "first_row": [-1, 1, 1, 0, 1, 0, 0]}
```

### 3.9 `affine_line_free_set`

A set S ⊆ F_p^d containing no complete affine line. Claim: |S| is achievable.

| field | type | constraint |
|---|---|---|
| `p` | integer | a prime, 2 ≤ p ≤ 2^20 |
| `dimension` | integer | 1 ≤ d ≤ 64, p^d < 2^62 |
| `points` | array of at most 2^20 points | each an array of d integers in [0, p) |
| `size` | integer, optional | ≥ 0 |

Size limit: min((p^d - 1)/(p - 1), |S|) · |S| ≤ 2^36.

**Valid** iff the points are pairwise distinct, `size` (if present) equals their number, and no
line {a + t v : t ∈ F_p} (v ≠ 0) has all its p points in S. (Over F_2 every pair of points is a
line, so only sets with at most one point are valid; over F_3 the condition says S is a cap set.)

**stats**: `p`, `dimension`, `size` (number of points listed), `full_lines` (number of lines
contained in S; `null` if the distinctness or size check already failed).

```json
{"type": "affine_line_free_set", "p": 3, "dimension": 2, "size": 4, "points": [[0, 0], [0, 1], [1, 0], [1, 1]]}
```

### 3.10 `kissing_configuration`

Nonzero integer vectors whose directions are pairwise at angle ≥ 60°: after normalisation they
form a kissing configuration of `count` unit spheres in dimension d.

| field | type | constraint |
|---|---|---|
| `dimension` | integer | 1 ≤ d ≤ 1024 |
| `vectors` | non-empty array of at most 100 000 vectors | each an array of d JSON integers with squared norm ≤ 2^61 |
| `squared_norm` | integer, optional | 1 ≤ N ≤ 2^61 |

**Valid** iff no vector is zero, every vector has squared norm N when `squared_norm` is present,
and for every pair x ≠ y (by index) with ⟨x, y⟩ > 0 we have 4⟨x, y⟩² ≤ |x|²|y|². With equal norms
N this is ⟨x, y⟩ ≤ N/2. Equal vectors and parallel rays violate the condition. Vectors of
different norms are allowed (the condition is about directions).

**stats**: `dimension`, `count`, `touching_pairs` (pairs with 4⟨x,y⟩² = |x|²|y|², i.e. exactly
60°), `violating_pairs` (pairs violating the condition); both `null` if a zero vector or a wrong
norm was found first.

```json
{"type": "kissing_configuration", "dimension": 3, "squared_norm": 2,
 "vectors": [[1, 1, 0], [1, -1, 0], [-1, 1, 0], [-1, -1, 0], [1, 0, 1], [1, 0, -1],
             [-1, 0, 1], [-1, 0, -1], [0, 1, 1], [0, 1, -1], [0, -1, 1], [0, -1, -1]]}
```

### 3.11 `heilbronn_configuration`

Points in a region whose smallest triangle has at least the claimed area.

| field | type | constraint |
|---|---|---|
| `region` | string or object | `"unit_square"` ([0,1]²), `"unit_triangle"` (vertices (0,0), (1,0), (0,1)), or `{"triangle": [[x1,y1],[x2,y2],[x3,y3]]}` with rational coordinates and nonzero area |
| `points` | array of 3 to 500 points | each `[x, y]` with rational coordinates (rule 6) |
| `min_area` | rational | ≥ 0 |

**Valid** iff every point lies in the closed region (the boundary belongs to it) and every
triangle formed by three of the points (three distinct indices) has area ≥ `min_area`. Repeated
or collinear points give area 0.

**stats**: `points` (number of points), `min_area` (the exact minimum triangle area, `"p/q"`),
`normalized_min_area` (`min_area` divided by the area of the region; for the unit triangle this is
the usual normalised Heilbronn value 2·min_area); both `null` if a point lies outside the region.

```json
{"type": "heilbronn_configuration", "region": "unit_triangle", "min_area": "1/6",
 "points": [["0", "0"], ["1", "0"], ["0", "1"], ["1/3", "1/3"]]}
```

### 3.12 `polynomial_positive_interval`

| field | type | constraint |
|---|---|---|
| `coefficients` | array of 1 to 2001 rationals | c_0 first: P(x) = c_0 + c_1 x + ... + c_m x^m (trailing zeros allowed) |
| `interval` | array of 2 entries | [a, b]: a is a rational or the string `"-inf"`, b is a rational or the string `"inf"`; a ≤ b when both are finite |
| `strict` | boolean, optional | default `true` |

**Valid** iff P(x) > 0 (strict) or P(x) ≥ 0 (non-strict) for every real x with a ≤ x ≤ b
(the interval is closed; infinite endpoints mean "for all x ≥ a", "for all x ≤ b" or all of R).
The zero polynomial is valid exactly when `strict` is `false`.

**stats**: `degree` (of P; `null` for the zero polynomial), `squarefree` (whether
gcd(P, P') is constant), `roots_in_interval` (number of distinct real roots of P in the closed
interval); `squarefree` and `roots_in_interval` are `null` for the zero polynomial.

```json
{"type": "polynomial_positive_interval", "coefficients": ["-2", "0", "1"], "interval": ["1415/1000", "inf"], "strict": true}
```

(x² - 2 > 0 for all x ≥ 1.415, since √2 = 1.41421...).

## 4. Algorithms (for reviewers)

Both implementations follow this document but were written separately and, where practical, use
different methods, so that a bug is unlikely to be shared.

| type | Python | Rust |
|---|---|---|
| graph Ramsey | clique search on bitsets (Python integers), highest vertex first | clique search with candidate lists over the colour matrix |
| hypergraph Ramsey | link bitsets built with vectorised colex ranks (numpy); pruned search adding vertices that keep all k-subsets monochromatic | link bitsets built by walking all k-subsets in colex order; same pruned search on u64 words |
| van der Waerden | per difference d, `B & B>>d & ... & B>>(k-1)d` on big-integer bitsets | the same identity on u64 word arrays, multithreaded over d |
| linear code | RREF, then the codeword table of the last rows combined with a loop over the others (numpy) | RREF, then a q-ary Gray code (one coordinate changes per step) |
| covering code | sumset C + B(R) when small, otherwise bitmap dilation | bitmap dilation on u64 words |
| girth | breadth-first search from all sources at once, vectorised (numpy) | breadth-first search from each vertex with pruning |
| Hadamard | packed bits, XOR and popcount (numpy) | i8 dot products |
| circulant weighing | pair counting by bincount (numpy) | pair counting in a loop |
| line-free set | normalised directions × points, or all pairs (chosen by size) | the same two methods, chosen by a different rule |
| kissing | int64 Gram blocks (numpy) and exact 128-bit comparison emulated in uint64 | i64 dot products and i128 comparison, multithreaded |
| Heilbronn | common denominator, Python integers | common denominator, big integers |
| polynomial | square-free part via a modular gcd (Brown/Collins, verified by exact division); Moebius map composed of shifts and reversals; Vincent-Collins-Akritas bisection with Descartes' rule; exact sign at sample points | the same steps with a different prime sequence, the homogeneous Horner form of the Moebius map and its own bisection code |

## 5. Test vectors

`worker/checkers/testdata/<type>/<category>/*.json`, where the category is the expected verdict:

* `pass` (exit 0), `fail` (exit 1), `malformed` (exit 2): small hand-made vectors, generated by
  `worker/checkers/tools/make_handmade_vectors.py` from classical objects (Paley and Clebsch
  colourings, Golay and Reed-Solomon codes, cages, Sylvester and Paley matrices, root systems, ...)
  and from deliberately broken versions of them;
* `real` (exit 0) and `real_fail` (exit 1): certificates from our research rounds, produced by
  `worker/checkers/tools/convert_real_data.py` from `release/papers/*/*/data/` (read-only); our
  research produced no graph Ramsey colouring, so `graph_ramsey_coloring/real/` holds the classical
  certificate R(6,6) > 101 (Paley graph of order 101) from the hand-made generator.

`worker/tests/test_checkers.py` runs both implementations on every vector
(`pytest worker/tests/test_checkers.py`; the real certificates with `-m slow`), and the Rust crate
checks the hand-made vectors itself (`cargo test`). `worker/checkers/tools/fuzz_compare.py`
compares the two implementations on random certificates of every type.
