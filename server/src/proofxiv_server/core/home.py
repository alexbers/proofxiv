"""Everything the front page shows, in one query: what the site is about at a glance (numbers),
the most notable recent results, the record board, solved and most wanted problems."""

from __future__ import annotations

import uuid
from typing import Any

from proofxiv_protocol.enums import Bound, PaperLabel, ProblemStatus, TrustLevel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    Attempt,
    AuditEvent,
    Claim,
    Lease,
    Paper,
    PaperProblem,
    Problem,
    Quantity,
    RecordEntry,
    WeeklyRun,
)
from .decisive import next_check_date
from .errors import Invalid
from .gamify import bounty, happenings, leaderboard, ledger
from .identity import now
from .importance import external_signals, prize_usd
from .levels import _settled_by
from .precheck import possibly_settled_ids
from .problems import best_records, value_to_float
from .significance import trending

CHECKED = (TrustLevel.C2, TrustLevel.C3, TrustLevel.C4)
VERIFIED = (TrustLevel.C3, TrustLevel.C4)
SIGN = {Bound.LOWER: "≥", Bound.UPPER: "≤", Bound.EXACT: "="}


def short_name(name: str) -> str:
    """'K_∞ = sup_d K_d (best constant in …)' -> 'K_∞ = sup_d K_d' for headlines."""
    if len(name) > 24 and name.endswith(")") and " (" in name:
        return name[: name.index(" (")]
    return name


def _relative_gain(status: dict[str, Any]) -> float:
    previous = status.get("previous")
    if previous is None:  # no earlier value to compare with
        return 0.0
    try:
        base = abs(value_to_float(str(previous)))
    except Invalid:
        return 0.0
    return abs(float(status.get("delta") or 0.0)) / base if base else 0.0


async def headlines(session: AsyncSession, paper: Paper) -> list[str]:
    """Numeric results in brief ('R(5,5;3) ≥ 98 (previous best 88)'), the biggest step first.
    Empty for other papers: their title says what they prove."""
    rows = (
        await session.execute(
            select(Claim, Quantity)
            .join(Quantity, Quantity.id == Claim.quantity_id)
            .where(Claim.paper_id == paper.id, Claim.version == paper.current_version)
        )
    ).all()
    numeric = [(c, q) for c, q in rows if c.value and c.bound]
    numeric.sort(
        key=lambda cq: (
            (cq[0].record_status or {}).get("status") == "new_record",
            _relative_gain(cq[0].record_status or {}),
        ),
        reverse=True,
    )
    out: list[str] = []
    for claim, quantity in numeric[:3]:
        assert claim.bound is not None
        text = f"{short_name(quantity.name)} {SIGN[claim.bound]} {claim.value}"
        status = claim.record_status or {}
        if status.get("status") == "new_record" and status.get("previous"):
            text += f" (previous best {status['previous']})"
        out.append(text)
    return out


async def _paper_problems(session: AsyncSession, paper_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Problem)
            .join(PaperProblem, PaperProblem.problem_id == Problem.id)
            .where(PaperProblem.paper_id == paper_id, Problem.hidden.is_(False))
            .order_by(Problem.importance.desc())
        )
    ).scalars()
    return [{"slug": p.slug, "title": p.title, "status": p.status.value} for p in rows]


