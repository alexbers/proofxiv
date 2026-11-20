"""Literature sync: external events, their confirmation and their consequences."""

from __future__ import annotations

import uuid
from datetime import timedelta

from proofxiv_protocol.enums import (
    Bound,
    DecisiveTaskKind,
    DecisiveTaskStatus,
    ExternalEventKind,
    NoveltyCategory,
    NoveltyStatus,
    ProblemStatus,
)
from proofxiv_protocol.problems import ExternalEventSpec, SyncReport
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Claim,
    DecisiveTask,
    ExternalEvent,
    Lease,
    NoveltyChange,
    Paper,
    PaperProblem,
    Problem,
    ProblemLead,
    Quantity,
    RecordEntry,
)
from . import audit
from .errors import Invalid, NotFound
from .identity import Principal, now
from .levels import recompute_paper
from .notify import notify
from .params import get_param
from .problems import upsert_approach, value_to_float
from .security import SCOPE_AGENT
from .signals import notify_paper_watchers
from .watch import WATCH_PREFIX

SYNC_STALE_DAYS = 6
DERIVED_REASON = "derived from a confirmed record event"


def _set_watch_prefix(event: ExternalEvent) -> None:
    if not event.auto_source:
        return
    for prefix in WATCH_PREFIX.values():
        if event.summary.startswith(prefix):
            event.summary = WATCH_PREFIX[event.confirmed] + event.summary.removeprefix(prefix)
            return


async def _resolve(
    session: AsyncSession, spec: ExternalEventSpec
) -> tuple[Problem | None, Paper | None]:
    problem = None
    paper = None
    if spec.problem_slug:
        problem = (
            await session.execute(select(Problem).where(Problem.slug == spec.problem_slug))
        ).scalar_one_or_none()
        if problem is None:
            raise Invalid(f"unknown problem '{spec.problem_slug}'")
    if spec.paper_id:
        paper = await session.get(Paper, spec.paper_id)
        if paper is None:
            raise Invalid(f"unknown paper '{spec.paper_id}'")
    return problem, paper


async def _new_event(
    session: AsyncSession, spec: ExternalEventSpec, reporter: uuid.UUID
) -> ExternalEvent:
    problem, paper = await _resolve(session, spec)
    if spec.kind is ExternalEventKind.RECORD_IMPROVED and problem is not None:
        quantity = await _quantity(session, problem.id, spec.quantity or "")
        if quantity is None:
            raise Invalid(f"problem has no quantity '{spec.quantity}'")
    event = ExternalEvent(
        kind=spec.kind,
        problem_id=problem.id if problem else None,
        paper_id=paper.id if paper else None,
        source_url=spec.source_url,
        source_title=spec.source_title,
        published_on=spec.published_on,
        summary=spec.summary,
        quantity_key=spec.quantity,
        value=spec.value,
        bound=spec.bound,
        reported_by=reporter,
    )
    session.add(event)
    await session.flush()
    return event


async def _quantity(session: AsyncSession, problem_id: uuid.UUID, key: str) -> Quantity | None:
    return (
        await session.execute(
            select(Quantity).where(Quantity.problem_id == problem_id, Quantity.key == key)
        )
    ).scalar_one_or_none()


async def report_sync(
    session: AsyncSession, principal: Principal, report: SyncReport
) -> list[ExternalEvent]:
    """Events reported by ordinary agents: visible at once as unconfirmed, checked weekly."""
    principal.require_scope(SCOPE_AGENT)
    kinds = await get_param(session, "priority_kind")
    events = []
    for spec in report.events:
        event = await _new_event(session, spec, principal.id)
        session.add(
            DecisiveTask(
                kind=DecisiveTaskKind.CONFIRM_EVENT,
                status=DecisiveTaskStatus.READY,
                base_priority=kinds.get("confirm_event", 60.0),
                event_id=event.id,
                problem_id=event.problem_id,
                paper_id=event.paper_id,
            )
        )
        events.append(event)
    await _apply_approaches(session, report, principal.id, confirmed=False)
    audit.record(
        session, principal.id, "sync.reported", "problems", ",".join(report.problem_slugs),
        events=len(events),
    )
    return events


