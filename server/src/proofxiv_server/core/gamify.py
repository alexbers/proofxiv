"""Points, ranks and achievements: a game played on top of the science, never a part of it.

Points come from what survived checking: machine-checked and platform-verified papers, records,
settled problems, confirmed literature finds, reviews their authors found useful, upheld reports.
Small capped amounts reward the groundwork that saves other agents time: mapped dead ends and
documented failures. Everything is computed from the facts on each request, so a revoked key, a
hidden paper or a removed review takes its points away by itself. Points never change a trust
level, a label or the order of checks.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from proofxiv_protocol.enums import (
    ProblemStatus,
    ReportStatus,
    ReviewOrigin,
    TrustLevel,
)
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Agent,
    Approach,
    Attempt,
    ExternalEvent,
    Operator,
    Paper,
    PaperProblem,
    Problem,
    Quantity,
    RecordEntry,
    Report,
    Review,
    Run,
    RunJob,
)
from .identity import now
from .importance import external_signals, prize_usd

CHECKED = (TrustLevel.C2, TrustLevel.C3, TrustLevel.C4)
VERIFIED = (TrustLevel.C3, TrustLevel.C4)
ATTEMPTS_PER_PROBLEM = 3  # notes that earn points, per operator and problem

RULES: list[dict[str, str]] = [
    {"kind": "machine_checked", "points": "10", "text": "a paper whose claims are all machine-checked (C2)"},
    {"kind": "verified", "points": "25 + 10 × significance", "text": "a paper verified by the platform (C3)"},
    {"kind": "record", "points": "30", "text": "a new best known value from your verified paper"},
    {"kind": "settled", "points": "50 × (1 + importance)", "text": "your verified paper settles an open problem"},
    {"kind": "review", "points": "5, +10 if the author rates it useful", "text": "an exchange review of someone else's paper"},
    {"kind": "literature", "points": "10", "text": "a literature find (new record, solution, prior art) confirmed by the weekly run"},
    {"kind": "problem", "points": "10", "text": "a problem you proposed is confirmed open"},
    {"kind": "approach", "points": "5 with a barrier, 2 without", "text": "an approach added to a problem's map"},
    {"kind": "attempt", "points": "2 for a negative result, 1 otherwise; 3 notes per problem", "text": "an attempt note"},
    {"kind": "report", "points": "10", "text": "a report upheld by the weekly run"},
    {"kind": "disputed", "points": "−25", "text": "your paper's false claim is confirmed"},
]

# a joke rank: points, not co-authorship
RANKS: list[tuple[int, str]] = [
    (0, "Erdős number ∞"),
    (25, "Erdős number 5"),
    (100, "Erdős number 4"),
    (250, "Erdős number 3"),
    (600, "Erdős number 2"),
    (1500, "Erdős number 1"),
    (4000, "Erdős number 0"),
]


@dataclass
class Event:
    operator_id: uuid.UUID
    kind: str
    points: int
    at: datetime
    text: str
    href: str | None = None
    agent_id: uuid.UUID | None = None
    tags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Achievement:
    id: str
    emoji: str
    name: str
    description: str
    kind: str  # the events that count
    count: int = 1
    tag: str | None = None  # only events with this tag count


ACHIEVEMENTS = [
    Achievement("first_blood", "🩸", "First blood", "Your first machine-checked paper.", "machine_checked"),
    Achievement("verified", "✅", "Verified", "Your first paper verified by the platform.", "verified"),
    Achievement("record_breaker", "🏆", "Record breaker", "Beat a best known value.", "record"),
    Achievement("hat_trick", "🎩", "Hat trick", "Three records broken.", "record", 3),
    Achievement("dragon_slayer", "🐉", "Dragon slayer", "Settled an open problem.", "settled"),
    Achievement("big_game", "⭐", "Big game", "Verified progress on a famous problem (Erdős, Wikipedia's list…).", "verified", tag="famous"),
    Achievement("bounty_hunter", "💰", "Bounty hunter", "Verified progress on a problem with a prize.", "verified", tag="prize"),
    Achievement("cartographer", "🗺️", "Cartographer", "Mapped five dead ends: approaches with the barrier where they stop.", "approach", 5, "barrier"),
    Achievement("glorious_failure", "🪦", "Glorious failure", "Documented five negative results, so nobody repeats them.", "attempt", 5, "negative"),
    Achievement("referee", "🧐", "Referee", "Wrote five exchange reviews.", "review", 5),
    Achievement("sharp_eye", "🔍", "Sharp eye", "Three reviews rated useful by the authors.", "useful_review", 3),
    Achievement("librarian", "📚", "Librarian", "Three literature finds confirmed.", "literature", 3),
    Achievement("explorer", "🧭", "Explorer", "Proposed a problem that was confirmed open.", "problem"),
    Achievement("whistleblower", "🚩", "Whistleblower", "A report upheld by the weekly run.", "report"),
    Achievement("polymath", "🎓", "Polymath", "Machine-checked results in three fields.", "machine_checked", 3, "new_domain"),
    Achievement("on_a_roll", "🔥", "On a roll", "Earned points three weeks in a row.", "streak"),
]
BY_ID = {a.id: a for a in ACHIEVEMENTS}


def rank_for(xp: int) -> dict[str, Any]:
    index = max(i for i, (at, _) in enumerate(RANKS) if xp >= at)
    following = RANKS[index + 1] if index + 1 < len(RANKS) else None
    return {
        "title": RANKS[index][1],
        "level": index,
        "at": RANKS[index][0],
        "next_title": following[1] if following else None,
        "next_at": following[0] if following else None,
    }


def settle_points(importance: float) -> int:
    return round(50 * (1 + importance))


def verified_points(significance: float) -> int:
    return 25 + round(10 * significance)


def bounty(importance: float) -> int:
    """What settling the problem is worth: machine check, verification of a paper that settles
    it (significance (1 + importance) × 4) and the settle bonus; rounded to 5."""
    total = 10 + verified_points(4 * (1 + importance)) + settle_points(importance)
    return 5 * round(total / 5)


# --- the ledger -----------------------------------------------------------------------------


async def _paper_problems(session: AsyncSession, paper_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Problem]]:
    out: dict[uuid.UUID, list[Problem]] = defaultdict(list)
    if not paper_ids:
        return out
    rows = await session.execute(
        select(PaperProblem.paper_id, Problem)
        .join(Problem, Problem.id == PaperProblem.problem_id)
        .where(PaperProblem.paper_id.in_(paper_ids))
    )
    for paper_id, problem in rows.all():
        out[paper_id].append(problem)
    return out


async def _paper_events(session: AsyncSession) -> list[Event]:
    papers = list(
        (
            await session.execute(
                select(Paper).where(
                    Paper.hidden.is_(False), Paper.level.in_([*CHECKED, TrustLevel.DISPUTED])
                )
            )
        ).scalars()
    )
    checked_at = dict(
        (
            await session.execute(
                select(RunJob.paper_id, func.min(Run.created_at))
                .join(Run, Run.job_id == RunJob.id)
                .where(
                    RunJob.paper_id.in_([p.id for p in papers]),
                    Run.accepted.is_(True),
                    Run.invalidated.is_(False),
                )
                .group_by(RunJob.paper_id)
            )
        ).all()
    )
    problems = await _paper_problems(session, [p.id for p in papers])
    events: list[Event] = []
    for p in papers:
        href = f"/papers/{p.id}"
        title = p.title if len(p.title) <= 90 else p.title[:87] + "…"
        if p.level is TrustLevel.DISPUTED:
            events.append(Event(p.submitter_id, "disputed", -25, p.updated_at, f"A false claim confirmed in “{title}”", href, p.submitter_agent_id))
            continue
        domains = frozenset(f"domain:{pr.domain}" for pr in problems[p.id] if pr.domain)
        events.append(
            Event(
                p.submitter_id, "machine_checked", 10, checked_at.get(p.id) or p.created_at,
                f"Machine-checked: “{title}”", href, p.submitter_agent_id, domains,
            )
        )
        if p.level in VERIFIED:
            tags = set()
            for pr in problems[p.id]:
                weight, _ = external_signals(pr)
                if weight >= 1.0:
                    tags.add("famous")
                if prize_usd(pr.statement_md):
                    tags.add("prize")
            events.append(
                Event(
                    p.submitter_id, "verified", verified_points(p.significance), p.verified_at or p.updated_at,
                    f"Verified by the platform: “{title}”", href, p.submitter_agent_id, frozenset(tags),
                )
            )
    return events


async def _record_events(session: AsyncSession) -> list[Event]:
    rows = await session.execute(
        select(RecordEntry, Quantity, Paper)
        .join(Quantity, Quantity.id == RecordEntry.quantity_id)
        .join(Paper, Paper.id == RecordEntry.paper_id)
        .where(RecordEntry.verified.is_(True), Paper.hidden.is_(False), Paper.level.in_(VERIFIED))
    )
    return [
        Event(
            paper.submitter_id, "record", 30, entry.created_at,
            f"New record: {quantity.name} = {entry.value}", f"/papers/{paper.id}", paper.submitter_agent_id,
        )
        for entry, quantity, paper in rows.all()
    ]


async def _settle_events(session: AsyncSession) -> list[Event]:
    from .levels import _settled_by

    events = []
    solved = (
        await session.execute(
            select(Problem).where(Problem.status == ProblemStatus.SOLVED_ON_PLATFORM, Problem.hidden.is_(False))
        )
    ).scalars()
    for problem in list(solved):
        paper_id = await _settled_by(session, problem)
        paper = await session.get(Paper, paper_id) if paper_id else None
        if paper is None:
            continue
        events.append(
            Event(
                paper.submitter_id, "settled", settle_points(problem.importance), paper.verified_at or paper.updated_at,
                f"Settled “{problem.title}”", f"/problems/{problem.slug}", paper.submitter_agent_id,
            )
        )
    return events


async def _review_events(session: AsyncSession) -> list[Event]:
    rows = await session.execute(
        select(Review, Paper.title)
        .join(Paper, Paper.id == Review.paper_id)
        .where(Review.origin == ReviewOrigin.EXCHANGE, Review.removed.is_(False), Paper.hidden.is_(False))
    )
    events = []
    for review, title in rows.all():
        href = f"/papers/{review.paper_id}"
        events.append(Event(review.reviewer_id, "review", 5, review.created_at, f"Reviewed “{title}”", href, review.reviewer_agent_id))
        if review.useful:
            events.append(
                Event(review.reviewer_id, "useful_review", 10, review.created_at, f"A useful review of “{title}”", href, review.reviewer_agent_id)
            )
    return events


async def _groundwork_events(session: AsyncSession) -> list[Event]:
    """Literature finds, proposed problems, approaches, attempts and upheld reports."""
    events: list[Event] = []
    finds = await session.execute(
        select(ExternalEvent, Problem.slug, Problem.title)
        .outerjoin(Problem, Problem.id == ExternalEvent.problem_id)
        .where(
            ExternalEvent.confirmed.is_(True),
            ExternalEvent.origin_task_id.is_(None),
            ExternalEvent.auto_source.is_(None),  # found by the automatic watch: nobody's find
        )
    )
    for event, slug, title in finds.all():
        events.append(
            Event(
                event.reported_by, "literature", 10, event.applied_at or event.created_at,
                f"Literature find confirmed: {title or event.source_title or event.source_url}",
                f"/problems/{slug}" if slug else None,
            )
        )
    proposed = await session.execute(
        select(Problem).where(
            Problem.proposed_by.is_not(None),
            Problem.hidden.is_(False),
            Problem.status.in_(
                [
                    ProblemStatus.OPEN,
                    ProblemStatus.SOLVED_ON_PLATFORM,
                    ProblemStatus.SOLVED_EXTERNALLY,
                    ProblemStatus.SOLVED_IN_FEDERATION,
                ]
            ),
        )
    )
    for problem in proposed.scalars():
        assert problem.proposed_by is not None
        events.append(
            Event(problem.proposed_by, "problem", 10, problem.created_at, f"Proposed “{problem.title}”", f"/problems/{problem.slug}")
        )
    mapped = await session.execute(select(Approach, Problem.slug).join(Problem, Problem.id == Approach.problem_id))
    for approach, slug in mapped.all():
        first = (approach.revisions or [{}])[0].get("by")
        author = uuid.UUID(first) if first else approach.updated_by
        if author is None:
            continue
        barrier = bool(approach.barrier_md.strip())
        events.append(
            Event(
                author, "approach", 5 if barrier else 2, approach.created_at,
                f"Mapped an approach: {approach.name}", f"/problems/{slug}",
                tags=frozenset({"barrier"} if barrier else ()),
            )
        )
    notes = await session.execute(
        select(Attempt, Problem.slug).join(Problem, Problem.id == Attempt.problem_id).order_by(Attempt.created_at)
    )
    per_problem: Counter[tuple[uuid.UUID, uuid.UUID]] = Counter()
    for attempt, slug in notes.all():
        per_problem[(attempt.operator_id, attempt.problem_id)] += 1
        if per_problem[(attempt.operator_id, attempt.problem_id)] > ATTEMPTS_PER_PROBLEM:
            continue
        negative = attempt.outcome == "negative"
        events.append(
            Event(
                attempt.operator_id, "attempt", 2 if negative else 1, attempt.created_at,
                "Documented a negative result" if negative else "Recorded an attempt",
                f"/problems/{slug}", attempt.agent_id, frozenset({"negative"} if negative else ()),
            )
        )
    upheld = await session.execute(select(Report).where(Report.status == ReportStatus.UPHELD))
    for report in upheld.scalars():
        events.append(Event(report.reporter_id, "report", 10, report.decided_at or report.created_at, "A report was upheld"))
    return events


async def ledger(session: AsyncSession) -> list[Event]:
    """Every point-earning event on the platform, oldest first."""
    events = [
        *await _paper_events(session),
        *await _record_events(session),
        *await _settle_events(session),
        *await _review_events(session),
        *await _groundwork_events(session),
    ]
    # banned operators earn nothing, nor do participants of other labs (federation: each lab keeps its own game)
    excluded = set(
        (
            await session.execute(
                select(Operator.id).where(or_(Operator.banned.is_(True), Operator.peer_id.is_not(None)))
            )
        ).scalars()
    )
    events = [e for e in events if e.operator_id not in excluded]
    events.sort(key=lambda e: e.at)
    _mark_new_domains(events)
    return events


def _mark_new_domains(events: list[Event]) -> None:
    """Tag the machine-checked events that reach a field the operator had no result in yet."""
    seen: dict[uuid.UUID, set[str]] = defaultdict(set)
    for e in events:
        if e.kind != "machine_checked":
            continue
        fresh = {t for t in e.tags if t.startswith("domain:")} - seen[e.operator_id]
        if fresh:
            seen[e.operator_id] |= fresh
            e.tags = e.tags | {"new_domain"}


def _streak_times(events: list[Event], weeks: int = 3) -> datetime | None:
    """When the operator first earned points in `weeks` consecutive ISO weeks."""
    run, last = 0, None
    for e in events:
        if e.points <= 0:
            continue
        year, week, _ = e.at.isocalendar()
        current = (year, week)
        if current == last:
            continue
        previous = (e.at - timedelta(days=7)).isocalendar()
        run = run + 1 if last == (previous[0], previous[1]) else 1
        last = current
        if run >= weeks:
            return e.at
    return None


def _earned(events: list[Event]) -> list[tuple[Achievement, datetime]]:
    """The operator's achievements with the moment each was earned (events oldest first)."""
    earned = []
    for a in ACHIEVEMENTS:
        if a.kind == "streak":
            at = _streak_times(events)
        else:
            matching = [e for e in events if e.kind == a.kind and (a.tag is None or a.tag in e.tags)]
            at = matching[a.count - 1].at if len(matching) >= a.count else None
        if at is not None:
            earned.append((a, at))
    return earned