async def _stats(session: AsyncSession) -> dict[str, int]:
    async def count(stmt: Any) -> int:
        return (await session.execute(stmt)).scalar_one()

    visible = Paper.hidden.is_(False)
    return {
        "open_problems": await count(
            select(func.count()).select_from(Problem).where(
                Problem.hidden.is_(False), Problem.status == ProblemStatus.OPEN
            )
        ),
        "solved_on_platform": await count(
            select(func.count()).select_from(Problem).where(Problem.status == ProblemStatus.SOLVED_ON_PLATFORM)
        ),
        "papers": await count(select(func.count()).select_from(Paper).where(visible)),
        "machine_checked": await count(
            select(func.count(func.distinct(Claim.paper_id)))
            .join(Paper, Paper.id == Claim.paper_id)
            .where(visible, Claim.version == Paper.current_version, Claim.level.in_(CHECKED))
        ),
        "platform_verified": await count(
            select(func.count()).select_from(Paper).where(visible, Paper.level.in_(VERIFIED))
        ),
        # problems, not claims: one paper improving a table of 191 entries is one record broken here
        "records_broken": await count(
            select(func.count(func.distinct(Quantity.problem_id)))
            .select_from(Claim)
            .join(Paper, Paper.id == Claim.paper_id)
            .join(Quantity, Quantity.id == Claim.quantity_id)
            .where(
                visible,
                Claim.level.in_(CHECKED),
                Claim.version == Paper.current_version,
                Claim.record_status["status"].astext == "new_record",
            )
        ),
        "agents_working": await count(
            select(func.count(func.distinct(Lease.agent_id))).where(
                Lease.released_at.is_(None), Lease.expires_at > now()
            )
        ),
        "contributors": await count(select(func.count(func.distinct(Paper.submitter_id))).where(visible)),
    }


async def _notable(session: AsyncSession, limit: int) -> list[dict[str, Any]]:
    from .views import (  # local import: views imports decisive, which is heavy
        _counts,
        paper_summary,
    )

    candidates = list(
        (
            await session.execute(
                select(Paper)
                .where(
                    Paper.hidden.is_(False),
                    Paper.significance > 0,
                    Paper.level.in_([TrustLevel.C1, *CHECKED]),
                )
                .order_by(Paper.significance.desc())
                .limit(100)
            )
        ).scalars()
    )
    at = now()
    chosen = sorted(candidates, key=lambda p: trending(p, at), reverse=True)[:limit]
    counts = await _counts(session, [p.id for p in chosen])
    nxt = await next_check_date(session)
    out = []
    for paper in chosen:
        item = paper_summary(paper, counts[paper.id], nxt.isoformat() if nxt else None)
        item["headlines"] = await headlines(session, paper)
        item["problems"] = await _paper_problems(session, paper.id)
        out.append(item)
    return out


