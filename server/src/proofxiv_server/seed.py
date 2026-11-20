"""Seed data: import problems from JSON, and a small demo world for local development."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from proofxiv_protocol.enums import ProblemStatus
from proofxiv_protocol.problems import ProblemProposal
from sqlalchemy import select

from .core.errors import Invalid
from .core.identity import get_or_create_operator
from .core.problems import create_problem
from .db.models import Problem
from .db.session import sessionmaker


async def import_problems(
    path: Path, status: ProblemStatus = ProblemStatus.OPEN, origin: str | None = None
) -> int:
    """Import a JSON list of ProblemProposal objects (skips those that already exist)."""
    items = json.loads(await asyncio.to_thread(path.read_text))
    proposals = [ProblemProposal.model_validate(item) for item in items]
    return await import_proposals(proposals, status, origin=origin or f"import:{path.name}")


async def import_proposals(
    proposals: list[ProblemProposal], status: ProblemStatus, origin: str
) -> int:
    created = 0
    async with sessionmaker()() as session:
        rows = await session.execute(select(Problem.title, Problem.variant))
        existing = {(title, variant) for title, variant in rows.all()}
        for proposal in proposals:
            key = (proposal.title, proposal.variant or None)
            if key in existing:
                continue
            try:
                await create_problem(session, proposal, None, status, origin=origin)
            except Invalid as exc:  # an imported statement that would run code is skipped
                print(f"skipped {proposal.title} ({proposal.variant}): {exc.message}")
                continue
            existing.add(key)
            created += 1
        await session.commit()
    return created


DEMO_PROBLEMS = [
    {
        "title": "Lower bounds for the hypergraph Ramsey number R(5,5;3)",
        "statement_md": "Find a red/blue colouring of the 3-subsets of an $n$-set with no "
        "monochromatic $K_5^{(3)}$ for $n$ as large as possible.",
        "domain": "combinatorics",
        "sources": [{"url": "https://www.combinatorics.org/ojs/index.php/eljc/article/view/DS1"}],
        "openness_evidence": "Radziszowski's dynamic survey DS1 lists 88 as the best lower bound.",
        "quantities": [{"key": "r553", "name": "R(5,5;3)", "best_lower": "88"}],
    },
    {
        "title": "Kissing number in dimension 19",
        "statement_md": "Find as many unit vectors in $\\mathbb{R}^{19}$ as possible with pairwise "
        "angles at least $60^\\circ$.",
        "domain": "geometry",
        "sources": [{"url": "https://cohn.mit.edu/kissing-numbers"}],
        "openness_evidence": "Cohn's table lists the best known lower bound.",
        "quantities": [{"key": "tau19", "name": "τ₁₉", "best_lower": "12276"}],
    },
    {
        "title": "Symmetric Hadamard matrices of order 260",
        "statement_md": "Does a symmetric Hadamard matrix of order 260 exist?",
        "domain": "combinatorics",
        "sources": [{"url": "https://arxiv.org/abs/2206.10353"}],
        "openness_evidence": "Listed among the open orders below 1000 by Đoković and Kotsireas.",
    },
]


async def seed_demo() -> None:
    async with sessionmaker()() as session:
        for login in ("owner", "alice", "bob"):
            await get_or_create_operator(session, login)
        for item in DEMO_PROBLEMS:
            proposal = ProblemProposal.model_validate(item)
            exists = (
                await session.execute(select(Problem.id).where(Problem.title == proposal.title))
            ).first()
            if not exists:
                await create_problem(session, proposal, None, ProblemStatus.OPEN, origin="demo")
        await session.commit()
