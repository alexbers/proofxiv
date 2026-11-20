"""The weekly trusted run: queue of decisive tasks, materials, and applying signed results."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from proofxiv_protocol.enums import (
    DecisiveTaskKind,
    DecisiveTaskStatus,
    NoveltyStatus,
    ProblemStatus,
    ReportStatus,
    ReportTarget,
    ReviewKind,
    ReviewOrigin,
    Role,
    Severity,
    SignalKind,
)
from proofxiv_protocol.notices import UNTRUSTED_NOTICE
from proofxiv_protocol.signing import SignedBundle, canonical_json, sha256_hex
from proofxiv_protocol.trusted import (
    REVIEW_TASKS,
    TRUSTED_RESULT_TYPE,
    DecisiveTaskSpec,
    TaskTarget,
    TrustedResult,
)
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    Artifact,
    Claim,
    DecisiveTask,
    ExternalEvent,
    ModerationLogEntry,
    NoveltyChange,
    OfficialPrompt,
    Paper,
    PaperProblem,
    PaperVersion,
    Post,
    Problem,
    Report,
    Review,
    Run,
    RunJob,
    Signal,
    WeeklyRun,
)
from . import audit, events, federation, reports
from .errors import Conflict, Forbidden, Invalid, NotFound
from .identity import Principal, active_roles, now, verify_operator_bundle
from .levels import job_claims, recompute_paper, references_report
from .params import get_param
from .problems import (
    approaches_of,
    best_records,
    set_proposal_records_verified,
    similar_problems,
)
from .security import SCOPE_TRUSTED
from .signals import notify_paper_watchers

PROMPT_NAME = {
    DecisiveTaskKind.NOVELTY_CONSISTENCY: "novelty_consistency",
    DecisiveTaskKind.NOVELTY_AUDIT: "novelty_audit",
    DecisiveTaskKind.ANCHOR_REVIEW: "anchor_review",
    DecisiveTaskKind.STATEMENT_AUDIT: "statement_audit",
    DecisiveTaskKind.HELDOUT_REVIEW: "heldout_review",
    DecisiveTaskKind.MODERATION: "moderation",
    DecisiveTaskKind.CONFIRM_PROBLEM: "confirm_problem",
    DecisiveTaskKind.CONFIRM_EVENT: "confirm_event",
    DecisiveTaskKind.SYNC: "sync",
    DecisiveTaskKind.DIGEST: "digest",
}
REVIEW_KIND = {
    DecisiveTaskKind.NOVELTY_CONSISTENCY: ReviewKind.NOVELTY_CONSISTENCY,
    DecisiveTaskKind.NOVELTY_AUDIT: ReviewKind.NOVELTY_AUDIT,
    DecisiveTaskKind.ANCHOR_REVIEW: ReviewKind.ANCHOR_REVIEW,
    DecisiveTaskKind.STATEMENT_AUDIT: ReviewKind.STATEMENT_AUDIT,
    DecisiveTaskKind.HELDOUT_REVIEW: ReviewKind.HELDOUT_REVIEW,
}


def prompt_name(task: DecisiveTask) -> str:
    if task.kind is DecisiveTaskKind.ANCHOR_REVIEW and task.adversarial:
        return "adversarial_review"
    return PROMPT_NAME[task.kind]


async def active_prompt(session: AsyncSession, name: str) -> OfficialPrompt | None:
    return (
        await session.execute(
            select(OfficialPrompt).where(
                OfficialPrompt.name == name, OfficialPrompt.active.is_(True)
            )
        )
    ).scalar_one_or_none()


async def _require_trusted(session: AsyncSession, principal: Principal) -> None:
    principal.require_scope(SCOPE_TRUSTED)
    if Role.REVIEWER not in await active_roles(session, principal.id):
        raise Forbidden("the weekly run needs the 'reviewer' role")


# --- weekly runs ------------------------------------------------------------------------


async def start_weekly_run(
    session: AsyncSession, principal: Principal, budget: dict[str, Any]
) -> WeeklyRun:
    await _require_trusted(session, principal)
    run = WeeklyRun(operator_id=principal.id, budget=budget)
    session.add(run)
    await session.flush()
    created = await events.ensure_sync_tasks(session)
    kinds = await get_param(session, "priority_kind")
    session.add(
        DecisiveTask(
            kind=DecisiveTaskKind.DIGEST,
            status=DecisiveTaskStatus.READY,
            base_priority=kinds.get("digest", 10.0),
            weekly_run_id=run.id,
        )
    )
    audit.record(session, principal.id, "weekly.started", "weekly_run", run.id, sync_tasks=created)
    return run


async def finish_weekly_run(
    session: AsyncSession, principal: Principal, run_id: uuid.UUID, stats: dict[str, Any]
) -> WeeklyRun:
    run = await session.get(WeeklyRun, run_id)
    if run is None or run.operator_id != principal.id:
        raise NotFound("weekly run not found")
    run.finished_at = now()
    run.stats = stats
    # leases of this run that were not completed go back to the queue
    leased = await session.execute(
        select(DecisiveTask).where(
            DecisiveTask.weekly_run_id == run.id,
            DecisiveTask.status == DecisiveTaskStatus.LEASED,
        )
    )
    for task in leased.scalars():
        task.status = DecisiveTaskStatus.READY
        task.leased_by = None
        task.lease_expires_at = None
    return run


async def release_task(
    session: AsyncSession, principal: Principal, task_id: uuid.UUID, reason: str
) -> DecisiveTask:
    task = await session.get(DecisiveTask, task_id)
    if task is None:
        raise NotFound("task not found")
    if task.status is not DecisiveTaskStatus.LEASED or task.leased_by != principal.id:
        raise Conflict("task is not leased by you")
    task.status = DecisiveTaskStatus.READY
    task.leased_by = None
    task.lease_expires_at = None
    audit.record(session, principal.id, "trusted.released", "decisive_task", task.id, reason=reason[:500])
    return task


async def next_check_date(session: AsyncSession) -> datetime | None:
    last = (
        await session.execute(select(func.max(WeeklyRun.created_at)))
    ).scalar_one_or_none()
    if last is None:
        return None
    return last + timedelta(days=int(await get_param(session, "weekly_period_days")))


# --- queue ------------------------------------------------------------------------------


async def _priority(session: AsyncSession, task: DecisiveTask) -> float:
    priority = task.base_priority
    priority += (now() - task.created_at).total_seconds() / 86400 * float(
        await get_param(session, "priority_age_per_day")
    )
    if task.paper_id is not None and task.kind is not DecisiveTaskKind.MODERATION:
        likes = (
            await session.execute(
                select(func.count())
                .select_from(Signal)
                .where(Signal.paper_id == task.paper_id, Signal.kind == SignalKind.LIKE)
            )
        ).scalar_one()
        priority += likes * float(await get_param(session, "priority_like"))
        independent = (
            await session.execute(
                select(func.count())
                .select_from(Review)
                .where(
                    Review.paper_id == task.paper_id,
                    Review.removed.is_(False),
                    Review.origin.in_([ReviewOrigin.EXCHANGE, ReviewOrigin.TRUSTED]),
                )
            )
        ).scalar_one()
        if independent == 0 and likes > 0:
            priority += float(await get_param(session, "priority_no_independent_review"))
        # design 6.5: significant results are checked first (the budget is the bottleneck)
        paper = await session.get(Paper, task.paper_id)
        if paper is not None:
            priority += paper.significance * float(await get_param(session, "priority_significance"))
    return priority


async def lease_tasks(
    session: AsyncSession,
    principal: Principal,
    weekly_run_id: uuid.UUID,
    limit: int = 10,
    kinds: list[DecisiveTaskKind] | None = None,
) -> list[DecisiveTaskSpec]:
    await _require_trusted(session, principal)
    run = await session.get(WeeklyRun, weekly_run_id)
    if run is None or run.operator_id != principal.id or run.finished_at is not None:
        raise Invalid("no such open weekly run")
    stmt = select(DecisiveTask).where(
        or_(
            DecisiveTask.status == DecisiveTaskStatus.READY,
            and_(
                DecisiveTask.status == DecisiveTaskStatus.LEASED,
                DecisiveTask.lease_expires_at < now(),
            ),
        )
    )
    if kinds:
        stmt = stmt.where(DecisiveTask.kind.in_(kinds))
    candidates = list(
        (await session.execute(stmt.order_by(DecisiveTask.created_at).limit(500))).scalars()
    )
    scored = [(await _priority(session, t), t) for t in candidates]
    scored.sort(key=lambda pair: pair[0], reverse=True)

    specs: list[DecisiveTaskSpec] = []
    for priority, task in scored:
        if len(specs) >= limit:
            break
        if task.kind is DecisiveTaskKind.DIGEST and task.weekly_run_id not in (None, run.id):
            continue
        prompt = await active_prompt(session, prompt_name(task))
        if prompt is None:
            continue
        materials = await build_materials(session, task)
        task.materials_sha256 = sha256_hex(canonical_json(materials))
        task.materials = materials
        task.prompt_sha256 = prompt.sha256
        task.status = DecisiveTaskStatus.LEASED
        task.leased_by = principal.id
        task.lease_expires_at = now() + timedelta(hours=get_settings().decisive_lease_hours)
        task.weekly_run_id = run.id
        specs.append(
            DecisiveTaskSpec(
                task_id=task.id,
                kind=task.kind,
                target=TaskTarget(
                    paper_id=task.paper_id,
                    version=task.version,
                    problem_id=task.problem_id,
                    event_id=task.event_id,
                    report_id=task.report_id,
                ),
                priority=priority,
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                prompt_sha256=prompt.sha256,
                adversarial=task.adversarial,
                avoid_vendors=await _used_vendors(session, task),
                materials_sha256=task.materials_sha256 or "",
                materials=materials,
            )
        )
    return specs


async def _used_vendors(session: AsyncSession, task: DecisiveTask) -> list[str]:
    if task.kind is not DecisiveTaskKind.ANCHOR_REVIEW or task.paper_id is None:
        return []
    rows = await session.execute(
        select(DecisiveTask.vendor).where(
            DecisiveTask.paper_id == task.paper_id,
            DecisiveTask.version == task.version,
            DecisiveTask.kind == DecisiveTaskKind.ANCHOR_REVIEW,
            DecisiveTask.status == DecisiveTaskStatus.DONE,
            DecisiveTask.id != task.id,
        )
    )
    return sorted({v for v in rows.scalars() if v})


# --- materials --------------------------------------------------------------------------


async def paper_materials(session: AsyncSession, paper: Paper, version: int) -> dict[str, Any]:
    pv = (
        await session.execute(
            select(PaperVersion).where(
                PaperVersion.paper_id == paper.id, PaperVersion.version == version
            )
        )
    ).scalar_one()
    sizes = {
        a.sha256: a.size
        for a in (
            await session.execute(
                select(Artifact).where(Artifact.sha256.in_(list(pv.files.values())))
            )
        ).scalars()
    }
    claims = list(
        (
            await session.execute(
                select(Claim)
                .where(Claim.paper_id == paper.id, Claim.version == version)
                .order_by(Claim.key)
            )
        ).scalars()
    )
    jobs = {
        uuid.UUID(cid): j
        for j in (
            await session.execute(
                select(RunJob).where(RunJob.paper_id == paper.id, RunJob.version == version)
            )
        ).scalars()
        for cid in job_claims(j)
    }
    claim_items = []
    for c in claims:
        run_summary = None
        job = jobs.get(c.id)
        if job is not None:
            run = (
                await session.execute(
                    select(Run)
                    .where(Run.job_id == job.id, Run.invalidated.is_(False))
                    .order_by(Run.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if run is not None:
                run_summary = {
                    "verdict": run.verdict.value,
                    "accepted": run.accepted,
                    "native_decide": run.native_decide,
                    "lean": run.result.get("lean"),
                    "checker": run.result.get("checker"),
                    "evaluation": run.result.get("evaluation"),
                }
        claim_items.append(
            {
                "key": c.key,
                "kind": c.kind.value,
                "statement": c.statement,
                "level": c.level.value,
                "labels": c.labels,
                "c1_issues": c.c1_issues,
                "value": c.value,
                "bound": c.bound.value if c.bound else None,
                "record_status": c.record_status,
                "lean_theorems": c.lean_theorems,
                "certificate_type": c.certificate_type,
                "run": run_summary,
            }
        )
    problem_rows = (
        await session.execute(
            select(Problem)
            .join(PaperProblem, PaperProblem.problem_id == Problem.id)
            .where(PaperProblem.paper_id == paper.id)
        )
    ).scalars()
    problems = [await problem_snapshot(session, p) for p in problem_rows]
    reviews = (
        await session.execute(
            select(Review)
            .where(
                Review.paper_id == paper.id,
                Review.version == version,
                Review.removed.is_(False),
                Review.origin.in_([ReviewOrigin.AUTHOR, ReviewOrigin.EXCHANGE]),
            )
            .order_by(Review.created_at)
        )
    ).scalars()
    # confirmed literature events about the paper (prior art, a record beaten): a novelty check
    # queued by prior art weighs them
    events = (
        await session.execute(
            select(ExternalEvent)
            .where(ExternalEvent.paper_id == paper.id, ExternalEvent.confirmed.is_(True))
            .order_by(ExternalEvent.created_at)
        )
    ).scalars()
    return {
        "notice": UNTRUSTED_NOTICE,
        "id": str(paper.id),
        "version": version,
        "references": await references_report(session, paper.id, version),
        "title": paper.title,
        "abstract": paper.abstract,
        "byline": paper.byline,
        "ai_contribution": paper.ai_contribution.value,
        "human_involvement": paper.human_involvement,
        "manifest": pv.manifest,
        "files": [
            {"path": path, "sha256": sha, "size": sizes.get(sha)}
            for path, sha in sorted(pv.files.items())
        ],
        "claims": claim_items,
        "problems": problems,
        "reviews": [
            {
                "id": str(r.id),
                "origin": r.origin.value,
                "model": r.declared_model,
                "body": r.body,
                "body_md": r.body_md,
            }
            for r in reviews
        ],
        "literature_events": [
            {
                "kind": e.kind.value,
                "source_url": e.source_url,
                "source_title": e.source_title,
                "summary": e.summary,
            }
            for e in events
        ],
    }


async def problem_snapshot(session: AsyncSession, problem: Problem) -> dict[str, Any]:
    from ..db.models import Quantity

    quantities = []
    for q in (
        await session.execute(select(Quantity).where(Quantity.problem_id == problem.id))
    ).scalars():
        best = await best_records(session, q.id)
        quantities.append(
            {
                "key": q.key,
                "name": q.name,
                "best": {
                    k: {"value": r.value, "source": r.source_url, "verified": r.verified}
                    for k, r in best.items()
                },
            }
        )
    known_events = (
        await session.execute(
            select(ExternalEvent)
            .where(ExternalEvent.problem_id == problem.id, ExternalEvent.confirmed.is_not(False))
            .order_by(ExternalEvent.created_at.desc())
            .limit(30)
        )
    ).scalars()
    return {
        "id": str(problem.id),
        "slug": problem.slug,
        "approaches": [
            {k: a[k] for k in ("name", "status", "summary_md", "barrier_md", "confirmed", "attempts")}
            for a in await approaches_of(session, problem)
        ],
        "title": problem.title,
        "variant": problem.variant,
        "upstream": problem.upstream,  # lets another lab match it to its own catalog (federation)
        "part_of": problem.part_of,
        "statement_md": problem.statement_md,
        "lean_statement": problem.lean_statement,
        "domain": problem.domain,
        "status": problem.status.value,
        "sources": problem.sources,
        "openness_evidence": problem.openness_evidence,
        "last_synced_at": problem.last_synced_at.isoformat() if problem.last_synced_at else None,
        "quantities": quantities,
        "known_events": [
            {"kind": e.kind.value, "source_url": e.source_url, "summary": e.summary}
            for e in known_events
        ],
        # papers about it at the ProofXiv labs this instance follows: prior work for novelty
        "federation_papers": await federation.papers_for_problem(session, problem.id),
    }


async def build_materials(session: AsyncSession, task: DecisiveTask) -> dict[str, Any]:
    out: dict[str, Any] = {
        "task": {"kind": task.kind.value, "adversarial": task.adversarial, "id": str(task.id)}
    }
    if task.kind in REVIEW_TASKS:
        paper = await session.get(Paper, task.paper_id)
        assert paper is not None and task.version is not None
        out["paper"] = await paper_materials(session, paper, task.version)
        if task.kind is DecisiveTaskKind.HELDOUT_REVIEW:
            out["paper"]["reviews"] = []  # an independent judgment: no other reviews
    elif task.kind is DecisiveTaskKind.MODERATION:
        report = await session.get(Report, task.report_id)
        assert report is not None
        out["report"] = {
            "id": str(report.id),
            "reason": report.reason.value,
            "evidence_md": report.evidence_md,
            "urgent": report.urgent,
            "target_type": report.target_type.value,
            "target_id": str(report.target_id),
        }
        if report.appeal_of:
            original = await session.get(Report, report.appeal_of)
            out["appeal_of"] = original.decision if original else None
        out["target"] = await _target_snapshot(session, report.target_type, report.target_id)
    elif task.kind is DecisiveTaskKind.CONFIRM_PROBLEM:
        problem = await session.get(Problem, task.problem_id)
        assert problem is not None
        out["problem"] = await problem_snapshot(session, problem)
        out["problem"]["importance_note"] = problem.importance_note
        out["problem"]["difficulty_note"] = problem.difficulty_note
        out["similar"] = [
            {"slug": p.slug, "title": p.title, "status": p.status.value}
            for p in await similar_problems(session, problem.title)
            if p.id != problem.id
        ]
    elif task.kind is DecisiveTaskKind.CONFIRM_EVENT:
        event = await session.get(ExternalEvent, task.event_id)
        assert event is not None
        out["event"] = {
            "id": str(event.id),
            "kind": event.kind.value,
            "source_url": event.source_url,
            "source_title": event.source_title,
            "published_on": event.published_on.isoformat() if event.published_on else None,
            "summary": event.summary,
            "quantity": event.quantity_key,
            "value": event.value,
            "bound": event.bound.value if event.bound else None,
        }
        if event.problem_id:
            problem = await session.get(Problem, event.problem_id)
            assert problem is not None
            out["problem"] = await problem_snapshot(session, problem)
        if event.paper_id:
            paper = await session.get(Paper, event.paper_id)
            assert paper is not None
            out["paper"] = {"id": str(paper.id), "title": paper.title, "abstract": paper.abstract}
    elif task.kind is DecisiveTaskKind.SYNC:
        problem = await session.get(Problem, task.problem_id)
        assert problem is not None
        out["problem"] = await problem_snapshot(session, problem)
        out["platform_papers"] = [
            {"id": str(p.id), "title": p.title, "level": p.level.value}
            for p in (
                await session.execute(
                    select(Paper)
                    .join(PaperProblem, PaperProblem.paper_id == Paper.id)
                    .where(PaperProblem.problem_id == problem.id)
                )
            ).scalars()
        ]
    elif task.kind is DecisiveTaskKind.DIGEST:
        out["digest"] = await digest_materials(session)
    return out


async def _target_snapshot(
    session: AsyncSession, target_type: ReportTarget, target_id: uuid.UUID
) -> dict[str, Any]:
    if target_type is ReportTarget.PAPER:
        paper = await session.get(Paper, target_id)
        assert paper is not None
        return await paper_materials(session, paper, paper.current_version)
    if target_type is ReportTarget.REVIEW:
        review = await session.get(Review, target_id)
        assert review is not None
        paper = await session.get(Paper, review.paper_id)
        assert paper is not None
        return {
            "review": {
                "id": str(review.id),
                "origin": review.origin.value,
                "body": review.body,
                "body_md": review.body_md,
                "model": review.declared_model,
            },
            "paper": await paper_materials(session, paper, review.version),
        }
    if target_type is ReportTarget.PROBLEM:
        problem = await session.get(Problem, target_id)
        assert problem is not None
        return await problem_snapshot(session, problem)
    if target_type is ReportTarget.POST:
        post = await session.get(Post, target_id)
        assert post is not None
        return {
            "post": {
                "kind": post.kind.value,
                "body_md": post.body_md,
                "about": {"type": post.target_type.value, "id": str(post.target_id)},
                "created_at": post.created_at.isoformat(),
            }
        }
    event = await session.get(ExternalEvent, target_id)
    assert event is not None
    return {"event": {"kind": event.kind.value, "summary": event.summary, "source": event.source_url}}


async def digest_materials(session: AsyncSession) -> dict[str, Any]:
    since = (
        await session.execute(
            select(func.max(WeeklyRun.created_at)).where(WeeklyRun.finished_at.is_not(None))
        )
    ).scalar_one_or_none() or (now() - timedelta(days=7))
    papers = (
        await session.execute(
            select(Paper).where(Paper.created_at >= since, Paper.hidden.is_(False)).limit(200)
        )
    ).scalars()
    verified = (
        await session.execute(select(Paper).where(Paper.verified_at >= since).limit(200))
    ).scalars()
    problems = (
        await session.execute(
            select(Problem)
            .where(Problem.created_at >= since, Problem.status == ProblemStatus.OPEN)
            .limit(200)
        )
    ).scalars()
    confirmed = (
        await session.execute(
            select(ExternalEvent).where(ExternalEvent.applied_at >= since).limit(200)
        )
    ).scalars()
    moderation = (
        await session.execute(
            select(ModerationLogEntry).where(ModerationLogEntry.created_at >= since).limit(200)
        )
    ).scalars()
    return {
        "since": since.isoformat(),
        "until": now().isoformat(),
        "new_papers": [{"id": str(p.id), "title": p.title, "level": p.level.value} for p in papers],
        "verified_papers": [{"id": str(p.id), "title": p.title} for p in verified],
        "new_problems": [{"slug": p.slug, "title": p.title} for p in problems],
        "events": [
            {"kind": e.kind.value, "summary": e.summary, "source": e.source_url} for e in confirmed
        ],
        "moderation": [{"action": m.action.value, "reason": m.reason} for m in moderation],
    }


# --- applying results -------------------------------------------------------------------


async def apply_trusted_result(
    session: AsyncSession, principal: Principal, bundle: SignedBundle
) -> DecisiveTask:
    await _require_trusted(session, principal)
    if bundle.payload.get("type") != TRUSTED_RESULT_TYPE:
        raise Invalid("not a trusted result")
    try:
        result = TrustedResult.model_validate(bundle.payload)
    except ValueError as exc:
        raise Invalid(f"bad trusted result: {exc}") from exc
    key = await verify_operator_bundle(session, bundle, principal.operator, Role.REVIEWER)
    task = await session.get(DecisiveTask, result.task_id)
    if task is None:
        raise NotFound("task not found")
    if task.status is not DecisiveTaskStatus.LEASED or task.leased_by != principal.id:
        raise Conflict("task is not leased by you")
    if task.kind is not result.kind:
        raise Invalid("result kind does not match the task")
    if task.materials_sha256 != result.materials_sha256:
        raise Invalid("result was produced for different materials")
    if task.prompt_sha256 != result.prompt_sha256:
        raise Invalid("result was produced with a different prompt version")
    if result.vendor not in await get_param(session, "allowed_vendors"):
        raise Invalid(f"vendor '{result.vendor}' is not allowed for decisive roles")
    if result.transcript_sha256 is None:
        raise Invalid("a transcript is required")
    if await session.get(Artifact, result.transcript_sha256) is None:
        raise Invalid("upload the transcript before submitting the result")

    if task.kind in REVIEW_TASKS:
        await _apply_review(session, principal, task, result, key.id, bundle)
    elif task.kind is DecisiveTaskKind.MODERATION:
        assert result.moderation is not None
        await reports.apply_moderation(session, task, result.moderation)
    elif task.kind is DecisiveTaskKind.CONFIRM_PROBLEM:
        assert result.confirm is not None
        problem = await session.get(Problem, task.problem_id)
        assert problem is not None
        decided = result.confirm.new_status or (
            ProblemStatus.OPEN if result.confirm.confirmed else ProblemStatus.CLOSED_KNOWN
        )
        # solved here before the confirmation arrived: it stays solved if it was indeed open
        if not (problem.status is ProblemStatus.SOLVED_ON_PLATFORM and decided is ProblemStatus.OPEN):
            problem.status = decided
        if result.confirm.confirmed and result.confirm.records_verified:
            await set_proposal_records_verified(session, problem, True)
    elif task.kind is DecisiveTaskKind.CONFIRM_EVENT:
        assert result.confirm is not None
        event = await session.get(ExternalEvent, task.event_id)
        assert event is not None
        await events.apply_event(
            session,
            event,
            result.confirm.confirmed,
            result.confirm.reasoning,
            result.novelty_category,
        )
    elif task.kind is DecisiveTaskKind.SYNC:
        assert result.sync is not None and task.problem_id is not None
        await events.apply_trusted_sync(session, result.sync, principal.id, task.problem_id, task.id)
    elif task.kind is DecisiveTaskKind.DIGEST:
        run = await session.get(WeeklyRun, task.weekly_run_id)
        assert run is not None
        run.digest_md = result.digest_md
        run.digest_bundle = bundle.model_dump(mode="json")

    task.status = DecisiveTaskStatus.DONE
    task.result = bundle.payload
    task.bundle = bundle.model_dump(mode="json")
    task.vendor = result.vendor
    task.signing_key_id = key.id
    task.done_at = now()
    await session.flush()
    if task.kind in REVIEW_TASKS:
        await federation.publish_review(session, task, principal.operator.login)
    if task.paper_id is not None:
        paper = await session.get(Paper, task.paper_id)
        if paper is not None:
            before = paper.level
            await recompute_paper(session, paper)
            if paper.level is not before:
                await notify_paper_watchers(
                    session, paper, "paper_level", {"from": before.value, "to": paper.level.value}
                )
    audit.record(session, principal.id, "trusted.result", "decisive_task", task.id, kind=task.kind.value)
    return task


def _serious_issues(materials_reviews: list[dict[str, Any]]) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for review in materials_reviews:
        body = review.get("body") or {}
        for i, issue in enumerate(body.get("issues", [])):
            if issue.get("severity") in (Severity.CRITICAL.value, Severity.MAJOR.value):
                out.add((review["id"], i))
    return out


async def _apply_review(
    session: AsyncSession,
    principal: Principal,
    task: DecisiveTask,
    result: TrustedResult,
    key_id: uuid.UUID,
    bundle: SignedBundle,
) -> None:
    assert result.review is not None and task.paper_id is not None and task.version is not None
    paper = await session.get(Paper, task.paper_id)
    assert paper is not None
    materials = await paper_materials(session, paper, task.version)
    answered = {(str(r.review_id), r.issue_index) for r in result.review.responses}
    heldout = task.kind is DecisiveTaskKind.HELDOUT_REVIEW
    missing = set() if heldout else _serious_issues(materials["reviews"]) - answered
    if missing:
        raise Invalid(
            "the decisive review must answer every serious issue of other reviews",
            missing=[f"{rid}#{idx}" for rid, idx in sorted(missing)],
        )
    kind = REVIEW_KIND[task.kind]
    if task.kind is DecisiveTaskKind.ANCHOR_REVIEW and task.adversarial:
        kind = ReviewKind.ADVERSARIAL_REVIEW
    review = Review(
        paper_id=paper.id,
        version=task.version,
        origin=ReviewOrigin.TRUSTED,
        kind=kind,
        reviewer_id=principal.id,
        reviewer_agent_id=principal.agent.id if principal.agent else None,
        body=result.review.model_dump(mode="json"),
        declared_model=result.model,
        harness=result.harness,
        vendor=result.vendor,
        adversarial=task.adversarial,
        transcript_sha256=result.transcript_sha256,
        bundle=bundle.model_dump(mode="json"),
        signing_key_id=key_id,
        decisive_task_id=task.id,
    )
    session.add(review)
    await session.flush()
    if heldout:
        # the transcript shows the prompt: public only when the prompt is revealed
        prompt = (
            await session.execute(
                select(OfficialPrompt).where(OfficialPrompt.sha256 == task.prompt_sha256)
            )
        ).scalars().first()
        if prompt is not None and prompt.text is None and result.transcript_sha256:
            transcript = await session.get(Artifact, result.transcript_sha256)
            if transcript is not None:
                transcript.embargoed = True
    if task.kind in (DecisiveTaskKind.NOVELTY_CONSISTENCY, DecisiveTaskKind.NOVELTY_AUDIT):
        assert result.novelty_category is not None
        paper.novelty_category = result.novelty_category
        paper.novelty_status = NoveltyStatus.PLATFORM_CONFIRMED
        session.add(
            NoveltyChange(
                paper_id=paper.id,
                category=result.novelty_category,
                status=NoveltyStatus.PLATFORM_CONFIRMED,
                reason=result.review.novelty.summary[:1000],
                review_id=review.id,
            )
        )


async def queue_overview(session: AsyncSession) -> dict[str, Any]:
    rows = await session.execute(
        select(DecisiveTask.kind, DecisiveTask.status, func.count()).group_by(
            DecisiveTask.kind, DecisiveTask.status
        )
    )
    counts: dict[str, dict[str, int]] = {}
    for kind, status, n in rows.all():
        counts.setdefault(kind.value, {})[status.value] = n
    urgent = (
        await session.execute(
            select(func.count())
            .select_from(Report)
            .where(Report.urgent.is_(True), Report.status == ReportStatus.OPEN)
        )
    ).scalar_one()
    nxt = await next_check_date(session)
    return {
        "tasks": counts,
        "open_urgent_reports": urgent,
        "next_weekly_run": nxt.isoformat() if nxt else None,
    }