async def _record_board(session: AsyncSession, limit: int) -> list[dict[str, Any]]:
    """Latest improvements: verified entries of the record tables (platform papers and the
    literature) and machine-checked claims still waiting for the platform's verdict."""
    web = get_settings().web_url
    items: list[dict[str, Any]] = []
    entries = (
        await session.execute(
            select(RecordEntry, Quantity, Problem)
            .join(Quantity, Quantity.id == RecordEntry.quantity_id)
            .join(Problem, Problem.id == Quantity.problem_id)
            .where(
                RecordEntry.verified.is_(True),
                (RecordEntry.paper_id.is_not(None)) | (RecordEntry.event_id.is_not(None)),
                Problem.hidden.is_(False),
            )
            .order_by(RecordEntry.created_at.desc())
            .limit(limit)
        )
    ).all()
    for entry, quantity, problem in entries:
        best_now = await best_records(session, quantity.id)
        previous = await _previous_best(session, quantity.id, entry.bound, entry.created_at)
        items.append(
            {
                "problem_slug": problem.slug,
                "problem_title": problem.title,
                "quantity": quantity.name,
                "bound": entry.bound.value,
                "value": entry.value,
                "previous": previous,
                "gain": value_gain(previous, entry.value),
                "still_best": best_now.get(entry.bound.value) is entry,
                "kind": "platform" if entry.paper_id else "literature",
                "paper_id": str(entry.paper_id) if entry.paper_id else None,
                "source_url": entry.source_url if not entry.paper_id else f"{web}/papers/{entry.paper_id}",
                "at": entry.created_at.isoformat(),
            }
        )
    # the two newest machine-checked record claims of each problem (a paper may carry hundreds)
    row = func.row_number().over(
        partition_by=Problem.id, order_by=(Paper.created_at.desc(), Claim.key)
    )
    newest = (
        select(Claim.id.label("claim_id"), row.label("row"))
        .join(Quantity, Quantity.id == Claim.quantity_id)
        .join(Problem, Problem.id == Quantity.problem_id)
        .join(Paper, Paper.id == Claim.paper_id)
        .where(
            Paper.hidden.is_(False),
            Problem.hidden.is_(False),
            Claim.level == TrustLevel.C2,  # this claim is machine-checked; the paper may not be C3 yet
            Claim.version == Paper.current_version,
            Claim.record_status["status"].astext == "new_record",
        )
        .subquery()
    )
    pending = (
        await session.execute(
            select(Claim, Quantity, Problem, Paper)
            .join(Quantity, Quantity.id == Claim.quantity_id)
            .join(Problem, Problem.id == Quantity.problem_id)
            .join(Paper, Paper.id == Claim.paper_id)
            .where(Claim.id.in_(select(newest.c.claim_id).where(newest.c.row <= 2)))
            .order_by(Paper.created_at.desc())
            .limit(limit * 2)
        )
    ).all()
    for claim, quantity, problem, paper in pending:
        assert claim.bound is not None and claim.value is not None
        items.append(
            {
                "problem_slug": problem.slug,
                "problem_title": problem.title,
                "quantity": quantity.name,
                "bound": claim.bound.value,
                "value": claim.value,
                "previous": (claim.record_status or {}).get("previous"),
                "gain": value_gain((claim.record_status or {}).get("previous"), claim.value),
                "still_best": True,
                # still in the platform's queue, or reviewed and not verified (e.g. the held-out
                # review disagrees)
                "kind": "machine_checked"
                if PaperLabel.AWAITING_PLATFORM.value in (paper.labels or [])
                else "machine_checked_unverified",
                "paper_id": str(paper.id),
                "source_url": f"{web}/papers/{paper.id}",
                "at": paper.created_at.isoformat(),
            }
        )
    items.sort(key=lambda i: i["at"], reverse=True)
    # at most two rows per problem, so one paper with many records does not fill the board
    shown: dict[str, int] = {}
    varied = []
    for item in items:
        shown[item["problem_slug"]] = shown.get(item["problem_slug"], 0) + 1
        if shown[item["problem_slug"]] <= 2:
            varied.append(item)
    return varied[:limit]


async def _previous_best(
    session: AsyncSession, quantity_id: uuid.UUID, bound: Bound, before: Any
) -> str | None:
    rows = (
        await session.execute(
            select(RecordEntry.value, RecordEntry.value_float).where(
                RecordEntry.quantity_id == quantity_id,
                RecordEntry.bound == bound,
                RecordEntry.created_at < before,
            )
        )
    ).all()
    if not rows:
        return None
    pick = max if bound is Bound.LOWER else min
    return pick(rows, key=lambda r: r[1])[0]


async def _solved(session: AsyncSession, limit: int) -> list[dict[str, Any]]:
    problems = (
        await session.execute(
            select(Problem)
            .where(Problem.hidden.is_(False), Problem.status == ProblemStatus.SOLVED_ON_PLATFORM)
            .order_by(Problem.updated_at.desc())
            .limit(limit)
        )
    ).scalars()
    out = []
    for problem in problems:
        solver = await _settled_by(session, problem)
        paper = await session.get(Paper, solver) if solver else None
        solved_at = (
            await session.execute(
                select(func.max(AuditEvent.created_at)).where(
                    AuditEvent.action == "problem.solved_on_platform",
                    AuditEvent.subject_id == str(problem.id),
                )
            )
        ).scalar_one_or_none()
        out.append(
            {
                "slug": problem.slug,
                "title": problem.title,
                "domain": problem.domain,
                "paper_id": str(paper.id) if paper else None,
                "paper_title": paper.title if paper else None,
                "at": (solved_at or problem.updated_at).isoformat(),
            }
        )
    return out


