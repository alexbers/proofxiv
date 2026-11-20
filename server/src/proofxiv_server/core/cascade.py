"""What happens when a signing key is revoked: dependent results are re-checked."""

from __future__ import annotations

from proofxiv_protocol.enums import (
    DecisiveTaskKind,
    DecisiveTaskStatus,
    ProblemStatus,
    RunJobStatus,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Approach,
    DecisiveTask,
    ExternalEvent,
    Paper,
    Problem,
    Review,
    Run,
    RunJob,
    SigningKey,
    WeeklyRun,
)
from . import audit, events, reports
from .levels import recompute_paper
from .problems import set_proposal_records_verified

# statuses a problem confirmation can set; undone back to "proposed"
CONFIRMED_STATUSES = (
    ProblemStatus.OPEN,
    ProblemStatus.CLOSED_KNOWN,
    ProblemStatus.SOLVED_EXTERNALLY,
)


async def _undo_effects(session: AsyncSession, task: DecisiveTask) -> None:
    """Take back what a task's signed result changed outside reviews, so that the re-check
    applies it once again from a clean state."""
    if task.kind is DecisiveTaskKind.CONFIRM_PROBLEM and task.problem_id is not None:
        problem = await session.get(Problem, task.problem_id)
        if problem is not None:
            if problem.status in CONFIRMED_STATUSES:
                problem.status = ProblemStatus.PROPOSED
            await set_proposal_records_verified(session, problem, False)
    elif task.kind is DecisiveTaskKind.CONFIRM_EVENT and task.event_id is not None:
        event = await session.get(ExternalEvent, task.event_id)
        if event is not None:
            await events.revert_event(session, event)
    elif task.kind is DecisiveTaskKind.SYNC:
        found = (
            await session.execute(select(ExternalEvent).where(ExternalEvent.origin_task_id == task.id))
        ).scalars()
        for event in list(found):
            await events.revert_event(session, event)
            await session.delete(event)
        # approaches it confirmed keep their text but lose the mark until checked again
        mapped = (
            await session.execute(select(Approach).where(Approach.problem_id == task.problem_id))
        ).scalars()
        for approach in mapped:
            if approach.revisions and approach.revisions[-1].get("task") == str(task.id):
                approach.confirmed = False
    elif task.kind is DecisiveTaskKind.MODERATION:
        await reports.revert_moderation(session, task)
    elif task.kind is DecisiveTaskKind.DIGEST and task.weekly_run_id is not None:
        run = await session.get(WeeklyRun, task.weekly_run_id)
        if run is not None:
            run.digest_md = None
            run.digest_bundle = None


async def invalidate_key(session: AsyncSession, key: SigningKey) -> None:
    since = key.revoke_from
    assert since is not None
    papers: set = set()

    runs = (
        await session.execute(
            select(Run).where(Run.signing_key_id == key.id, Run.created_at >= since)
        )
    ).scalars()
    for run in runs:
        run.invalidated = True
        job = await session.get(RunJob, run.job_id)
        if job is not None and not job.cancelled:
            job.status = RunJobStatus.QUEUED
            job.leased_by = None
            job.lease_expires_at = None
            job.finished_at = None
            papers.add(job.paper_id)

    tasks = (
        await session.execute(
            select(DecisiveTask).where(
                DecisiveTask.signing_key_id == key.id, DecisiveTask.done_at >= since
            )
        )
    ).scalars()
    for task in list(tasks):
        await _undo_effects(session, task)
        task.status = DecisiveTaskStatus.READY
        task.leased_by = None
        task.lease_expires_at = None
        task.done_at = None
        reviews = (
            await session.execute(select(Review).where(Review.decisive_task_id == task.id))
        ).scalars()
        for review in reviews:
            review.removed = True
            review.removed_reason = "signing key revoked; result is being re-checked"
        if task.paper_id is not None:
            papers.add(task.paper_id)

    await session.flush()
    for paper_id in papers:
        paper = await session.get(Paper, paper_id)
        if paper is not None:
            await recompute_paper(session, paper)
    audit.record(
        session, None, "key.invalidated", "signing_key", key.id, papers=[str(p) for p in papers]
    )