async def _apply_approaches(
    session: AsyncSession,
    report: SyncReport,
    by: uuid.UUID,
    confirmed: bool,
    task_id: uuid.UUID | None = None,
    only: uuid.UUID | None = None,
) -> None:
    """Approaches found while checking the literature go into the problems' maps."""
    for slug, specs in report.approaches.items():
        problem = (
            await session.execute(select(Problem).where(Problem.slug == slug))
        ).scalar_one_or_none()
        if problem is None or (only is not None and problem.id != only):
            continue
        for spec in specs:
            await upsert_approach(session, problem, spec, by, confirmed=confirmed, task_id=task_id)


async def apply_event(
    session: AsyncSession,
    event: ExternalEvent,
    confirmed: bool,
    reason: str,
    novelty_category: NoveltyCategory | None = None,
) -> None:
    event.confirmed = confirmed
    event.confirm_reason = reason
    _set_watch_prefix(event)
    if not confirmed:
        return
    event.applied_at = now()
    touched_papers: set[uuid.UUID] = set()

    if event.problem_id is not None:
        problem = await session.get(Problem, event.problem_id)
        assert problem is not None
        if event.kind is ExternalEventKind.SOLVED:
            problem.status = ProblemStatus.SOLVED_EXTERNALLY
        elif event.kind is ExternalEventKind.RECORD_IMPROVED:
            touched_papers |= await _apply_record(session, event, problem)
        await _notify_workers(session, problem, event)

    if event.paper_id is not None:
        paper = await session.get(Paper, event.paper_id)
        assert paper is not None
        if event.kind is ExternalEventKind.PRIOR_ART and novelty_category is not None:
            # a decisive check of this event weighed it against the paper and named the category
            paper.novelty_category = novelty_category
            paper.novelty_status = NoveltyStatus.PLATFORM_CONFIRMED
            session.add(
                NoveltyChange(
                    paper_id=paper.id,
                    category=novelty_category,
                    status=NoveltyStatus.PLATFORM_CONFIRMED,
                    reason=f"literature event: {event.summary[:300]}",
                    event_id=event.id,
                )
            )
        elif event.kind is ExternalEventKind.PRIOR_ART:
            # earlier work with the result or a part of it (a literature sync): how much it takes
            # away is for a new novelty audit to decide, with the event in its materials
            paper.novelty_status = NoveltyStatus.DISPUTED
            session.add(
                NoveltyChange(
                    paper_id=paper.id,
                    category=paper.novelty_category,
                    status=NoveltyStatus.DISPUTED,
                    reason=f"prior art found by the literature sync; a new novelty audit decides: {event.summary[:300]}",
                    event_id=event.id,
                )
            )
            await _queue_novelty_audit(session, paper)
        await notify_paper_watchers(
            session, paper, "paper_event", {"kind": event.kind.value, "source": event.source_url}
        )
        touched_papers.add(paper.id)

    await session.flush()
    for paper_id in touched_papers:
        paper = await session.get(Paper, paper_id)
        if paper is not None:
            await recompute_paper(session, paper)


async def _queue_novelty_audit(session: AsyncSession, paper: Paper) -> None:
    """A new novelty check of the paper's current version, of the kind it had (consistency for a
    fully formal paper, audit otherwise); none if one is still waiting."""
    novelty_kinds = (DecisiveTaskKind.NOVELTY_CONSISTENCY, DecisiveTaskKind.NOVELTY_AUDIT)
    earlier = (
        await session.execute(
            select(DecisiveTask.kind, DecisiveTask.status).where(
                DecisiveTask.paper_id == paper.id,
                DecisiveTask.version == paper.current_version,
                DecisiveTask.kind.in_(novelty_kinds),
            )
        )
    ).all()
    if any(status is not DecisiveTaskStatus.DONE for _, status in earlier):
        return
    kind = earlier[0][0] if earlier else DecisiveTaskKind.NOVELTY_AUDIT
    kinds = await get_param(session, "priority_kind")
    session.add(
        DecisiveTask(
            kind=kind,
            status=DecisiveTaskStatus.READY,
            base_priority=kinds.get(kind.value, 40.0),
            paper_id=paper.id,
            version=paper.current_version,
        )
    )


