"""Problem catalog: proposals, search, leases, attempts and record tables."""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import timedelta
from fractions import Fraction
from typing import Any

from proofxiv_protocol.enums import (
    ApproachStatus,
    Bound,
    DecisiveTaskKind,
    DecisiveTaskStatus,
    ProblemStatus,
)
from proofxiv_protocol.leanscan import statement_problems
from proofxiv_protocol.problems import ApproachSpec, ProblemProposal, QuantitySpec
from sqlalchemy import Select, Update, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    Agent,
    Approach,
    Attempt,
    DecisiveTask,
    ExternalEvent,
    Lease,
    Operator,
    Problem,
    Quantity,
    RecordEntry,
)
from . import audit
from .errors import Forbidden, Invalid, NotFound
from .gamify import bounty
from .identity import Principal, now
from .importance import prize_usd
from .params import get_param
from .precheck import lead_json, problem_leads

ATTEMPT_OUTCOMES = frozenset({"negative", "partial", "progress", "solved", "abandoned", "note"})


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:100] or "problem"


async def unique_slug(session: AsyncSession, title: str) -> str:
    base = slugify(title)
    slug, n = base, 1
    while (await session.execute(select(Problem.id).where(Problem.slug == slug))).first():
        n += 1
        slug = f"{base}-{n}"
    return slug


def value_to_float(value: str) -> float:
    """Parse '98', '2.2203344', '1984', '48/50' into a float for ordering."""
    text = value.strip().replace(" ", "")
    try:
        return float(Fraction(text))
    except (ValueError, ZeroDivisionError):
        pass
    try:
        return float(text)
    except ValueError as exc:
        raise Invalid(f"cannot parse numeric value '{value}'") from exc


async def similar_problems(session: AsyncSession, title: str, limit: int = 5) -> list[Problem]:
    rows = await session.execute(
        select(Problem)
        .where(func.similarity(Problem.title, title) > 0.45)
        .order_by(func.similarity(Problem.title, title).desc())
        .limit(limit)
    )
    return list(rows.scalars())


async def add_quantity(
    session: AsyncSession, problem: Problem, spec: QuantitySpec, verified: bool
) -> Quantity:
    quantity = Quantity(
        problem_id=problem.id, key=spec.key, name=spec.name, description=spec.description
    )
    session.add(quantity)
    await session.flush()
    for bound, value in ((Bound.LOWER, spec.best_lower), (Bound.UPPER, spec.best_upper)):
        if value:
            session.add(
                RecordEntry(
                    quantity_id=quantity.id,
                    bound=bound,
                    value=value,
                    value_float=value_to_float(value),
                    source_url=spec.source,
                    verified=verified,
                )
            )
    return quantity


MAX_LEAN_STATEMENT = 20_000


def refuse_unsafe_statement(proposal: ProblemProposal) -> None:
    """A Lean statement is compiled by every agent that works on the problem: it must not run
    code on their machines (nor import anything, add axioms or define syntax)."""
    if not proposal.lean_statement:
        return
    if len(proposal.lean_statement) > MAX_LEAN_STATEMENT:
        raise Invalid(f"lean_statement is longer than {MAX_LEAN_STATEMENT} characters")
    problems = statement_problems(proposal.lean_statement)
    if problems:
        raise Invalid("lean_statement is refused: " + "; ".join(problems), problems=problems)


async def create_problem(
    session: AsyncSession,
    proposal: ProblemProposal,
    proposed_by: uuid.UUID | None,
    status: ProblemStatus,
    origin: str,
) -> Problem:
    refuse_unsafe_statement(proposal)
    variant = (proposal.variant or "").strip() or None
    problem = Problem(
        slug=await unique_slug(session, f"{proposal.title}: {variant}" if variant else proposal.title),
        title=proposal.title,
        variant=variant,
        part_of=(proposal.part_of or "").strip() or None,
        statement_md=proposal.statement_md,
        lean_statement=proposal.lean_statement,
        domain=proposal.domain,
        status=status,
        sources=[s.model_dump(mode="json") for s in proposal.sources],
        openness_evidence=proposal.openness_evidence,
        importance_note=proposal.importance_note,
        difficulty_note=proposal.difficulty_note,
        formalizable=proposal.formalizable,
        proposed_by=proposed_by,
        origin=origin,
    )
    session.add(problem)
    await session.flush()
    for spec in proposal.quantities:
        await add_quantity(session, problem, spec, verified=status is not ProblemStatus.PROPOSED)
    return problem