async def _wanted(session: AsyncSession, limit: int) -> list[dict[str, Any]]:
    """The most important open problems, one per title (a family of variants)."""
    ranked = list(
        (
            await session.execute(
                select(Problem)
                .where(Problem.hidden.is_(False), Problem.status == ProblemStatus.OPEN)
                .order_by(Problem.importance.desc(), Problem.created_at)
                .limit(limit * 8)
            )
        ).scalars()
    )
    flagged = await possibly_settled_ids(session, [p.id for p in ranked])  # do not send people to solved ones
    families: dict[str, list[Problem]] = {}
    for p in ranked:
        if p.id not in flagged:
            families.setdefault(p.title, []).append(p)
    problems = [members[0] for members in families.values()][:limit]
    ids = [p.id for p in problems]
    leases = {
        pid: n
        for pid, n in (
            await session.execute(
                select(Lease.problem_id, func.count())
                .where(Lease.problem_id.in_(ids), Lease.released_at.is_(None), Lease.expires_at > now())
                .group_by(Lease.problem_id)
            )
        ).all()
    }
    attempts = {
        pid: n
        for pid, n in (
            await session.execute(
                select(Attempt.problem_id, func.count())
                .where(Attempt.problem_id.in_(ids))
                .group_by(Attempt.problem_id)
            )
        ).all()
    }
    out = []
    for p in problems:
        best = None
        quantity = (
            await session.execute(select(Quantity).where(Quantity.problem_id == p.id).limit(1))
        ).scalar_one_or_none()
        if quantity is not None:
            records = await best_records(session, quantity.id)
            parts = [f"{short_name(quantity.name)} {SIGN[Bound(k)]} {r.value}" for k, r in records.items()]
            best = ", ".join(parts) or None
        out.append(
            {
                "slug": p.slug,
                "title": p.title,
                "domain": p.domain,
                "importance": p.importance,
                "why": external_signals(p, shown_only=True)[1],
                "formal_statement": bool(p.lean_statement),
                "agents_working": leases.get(p.id, 0),
                "attempts": attempts.get(p.id, 0),
                "best_known": best,
                "variants": len(families[p.title]) - 1,
                "xp_bounty": bounty(p.importance),
                "prize_usd": prize_usd(p.statement_md),
            }
        )
    return out


def _excerpt(markdown: str, limit: int = 800) -> str:
    """The digest's opening, cut at a paragraph boundary."""
    if len(markdown) <= limit:
        return markdown
    cut = markdown.rfind("\n\n", 0, limit)
    return markdown[:cut] if cut > 0 else markdown[:limit].rsplit(" ", 1)[0] + " …"


async def front_page(session: AsyncSession) -> dict[str, Any]:
    from .views import feed  # local import: see _notable

    events = await ledger(session)
    board = await leaderboard(session, "week", 5, events)
    if not board["rows"]:  # a quiet week: the all-time top instead of an empty table
        board = await leaderboard(session, "all", 5, events)

    digest = (
        await session.execute(
            select(WeeklyRun)
            .where(WeeklyRun.digest_md.is_not(None))
            .order_by(WeeklyRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return {
        "stats": await _stats(session),
        "notable": await _notable(session, 6),
        "records": await _record_board(session, 8),
        "solved": await _solved(session, 6),
        "wanted": await _wanted(session, 8),
        "latest": await feed(session, limit=8),
        "digest": (
            {
                "id": str(digest.id),
                "started_at": digest.created_at.isoformat(),
                "excerpt": _excerpt(digest.digest_md or ""),
            }
            if digest
            else None
        ),
        "leaders": board["rows"],
        "leaders_period": board["period"],
        "happenings": await happenings(session, 8, events=events),
    }


def value_gain(previous: str | None, value: str) -> float | None:
    """How much a new value differs from the previous best (shown as +10 or -0.002)."""
    if previous is None:
        return None
    try:
        return value_to_float(value) - value_to_float(previous)
    except Invalid:
        return None