async def _apply_record(
    session: AsyncSession, event: ExternalEvent, problem: Problem
) -> set[uuid.UUID]:
    quantity = await _quantity(session, problem.id, event.quantity_key or "")
    if quantity is None or event.value is None or event.bound is None:
        return set()
    new = value_to_float(event.value)
    session.add(
        RecordEntry(
            quantity_id=quantity.id,
            bound=event.bound,
            value=event.value,
            value_float=new,
            source_url=event.source_url,
            source_title=event.source_title,
            event_id=event.id,
            recorded_on=event.published_on,
            verified=True,
        )
    )
    # Papers whose record on this quantity is now beaten get their own "superseded" event.
    beaten = select(Claim).where(Claim.quantity_id == quantity.id, Claim.bound == event.bound)
    papers: set[uuid.UUID] = set()
    for claim in (await session.execute(beaten)).scalars():
        if claim.value is None:
            continue
        old = value_to_float(claim.value)
        is_beaten = new > old if event.bound is Bound.LOWER else new < old
        if is_beaten and claim.paper_id != event.paper_id:
            papers.add(claim.paper_id)
    for paper_id in papers:
        session.add(
            ExternalEvent(
                kind=ExternalEventKind.RECORD_IMPROVED,
                problem_id=problem.id,
                paper_id=paper_id,
                source_url=event.source_url,
                source_title=event.source_title,
                published_on=event.published_on,
                summary=f"Record improved to {event.value}: {event.summary}"[:2000],
                quantity_key=event.quantity_key,
                value=event.value,
                bound=event.bound,
                reported_by=event.reported_by,
                confirmed=True,
                confirm_reason=DERIVED_REASON,
                applied_at=now(),
            )
        )
    return papers


async def _notify_workers(session: AsyncSession, problem: Problem, event: ExternalEvent) -> None:
    leases = await session.execute(
        select(Lease.operator_id)
        .where(
            Lease.problem_id == problem.id,
            Lease.released_at.is_(None),
            Lease.expires_at > now(),
        )
        .distinct()
    )
    for operator_id in leases.scalars():
        notify(
            session,
            operator_id,
            "problem_event",
            {
                "problem": problem.slug,
                "kind": event.kind.value,
                "summary": event.summary[:300],
                "source": event.source_url,
            },
        )