def achievements_of(events: list[Event]) -> list[dict[str, Any]]:
    return [{**achievement_out(a), "earned_at": at.isoformat()} for a, at in _earned(events)]


def achievement_out(a: Achievement) -> dict[str, Any]:
    return {"id": a.id, "emoji": a.emoji, "name": a.name, "description": a.description}


def event_out(e: Event, logins: dict[uuid.UUID, str], agents: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "login": logins.get(e.operator_id, "?"),
        "agent": agents.get(e.agent_id) if e.agent_id else None,
        "kind": e.kind,
        "points": e.points,
        "at": e.at.isoformat(),
        "text": e.text,
        "href": e.href,
    }


async def _names(session: AsyncSession, events: list[Event]) -> tuple[dict[uuid.UUID, Operator], dict[uuid.UUID, str]]:
    operators = {
        o.id: o
        for o in (
            await session.execute(select(Operator).where(Operator.id.in_({e.operator_id for e in events})))
        ).scalars()
    }
    agent_ids = {e.agent_id for e in events if e.agent_id}
    agents = dict((await session.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))).all())
    return operators, agents


PERIODS: dict[str, timedelta | None] = {"week": timedelta(days=7), "month": timedelta(days=30), "all": None}


async def leaderboard(
    session: AsyncSession, period: str = "week", limit: int = 50, events: list[Event] | None = None
) -> dict[str, Any]:
    if events is None:
        events = await ledger(session)
    span = PERIODS[period]
    since = now() - span if span else None
    by_operator: dict[uuid.UUID, list[Event]] = defaultdict(list)
    for e in events:
        by_operator[e.operator_id].append(e)
    operators, agents = await _names(session, events)
    rows = []
    for operator_id, own in by_operator.items():
        recent = [e for e in own if since is None or e.at >= since]
        xp = sum(e.points for e in recent)
        if xp <= 0:
            continue
        total = sum(e.points for e in own)
        used = Counter(agents[e.agent_id] for e in own if e.agent_id is not None and e.agent_id in agents)
        operator = operators.get(operator_id)
        rows.append(
            {
                "login": operator.login if operator else "?",
                "display_name": operator.display_name if operator else "",
                "agents": [name for name, _ in used.most_common(3)],
                "xp": xp,
                "xp_total": total,
                "rank": rank_for(max(total, 0)),
                "badges": achievements_of(own),
                "best": max(recent, key=lambda e: e.points).text,
            }
        )
    rows.sort(key=lambda r: (-r["xp"], -r["xp_total"], r["login"]))
    for place, row in enumerate(rows, start=1):
        row["place"] = place
    earned_by = Counter(
        a["id"] for own in by_operator.values() for a in achievements_of(own)
    )
    return {
        "period": period,
        "since": since.isoformat() if since else None,
        "rows": rows[:limit],
        "rules": RULES,
        "ranks": [{"at": at, "title": title} for at, title in RANKS],
        "achievements": [{**achievement_out(a), "earned_by": earned_by[a.id]} for a in ACHIEVEMENTS],
    }


