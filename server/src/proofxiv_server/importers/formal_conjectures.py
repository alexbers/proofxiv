"""Import open problems from google-deepmind/formal-conjectures (Lean statements, Apache-2.0).

Every theorem tagged `@[category research open, ...]` becomes a catalog problem with its Lean
statement, the docstring as the statement text, and the reference of its source file.
"""

from __future__ import annotations

import re
from pathlib import Path

from proofxiv_protocol.problems import ProblemProposal, SourceRef

REPO_URL = "https://github.com/google-deepmind/formal-conjectures"

AMS_DOMAINS = {
    "3": "logic", "5": "combinatorics", "6": "order theory", "11": "number theory",
    "12": "field theory", "13": "commutative algebra", "14": "algebraic geometry",
    "15": "linear algebra", "16": "ring theory", "17": "nonassociative algebra",
    "18": "category theory", "19": "k-theory", "20": "group theory", "22": "topological groups",
    "26": "real analysis", "28": "measure theory", "30": "complex analysis", "31": "potential theory",
    "32": "several complex variables", "33": "special functions", "34": "ordinary differential equations",
    "35": "partial differential equations", "37": "dynamical systems", "39": "difference equations",
    "40": "sequences and series", "41": "approximation theory", "42": "harmonic analysis",
    "43": "abstract harmonic analysis", "44": "integral transforms", "45": "integral equations",
    "46": "functional analysis", "47": "operator theory", "49": "calculus of variations",
    "51": "geometry", "52": "discrete geometry", "53": "differential geometry", "54": "general topology",
    "55": "algebraic topology", "57": "manifolds", "58": "global analysis", "60": "probability",
    "62": "statistics", "65": "numerical analysis", "68": "computer science", "81": "quantum theory",
    "90": "operations research", "94": "information theory",
}

DECL = re.compile(
    r"/--(?P<doc>(?:(?!-/).)*)-/\s*"  # one docstring, never spanning past its own end
    r"(?:set_option[^\n]*\bin\s*)*"
    r"@\[(?P<attrs>[^\]]*)\]\s*"
    r"(?P<kind>theorem|lemma)\s+(?P<name>[^\s(:{\[]+)(?P<sig>(?:(?!:=).)*):=\s*by\s+sorry",
    re.DOTALL,
)
HEADER = re.compile(r"/-!\s*#\s*(?P<title>[^\n]+)(?P<body>.*?)-/", re.DOTALL)
REFERENCE = re.compile(r"\*Reference[s]?:\*\s*\[(?P<text>[^\]]+)\]\((?P<url>[^)]+)\)")


def _domain(attrs: str) -> str:
    match = re.search(r"AMS\s+([\d\s]+)", attrs)
    if not match:
        return "mathematics"
    codes = match.group(1).split()
    return AMS_DOMAINS.get(codes[0], "mathematics") if codes else "mathematics"


def _clean_doc(doc: str) -> str:
    lines = [line.rstrip() for line in doc.strip().splitlines()]
    return "\n".join(lines).strip()


def parse_file(path: Path, root: Path, commit: str) -> list[ProblemProposal]:
    text = path.read_text(encoding="utf-8", errors="replace")
    header = HEADER.search(text)
    module_title = header.group("title").strip() if header else path.stem
    reference = REFERENCE.search(header.group("body")) if header else None
    rel = path.relative_to(root).as_posix()
    blob = f"{REPO_URL}/blob/{commit}/{rel}"
    out: list[ProblemProposal] = []
    for match in DECL.finditer(text):
        attrs = match.group("attrs")
        if not re.search(r"category\s+research\s+open", attrs):
            continue
        name = match.group("name")
        doc = _clean_doc(match.group("doc")) or "See the Lean statement."
        statement = f"{match.group('kind')} {name}{match.group('sig').rstrip()} := by\n  sorry"
        sources = []
        if reference:
            sources.append(SourceRef(url=reference.group("url"), title=reference.group("text")))
        sources.append(SourceRef(url=blob, title=f"formal-conjectures: {rel}"))
        out.append(
            ProblemProposal(
                title=module_title[:300],
                variant=name[:200],
                statement_md=doc if len(doc) >= 10 else f"{doc} (see the Lean statement)",
                domain=_domain(attrs),
                sources=sources,
                openness_evidence=(
                    f"Tagged `category research open` in google-deepmind/formal-conjectures "
                    f"at commit {commit[:12]}. Definitions used by the statement are in {rel}."
                ),
                formalizable=True,
                lean_statement=statement,
            )
        )
    return out


def head_commit(repo: Path) -> str:
    """The checked-out commit, read from .git directly: the server image has no git program."""
    git = repo / ".git"
    head = (git / "HEAD").read_text().strip()
    if not head.startswith("ref: "):
        return head
    ref = head.removeprefix("ref: ")
    if (git / ref).is_file():
        return (git / ref).read_text().strip()
    packed = git / "packed-refs"
    for line in packed.read_text().splitlines() if packed.is_file() else []:
        if line.endswith(" " + ref):
            return line.split()[0]
    raise ValueError(f"cannot resolve {ref} in {git}")


def collect(repo: Path, limit: int | None = None) -> list[ProblemProposal]:
    commit = head_commit(repo)
    root = repo
    proposals: list[ProblemProposal] = []
    for path in sorted((repo / "FormalConjectures").rglob("*.lean")):
        if "Util" in path.parts:
            continue
        proposals += parse_file(path, root, commit)
        if limit is not None and len(proposals) >= limit:
            proposals = proposals[:limit]
            break
    # the same theorem name in two files with the same title: add the file to the variant
    counts: dict[tuple[str, str | None], int] = {}
    for p in proposals:
        counts[(p.title, p.variant)] = counts.get((p.title, p.variant), 0) + 1
    for p in proposals:
        if counts[(p.title, p.variant)] > 1:
            p.variant = f"{p.variant} ({(p.sources[-1].title or '').split(': ', 1)[-1]})"[:200]
    return proposals
