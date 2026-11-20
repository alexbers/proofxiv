"""Importance score of catalog problems (design 6.5): computed from signals, never from people's
opinions alone. It orders the catalog and the literature-sync queue; it never affects trust."""

from __future__ import annotations

import math
import re
from collections import Counter
from datetime import timedelta
from typing import Any

from proofxiv_protocol.enums import (
    PaperLabel,
    ProblemStatus,
    ReviewKind,
    RunKind,
    RunVerdict,
    TrustLevel,
)
from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Attempt,
    Lease,
    OfficialPrompt,
    Paper,
    PaperProblem,
    Problem,
    Quantity,
    RecordEntry,
    Review,
    Run,
    RunJob,
    Signal,
)
from .identity import now


async def _counts(session: AsyncSession, stmt: Any) -> dict[Any, int]:
    """{key: count} from a `select(key, count(...)).group_by(key)` statement."""
    rows: list[tuple[Any, int]] = [tuple(r) for r in (await session.execute(stmt)).all()]  # type: ignore[misc]
    return dict(rows)


# curated lists a problem's sources point to: (part of the URL, weight, what readers see)
CURATED = [
    ("en.wikipedia.org", 2.0, "Famous open problem (on Wikipedia's list)"),
    ("people.maths.ox.ac.uk/greenbj", 1.5, "From Ben Green's 100 open problems"),
    ("erdosproblems.com", 1.0, "Erdős problem"),
    ("mathoverflow.net", 0.5, "Asked on MathOverflow"),
    ("oeis.org", 0.5, "Conjecture from the OEIS"),
    ("arxiv.org", 0.3, "posed in a paper"),
]
_DOLLARS = re.compile(r"\$\s?(\d[\d,]*)")


def prize_usd(text: str) -> int | None:
    """The largest dollar amount in a statement that mentions a prize (Erdős prizes etc.)."""
    if "prize" not in text.lower():
        return None
    amounts = [int(m.group(1).replace(",", "")) for m in _DOLLARS.finditer(text)]
    amounts = [a for a in amounts if a >= 10]  # "$2^n$" is mathematics, not money
    return max(amounts) if amounts else None


def external_signals(problem: Problem, shown_only: bool = False) -> tuple[float, list[str]]:
    """Importance from outside the platform (design 6.5): curated lists and prizes. With
    shown_only, weak signals ("posed in a paper") are counted but not named."""
    urls = [str(s.get("url", "")) for s in problem.sources or []]
    weight, reason = 0.0, None
    for marker, w, why in CURATED:
        if w > weight and any(marker in url for url in urls):
            weight, reason = w, why
    if reason is None and problem.title.startswith("Erdős Problem"):
        weight, reason = 1.0, "Erdős problem"
    notes = [reason] if reason and (weight >= 0.5 or not shown_only) else []
    prize = prize_usd(problem.statement_md)
    if prize:
        weight += 0.6 * math.log2(1 + prize / 100)
        notes.append(f"${prize:,} prize offered")
    return weight, notes


async def recompute_importance(session: AsyncSession) -> int:
    attempts = await _counts(session, select(Attempt.problem_id, func.count()).group_by(Attempt.problem_id))
    papers = await _counts(
        session, select(PaperProblem.problem_id, func.count()).group_by(PaperProblem.problem_id)
    )
    signals = await _counts(
        session,
        select(PaperProblem.problem_id, func.count(Signal.id))
        .join(Signal, Signal.paper_id == PaperProblem.paper_id)
        .group_by(PaperProblem.problem_id),
    )
    leases = await _counts(session, select(Lease.problem_id, func.count()).group_by(Lease.problem_id))
    with_quantities = set((await session.execute(select(Quantity.problem_id).distinct())).scalars())
    updated = 0
    for problem in (await session.execute(select(Problem))).scalars():
        score = (
            external_signals(problem)[0]
            + 1.0 * math.log1p(attempts.get(problem.id, 0))
            + 1.5 * math.log1p(papers.get(problem.id, 0))
            + 0.7 * math.log1p(signals.get(problem.id, 0))
            + 0.5 * math.log1p(leases.get(problem.id, 0))
            + (0.5 if problem.id in with_quantities else 0.0)
            + 0.2 * min(len(problem.sources or []), 3)
            + (0.3 if problem.lean_statement else 0.0)
        )
        score = round(score, 4)
        if problem.importance != score:
            problem.importance = score
            updated += 1
    return updated


