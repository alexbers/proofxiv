"""Reports, appeals, the public moderation log and owner actions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from proofxiv_protocol.enums import ReportReason, ReportTarget
from proofxiv_protocol.signing import SignedBundle
from pydantic import BaseModel, Field

from ...core import reports, views
from ...core.decisive import queue_overview
from ...core.errors import Forbidden
from ...core.identity import apply_owner_statement, owner_hide_now
from ..deps import Session, User
from ..schemas import ModerationEntryOut, OkOut, OwnerJournalEntryOut, QueueOut, ReportOut

router = APIRouter(tags=["moderation"])


class ReportIn(BaseModel):
    target_type: ReportTarget
    target_id: uuid.UUID
    reason: ReportReason
    evidence_md: str = Field(default="", max_length=20000)


@router.post("/reports", response_model=ReportOut)
async def report(body: ReportIn, user: User, session: Session) -> ReportOut:
    created = await reports.create_report(
        session, user, body.target_type, body.target_id, body.reason, body.evidence_md
    )
    await session.commit()
    return ReportOut(id=str(created.id), status=created.status.value, urgent=created.urgent)


class AppealIn(BaseModel):
    text: str = Field(min_length=10, max_length=20000)


@router.post("/reports/{report_id}/appeal", response_model=ReportOut)
async def appeal(report_id: uuid.UUID, body: AppealIn, user: User, session: Session) -> ReportOut:
    created = await reports.appeal(session, user, report_id, body.text)
    await session.commit()
    return ReportOut(id=str(created.id), status=created.status.value, urgent=created.urgent)


@router.get("/moderation-log", response_model=list[ModerationEntryOut])
async def moderation_log(session: Session, limit: int = Query(100, le=500)) -> list[ModerationEntryOut]:
    return [ModerationEntryOut(**e) for e in await views.moderation_log(session, limit)]


@router.post("/owner/statements", response_model=OkOut)
async def owner_statement(bundle: SignedBundle, session: Session) -> OkOut:
    """Apply a statement signed by an owner key (roles, keys, prompts, params, hide)."""
    await apply_owner_statement(session, bundle)
    await session.commit()
    return OkOut()


@router.get("/owner/journal", response_model=list[OwnerJournalEntryOut])
async def journal(session: Session) -> list[OwnerJournalEntryOut]:
    """Public journal of signed owner statements."""
    return [OwnerJournalEntryOut(**e) for e in await views.owner_journal(session)]


@router.get("/owner/queue", response_model=QueueOut)
async def owner_queue(user: User, session: Session) -> QueueOut:
    if not user.is_owner:
        raise Forbidden("owners only")
    return QueueOut(**await queue_overview(session))


class HideIn(BaseModel):
    target_type: ReportTarget
    target_id: uuid.UUID
    reason: str = Field(min_length=3)


@router.post("/owner/hide", response_model=OkOut)
async def hide(body: HideIn, user: User, session: Session) -> OkOut:
    """Urgent manual hide (malware, illegal content) from the owner panel."""
    await owner_hide_now(session, user, body.target_type, body.target_id, body.reason)
    await session.commit()
    return OkOut()
