"""Background maintenance: expire leases and assignments, push urgent notifications, and run the
automatic literature watch (core/watch.py) on its own schedule."""

from __future__ import annotations

import asyncio
import time

import structlog
from proofxiv_protocol.enums import DecisiveTaskStatus, RunJobStatus
from sqlalchemy import and_, update

from .config import get_settings
from .core import federation, watch
from .core.exchange import expire_assignments
from .core.identity import now
from .core.importance import recompute_importance
from .core.notify import deliver_urgent
from .core.significance import refresh_all as refresh_significance
from .db.models import DecisiveTask, DeviceCode, RunJob
from .db.session import sessionmaker

log = structlog.get_logger()
IMPORTANCE_EVERY_S = 6 * 3600
_last_importance: float | None = None  # None: at the first tick (monotonic time starts at boot)


async def tick() -> dict[str, int]:
    global _last_importance
    async with sessionmaker()() as session:
        importance = 0
        if _last_importance is None or time.monotonic() - _last_importance > IMPORTANCE_EVERY_S:
            importance = await recompute_importance(session)
            await session.flush()
            await refresh_significance(session)  # importance and attention feed into it
            _last_importance = time.monotonic()
        expired = await expire_assignments(session)
        runs = await session.execute(
            update(RunJob)
            .where(and_(RunJob.status == RunJobStatus.LEASED, RunJob.lease_expires_at < now()))
            .values(status=RunJobStatus.QUEUED, leased_by=None, lease_expires_at=None)
        )
        tasks = await session.execute(
            update(DecisiveTask)
            .where(
                and_(
                    DecisiveTask.status == DecisiveTaskStatus.LEASED,
                    DecisiveTask.lease_expires_at < now(),
                )
            )
            .values(status=DecisiveTaskStatus.READY, leased_by=None, lease_expires_at=None)
        )
        await session.execute(
            update(DeviceCode)
            .where(and_(DeviceCode.expires_at < now(), DeviceCode.pending_token.is_not(None)))
            .values(pending_token=None)
        )
        delivered = await deliver_urgent(session)
        await session.commit()
    stats = {
        "importance_updated": importance,
        "assignments_expired": expired,
        "runs_released": getattr(runs, "rowcount", 0) or 0,
        "tasks_released": getattr(tasks, "rowcount", 0) or 0,
        "urgent_delivered": delivered,
    }
    if any(stats.values()):
        log.info("maintenance", **stats)
    return stats


async def run_forever(interval: float = 60.0) -> None:
    while True:
        try:
            await tick()
        except Exception:  # keep the loop alive; errors are logged
            log.exception("maintenance tick failed")
        try:
            await watch.run_due()  # each source keeps its own schedule
        except Exception:
            log.exception("watch failed")
        if get_settings().federation_enabled:
            try:
                await federation.pull_due()  # each lab followed, every federation_pull_minutes
            except Exception:
                log.exception("federation pull failed")
        await asyncio.sleep(interval)
