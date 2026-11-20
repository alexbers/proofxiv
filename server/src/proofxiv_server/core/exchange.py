"""Review exchange: review others' papers to get reviews of your own."""

from __future__ import annotations

import random
import uuid
from datetime import timedelta
from typing import Any

from proofxiv_protocol.enums import ReviewKind, ReviewOrigin
from proofxiv_protocol.reviews import ReviewBody
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    ExchangeEntry,
    Paper,
    PaperProblem,
    Problem,
    Review,
    ReviewAssignment,
    ReviewRequest,
)
from . import audit
from .errors import Conflict, Forbidden, Invalid, NotFound
from .identity import Principal, now
from .levels import recompute_paper
from .security import SCOPE_AGENT
from .signals import notify_paper_watchers

MAX_OPEN_ASSIGNMENTS = 3


async def balance(session: AsyncSession, operator_id: uuid.UUID) -> int:
    total = await session.execute(
        select(func.coalesce(func.sum(ExchangeEntry.delta), 0)).where(
            ExchangeEntry.operator_id == operator_id
        )
    )
    return int(total.scalar_one())


async def request_reviews(
    session: AsyncSession, principal: Principal, paper_id: uuid.UUID, count: int
) -> ReviewRequest:
    principal.require_scope(SCOPE_AGENT)
    settings = get_settings()
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise NotFound("paper not found")
    if paper.submitter_id != principal.id:
        raise Forbidden("only the submitter can request exchange reviews")
    if paper.hidden:
        raise Invalid("paper is hidden by moderation")
    if count < 1:
        raise Invalid("count must be positive")
    already = await session.execute(
        select(func.coalesce(func.sum(ReviewRequest.count_requested), 0)).where(
            ReviewRequest.paper_id == paper.id, ReviewRequest.version == paper.current_version
        )
    )
    if int(already.scalar_one()) + count > settings.exchange_max_per_version:
        raise Invalid(
            f"at most {settings.exchange_max_per_version} exchange reviews per paper version"
        )
    have = await balance(session, principal.id)
    if have < count:
        raise Invalid(
            f"exchange balance is {have}; review other papers to earn more",
            balance=have,
        )
    request = ReviewRequest(
        paper_id=paper.id,
        version=paper.current_version,
        requester_id=principal.id,
        count_requested=count,
    )
    session.add(request)
    await session.flush()
    session.add(
        ExchangeEntry(operator_id=principal.id, delta=-count, reason="request", ref_id=request.id)
    )
    audit.record(session, principal.id, "exchange.requested", "paper", paper.id, count=count)
    return request


async def _swap_partners(session: AsyncSession, operator_id: uuid.UUID) -> set[uuid.UUID]:
    """Operators who recently reviewed my papers: I will not get their papers (no direct swap)."""
    since = now() - timedelta(days=get_settings().exchange_swap_window_days)
    rows = await session.execute(
        select(Review.reviewer_id)
        .join(Paper, Paper.id == Review.paper_id)
        .where(
            Paper.submitter_id == operator_id,
            Review.origin == ReviewOrigin.EXCHANGE,
            Review.created_at >= since,
        )
    )
    return set(rows.scalars())


async def next_assignment(
    session: AsyncSession, principal: Principal, domains: list[str] | None = None
) -> ReviewAssignment | None:
    principal.require_scope(SCOPE_AGENT)
    me = principal.id
    open_count = (
        await session.execute(
            select(func.count())
            .select_from(ReviewAssignment)
            .where(ReviewAssignment.reviewer_id == me, ReviewAssignment.status == "assigned")
        )
    ).scalar_one()
    if open_count >= MAX_OPEN_ASSIGNMENTS:
        raise Conflict(f"finish your {open_count} open assignments first")

    active = (
        select(ReviewAssignment.request_id, func.count().label("n"))
        .where(ReviewAssignment.status == "assigned")
        .group_by(ReviewAssignment.request_id)
        .subquery()
    )
    reviewed_by_me = select(Review.paper_id).where(
        Review.reviewer_id == me, Review.origin == ReviewOrigin.EXCHANGE
    )
    assigned_to_me = select(ReviewAssignment.paper_id).where(ReviewAssignment.reviewer_id == me)
    stmt = (
        select(ReviewRequest)
        .join(Paper, Paper.id == ReviewRequest.paper_id)
        .outerjoin(active, active.c.request_id == ReviewRequest.id)
        .where(
            ReviewRequest.cancelled_at.is_(None),
            ReviewRequest.count_fulfilled + func.coalesce(active.c.n, 0)
            < ReviewRequest.count_requested,
            ReviewRequest.version == Paper.current_version,
            Paper.hidden.is_(False),
            Paper.submitter_id != me,
            ReviewRequest.paper_id.not_in(reviewed_by_me),
            ReviewRequest.paper_id.not_in(assigned_to_me),
        )
        .order_by(ReviewRequest.created_at)
        .limit(50)
    )
    partners = await _swap_partners(session, me)
    if partners:
        stmt = stmt.where(Paper.submitter_id.not_in(partners))
    if domains:
        in_domain = (
            select(PaperProblem.paper_id)
            .join(Problem, Problem.id == PaperProblem.problem_id)
            .where(Problem.domain.in_(domains))
        )
        stmt = stmt.where(ReviewRequest.paper_id.in_(in_domain))
    candidates = list((await session.execute(stmt)).scalars())
    if not candidates:
        return None
    request = random.choice(candidates[:10])
    assignment = ReviewAssignment(
        request_id=request.id,
        paper_id=request.paper_id,
        version=request.version,
        reviewer_id=me,
        reviewer_agent_id=principal.agent.id if principal.agent else None,
        due_at=now() + timedelta(days=get_settings().exchange_due_days),
    )
    session.add(assignment)
    await session.flush()
    audit.record(session, me, "exchange.assigned", "paper", request.paper_id)
    return assignment


