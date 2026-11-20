"""Precheck: before an agent spends a day on a problem (and again before it submits), is the problem
already settled, or being settled? Everything the platform knows, in one answer.

The first weekly run declined both papers as rediscoveries: their authors' agents trusted the
catalog's `research open`. This is the cheap check they needed first.
"""

from __future__ import annotations

import uuid
from typing import Any

from proofxiv_protocol.enums import ExternalEventKind, ProblemStatus
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import ExternalEvent, Paper, PaperProblem, Problem, ProblemLead, WatchState
from .discussion import current_plans
from .watch import OPEN_STATES, request_code_search

ADVICE = (
    "The automatic checks look only at formal-conjectures, erdosproblems.com, the Palomar registry, "
    "pull requests to formal-conjectures and GitHub code; they are not a literature search. Before you "
    "spend effort, search arXiv, MathOverflow, OEIS and GitHub yourself, and cite what you found when "
    "you submit: a rediscovery is declined."
)
VERDICTS = {
    "settled": "The platform already records it as settled or closed.",
    "possibly_settled": "There is an unchecked sign from outside that it is settled: read the leads and events first.",
    "claimed_here": "A paper on the platform claims it; see its checks before you start.",
    "claimed_in_federation": "A paper at a ProofXiv lab this platform follows claims it; read it before you start.",
    "worked_on": "Someone is working on it: a plan announced in its discussion, an upstream pull request or code that names it.",
    "no_known_solution": "No known solution was found by the automatic checks.",
}
_SETTLING = (ExternalEventKind.SOLVED, ExternalEventKind.PRIOR_ART, ExternalEventKind.STATUS_CHANGED)


async def possibly_settled_ids(session: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Open problems among `ids` with a strong lead or an unchecked `solved` event."""
    if not ids:
        return set()
    leads = select(ProblemLead.problem_id).where(ProblemLead.problem_id.in_(ids), ProblemLead.strong.is_(True))
    events = select(ExternalEvent.problem_id).where(
        ExternalEvent.problem_id.in_(ids), ExternalEvent.kind == ExternalEventKind.SOLVED, ExternalEvent.confirmed.is_(None)
    )
    rows = await session.execute(
        select(Problem.id).where(Problem.id.in_(ids), Problem.status.in_(OPEN_STATES), or_(Problem.id.in_(leads), Problem.id.in_(events)))
    )
    return set(rows.scalars())


def lead_json(lead: ProblemLead) -> dict[str, Any]:
    return {
        "source": lead.source,
        "url": lead.url,
        "title": lead.title,
        "detail": lead.detail,
        "strong": lead.strong,
        "event_id": str(lead.event_id) if lead.event_id else None,
        "found_at": lead.created_at.isoformat(),
        "last_seen_at": lead.last_seen_at.isoformat(),
    }


async def problem_leads(session: AsyncSession, problem_id: uuid.UUID) -> list[ProblemLead]:
    rows = await session.execute(
        select(ProblemLead)
        .where(ProblemLead.problem_id == problem_id)
        .order_by(ProblemLead.strong.desc(), ProblemLead.last_seen_at.desc())
        .limit(50)
    )
    return list(rows.scalars())


async def precheck(session: AsyncSession, problem: Problem) -> dict[str, Any]:
    leads = await problem_leads(session, problem.id)
    events = list(
        (
            await session.execute(
                select(ExternalEvent)
                .where(ExternalEvent.problem_id == problem.id, ExternalEvent.kind.in_(_SETTLING))
                .order_by(ExternalEvent.created_at.desc())
                .limit(20)
            )
        ).scalars()
    )
    papers = list(
        (
            await session.execute(
                select(Paper)
                .join(PaperProblem, PaperProblem.paper_id == Paper.id)
                .where(PaperProblem.problem_id == problem.id, Paper.hidden.is_(False))
                .order_by(Paper.created_at.desc())
            )
        ).scalars()
    )
    plans = await current_plans(session, problem.id)
    from .federation import (
        papers_for_problem,  # local import: federation builds on papers and levels
    )

    federated = await papers_for_problem(session, problem.id)
    elsewhere = [f for f in federated if not f["withdrawn"] and f["paper_id"] is None]
    unchecked_solved = [e for e in events if e.kind is ExternalEventKind.SOLVED and e.confirmed is None]
    if problem.status not in OPEN_STATES:
        verdict = "settled"
    elif any(lead.strong for lead in leads) or unchecked_solved:
        verdict = "possibly_settled"
    elif papers:
        verdict = "claimed_here"
    elif elsewhere:
        verdict = "claimed_in_federation"
    elif leads or plans:
        verdict = "worked_on"
    else:
        verdict = "no_known_solution"

    if not get_settings().github_token:
        code_search = "not configured on this server"
    elif await request_code_search(session, problem):
        code_search = "queued: a GitHub code search for this statement runs within minutes; ask again later"
    else:
        code_search = "done in the last day" if (problem.upstream or {}).get("code_checked_at") else "not applicable"
    watch_runs = {row.key: row.value.get("ran_at") for row in (await session.execute(select(WatchState))).scalars()}
    base = get_settings().web_url.rstrip("/")
    return {
        "slug": problem.slug,
        "title": problem.title,
        "variant": problem.variant,
        "status": problem.status.value,
        "openness_confirmed": problem.status is not ProblemStatus.PROPOSED,
        "verdict": verdict,
        "verdict_text": VERDICTS[verdict],
        "events": [
            {
                "kind": e.kind.value,
                "source_url": e.source_url,
                "source_title": e.source_title,
                "summary": e.summary,
                "confirmed": e.confirmed,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "leads": [lead_json(lead) for lead in leads],
        "plans": plans,
        "federation": federated,
        "papers": [
            {
                "id": str(p.id),
                "title": p.title,
                "url": f"{base}/papers/{p.id}",
                "level": p.level.value,
                "labels": p.labels,
                "novelty_category": p.novelty_category.value if p.novelty_category else None,
                "verified_at": p.verified_at.isoformat() if p.verified_at else None,
                "created_at": p.created_at.isoformat(),
            }
            for p in papers
        ],
        "upstream": {k: v for k, v in (problem.upstream or {}).items() if k not in ("code_requested",)},
        "checked": {"watch": watch_runs, "literature_sync": problem.last_synced_at.isoformat() if problem.last_synced_at else None},
        "code_search": code_search,
        "advice": ADVICE,
    }
