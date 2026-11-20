"""Read-side queries for the web UI and agents: feed, paper pages, profiles, journals."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from proofxiv_protocol.enums import (
    DecisiveTaskStatus,
    PaperLabel,
    ReportTarget,
    ReviewOrigin,
    Role,
    SignalKind,
    TrustLevel,
)
from proofxiv_protocol.reviews import ReviewBody
from pydantic import ValidationError
from sqlalchemy import Select, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Artifact,
    Claim,
    DecisiveTask,
    ExternalEvent,
    ModerationLogEntry,
    NoveltyChange,
    Operator,
    OwnerStatementRecord,
    Paper,
    PaperProblem,
    PaperVersion,
    Peer,
    Problem,
    Review,
    RoleGrant,
    Run,
    RunJob,
    Signal,
    SigningKey,
    WeeklyRun,
)
from .decisive import next_check_date
from .errors import NotFound
from .exchange import exchange_summary
from .gamify import progress
from .identity import Principal, active_roles
from .levels import job_claims, reference_counts, references_report, review_is_positive

INDEPENDENT = (ReviewOrigin.EXCHANGE, ReviewOrigin.TRUSTED)
log = structlog.get_logger()


async def _counts(session: AsyncSession, paper_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    out: dict[uuid.UUID, dict] = {
        pid: {"likes": 0, "follows": 0, "reviews": {}, "claims": {}, "records": 0} for pid in paper_ids
    }
    if not paper_ids:
        return out
    current = (Claim.paper_id == Paper.id) & (Claim.version == Paper.current_version)
    for pid, level, n in (
        await session.execute(
            select(Claim.paper_id, Claim.level, func.count())
            .join(Paper, current)
            .where(Claim.paper_id.in_(paper_ids))
            .group_by(Claim.paper_id, Claim.level)
        )
    ).all():
        out[pid]["claims"][level.value] = n
    for pid, n in (
        await session.execute(
            select(Claim.paper_id, func.count())
            .join(Paper, current)
            .where(
                Claim.paper_id.in_(paper_ids),
                Claim.record_status["status"].astext == "new_record",
            )
            .group_by(Claim.paper_id)
        )
    ).all():
        out[pid]["records"] = n
    for pid, kind, n in (
        await session.execute(
            select(Signal.paper_id, Signal.kind, func.count())
            .where(Signal.paper_id.in_(paper_ids))
            .group_by(Signal.paper_id, Signal.kind)
        )
    ).all():
        out[pid]["likes" if kind is SignalKind.LIKE else "follows"] = n
    for pid, origin, n in (
        await session.execute(
            select(Review.paper_id, Review.origin, func.count())
            .join(Paper, Paper.id == Review.paper_id)
            .where(
                Review.paper_id.in_(paper_ids),
                Review.removed.is_(False),
                Review.version == Paper.current_version,
            )
            .group_by(Review.paper_id, Review.origin)
        )
    ).all():
        out[pid]["reviews"][origin.value] = n
    for pid, name, url in (
        await session.execute(
            select(Paper.id, Peer.name, Peer.base_url)
            .join(Peer, Peer.id == Paper.origin_peer_id)
            .where(Paper.id.in_(paper_ids))
        )
    ).all():
        out[pid]["origin_lab"] = name or url
    return out


def paper_summary(paper: Paper, counts: dict, next_check: str | None) -> dict[str, Any]:
    return {
        "id": str(paper.id),
        "title": paper.title,
        "abstract": paper.abstract,
        "byline": paper.byline,
        "level": paper.level.value,
        "labels": paper.labels,
        "moderation_labels": paper.moderation_labels,
        "novelty_category": paper.novelty_category.value if paper.novelty_category else None,
        "novelty_status": paper.novelty_status.value,
        "version": paper.current_version,
        "hidden": paper.hidden,
        "hidden_reason": paper.hidden_reason,
        "created_at": paper.created_at.isoformat(),
        "verified_at": paper.verified_at.isoformat() if paper.verified_at else None,
        "likes": counts["likes"],
        "follows": counts["follows"],
        "review_counts": counts["reviews"],
        "next_platform_check": (
            next_check if PaperLabel.AWAITING_PLATFORM.value in paper.labels else None
        ),
        "significance": paper.significance,
        "significance_notes": paper.significance_notes,
        "tier": paper.tier,
        "tier_basis": paper.tier_basis,
        "claim_levels": counts["claims"],
        "record_claims": counts["records"],
        "origin_lab": counts.get("origin_lab"),
    }


def _feed_query(
    q: str | None,
    independent_only: bool,
    verified_only: bool,
    problem: str | None,
    include_hidden: bool,
    submitter: str | None,
) -> Select[Paper]:
    stmt = select(Paper)
    if not include_hidden:
        stmt = stmt.where(Paper.hidden.is_(False))
    if verified_only:
        stmt = stmt.where(Paper.level == TrustLevel.C3)
    if independent_only:
        # the same rule as the labels: independent reviews of the current version
        stmt = stmt.where(
            exists().where(
                Review.paper_id == Paper.id,
                Review.version == Paper.current_version,
                Review.origin.in_(INDEPENDENT),
                Review.removed.is_(False),
            )
        )
    if problem:
        stmt = stmt.where(
            Paper.id.in_(
                select(PaperProblem.paper_id)
                .join(Problem, Problem.id == PaperProblem.problem_id)
                .where(Problem.slug == problem)
            )
        )
    if submitter:
        stmt = stmt.where(
            Paper.submitter_id.in_(select(Operator.id).where(Operator.login == submitter))
        )
    if q:
        tsv = func.to_tsvector("simple", Paper.title + " " + Paper.abstract)
        stmt = stmt.where(tsv.op("@@")(func.websearch_to_tsquery("simple", q)))
    return stmt


async def feed_total(
    session: AsyncSession,
    q: str | None = None,
    independent_only: bool = False,
    verified_only: bool = False,
    problem: str | None = None,
    include_hidden: bool = False,
    submitter: str | None = None,
) -> int:
    filtered = _feed_query(
        q, independent_only, verified_only, problem, include_hidden, submitter
    ).subquery()
    return (await session.execute(select(func.count()).select_from(filtered))).scalar_one()


async def feed(
    session: AsyncSession,
    q: str | None = None,
    independent_only: bool = False,
    verified_only: bool = False,
    problem: str | None = None,
    sort: str = "new",
    include_hidden: bool = False,
    limit: int = 30,
    offset: int = 0,
    submitter: str | None = None,
) -> list[dict[str, Any]]:
    stmt = _feed_query(q, independent_only, verified_only, problem, include_hidden, submitter)
    if sort == "attention":
        likes = (
            select(func.count())
            .select_from(Signal)
            .where(Signal.paper_id == Paper.id)
            .correlate(Paper)
            .scalar_subquery()
        )
        stmt = stmt.order_by(likes.desc(), Paper.created_at.desc())
    elif sort == "notable":
        stmt = stmt.order_by(Paper.significance.desc(), Paper.created_at.desc())
    elif sort == "verified":
        stmt = stmt.order_by(Paper.verified_at.desc().nulls_last(), Paper.created_at.desc())
    else:
        stmt = stmt.order_by(Paper.created_at.desc())
    papers = list((await session.execute(stmt.limit(min(limit, 100)).offset(offset))).scalars())
    counts = await _counts(session, [p.id for p in papers])
    nxt = await next_check_date(session)
    nxt_s = nxt.isoformat() if nxt else None
    return [paper_summary(p, counts[p.id], nxt_s) for p in papers]


async def paper_detail(
    session: AsyncSession, paper_id: uuid.UUID, viewer: Principal | None
) -> dict[str, Any]:
    paper = await session.get(Paper, paper_id)
    if paper is None:
        raise NotFound("paper not found")
    counts = await _counts(session, [paper.id])
    nxt = await next_check_date(session)
    out = paper_summary(paper, counts[paper.id], nxt.isoformat() if nxt else None)
    submitter = await session.get(Operator, paper.submitter_id)
    out["submitter"] = submitter.login if submitter else None
    from .federation import origin_of  # local import: federation builds on papers and levels

    out["origin"] = await origin_of(session, paper)
    out["ai_contribution"] = paper.ai_contribution.value
    out["human_involvement"] = paper.human_involvement

    versions = list(
        (
            await session.execute(
                select(PaperVersion)
                .where(PaperVersion.paper_id == paper.id)
                .order_by(PaperVersion.version)
            )
        ).scalars()
    )
    current = versions[-1]
    sizes = {
        a.sha256: a.size
        for a in (
            await session.execute(
                select(Artifact).where(Artifact.sha256.in_(list(current.files.values())))
            )
        ).scalars()
    }
    out["versions"] = [
        {"version": v.version, "created_at": v.created_at.isoformat(), "notes": v.notes}
        for v in versions
    ]
    out["manifest"] = current.manifest
    out["files"] = [
        {"path": p, "sha256": s, "size": sizes.get(s)} for p, s in sorted(current.files.items())
    ]
    out["pdf_sha256"] = current.pdf_sha256

    claims = list(
        (
            await session.execute(
                select(Claim)
                .where(Claim.paper_id == paper.id, Claim.version == paper.current_version)
                .order_by(Claim.key)
            )
        ).scalars()
    )
    jobs = {
        uuid.UUID(cid): j
        for j in (
            await session.execute(
                select(RunJob).where(
                    RunJob.paper_id == paper.id, RunJob.version == paper.current_version
                )
            )
        ).scalars()
        for cid in job_claims(j)
    }
    claim_items = []
    for c in claims:
        job = jobs.get(c.id)
        runs = []
        if job is not None:
            for r in (
                await session.execute(
                    select(Run).where(Run.job_id == job.id).order_by(Run.created_at)
                )
            ).scalars():
                runner = await session.get(Operator, r.runner_id)
                runs.append(
                    {
                        "id": str(r.id),
                        "runner": runner.login if runner else None,
                        "verdict": r.verdict.value,
                        "accepted": r.accepted,
                        "invalidated": r.invalidated,
                        "native_decide": r.native_decide,
                        "evaluation": r.result.get("evaluation"),
                        "lean": r.result.get("lean"),
                        "checker": r.result.get("checker"),
                        "log_sha256": r.result.get("log_sha256"),
                        "bundle": r.bundle,
                        "created_at": r.created_at.isoformat(),
                    }
                )
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
                "job_status": job.status.value if job else None,
                "runs": runs,
            }
        )
    out["claims"] = claim_items

    reviews_list = list(
        (
            await session.execute(
                select(Review)
                .where(Review.paper_id == paper.id)
                .order_by(Review.version, Review.created_at)
            )
        ).scalars()
    )
    review_items = []
    for r in reviews_list:
        reviewer = await session.get(Operator, r.reviewer_id)
        review_items.append(
            {
                "id": str(r.id),
                "version": r.version,
                "origin": r.origin.value,
                "kind": r.kind.value,
                "reviewer": reviewer.login if reviewer else None,
                "model": r.declared_model,
                "harness": r.harness,
                "vendor": r.vendor,
                "adversarial": r.adversarial,
                "body": None if r.removed else _review_body(r.body),
                "body_md": None if r.removed else r.body_md,
                "transcript_sha256": r.transcript_sha256,
                "signed": r.bundle is not None,
                "useful": r.useful,
                "author_response_md": r.author_response_md,
                "removed": r.removed,
                "removed_reason": r.removed_reason,
                "created_at": r.created_at.isoformat(),
            }
        )
    embargoed = set(
        (
            await session.execute(
                select(Artifact.sha256).where(
                    Artifact.sha256.in_([r["transcript_sha256"] for r in review_items if r["transcript_sha256"]]),
                    Artifact.embargoed.is_(True),
                )
            )
        ).scalars()
    )
    for item in review_items:
        item["transcript_embargoed"] = item["transcript_sha256"] in embargoed
    out["reviews"] = review_items
    # the decisive reviews that said no to this version (each one alone blocks verification)
    out["declined_by"] = [
        item["id"]
        for item, r in zip(review_items, reviews_list, strict=True)
        if PaperLabel.PLATFORM_DECLINED.value in paper.labels
        and r.version == paper.current_version
        and r.origin is ReviewOrigin.TRUSTED
        and r.decisive_task_id is not None
        and not r.removed
        and not review_is_positive(r.body)
    ]
    report = await references_report(session, paper.id, paper.current_version)
    out["references"] = (
        {"counts": reference_counts(report), "items": report.get("items", [])} if report else None
    )

    out["novelty_history"] = [
        {
            "category": n.category.value if n.category else None,
            "status": n.status.value,
            "reason": n.reason,
            "created_at": n.created_at.isoformat(),
        }
        for n in (
            await session.execute(
                select(NoveltyChange)
                .where(NoveltyChange.paper_id == paper.id)
                .order_by(NoveltyChange.created_at)
            )
        ).scalars()
    ]
    out["pending_tasks"] = [
        {
            "kind": t.kind.value,
            "status": t.status.value,
            "blocked_reason": t.blocked_reason,
            "adversarial": t.adversarial,
        }
        for t in (
            await session.execute(
                select(DecisiveTask).where(
                    DecisiveTask.paper_id == paper.id,
                    DecisiveTask.version == paper.current_version,
                    DecisiveTask.status.in_(
                        [
                            DecisiveTaskStatus.BLOCKED,
                            DecisiveTaskStatus.READY,
                            DecisiveTaskStatus.LEASED,
                        ]
                    ),
                )
            )
        ).scalars()
    ]
    out["problems"] = [
        {"slug": p.slug, "title": p.title, "variant": p.variant, "status": p.status.value}
        for p in (
            await session.execute(
                select(Problem)
                .join(PaperProblem, PaperProblem.problem_id == Problem.id)
                .where(PaperProblem.paper_id == paper.id)
            )
        ).scalars()
    ]
    out["events"] = [
        {
            "kind": e.kind.value,
            "summary": e.summary,
            "source_url": e.source_url,
            "confirmed": e.confirmed,
        }
        for e in (
            await session.execute(
                select(ExternalEvent)
                .where(ExternalEvent.paper_id == paper.id)
                .order_by(ExternalEvent.created_at.desc())
            )
        ).scalars()
    ]
    if viewer is not None:
        mine = (
            await session.execute(
                select(Signal.kind).where(
                    Signal.paper_id == paper.id, Signal.operator_id == viewer.id
                )
            )
        ).scalars()
        out["my_signals"] = [k.value for k in mine]
        out["is_submitter"] = viewer.id == paper.submitter_id
    return out


async def trusted_list(session: AsyncSession) -> list[dict[str, Any]]:
    grants = (
        await session.execute(select(RoleGrant).order_by(RoleGrant.created_at))
    ).scalars()
    by_operator: dict[uuid.UUID, dict[str, Any]] = {}
    for g in grants:
        operator = await session.get(Operator, g.operator_id)
        if operator is None:
            continue
        entry = by_operator.setdefault(
            operator.id,
            {"login": operator.login, "roles": [], "history": [], "keys": []},
        )
        entry["history"].append(
            {
                "role": g.role.value,
                "granted_at": g.created_at.isoformat(),
                "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
            }
        )
        if g.revoked_at is None and g.role.value not in entry["roles"]:
            entry["roles"].append(g.role.value)
    for operator_id, entry in by_operator.items():
        keys = (
            await session.execute(select(SigningKey).where(SigningKey.operator_id == operator_id))
        ).scalars()
        entry["keys"] = [
            {
                "fingerprint": k.fingerprint,
                "label": k.label,
                "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            }
            for k in keys
        ]
        entry["stats"] = {
            "decisive_done": (
                await session.execute(
                    select(func.count())
                    .select_from(DecisiveTask)
                    .where(DecisiveTask.leased_by == operator_id, DecisiveTask.status == DecisiveTaskStatus.DONE)
                )
            ).scalar_one(),
            "runs": (
                await session.execute(
                    select(func.count()).select_from(Run).where(Run.runner_id == operator_id)
                )
            ).scalar_one(),
        }
    return list(by_operator.values())


async def operator_profile(session: AsyncSession, login: str) -> dict[str, Any]:
    operator = (
        await session.execute(select(Operator).where(Operator.login == login))
    ).scalar_one_or_none()
    if operator is None:
        raise NotFound("operator not found")
    papers = (
        await session.execute(
            select(func.count()).select_from(Paper).where(Paper.submitter_id == operator.id)
        )
    ).scalar_one()
    roles = sorted(r.value for r in await active_roles(session, operator.id))
    return {
        "login": operator.login,
        "display_name": operator.display_name,
        "created_at": operator.created_at.isoformat(),
        "papers": papers,
        "roles": roles,
        "trusted": bool(set(roles) & {Role.REVIEWER.value, Role.RUNNER.value}),
        "exchange": await exchange_summary(session, operator.id),
        "progress": await progress(session, operator),
    }


async def owner_journal(session: AsyncSession, limit: int = 200) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(OwnerStatementRecord).order_by(OwnerStatementRecord.created_at.desc()).limit(limit)
        )
    ).scalars()
    return [
        {
            "id": str(r.id),
            "action": r.action,
            "payload": r.payload,
            "signature": r.signature,
            "key_fingerprint": r.key_fingerprint,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


async def digests(session: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(WeeklyRun)
            .where(WeeklyRun.digest_md.is_not(None))
            .order_by(WeeklyRun.created_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        {
            "id": str(r.id),
            "started_at": r.created_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "digest_md": r.digest_md,
            "signed": r.digest_bundle is not None,
        }
        for r in rows
    ]


def _review_body(body: dict | None) -> dict | None:
    """Stored bodies were validated on submission; one that no longer fits the schema is
    left out rather than breaking the whole paper page (the Markdown text stays)."""
    if body is None:
        return None
    try:
        return ReviewBody.model_validate(body).model_dump(mode="json")
    except ValidationError:
        log.warning("review body does not fit the schema", keys=sorted(body))
        return None


async def moderation_log(session: AsyncSession, limit: int = 100) -> list[dict[str, Any]]:
    """The public moderation log, with enough context to link each entry to its page."""
    entries = list(
        (
            await session.execute(
                select(ModerationLogEntry).order_by(ModerationLogEntry.created_at.desc()).limit(limit)
            )
        ).scalars()
    )

    def ids(kind: ReportTarget) -> set[uuid.UUID]:
        return {e.target_id for e in entries if e.target_type is kind}

    reviews = {
        r.id: r.paper_id
        for r in (
            await session.execute(select(Review).where(Review.id.in_(ids(ReportTarget.REVIEW))))
        ).scalars()
    }
    paper_ids = ids(ReportTarget.PAPER) | set(reviews.values())
    titles = {
        pid: title
        for pid, title in (
            await session.execute(select(Paper.id, Paper.title).where(Paper.id.in_(paper_ids)))
        ).all()
    }
    problems = {
        p.id: p
        for p in (
            await session.execute(select(Problem).where(Problem.id.in_(ids(ReportTarget.PROBLEM))))
        ).scalars()
    }
    out = []
    for e in entries:
        paper_id = (
            e.target_id
            if e.target_type is ReportTarget.PAPER
            else reviews.get(e.target_id)
            if e.target_type is ReportTarget.REVIEW
            else None
        )
        problem = problems.get(e.target_id) if e.target_type is ReportTarget.PROBLEM else None
        out.append(
            {
                "target_type": e.target_type.value,
                "target_id": str(e.target_id),
                "action": e.action.value,
                "reason": e.reason,
                "actor": e.actor,
                "created_at": e.created_at.isoformat(),
                "paper_id": str(paper_id) if paper_id else None,
                "problem_slug": problem.slug if problem else None,
                "title": problem.title if problem else titles.get(paper_id) if paper_id else None,
            }
        )
    return out