async def propose_problem(
    session: AsyncSession, principal: Principal, proposal: ProblemProposal
) -> tuple[Problem, list[Problem]]:
    duplicates = await similar_problems(session, proposal.title)
    problem = await create_problem(
        session, proposal, principal.id, ProblemStatus.PROPOSED, origin="proposal"
    )
    kinds = await get_param(session, "priority_kind")
    session.add(
        DecisiveTask(
            kind=DecisiveTaskKind.CONFIRM_PROBLEM,
            status=DecisiveTaskStatus.READY,
            base_priority=kinds.get("confirm_problem", 30.0),
            problem_id=problem.id,
        )
    )
    audit.record(session, principal.id, "problem.proposed", "problem", problem.id)
    return problem, duplicates


async def get_problem(session: AsyncSession, slug: str) -> Problem:
    problem = (
        await session.execute(select(Problem).where(Problem.slug == slug))
    ).scalar_one_or_none()
    if problem is None:
        raise NotFound(f"problem '{slug}' not found")
    return problem


def _problems_query(
    q: str | None, status: ProblemStatus | None, domain: str | None
) -> Select[Problem]:
    stmt = select(Problem).where(Problem.hidden.is_(False))
    if status is not None:
        stmt = stmt.where(Problem.status == status)
    if domain:
        stmt = stmt.where(Problem.domain == domain)
    if q:
        tsv = func.to_tsvector(
            "simple", Problem.title + " " + func.coalesce(Problem.variant, "") + " " + Problem.statement_md
        )
        stmt = stmt.where(
            or_(
                tsv.op("@@")(func.websearch_to_tsquery("simple", q)),
                Problem.title.op("%")(q),  # trigram operator: uses the GIN indexes
                Problem.variant.op("%")(q),
            )
        )
    return stmt


async def search_problems(
    session: AsyncSession,
    q: str | None = None,
    status: ProblemStatus | None = None,
    domain: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Problem]:
    stmt = _problems_query(q, status, domain)
    if q:
        stmt = stmt.order_by(
            func.greatest(func.similarity(Problem.title, q), func.similarity(func.coalesce(Problem.variant, ""), q)).desc()
        )
    stmt = stmt.order_by(Problem.importance.desc(), Problem.created_at.desc())
    return list((await session.execute(stmt.limit(min(limit, 200)).offset(offset))).scalars())


async def count_problems(
    session: AsyncSession,
    q: str | None = None,
    status: ProblemStatus | None = None,
    domain: str | None = None,
) -> int:
    filtered = _problems_query(q, status, domain).subquery()
    return (await session.execute(select(func.count()).select_from(filtered))).scalar_one()


async def problem_domains(session: AsyncSession) -> list[tuple[str, int]]:
    """Domains of visible problems with their sizes, largest first."""
    n = func.count()
    rows = await session.execute(
        select(Problem.domain, n)
        .where(Problem.hidden.is_(False), Problem.domain != "")
        .group_by(Problem.domain)
        .order_by(n.desc(), Problem.domain)
    )
    return [(domain, count) for domain, count in rows.all()]


async def best_records(
    session: AsyncSession, quantity_id: uuid.UUID, verified_only: bool = False
) -> dict[str, RecordEntry]:
    stmt = select(RecordEntry).where(RecordEntry.quantity_id == quantity_id)
    if verified_only:
        stmt = stmt.where(RecordEntry.verified.is_(True))
    rows = list((await session.execute(stmt)).scalars())
    best: dict[str, RecordEntry] = {}
    for r in rows:
        if r.bound is Bound.EXACT:
            best["exact"] = r
        elif r.bound is Bound.LOWER:
            if "lower" not in best or r.value_float > best["lower"].value_float:
                best["lower"] = r
        elif "upper" not in best or r.value_float < best["upper"].value_float:
            best["upper"] = r
    return best


async def compare_with_records(
    session: AsyncSession,
    quantity_id: uuid.UUID,
    value: str,
    bound: Bound,
    verified_only: bool = False,
) -> dict[str, Any]:
    """How a claimed value stands against the recorded best of the same bound."""
    best = await best_records(session, quantity_id, verified_only)
    new = value_to_float(value)
    previous = best.get(bound.value)
    if previous is None:
        return {"status": "no_previous", "previous": None, "source": None}
    better = (
        new > previous.value_float
        if bound is Bound.LOWER
        else new < previous.value_float
        if bound is Bound.UPPER
        else False
    )
    return {
        "status": "new_record" if better else "not_better",
        "previous": previous.value,
        "source": previous.source_url,
        "delta": new - previous.value_float,
    }


def _proposal_records(problem: Problem) -> Update:
    """Best-known values typed into a proposal (not from papers or literature events)."""
    return update(RecordEntry).where(
        RecordEntry.quantity_id.in_(select(Quantity.id).where(Quantity.problem_id == problem.id)),
        RecordEntry.paper_id.is_(None),
        RecordEntry.event_id.is_(None),
    )


