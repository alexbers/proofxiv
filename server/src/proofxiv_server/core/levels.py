"""Trust levels of claims and papers, and the labels shown to readers.

Everything here is derived from stored facts (runs, decisive results, reviews, events), so it
can be recomputed at any time, e.g. after a key is revoked.
"""

from __future__ import annotations

import uuid
from typing import Any

from proofxiv_protocol.enums import (
    POSITIVE_RECOMMENDATIONS,
    ClaimKind,
    DecisiveTaskKind,
    DecisiveTaskStatus,
    ExternalEventKind,
    NoveltyCategory,
    NoveltyStatus,
    PaperLabel,
    ProblemStatus,
    ReviewKind,
    ReviewOrigin,
    RunJobStatus,
    RunKind,
    Severity,
    TrustLevel,
)
from proofxiv_protocol.leanscan import is_native_decide_axiom
from proofxiv_protocol.trusted import REVIEW_TASKS
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    Claim,
    DecisiveTask,
    ExternalEvent,
    Paper,
    PaperProblem,
    Problem,
    RecordEntry,
    Review,
    Run,
    RunJob,
)
from . import audit
from .identity import now
from .problems import compare_with_records, value_to_float

OPEN_TASK_STATES = (
    DecisiveTaskStatus.BLOCKED,
    DecisiveTaskStatus.READY,
    DecisiveTaskStatus.LEASED,
)
PEER_NOVELTY_OK = {
    NoveltyCategory.NEW.value,
    NoveltyCategory.NEW_WITH_PRECEDENTS.value,
    NoveltyCategory.RECORD_IMPROVEMENT.value,
}


def job_claims(job: RunJob) -> list[str]:
    """Ids of the claims a job checks (jobs from before grouping carry only claim_id; the
    paper-level references check has none)."""
    if job.claim_ids:
        return job.claim_ids
    return [str(job.claim_id)] if job.claim_id else []


