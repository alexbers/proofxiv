"""Human signals on papers: 👍 like and 🔔 follow. They never change trust levels."""

from __future__ import annotations

import uuid

from proofxiv_protocol.enums import SignalKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Paper, Signal
from .errors import NotFound
from .identity import Principal
from .notify import notify


async def notify_paper_watchers(
    session: AsyncSession, paper: Paper, kind: str, payload: dict, include_submitter: bool = True
) -> None:
    """Tell the submitter and everyone who follows (🔔) the paper."""
    followers = set(
        (
            await session.execute(
                select(Signal.operator_id).where(
                    Signal.paper_id == paper.id, Signal.kind == SignalKind.FOLLOW
                )
            )
        ).scalars()
    )
    if include_submitter:
        followers.add(paper.submitter_id)
    data = {"paper": str(paper.id), "title": paper.title, **payload}
    for operator_id in followers:
        notify(session, operator_id, kind, data)


async def set_signal(
    session: AsyncSession, principal: Principal, paper_id: uuid.UUID, kind: SignalKind, on: bool
) -> bool:
    if await session.get(Paper, paper_id) is None:
        raise NotFound("paper not found")
    existing = (
        await session.execute(
            select(Signal).where(
                Signal.operator_id == principal.id,
                Signal.paper_id == paper_id,
                Signal.kind == kind,
            )
        )
    ).scalar_one_or_none()
    if on and existing is None:
        session.add(Signal(operator_id=principal.id, paper_id=paper_id, kind=kind))
    elif not on and existing is not None:
        await session.delete(existing)
    return on