async def set_proposal_records_verified(
    session: AsyncSession, problem: Problem, verified: bool
) -> None:
    """A trusted reviewer checked (or a revoked key had checked) the proposal's values."""
    await session.execute(_proposal_records(problem).values(verified=verified))


async def active_leases(session: AsyncSession, problem_id: uuid.UUID) -> list[Lease]:
    rows = await session.execute(
        select(Lease).where(
            Lease.problem_id == problem_id,
            Lease.released_at.is_(None),
            Lease.expires_at > now(),
        )
    )
    return list(rows.scalars())


async def claim_task(
    session: AsyncSession, principal: Principal, slug: str, note: str = ""
) -> tuple[Lease, list[Lease]]:
    problem = await get_problem(session, slug)
    if problem.status in (ProblemStatus.SOLVED_EXTERNALLY, ProblemStatus.SOLVED_IN_FEDERATION, ProblemStatus.CLOSED_KNOWN):
        raise Invalid(f"problem is {problem.status}; check its events before working on it")
    others = [
        lease for lease in await active_leases(session, problem.id) if lease.operator_id != principal.id
    ]
    lease = Lease(
        problem_id=problem.id,
        operator_id=principal.id,
        agent_id=principal.agent.id if principal.agent else None,
        note=note,
        expires_at=now() + timedelta(hours=get_settings().problem_lease_hours),
    )
    session.add(lease)
    await session.flush()
    audit.record(session, principal.id, "lease.created", "problem", problem.id, lease=str(lease.id))
    return lease, others


async def _own_lease(session: AsyncSession, principal: Principal, lease_id: uuid.UUID) -> Lease:
    lease = await session.get(Lease, lease_id)
    if lease is None:
        raise NotFound("lease not found")
    if lease.operator_id != principal.id:
        raise Forbidden("not your lease")
    return lease


async def heartbeat(session: AsyncSession, principal: Principal, lease_id: uuid.UUID) -> Lease:
    lease = await _own_lease(session, principal, lease_id)
    if lease.released_at is not None:
        raise Invalid("lease already released")
    lease.expires_at = now() + timedelta(hours=get_settings().problem_lease_hours)
    return lease


async def release(session: AsyncSession, principal: Principal, lease_id: uuid.UUID) -> None:
    lease = await _own_lease(session, principal, lease_id)
    lease.released_at = now()


async def post_attempt(
    session: AsyncSession,
    principal: Principal,
    slug: str,
    outcome: str,
    notes_md: str,
    approach: str | None = None,
) -> Attempt:
    if outcome not in ATTEMPT_OUTCOMES:
        raise Invalid(f"outcome must be one of {sorted(ATTEMPT_OUTCOMES)}")
    if len(notes_md.strip()) < 10:
        raise Invalid("notes are too short to be useful")
    problem = await get_problem(session, slug)
    if approach is not None:
        known = await _find_approach(session, problem.id, approach)
        approach = known.name if known else approach.strip()[:120] or None
    attempt = Attempt(
        problem_id=problem.id,
        operator_id=principal.id,
        agent_id=principal.agent.id if principal.agent else None,
        outcome=outcome,
        notes_md=notes_md,
        approach=approach,
    )
    session.add(attempt)
    return attempt


# --- the map of approaches -------------------------------------------------------------------

STATUS_ORDER = {
    ApproachStatus.SUCCEEDED: 0,
    ApproachStatus.PROMISING: 1,
    ApproachStatus.STALLED: 2,
    ApproachStatus.REFUTED: 3,
}


async def _find_approach(session: AsyncSession, problem_id: uuid.UUID, name: str) -> Approach | None:
    return (
        await session.execute(
            select(Approach).where(
                Approach.problem_id == problem_id,
                func.lower(Approach.name) == name.strip().lower(),
            )
        )
    ).scalar_one_or_none()


async def upsert_approach(
    session: AsyncSession,
    problem: Problem,
    spec: ApproachSpec,
    by: uuid.UUID | None,
    confirmed: bool = False,
    task_id: uuid.UUID | None = None,
) -> Approach:
    """Add an approach to the problem's map or update it (by name, case-insensitive). An
    ordinary agent's edit drops the 'confirmed by the weekly run' mark; every change is kept
    in the revisions."""
    approach = await _find_approach(session, problem.id, spec.name)
    if approach is None:
        approach = Approach(problem_id=problem.id, name=spec.name.strip(), revisions=[])
        session.add(approach)
    approach.status = spec.status
    approach.summary_md = spec.summary_md
    approach.barrier_md = spec.barrier_md
    approach.sources = [src.model_dump() for src in spec.sources]
    approach.confirmed = confirmed
    approach.updated_by = by
    approach.revisions = [
        *(approach.revisions or [])[-49:],
        {
            "at": now().isoformat(),
            "by": str(by) if by else None,
            "status": spec.status.value,
            "confirmed": confirmed,
            "task": str(task_id) if task_id else None,
        },
    ]
    await session.flush()
    audit.record(session, by, "approach.updated", "problem", problem.id, name=approach.name)
    return approach


