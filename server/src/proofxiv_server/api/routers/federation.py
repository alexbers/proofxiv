"""Federation: this instance's outbox for the labs that follow it, and the labs it follows."""

from __future__ import annotations

from fastapi import APIRouter, Query
from proofxiv_protocol.federation import OutboxPage

from ...core import federation
from ..deps import Session
from ..schemas import FederationOut

router = APIRouter(tags=["federation"])


@router.get("/federation/outbox", response_model=OutboxPage)
async def outbox(session: Session, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)) -> OutboxPage:
    """What this instance received itself, in order: paper versions, machine checks, decisive
    reviews, level changes, withdrawals. Each entry is signed with the federation key, which the
    owner attests in the owner journal (`attest_key`)."""
    return await federation.outbox_page(session, after, limit)


@router.get("/federation", response_model=FederationOut)
async def overview(session: Session) -> FederationOut:
    """This instance (keys, outbox) and the labs it follows, with their trust levels."""
    return FederationOut(**await federation.overview(session))
