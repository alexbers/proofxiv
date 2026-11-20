"""Review exchange: assignments, exchange reviews, ratings and author responses."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Response
from proofxiv_protocol.notices import REVIEW_SAFETY_CHECK
from proofxiv_protocol.reviews import ReviewBody
from pydantic import BaseModel, Field

from ...core import exchange
from ...core.decisive import paper_materials
from ...db.models import Paper, ReviewAssignment
from ..deps import Session, User
from ..schemas import AssignmentOut, OkOut

router = APIRouter(tags=["exchange"])


async def assignment_out(session: Session, a: ReviewAssignment, materials: bool) -> AssignmentOut:
    data: dict[str, Any] | None = None
    if materials:
        paper = await session.get(Paper, a.paper_id)
        assert paper is not None
        data = await paper_materials(session, paper, a.version)
        # exchange reviews are independent: other reviews are not part of the materials
        data.pop("reviews", None)
    return AssignmentOut(
        id=str(a.id),
        paper_id=str(a.paper_id),
        version=a.version,
        status=a.status,
        due_at=a.due_at.isoformat(),
        safety_check=REVIEW_SAFETY_CHECK if materials else None,
        materials=data,
    )


class NextIn(BaseModel):
    domains: list[str] = Field(default_factory=list)


@router.post("/exchange/assignments/next", response_model=AssignmentOut | None)
async def next_assignment(body: NextIn, user: User, session: Session, response: Response):
    assignment = await exchange.next_assignment(session, user, body.domains or None)
    if assignment is None:
        response.status_code = 204
        return None
    out = await assignment_out(session, assignment, materials=True)
    await session.commit()
    return out


@router.get("/exchange/assignments", response_model=list[AssignmentOut])
async def my_assignments(user: User, session: Session) -> list[AssignmentOut]:
    rows = await exchange.my_assignments(session, user)
    return [await assignment_out(session, a, materials=False) for a in rows]


@router.get("/exchange/assignments/{assignment_id}", response_model=AssignmentOut)
async def get_assignment(assignment_id: uuid.UUID, user: User, session: Session) -> AssignmentOut:
    assignment = await exchange.get_own_assignment(session, user, assignment_id)
    return await assignment_out(session, assignment, materials=True)


class ReviewSubmitOut(BaseModel):
    review_id: str
    balance: int


@router.post("/exchange/assignments/{assignment_id}/review", response_model=ReviewSubmitOut)
async def submit_review(
    assignment_id: uuid.UUID, body: ReviewBody, user: User, session: Session
) -> ReviewSubmitOut:
    review = await exchange.submit_exchange_review(session, user, assignment_id, body)
    await session.commit()
    return ReviewSubmitOut(review_id=str(review.id), balance=await exchange.balance(session, user.id))


class BalanceOut(BaseModel):
    balance: int
    given: int
    rated_useful: int


@router.get("/exchange/balance", response_model=BalanceOut)
async def my_balance(user: User, session: Session) -> BalanceOut:
    return BalanceOut(**await exchange.exchange_summary(session, user.id))


class RateIn(BaseModel):
    useful: bool


@router.post("/reviews/{review_id}/rate", response_model=OkOut)
async def rate(review_id: uuid.UUID, body: RateIn, user: User, session: Session) -> OkOut:
    await exchange.rate_review(session, user, review_id, body.useful)
    await session.commit()
    return OkOut()


class RespondIn(BaseModel):
    text: str = Field(min_length=5, max_length=20000)


@router.post("/reviews/{review_id}/response", response_model=OkOut)
async def respond(review_id: uuid.UUID, body: RespondIn, user: User, session: Session) -> OkOut:
    await exchange.respond_to_review(session, user, review_id, body.text)
    await session.commit()
    return OkOut()