async def get_own_assignment(
    session: AsyncSession, principal: Principal, assignment_id: uuid.UUID
) -> ReviewAssignment:
    assignment = await session.get(ReviewAssignment, assignment_id)
    if assignment is None or assignment.reviewer_id != principal.id:
        raise NotFound("assignment not found")
    return assignment


async def submit_exchange_review(
    session: AsyncSession, principal: Principal, assignment_id: uuid.UUID, body: ReviewBody
) -> Review:
    principal.require_scope(SCOPE_AGENT)
    assignment = await get_own_assignment(session, principal, assignment_id)
    if assignment.status != "assigned":
        raise Conflict(f"assignment is {assignment.status}")
    problems = body.exchange_problems()
    if problems:
        raise Invalid("review does not meet the exchange format", problems=problems)
    review = Review(
        paper_id=assignment.paper_id,
        version=assignment.version,
        origin=ReviewOrigin.EXCHANGE,
        kind=ReviewKind.REVIEW,
        reviewer_id=principal.id,
        reviewer_agent_id=principal.agent.id if principal.agent else None,
        body=body.model_dump(mode="json"),
        declared_model=body.model,
        harness=body.harness,
        assignment_id=assignment.id,
    )
    session.add(review)
    await session.flush()
    assignment.status = "submitted"
    assignment.review_id = review.id
    request = await session.get(ReviewRequest, assignment.request_id)
    assert request is not None
    request.count_fulfilled += 1
    session.add(
        ExchangeEntry(operator_id=principal.id, delta=1, reason="review_given", ref_id=review.id)
    )
    paper = await session.get(Paper, assignment.paper_id)
    assert paper is not None
    await recompute_paper(session, paper)
    await notify_paper_watchers(
        session, paper, "exchange_review", {"review": str(review.id), "recommendation": body.recommendation.value}
    )
    audit.record(session, principal.id, "exchange.reviewed", "paper", paper.id, review=str(review.id))
    return review


async def _authored_review(
    session: AsyncSession, principal: Principal, review_id: uuid.UUID
) -> tuple[Review, Paper]:
    review = await session.get(Review, review_id)
    if review is None:
        raise NotFound("review not found")
    paper = await session.get(Paper, review.paper_id)
    assert paper is not None
    if paper.submitter_id != principal.id:
        raise Forbidden("only the paper's submitter can do this")
    return review, paper


async def rate_review(
    session: AsyncSession, principal: Principal, review_id: uuid.UUID, useful: bool
) -> Review:
    review, _ = await _authored_review(session, principal, review_id)
    if review.origin is not ReviewOrigin.EXCHANGE:
        raise Invalid("only exchange reviews can be rated")
    review.useful = useful
    return review


async def respond_to_review(
    session: AsyncSession, principal: Principal, review_id: uuid.UUID, text: str
) -> Review:
    review, _ = await _authored_review(session, principal, review_id)
    if len(text.strip()) < 5:
        raise Invalid("response is empty")
    review.author_response_md = text
    return review


async def expire_assignments(session: AsyncSession) -> int:
    rows = await session.execute(
        select(ReviewAssignment).where(
            and_(ReviewAssignment.status == "assigned", ReviewAssignment.due_at < now())
        )
    )
    expired = 0
    for assignment in rows.scalars():
        assignment.status = "expired"
        expired += 1
    return expired


async def my_assignments(session: AsyncSession, principal: Principal) -> list[ReviewAssignment]:
    rows = await session.execute(
        select(ReviewAssignment)
        .where(ReviewAssignment.reviewer_id == principal.id)
        .order_by(ReviewAssignment.created_at.desc())
        .limit(100)
    )
    return list(rows.scalars())


async def exchange_summary(session: AsyncSession, operator_id: uuid.UUID) -> dict[str, Any]:
    given = (
        await session.execute(
            select(func.count())
            .select_from(Review)
            .where(Review.reviewer_id == operator_id, Review.origin == ReviewOrigin.EXCHANGE)
        )
    ).scalar_one()
    useful = (
        await session.execute(
            select(func.count())
            .select_from(Review)
            .where(
                Review.reviewer_id == operator_id,
                Review.origin == ReviewOrigin.EXCHANGE,
                Review.useful.is_(True),
            )
        )
    ).scalar_one()
    return {"balance": await balance(session, operator_id), "given": given, "rated_useful": useful}
