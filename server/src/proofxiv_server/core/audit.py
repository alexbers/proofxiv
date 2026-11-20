from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditEvent


def record(
    session: AsyncSession,
    actor_id: uuid.UUID | None,
    action: str,
    subject_type: str,
    subject_id: object,
    **data: Any,
) -> None:
    session.add(
        AuditEvent(
            actor_id=actor_id,
            action=action,
            subject_type=subject_type,
            subject_id=str(subject_id),
            data=data,
        )
    )
