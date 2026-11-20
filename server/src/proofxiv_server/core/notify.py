"""Notifications: stored for the web UI; urgent ones are pushed to the owners' channel."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Notification
from .identity import now

log = structlog.get_logger()


def notify(
    session: AsyncSession,
    operator_id: uuid.UUID | None,
    kind: str,
    payload: dict[str, Any],
    urgent: bool = False,
    for_owners: bool = False,
) -> Notification:
    note = Notification(
        operator_id=operator_id, kind=kind, payload=payload, urgent=urgent, for_owners=for_owners
    )
    session.add(note)
    return note


def _format(note: Notification) -> str:
    lines = [f"[ProofXiv] {note.kind}"]
    for key, value in note.payload.items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


async def _push(text: str) -> bool:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        if settings.notify_telegram_token and settings.notify_telegram_chat:
            url = f"https://api.telegram.org/bot{settings.notify_telegram_token}/sendMessage"
            resp = await client.post(url, json={"chat_id": settings.notify_telegram_chat, "text": text})
            return resp.is_success
        if settings.notify_webhook_url:
            resp = await client.post(settings.notify_webhook_url, json={"text": text})
            return resp.is_success
    log.warning("urgent notification (no channel configured)", text=text)
    return True


async def deliver_urgent(session: AsyncSession) -> int:
    rows = await session.execute(
        select(Notification).where(
            Notification.urgent.is_(True), Notification.delivered_at.is_(None)
        )
    )
    delivered = 0
    for note in rows.scalars():
        try:
            ok = await _push(_format(note))
        except httpx.HTTPError as exc:
            log.error("notification delivery failed", error=str(exc))
            ok = False
        if ok:
            note.delivered_at = now()
            delivered += 1
    return delivered