async def sync_candidates(session: AsyncSession, limit: int = 20) -> list[Problem]:
    """Problems to check against the literature: those being worked on first, then stale ones."""
    stale = now() - timedelta(days=SYNC_STALE_DAYS)
    active = (
        select(Lease.problem_id)
        .where(Lease.released_at.is_(None), Lease.expires_at > now())
        .distinct()
    )
    recent_papers = (
        select(PaperProblem.problem_id)
        .join(Paper, Paper.id == PaperProblem.paper_id)
        .where(Paper.created_at > now() - timedelta(days=30))
    )
    # the automatic watch found something new about it (core/watch.py)
    recent_leads = select(ProblemLead.problem_id).where(ProblemLead.created_at > now() - timedelta(days=14))
    stmt = (
        select(Problem)
        .where(
            Problem.hidden.is_(False),
            Problem.status.in_([ProblemStatus.OPEN, ProblemStatus.PROPOSED]),
            or_(Problem.last_synced_at.is_(None), Problem.last_synced_at < stale),
        )
        .order_by(
            Problem.id.in_(active).desc(),
            Problem.id.in_(recent_leads).desc(),
            Problem.id.in_(recent_papers).desc(),
            Problem.last_synced_at.asc().nulls_first(),
            Problem.importance.desc(),
        )
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def ensure_sync_tasks(session: AsyncSession, limit: int = 20) -> int:
    """Create SYNC tasks for the weekly run (one per problem that needs a literature check)."""
    open_sync = select(DecisiveTask.problem_id).where(
        DecisiveTask.kind == DecisiveTaskKind.SYNC,
        DecisiveTask.status.in_([DecisiveTaskStatus.READY, DecisiveTaskStatus.LEASED]),
    )
    kinds = await get_param(session, "priority_kind")
    created = 0
    for problem in await sync_candidates(session, limit):
        exists = (
            await session.execute(
                select(func.count()).select_from(open_sync.where(DecisiveTask.problem_id == problem.id).subquery())
            )
        ).scalar_one()
        if exists:
            continue
        session.add(
            DecisiveTask(
                kind=DecisiveTaskKind.SYNC,
                status=DecisiveTaskStatus.READY,
                base_priority=kinds.get("sync", 20.0),
                problem_id=problem.id,
            )
        )
        created += 1
    return created


async def apply_trusted_sync(
    session: AsyncSession,
    report: SyncReport,
    reporter: uuid.UUID,
    problem_id: uuid.UUID,
    task_id: uuid.UUID | None = None,
) -> list[ExternalEvent]:
    """Events found by the trusted weekly run are confirmed at once."""
    problem = await session.get(Problem, problem_id)
    if problem is None:
        raise NotFound("problem not found")
    events = []
    for spec in report.events:
        event = await _new_event(session, spec, reporter)
        event.origin_task_id = task_id
        await apply_event(session, event, True, "found by the trusted weekly run")
        events.append(event)
    await _apply_approaches(session, report, reporter, True, task_id, only=problem.id)
    problem.last_synced_at = now()
    return events


async def revert_event(session: AsyncSession, event: ExternalEvent) -> None:
    """Undo a confirmation whose signing key was revoked; the event waits for a new check."""
    applied = event.confirmed is True
    event.confirmed = None
    event.confirm_reason = None
    event.applied_at = None
    _set_watch_prefix(event)
    if not applied:
        return
    touched: set[uuid.UUID] = set()
    await session.execute(delete(RecordEntry).where(RecordEntry.event_id == event.id))
    if event.kind is ExternalEventKind.RECORD_IMPROVED and event.problem_id is not None:
        derived = await session.execute(
            select(ExternalEvent).where(
                ExternalEvent.id != event.id,
                ExternalEvent.problem_id == event.problem_id,
                ExternalEvent.kind == ExternalEventKind.RECORD_IMPROVED,
                ExternalEvent.source_url == event.source_url,
                ExternalEvent.value == event.value,
                ExternalEvent.confirm_reason == DERIVED_REASON,
            )
        )
        for d in derived.scalars():
            if d.paper_id is not None:
                touched.add(d.paper_id)
            await session.delete(d)
    if event.kind is ExternalEventKind.SOLVED and event.problem_id is not None:
        problem = await session.get(Problem, event.problem_id)
        if problem is not None and problem.status is ProblemStatus.SOLVED_EXTERNALLY:
            problem.status = ProblemStatus.OPEN
    if event.paper_id is not None:
        touched.add(event.paper_id)
        if event.kind is ExternalEventKind.PRIOR_ART:
            await _restore_novelty(session, event)
    await session.flush()
    for paper_id in touched:
        paper = await session.get(Paper, paper_id)
        if paper is not None:
            await recompute_paper(session, paper)


async def _restore_novelty(session: AsyncSession, event: ExternalEvent) -> None:
    await session.execute(delete(NoveltyChange).where(NoveltyChange.event_id == event.id))
    paper = await session.get(Paper, event.paper_id)
    if paper is None:
        return
    latest = (
        await session.execute(
            select(NoveltyChange)
            .where(NoveltyChange.paper_id == paper.id)
            .order_by(NoveltyChange.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is not None:
        paper.novelty_category = latest.category
        paper.novelty_status = latest.status
    else:
        paper.novelty_status = NoveltyStatus.CLAIMED  # recompute_paper re-derives peer status
