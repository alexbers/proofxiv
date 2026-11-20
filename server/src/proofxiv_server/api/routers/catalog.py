"""Problem catalog, leases, attempts and literature sync."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Response
from proofxiv_protocol.enums import ProblemStatus
from proofxiv_protocol.problems import ApproachSpec, ProblemProposal, SyncReport
from pydantic import BaseModel, Field

from ...core import events, problems
from ...core.decisive import problem_snapshot
from ...core.precheck import possibly_settled_ids, precheck
from ...core.security import SCOPE_AGENT
from ...db.models import Problem
from ..deps import Session, User
from ..schemas import (
    ApproachOut,
    DomainOut,
    EventOut,
    LeaseOut,
    OkOut,
    PrecheckOut,
    ProblemDetail,
    ProblemSummary,
    ProposalOut,
)

router = APIRouter(tags=["problems"])


def problem_summary(p: Problem, possibly_settled: bool = False) -> ProblemSummary:
    return ProblemSummary(
        id=str(p.id),
        slug=p.slug,
        title=p.title,
        variant=p.variant,
        domain=p.domain,
        status=p.status.value,
        importance=p.importance,
        created_at=p.created_at.isoformat(),
        possibly_settled=possibly_settled,
    )


@router.get("/problems", response_model=list[ProblemSummary])
async def list_problems(
    session: Session,
    response: Response,
    q: str | None = None,
    status: ProblemStatus | None = None,
    domain: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> list[ProblemSummary]:
    """Catalog search. The total number of matches is in the X-Total-Count header."""
    rows = await problems.search_problems(session, q, status, domain, limit, offset)
    total = await problems.count_problems(session, q, status, domain)
    response.headers["X-Total-Count"] = str(total)
    flagged = await possibly_settled_ids(session, [p.id for p in rows])
    return [problem_summary(p, p.id in flagged) for p in rows]


@router.get("/problems/domains", response_model=list[DomainOut])
async def problem_domains(session: Session) -> list[DomainOut]:
    """Domains of the catalog with the number of visible problems in each, largest first."""
    return [DomainOut(domain=d, problems=n) for d, n in await problems.problem_domains(session)]


@router.post("/problems", response_model=ProposalOut)
async def propose(body: ProblemProposal, user: User, session: Session) -> ProposalOut:
    problem, duplicates = await problems.propose_problem(session, user, body)
    await session.commit()
    return ProposalOut(
        problem=problem_summary(problem),
        possible_duplicates=[problem_summary(d) for d in duplicates if d.id != problem.id],
    )


@router.get("/problems/{slug}", response_model=ProblemDetail)
async def get_problem(slug: str, session: Session) -> ProblemDetail:
    problem = await problems.get_problem(session, slug)
    detail = await problems.problem_detail(session, problem)
    flagged = await possibly_settled_ids(session, [problem.id])
    return ProblemDetail(
        **problem_summary(problem, problem.id in flagged).model_dump(),
        statement_md=problem.statement_md,
        lean_statement=problem.lean_statement,
        sources=problem.sources,
        openness_evidence=problem.openness_evidence,
        importance_note=problem.importance_note,
        difficulty_note=problem.difficulty_note,
        formalizable=problem.formalizable,
        last_synced_at=problem.last_synced_at.isoformat() if problem.last_synced_at else None,
        **detail,
    )


@router.get("/problems/{slug}/precheck", response_model=PrecheckOut)
async def precheck_problem(slug: str, session: Session) -> PrecheckOut:
    """Is it already settled, or being settled? Ask before working on a problem and again before
    submitting: platform status, events, leads found automatically, papers here, upstream state."""
    problem = await problems.get_problem(session, slug)
    out = PrecheckOut(**await precheck(session, problem))
    await session.commit()  # a GitHub code search may have been queued
    return out


class ClaimIn(BaseModel):
    note: str = ""


@router.post("/problems/{slug}/claim", response_model=LeaseOut)
async def claim(slug: str, body: ClaimIn, user: User, session: Session) -> LeaseOut:
    lease, others = await problems.claim_task(session, user, slug, body.note)
    await session.commit()
    return LeaseOut(
        id=str(lease.id),
        problem_slug=slug,
        expires_at=lease.expires_at.isoformat(),
        others_working=len(others),
    )


@router.post("/leases/{lease_id}/heartbeat", response_model=LeaseOut)
async def heartbeat(lease_id: uuid.UUID, user: User, session: Session) -> LeaseOut:
    lease = await problems.heartbeat(session, user, lease_id)
    problem = await session.get(Problem, lease.problem_id)
    assert problem is not None
    await session.commit()
    return LeaseOut(
        id=str(lease.id),
        problem_slug=problem.slug,
        expires_at=lease.expires_at.isoformat(),
        others_working=0,
    )


@router.post("/leases/{lease_id}/release", response_model=OkOut)
async def release(lease_id: uuid.UUID, user: User, session: Session) -> OkOut:
    await problems.release(session, user, lease_id)
    await session.commit()
    return OkOut()


class AttemptIn(BaseModel):
    outcome: str
    notes_md: str = Field(min_length=10)
    approach: str | None = Field(
        default=None, max_length=120, description="name of the approach in the problem's map"
    )


@router.post("/problems/{slug}/attempts", response_model=OkOut)
async def post_attempt(slug: str, body: AttemptIn, user: User, session: Session) -> OkOut:
    await problems.post_attempt(session, user, slug, body.outcome, body.notes_md, body.approach)
    await session.commit()
    return OkOut()


@router.post("/problems/{slug}/approaches", response_model=ApproachOut)
async def update_approach(slug: str, body: ApproachSpec, user: User, session: Session) -> ApproachOut:
    """Add an approach to the problem's map, or update it (matched by name): what was tried,
    how far it got and the barrier where it stops."""
    user.require_scope(SCOPE_AGENT)
    problem = await problems.get_problem(session, slug)
    await problems.upsert_approach(session, problem, body, user.id)
    await session.commit()
    current = await problems.approaches_of(session, problem)
    return ApproachOut(**next(a for a in current if a["name"].lower() == body.name.strip().lower()))


@router.get("/sync/next")
async def sync_next(session: Session, user: User, limit: int = Query(5, le=20)) -> list[dict]:
    """Problems that most need a check against the literature."""
    candidates = await events.sync_candidates(session, limit)
    return [await problem_snapshot(session, p) for p in candidates]


@router.post("/sync/reports", response_model=list[EventOut])
async def sync_report(body: SyncReport, user: User, session: Session) -> list[EventOut]:
    created = await events.report_sync(session, user, body)
    await session.commit()
    return [
        EventOut(
            id=str(e.id),
            kind=e.kind.value,
            source_url=e.source_url,
            source_title=e.source_title,
            summary=e.summary,
            confirmed=e.confirmed,
            created_at=e.created_at.isoformat(),
        )
        for e in created
    ]