async def approaches_of(session: AsyncSession, problem: Problem) -> list[dict[str, Any]]:
    rows = list(
        (await session.execute(select(Approach).where(Approach.problem_id == problem.id))).scalars()
    )
    counts = {
        (name or "").lower(): n
        for name, n in (
            await session.execute(
                select(Attempt.approach, func.count())
                .where(Attempt.problem_id == problem.id, Attempt.approach.is_not(None))
                .group_by(Attempt.approach)
            )
        ).all()
    }
    logins = {
        o.id: o.login
        for o in (
            await session.execute(
                select(Operator).where(Operator.id.in_([a.updated_by for a in rows if a.updated_by]))
            )
        ).scalars()
    }
    rows.sort(key=lambda a: (STATUS_ORDER[a.status], -a.updated_at.timestamp()))
    return [
        {
            "name": a.name,
            "status": a.status.value,
            "summary_md": a.summary_md,
            "barrier_md": a.barrier_md,
            "sources": a.sources,
            "confirmed": a.confirmed,
            "attempts": counts.get(a.name.lower(), 0),
            "updated_by": logins.get(a.updated_by) if a.updated_by else None,
            "updated_at": a.updated_at.isoformat(),
            "revisions": len(a.revisions or []),
        }
        for a in rows
    ]


async def problem_detail(session: AsyncSession, problem: Problem) -> dict[str, Any]:
    quantities = list(
        (
            await session.execute(select(Quantity).where(Quantity.problem_id == problem.id))
        ).scalars()
    )
    records: list[dict[str, Any]] = []
    for q in quantities:
        best = await best_records(session, q.id)
        history = (
            await session.execute(
                select(RecordEntry)
                .where(RecordEntry.quantity_id == q.id)
                .order_by(RecordEntry.created_at)
            )
        ).scalars()
        records.append(
            {
                "key": q.key,
                "name": q.name,
                "description": q.description,
                "best": {k: _record_json(v) for k, v in best.items()},
                "history": [_record_json(r) for r in history],
            }
        )
    events = (
        await session.execute(
            select(ExternalEvent)
            .where(ExternalEvent.problem_id == problem.id)
            .order_by(ExternalEvent.created_at.desc())
            .limit(50)
        )
    ).scalars()
    attempts = (
        await session.execute(
            select(Attempt, Operator.login, Agent.name)
            .join(Operator, Operator.id == Attempt.operator_id)
            .outerjoin(Agent, Agent.id == Attempt.agent_id)
            .where(Attempt.problem_id == problem.id)
            .order_by(Attempt.created_at.desc())
            .limit(50)
        )
    ).all()
    leases = await active_leases(session, problem.id)
    # the other catalog entries of the same problem (formal variants and parts share a title)
    family = (
        await session.execute(
            select(Problem)
            .where(Problem.title == problem.title, Problem.id != problem.id, Problem.hidden.is_(False))
            .order_by(Problem.variant)
            .limit(50)
        )
    ).scalars()
    from .federation import papers_for_problem  # local import: federation builds on this module

    return {
        "federation": await papers_for_problem(session, problem.id),
        "part_of": problem.part_of,
        "family": [{"slug": p.slug, "variant": p.variant, "status": p.status.value} for p in family],
        "leads": [lead_json(lead) for lead in await problem_leads(session, problem.id)],
        "approaches": await approaches_of(session, problem),
        "quantities": records,
        "events": [
            {
                "id": str(e.id),
                "kind": e.kind.value,
                "source_url": e.source_url,
                "source_title": e.source_title,
                "summary": e.summary,
                "confirmed": e.confirmed,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "attempts": [
            {
                "id": str(a.id),
                "outcome": a.outcome,
                "notes_md": a.notes_md,
                "operator_id": str(a.operator_id),
                "operator_login": login,
                "agent_name": agent_name,
                "approach": a.approach,
                "created_at": a.created_at.isoformat(),
            }
            for a, login, agent_name in attempts
        ],
        "active_leases": len(leases),
        "xp_bounty": bounty(problem.importance),
        "prize_usd": prize_usd(problem.statement_md),
    }


def _record_json(r: RecordEntry) -> dict[str, Any]:
    return {
        "bound": r.bound.value,
        "value": r.value,
        "source_url": r.source_url,
        "source_title": r.source_title,
        "paper_id": str(r.paper_id) if r.paper_id else None,
        "verified": r.verified,
        "recorded_on": r.recorded_on.isoformat() if r.recorded_on else None,
        "created_at": r.created_at.isoformat(),
    }
