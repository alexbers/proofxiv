"""Results going upstream, never on their own: an open feed of confirmed status changes (for
readers, RSS bots, a Zulip stream) and, for the catalogs we import from, the statements that are
settled here but still tagged open there, with the exact edit and pull-request text. A person opens
each pull request after reading it (`proofxiv owner upstream open`); nothing is sent from here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from xml.sax.saxutils import escape

from proofxiv_protocol.enums import ExternalEventKind, ProblemStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import ExternalEvent, Paper, Problem, ProblemLead
from .levels import _settled_by
from .watch import FC_REPO

SETTLED = (ProblemStatus.SOLVED_EXTERNALLY, ProblemStatus.SOLVED_ON_PLATFORM, ProblemStatus.SOLVED_IN_FEDERATION)
_FEED_KINDS = (ExternalEventKind.SOLVED, ExternalEventKind.RECORD_IMPROVED, ExternalEventKind.STATUS_CHANGED)


def _problem_url(problem: Problem) -> str:
    return f"{get_settings().web_url.rstrip('/')}/problems/{problem.slug}"


def _label(problem: Problem) -> str:
    return f"{problem.title} ({problem.variant})" if problem.variant else problem.title


async def status_items(session: AsyncSession, limit: int = 100) -> list[dict[str, Any]]:
    """Confirmed changes of what is known about catalog problems, newest first."""
    rows = await session.execute(
        select(ExternalEvent, Problem)
        .join(Problem, Problem.id == ExternalEvent.problem_id)
        .where(ExternalEvent.confirmed.is_(True), ExternalEvent.kind.in_(_FEED_KINDS), Problem.hidden.is_(False))
        .order_by(ExternalEvent.applied_at.desc().nulls_last(), ExternalEvent.created_at.desc())
        .limit(limit)
    )
    items = []
    for event, problem in rows.all():
        verb = {"solved": "solved", "record_improved": "new record", "status_changed": "status changed"}[event.kind.value]
        items.append(
            {
                "id": f"event:{event.id}",
                "title": f"{_label(problem)}: {verb}",
                "url": _problem_url(problem),
                "date": (event.applied_at or event.created_at).isoformat(),
                "summary": event.summary,
                "source_url": event.source_url,
                "confirmation": event.confirm_reason,
                "problem": problem.slug,
                "upstream": {k: v for k, v in (problem.upstream or {}).items() if k in ("repo", "path", "name", "category")},
            }
        )
    solved_here = await session.execute(
        select(Problem).where(Problem.status == ProblemStatus.SOLVED_ON_PLATFORM, Problem.hidden.is_(False)).limit(limit)
    )
    for problem in solved_here.scalars():
        paper_id = await _settled_by(session, problem)
        paper = await session.get(Paper, paper_id) if paper_id else None
        if paper is None:
            continue
        items.append(
            {
                "id": f"paper:{paper.id}:{problem.slug}",
                "title": f"{_label(problem)}: settled on ProofXiv",
                "url": _problem_url(problem),
                "date": (paper.verified_at or paper.updated_at).isoformat(),
                "summary": f"Settled by “{paper.title}”, verified by the platform.",
                "source_url": f"{get_settings().web_url.rstrip('/')}/papers/{paper.id}",
                "confirmation": "machine checks and the decisive reviews of the weekly trusted run",
                "problem": problem.slug,
                "upstream": {k: v for k, v in (problem.upstream or {}).items() if k in ("repo", "path", "name", "category")},
            }
        )
    items.sort(key=lambda item: item["date"], reverse=True)
    return items[:limit]


async def status_feed(session: AsyncSession) -> dict[str, Any]:
    return {
        "source": "ProofXiv",
        "about": "Confirmed changes of open problems' status (solved, new records), checked by the weekly trusted run.",
        "generated": datetime.now(UTC).isoformat(),
        "items": await status_items(session),
    }


def status_atom(items: list[dict[str, Any]]) -> str:
    base = get_settings().web_url.rstrip("/")
    updated = items[0]["date"] if items else datetime.now(UTC).isoformat()
    entries = "".join(
        "<entry>"
        f"<id>{escape(base)}/{escape(item['id'])}</id>"
        f"<title>{escape(item['title'])}</title>"
        f'<link href="{escape(item["url"])}"/>'
        f"<updated>{escape(item['date'])}</updated>"
        f"<summary>{escape(item['summary'] + ' Source: ' + item['source_url'])}</summary>"
        "</entry>"
        for item in items
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
        f"<id>{escape(base)}/feeds/problems</id><title>ProofXiv: open problems settled</title>"
        f'<link href="{escape(base)}/problems"/><updated>{escape(updated)}</updated>'
        f"{entries}</feed>"
    )


async def fc_candidates(session: AsyncSession) -> list[dict[str, Any]]:
    """Statements settled here (confirmed) that formal-conjectures still tags `research open`,
    with the edit its CONTRIBUTING asks for and the pull-request text."""
    rows = await session.execute(
        select(Problem).where(Problem.origin == "formal-conjectures", Problem.status.in_(SETTLED), Problem.hidden.is_(False))
    )
    out = []
    for problem in rows.scalars():
        upstream = problem.upstream or {}
        if upstream.get("category") != "research open" or not upstream.get("path") or not upstream.get("name"):
            continue
        events = list(
            (
                await session.execute(
                    select(ExternalEvent).where(
                        ExternalEvent.problem_id == problem.id,
                        ExternalEvent.kind == ExternalEventKind.SOLVED,
                        ExternalEvent.confirmed.is_(True),
                    )
                )
            ).scalars()
        )
        leads = list((await session.execute(select(ProblemLead).where(ProblemLead.problem_id == problem.id))).scalars())
        palomar = next((e.source_url for e in events if "palomar-registry.org" in e.source_url), None) or next(
            (lead.url for lead in leads if lead.source == "palomar"), None
        )
        pulls = [{"url": lead.url, "title": lead.title} for lead in leads if lead.source == "formal-conjectures-pr"]
        paper_id = await _settled_by(session, problem) if problem.status is ProblemStatus.SOLVED_ON_PLATFORM else None
        evidence = [{"url": e.source_url, "title": e.source_title, "confirmation": e.confirm_reason} for e in events]
        if paper_id:
            evidence.append({"url": f"{get_settings().web_url.rstrip('/')}/papers/{paper_id}", "title": "ProofXiv paper", "confirmation": "verified by the platform"})
        if not evidence:
            continue
        written = (problem.variant or "").split(" (")[0]
        name = upstream["name"]
        lines = [f"`{name}` in `{upstream['path']}` is solved:", ""]
        lines += [f"- {e['title'] or e['url']}: {e['url']}" for e in evidence]
        if palomar:
            lines += ["", f"A Lean proof is registered in Palomar: {palomar}"]
        lines += [
            "",
            f"Found and checked on ProofXiv ({_problem_url(problem)}): the solution was confirmed by its weekly "
            "trusted run (AI review by trusted operators, not by human experts).",
        ]
        out.append(
            {
                "slug": problem.slug,
                "problem_url": _problem_url(problem),
                "repo": FC_REPO,
                "path": upstream["path"],
                "name": written,
                "qualified": name,
                "seen_at_commit": upstream.get("commit"),
                "edit": {"category": ["research open", "research solved"], "formal_proof": palomar},
                "evidence": evidence,
                "pull_requests_seen": pulls,
                "pr_title": f"Mark {name} as solved",
                "pr_body": "\n".join(lines),
            }
        )
    return out