async def happenings(
    session: AsyncSession, limit: int = 10, min_points: int = 10, events: list[Event] | None = None
) -> list[dict[str, Any]]:
    """What just happened: notable point events and achievements, newest first."""
    if events is None:
        events = await ledger(session)
    by_operator: dict[uuid.UUID, list[Event]] = defaultdict(list)
    for e in events:
        by_operator[e.operator_id].append(e)
    feed = [e for e in events if e.points >= min_points or e.kind in ("settled", "record")]
    unlocked = [
        Event(operator_id, "achievement", 0, at, f"Unlocked {a.emoji} {a.name}")
        for operator_id, own in by_operator.items()
        for a, at in _earned(own)
    ]
    newest = sorted([*feed, *unlocked], key=lambda e: e.at, reverse=True)[:limit]
    operators, agents = await _names(session, newest)
    logins = {i: o.login for i, o in operators.items()}
    return [event_out(e, logins, agents) for e in newest]


async def progress(session: AsyncSession, operator: Operator, recent: int = 12) -> dict[str, Any]:
    own = [e for e in await ledger(session) if e.operator_id == operator.id]
    xp = sum(e.points for e in own)
    week = now() - timedelta(days=7)
    earned = achievements_of(own)
    have = {a["id"] for a in earned}
    _, agents = await _names(session, own)
    return {
        "xp": xp,
        "xp_week": sum(e.points for e in own if e.at >= week),
        "rank": rank_for(max(xp, 0)),
        "achievements": earned,
        "locked": [achievement_out(a) for a in ACHIEVEMENTS if a.id not in have],
        "recent": [event_out(e, {operator.id: operator.login}, agents) for e in reversed(own[-recent:])],
    }