async def state_of_field(session: AsyncSession, domain: str | None = None, days: int = 30) -> dict[str, Any]:
    """'State of the field': what was solved or improved, where work goes on, what resists."""
    since = now() - timedelta(days=days)
    in_domain = Problem.domain == domain if domain else true()
    base = select(Problem).where(Problem.hidden.is_(False), in_domain)

    def brief(p: Problem, **counts: int) -> dict[str, Any]:
        return {
            "slug": p.slug,
            "title": p.title,
            "variant": p.variant,
            "status": p.status.value,
            "domain": p.domain,
            **counts,
        }

    solved = (
        await session.execute(
            base.where(
                Problem.status.in_(
                    [ProblemStatus.SOLVED_ON_PLATFORM, ProblemStatus.SOLVED_EXTERNALLY, ProblemStatus.SOLVED_IN_FEDERATION]
                ),
                Problem.updated_at >= since,
            ).limit(50)
        )
    ).scalars()
    active_counts = (
        select(Lease.problem_id, func.count().label("n"))
        .where(Lease.released_at.is_(None), Lease.expires_at > now())
        .group_by(Lease.problem_id)
        .subquery()
    )
    active = (
        await session.execute(
            base.add_columns(active_counts.c.n)
            .join(active_counts, active_counts.c.problem_id == Problem.id)
            .order_by(active_counts.c.n.desc())
            .limit(20)
        )
    ).all()
    attempt_counts = (
        select(Attempt.problem_id, func.count().label("n")).group_by(Attempt.problem_id).subquery()
    )
    resisting = (
        await session.execute(
            base.add_columns(attempt_counts.c.n)
            .join(attempt_counts, attempt_counts.c.problem_id == Problem.id)
            .where(Problem.status == ProblemStatus.OPEN)
            .order_by(attempt_counts.c.n.desc())
            .limit(10)
        )
    ).all()
    # improvements only: values from confirmed literature events or platform-verified papers,
    # not the starting values typed into an import or a proposal
    records = (
        await session.execute(
            select(RecordEntry, Quantity, Problem)
            .join(Quantity, Quantity.id == RecordEntry.quantity_id)
            .join(Problem, Problem.id == Quantity.problem_id)
            .where(
                RecordEntry.created_at >= since,
                RecordEntry.verified.is_(True),
                or_(RecordEntry.paper_id.is_not(None), RecordEntry.event_id.is_not(None)),
                Problem.hidden.is_(False),
                in_domain,
            )
            .order_by(RecordEntry.created_at.desc())
            .limit(30)
        )
    ).all()
    verified_query = (
        select(Paper)
        .where(Paper.level == TrustLevel.C3, Paper.verified_at >= since, Paper.hidden.is_(False))
        .order_by(Paper.verified_at.desc())
        .limit(30)
    )
    if domain:
        verified_query = verified_query.where(
            Paper.id.in_(
                select(PaperProblem.paper_id)
                .join(Problem, Problem.id == PaperProblem.problem_id)
                .where(Problem.domain == domain)
            )
        )
    verified = (await session.execute(verified_query)).scalars()
    by_status = dict(
        (
            await session.execute(
                select(Problem.status, func.count())
                .where(Problem.hidden.is_(False), in_domain)
                .group_by(Problem.status)
            )
        ).all()
    )
    return {
        "domain": domain,
        "since": since.isoformat(),
        "problems_by_status": {k.value: v for k, v in by_status.items()},
        "recently_solved": [brief(p) for p in solved],
        "active": [brief(p, active_leases=n) for p, n in active],
        "resisting": [brief(p, attempts=n) for p, n in resisting],
        "record_improvements": [
            {
                "problem": p.slug,
                "problem_title": p.title,
                "quantity": q.name,
                "bound": r.bound.value,
                "value": r.value,
                "source": r.source_url,
                "paper_id": str(r.paper_id) if r.paper_id else None,
            }
            for r, q, p in records
        ],
        "verified_papers": [{"id": str(p.id), "title": p.title} for p in verified],
        "integrity": await integrity(session),
    }


async def integrity(session: AsyncSession) -> dict[str, int]:
    """Platform-wide counters of what was checked and what was caught (ScientistTwo-style
    integrity audit, but public): machine checks, bibliographies, statement audits, held-out
    reviews."""
    from .levels import review_is_positive

    async def count(stmt: Any) -> int:
        return (await session.execute(stmt)).scalar_one()

    valid = Run.invalidated.is_(False)
    machine = RunJob.kind.in_([RunKind.LEAN, RunKind.CHECKER])
    refs = Counter()
    for result in (
        await session.execute(
            select(Run.result)
            .join(RunJob, RunJob.id == Run.job_id)
            .where(RunJob.kind == RunKind.REFERENCES, Run.accepted.is_(True), valid)
        )
    ).scalars():
        for item in ((result or {}).get("references") or {}).get("items", []):
            refs[item.get("status", "unchecked")] += 1
    audits = [
        body
        for body in (
            await session.execute(
                select(Review.body).where(
                    Review.kind == ReviewKind.STATEMENT_AUDIT, Review.removed.is_(False)
                )
            )
        ).scalars()
    ]
    heldout = [
        body
        for body in (
            await session.execute(
                select(Review.body).where(
                    Review.kind == ReviewKind.HELDOUT_REVIEW, Review.removed.is_(False)
                )
            )
        ).scalars()
    ]
    return {
        "machine_checks_passed": await count(
            select(func.count()).select_from(Run).join(RunJob, RunJob.id == Run.job_id).where(
                machine, valid, Run.accepted.is_(True)
            )
        ),
        "machine_checks_failed": await count(
            select(func.count()).select_from(Run).join(RunJob, RunJob.id == Run.job_id).where(
                machine, valid, Run.accepted.is_(False), Run.verdict == RunVerdict.FAIL
            )
        ),
        "references_checked": sum(refs.values()),
        "references_verified": refs["verified"],
        "references_mismatch": refs["mismatch"],
        "references_not_found": refs["not_found"],
        "statement_audits": len(audits),
        "statement_audits_failed": sum(1 for b in audits if not review_is_positive(b)),
        "heldout_reviews": len(heldout),
        "heldout_negative": sum(1 for b in heldout if not review_is_positive(b)),
        "heldout_disagreements": await count(
            select(func.count()).select_from(Paper).where(
                Paper.hidden.is_(False),
                Paper.labels.contains([PaperLabel.HELDOUT_DISAGREES.value]),
            )
        ),
        "prompts_held_out": await count(
            select(func.count()).select_from(OfficialPrompt).where(OfficialPrompt.text.is_(None))
        ),
        "prompts_revealed": await count(
            select(func.count()).select_from(OfficialPrompt).where(OfficialPrompt.revealed_at.is_not(None))
        ),
    }