async def references_report(
    session: AsyncSession, paper_id: uuid.UUID, version: int
) -> dict[str, Any] | None:
    """The latest valid bibliography check of a paper version, if any."""
    result = (
        await session.execute(
            select(Run.result)
            .join(RunJob, RunJob.id == Run.job_id)
            .where(
                RunJob.paper_id == paper_id,
                RunJob.version == version,
                RunJob.kind == RunKind.REFERENCES,
                Run.accepted.is_(True),
                Run.invalidated.is_(False),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return (result or {}).get("references")


def reference_counts(report: dict[str, Any] | None) -> dict[str, int]:
    counts = {"verified": 0, "mismatch": 0, "not_found": 0, "unchecked": 0}
    for item in (report or {}).get("items", []):
        counts[item.get("status", "unchecked")] = counts.get(item.get("status", "unchecked"), 0) + 1
    return counts


def _claim_uses_native(run: Run, claim: Claim) -> bool:
    """Whether this claim's own theorems rely on native_decide (one Lean file may mix)."""
    theorems = ((run.result or {}).get("lean") or {}).get("theorems") or {}
    if not theorems:
        return run.native_decide
    for name in claim.lean_theorems:
        axioms = theorems.get(name)
        if axioms is None:
            axioms = next((v for k, v in theorems.items() if k.endswith("." + name)), [])
        if any(is_native_decide_axiom(a) for a in axioms):
            return True
    return False


def review_is_positive(body: dict | None) -> bool:
    if not body:
        return False
    if body.get("recommendation") not in {r.value for r in POSITIVE_RECOMMENDATIONS}:
        return False
    return not any(i.get("severity") == Severity.CRITICAL.value for i in body.get("issues", []))


def declined_reviews(tasks: list[DecisiveTask], trusted_by_task: dict[Any, Review]) -> list[Review]:
    """The negative reviews of done decisive review tasks: each one alone blocks C3."""
    return [
        review
        for t in tasks
        if t.kind in REVIEW_TASKS and t.status is DecisiveTaskStatus.DONE
        if (review := trusted_by_task.get(t.id)) is not None and not review_is_positive(review.body)
    ]


async def recompute_paper(session: AsyncSession, paper: Paper) -> None:
    v = paper.current_version
    level_before = paper.level
    claims = list(
        (
            await session.execute(
                select(Claim).where(Claim.paper_id == paper.id, Claim.version == v)
            )
        ).scalars()
    )
    jobs = list(
        (
            await session.execute(
                select(RunJob).where(
                    RunJob.paper_id == paper.id, RunJob.version == v, RunJob.cancelled.is_(False)
                )
            )
        ).scalars()
    )
    runs: dict[object, list[Run]] = {}
    if jobs:
        rows = await session.execute(select(Run).where(Run.job_id.in_([j.id for j in jobs])))
        for run in rows.scalars():
            runs.setdefault(run.job_id, []).append(run)
    job_by_claim = {uuid.UUID(cid): j for j in jobs for cid in job_claims(j)}

    machine_failed = False
    native = False
    recheck = False
    for claim in claims:
        claim_labels = set(claim.labels) - {PaperLabel.RECHECK.value}
        if claim.c1_issues:
            claim.level = TrustLevel.C0
            machine_failed = machine_failed or claim.kind is not ClaimKind.INFORMAL
        elif claim.kind is ClaimKind.INFORMAL:
            claim.level = TrustLevel.C1
        else:
            job = job_by_claim.get(claim.id)
            job_runs = runs.get(job.id, []) if job else []
            valid = [r for r in job_runs if r.accepted and r.trusted and not r.invalidated]
            if valid:
                claim.level = TrustLevel.C2
                if any(_claim_uses_native(r, claim) for r in valid):
                    claim_labels.add(PaperLabel.NATIVE_DECIDE.value)
                    native = True
            else:
                claim.level = TrustLevel.C1
                if any(r.invalidated for r in job_runs):
                    claim_labels.add(PaperLabel.RECHECK.value)
                    recheck = True
                if job is not None and job.status is RunJobStatus.DONE and job_runs:
                    latest = max(job_runs, key=lambda r: r.created_at)
                    if not latest.accepted and not latest.invalidated:
                        machine_failed = True
        claim.labels = sorted(claim_labels)

    formal = [c for c in claims if c.kind is not ClaimKind.INFORMAL]
    informal = [c for c in claims if c.kind is ClaimKind.INFORMAL]
    formal_ok = all(c.level is TrustLevel.C2 for c in formal)

    tasks = list(
        (
            await session.execute(
                select(DecisiveTask).where(
                    DecisiveTask.paper_id == paper.id,
                    DecisiveTask.version == v,
                    DecisiveTask.status != DecisiveTaskStatus.CANCELLED,
                )
            )
        ).scalars()
    )
    reviews = list(
        (
            await session.execute(
                select(Review).where(
                    Review.paper_id == paper.id, Review.version == v, Review.removed.is_(False)
                )
            )
        ).scalars()
    )
    trusted_by_task = {r.decisive_task_id: r for r in reviews if r.origin is ReviewOrigin.TRUSTED}

    def done_positive(kind: DecisiveTaskKind) -> list[Review]:
        out = []
        for t in tasks:
            if t.kind is kind and t.status is DecisiveTaskStatus.DONE:
                review = trusted_by_task.get(t.id)
                if review is not None and review_is_positive(review.body):
                    out.append(review)
        return out

    # the held-out review (prompt committed by hash) must agree when the version has one
    heldout = [t for t in tasks if t.kind is DecisiveTaskKind.HELDOUT_REVIEW]
    heldout_ok = len(done_positive(DecisiveTaskKind.HELDOUT_REVIEW)) >= len(heldout)
    single_vendor = False
    if not informal:
        c3 = bool(formal) and formal_ok and bool(done_positive(DecisiveTaskKind.NOVELTY_CONSISTENCY))
    else:
        anchors = done_positive(DecisiveTaskKind.ANCHOR_REVIEW)
        c3 = (
            formal_ok
            and bool(done_positive(DecisiveTaskKind.NOVELTY_AUDIT))
            and bool(done_positive(DecisiveTaskKind.STATEMENT_AUDIT))
            and len(anchors) >= 2
        )
        if c3:
            vendors = {r.vendor for r in anchors}
            single_vendor = len(vendors) < 2
    c3 = c3 and heldout_ok

    if "false_claim_confirmed" in paper.moderation_labels:
        paper.level = TrustLevel.DISPUTED
    elif c3:
        paper.level = TrustLevel.C3
    elif formal and not informal and formal_ok:
        paper.level = TrustLevel.C2
    elif claims and all(c.level is not TrustLevel.C0 for c in claims):
        paper.level = TrustLevel.C1
    else:
        paper.level = TrustLevel.C0

    if c3:
        for claim in claims:
            claim.level = TrustLevel.C3
        if paper.verified_at is None:
            paper.verified_at = now()
    else:
        paper.verified_at = None
    if paper.level is not level_before:
        from .federation import publish_level  # local import: federation builds on this module

        publish_level(session, paper)

    # --- labels --------------------------------------------------------------------------
    by_origin = {o: [r for r in reviews if r.origin is o] for o in ReviewOrigin}
    total = len(reviews)
    labels: list[str] = []
    if total == 0:
        labels.append(PaperLabel.NO_REVIEWS.value)
    elif not by_origin[ReviewOrigin.EXCHANGE] and not by_origin[ReviewOrigin.TRUSTED]:
        labels.append(PaperLabel.AUTHOR_REVIEWS_ONLY.value)
    if total == 1:
        labels.append(PaperLabel.ONE_REVIEW.value)
    if by_origin[ReviewOrigin.EXCHANGE]:
        labels.append(PaperLabel.EXCHANGE_REVIEWS.value)
    if formal and formal_ok:
        labels.append(PaperLabel.MACHINE_VERIFIED.value)
    if c3:
        labels.append(PaperLabel.PLATFORM_VERIFIED.value)
    if any(t.status in OPEN_TASK_STATES for t in tasks):
        labels.append(PaperLabel.AWAITING_PLATFORM.value)
    if paper.hidden:
        labels.append(PaperLabel.HIDDEN.value)
    if single_vendor:
        labels.append(PaperLabel.SINGLE_VENDOR.value)
    if informal:
        labels.append(PaperLabel.INFORMAL_STEPS.value)
    if native:
        labels.append(PaperLabel.NATIVE_DECIDE.value)
    if machine_failed:
        labels.append(PaperLabel.MACHINE_CHECK_FAILED.value)
    # a decisive review said no: this version cannot be verified (a new version is checked anew)
    if not c3 and declined_reviews(tasks, trusted_by_task):
        labels.append(PaperLabel.PLATFORM_DECLINED.value)
    heldout_done = [
        trusted_by_task.get(t.id) for t in heldout if t.status is DecisiveTaskStatus.DONE
    ]
    # the other reviewers are the platform's and the participants'; the authors' praise of their
    # own paper is no disagreement with anybody
    others_positive = any(
        review_is_positive(r.body)
        for r in reviews
        if r.origin is ReviewOrigin.EXCHANGE
        or (r.origin is ReviewOrigin.TRUSTED and r.kind is not ReviewKind.HELDOUT_REVIEW)
    )
    if others_positive and any(r is not None and not review_is_positive(r.body) for r in heldout_done):
        labels.append(PaperLabel.HELDOUT_DISAGREES.value)
    refs = reference_counts(await references_report(session, paper.id, v))
    if refs["mismatch"]:
        labels.append(PaperLabel.REFERENCES_MISMATCH.value)
    elif refs["not_found"]:
        labels.append(PaperLabel.REFERENCES_UNRESOLVED.value)
    elif refs["verified"]:
        labels.append(PaperLabel.REFERENCES_VERIFIED.value)
    if recheck:
        labels.append(PaperLabel.RECHECK.value)

    events = (
        await session.execute(
            select(ExternalEvent.kind).where(
                ExternalEvent.paper_id == paper.id, ExternalEvent.confirmed.is_(True)
            )
        )
    ).scalars()
    for kind in set(events):
        if kind is ExternalEventKind.RECORD_IMPROVED:
            labels.append(PaperLabel.SUPERSEDED.value)
        elif kind is ExternalEventKind.PRIOR_ART:
            labels.append(PaperLabel.PRIOR_ART_FOUND.value)
    paper.labels = labels

    if paper.novelty_status not in (NoveltyStatus.PLATFORM_CONFIRMED, NoveltyStatus.DISPUTED):
        peer_ok = any(
            (r.body or {}).get("novelty", {}).get("category") in PEER_NOVELTY_OK
            for r in by_origin[ReviewOrigin.EXCHANGE]
        )
        paper.novelty_status = NoveltyStatus.PEER_CONFIRMED if peer_ok else NoveltyStatus.CLAIMED

    await _refresh_records(session, paper, claims, c3, recheck)
    await refresh_resolutions(session, paper)
    from .significance import update  # local import: significance builds on this module

    await update(session, paper)


async def _refresh_records(
    session: AsyncSession, paper: Paper, claims: list[Claim], c3: bool, recheck: bool
) -> None:
    """Keep the record tables in step with the paper: a platform-verified value enters the
    table as verified, a re-check unverifies it, a disputed or hidden paper loses it."""
    entries = list(
        (await session.execute(select(RecordEntry).where(RecordEntry.paper_id == paper.id))).scalars()
    )
    if paper.level is TrustLevel.DISPUTED or paper.hidden:
        for entry in entries:
            await session.delete(entry)
        return
    if recheck:
        for entry in entries:
            entry.verified = False
    if not c3:
        return
    existing = {(e.quantity_id, e.bound, e.value): e for e in entries}
    for claim in claims:
        if claim.quantity_id is None or claim.value is None or claim.bound is None:
            continue
        entry = existing.get((claim.quantity_id, claim.bound, claim.value))
        if entry is not None:
            entry.verified = True
            continue
        standing = await compare_with_records(
            session, claim.quantity_id, claim.value, claim.bound, verified_only=True
        )
        if standing["status"] == "not_better":
            continue
        session.add(
            RecordEntry(
                quantity_id=claim.quantity_id,
                bound=claim.bound,
                value=claim.value,
                value_float=value_to_float(claim.value),
                source_url=f"{get_settings().web_url}/papers/{paper.id}",
                source_title=paper.title,
                paper_id=paper.id,
                recorded_on=now().date(),
                verified=True,
            )
        )


async def refresh_resolutions(session: AsyncSession, paper: Paper) -> None:
    """A catalog problem is solved on the platform while a visible platform-verified paper
    is said to settle it by a decisive reviewer of the paper's current version."""
    await session.flush()
    linked = (
        await session.execute(
            select(Problem)
            .join(PaperProblem, PaperProblem.problem_id == Problem.id)
            .where(PaperProblem.paper_id == paper.id)
        )
    ).scalars()
    for problem in list(linked):
        solver = await _settled_by(session, problem)
        if solver is not None and problem.status in (ProblemStatus.OPEN, ProblemStatus.PROPOSED):
            # a paper that came verified from a fully trusted lab settles it "in the federation"
            by = await session.get(Paper, solver)
            from_lab = by is not None and by.origin_peer_id is not None
            problem.status = ProblemStatus.SOLVED_IN_FEDERATION if from_lab else ProblemStatus.SOLVED_ON_PLATFORM
            audit.record(
                session, None, f"problem.{problem.status.value}", "problem", problem.id, paper=str(solver)
            )
        elif solver is None and problem.status in (ProblemStatus.SOLVED_ON_PLATFORM, ProblemStatus.SOLVED_IN_FEDERATION):
            pending_confirmation = (
                await session.execute(
                    select(func.count())
                    .select_from(DecisiveTask)
                    .where(
                        DecisiveTask.problem_id == problem.id,
                        DecisiveTask.kind == DecisiveTaskKind.CONFIRM_PROBLEM,
                        DecisiveTask.status.in_(OPEN_TASK_STATES),
                    )
                )
            ).scalar_one()
            problem.status = ProblemStatus.PROPOSED if pending_confirmation else ProblemStatus.OPEN
            audit.record(session, None, "problem.reopened", "problem", problem.id, paper=str(paper.id))


async def _settled_by(session: AsyncSession, problem: Problem) -> uuid.UUID | None:
    rows = await session.execute(
        select(Paper.id, DecisiveTask.result)
        .join(PaperProblem, PaperProblem.paper_id == Paper.id)
        .join(DecisiveTask, DecisiveTask.paper_id == Paper.id)
        .join(Review, Review.decisive_task_id == DecisiveTask.id)
        .where(
            PaperProblem.problem_id == problem.id,
            Paper.level == TrustLevel.C3,
            Paper.hidden.is_(False),
            DecisiveTask.version == Paper.current_version,
            DecisiveTask.status == DecisiveTaskStatus.DONE,
            DecisiveTask.kind.in_(list(REVIEW_TASKS)),
            Review.removed.is_(False),
        )
    )
    for paper_id, result in rows.all():
        if result and problem.slug in result.get("resolves_problem_slugs", []):
            return paper_id
    return None
