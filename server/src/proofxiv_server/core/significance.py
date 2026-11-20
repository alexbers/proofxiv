"""Significance of a paper's result (design 6.5): how much it matters, never whether it is true.

    significance = trust × novelty × (1 + importance) × progress + attention

- importance: the most important catalog problem the paper addresses (Problem.importance:
  curated lists, prizes, activity on the platform);
- progress, the largest of
    - for a numeric record: log2(1 + gain / usual step), at most 4, where the usual step is the
      median earlier improvement of that quantity, or 1% of the previous value when the record
      table has no history yet: R(5,5;3) from 88 to 98 is large, +1 to τ19 is small;
    - 4 when the platform confirmed that the paper settles a catalog problem, 3 while the
      authors only claim it (paper.json "settles");
    - the reviewers' significance score, 1-5 mapped to 0-4 (decisive reviews if any, else
      exchange reviews; the author's own reviews do not count);
  a record's step counts half when reviewers say it comes from more computation with a known
  method, and 0.3 when it comes from parameter tuning;
    - 1 for any result without other evidence;
- trust: C3 1, C2 0.8, C1 0.5, C0 0.15, disputed or retracted 0; × 0.6 when superseded, × 0.3
  when prior art was found, the machine check failed or a decisive review said no;
- novelty: category (new and record improvement 1, with precedents 0.6, rediscovery 0.15, found
  in the literature 0.1, not yet assessed 0.8) × status (platform-confirmed 1, peer-confirmed 0.8,
  claimed 0.6, disputed 0.1);
- attention: 0.25 × ln(1 + likes + follows).

The reasons behind the number are stored with it and shown to readers.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import datetime
from typing import Any

from proofxiv_protocol.enums import (
    Bound,
    ImprovementSource,
    NoveltyCategory,
    NoveltyStatus,
    PaperLabel,
    ProblemStatus,
    ReviewOrigin,
    SignalKind,
    TrustLevel,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Claim,
    Paper,
    PaperProblem,
    PaperVersion,
    Problem,
    Quantity,
    RecordEntry,
    Review,
    Signal,
)
from .importance import external_signals
from .levels import _settled_by
from .problems import value_to_float

TRUST = {
    TrustLevel.C4: 1.0,
    TrustLevel.C3: 1.0,
    TrustLevel.C2: 0.8,
    TrustLevel.C1: 0.5,
    TrustLevel.C0: 0.15,
}
LABEL_FACTOR = {
    PaperLabel.SUPERSEDED.value: 0.6,
    PaperLabel.PRIOR_ART_FOUND.value: 0.3,
    PaperLabel.MACHINE_CHECK_FAILED.value: 0.3,
    PaperLabel.PLATFORM_DECLINED.value: 0.3,
}
CATEGORY = {
    NoveltyCategory.NEW: 1.0,
    NoveltyCategory.RECORD_IMPROVEMENT: 1.0,
    NoveltyCategory.NEW_WITH_PRECEDENTS: 0.6,
    NoveltyCategory.REDISCOVERY: 0.15,
    NoveltyCategory.FOUND_IN_LITERATURE: 0.1,
}
STATUS = {
    NoveltyStatus.PLATFORM_CONFIRMED: 1.0,
    NoveltyStatus.PEER_CONFIRMED: 0.8,
    NoveltyStatus.CLAIMED: 0.6,
    NoveltyStatus.DISPUTED: 0.1,
}
MAX_PROGRESS = 4.0
# a record from more search with a known method counts less than one from a new idea
SOURCE_FACTOR = {
    ImprovementSource.NEW_IDEA: 1.0,
    ImprovementSource.MORE_COMPUTE: 0.5,
    ImprovementSource.TUNING: 0.3,
}
SOURCE_NOTE = {
    ImprovementSource.NEW_IDEA: "a new idea, not just more search",
    ImprovementSource.MORE_COMPUTE: "more computation with a known method",
    ImprovementSource.TUNING: "parameter tuning of a known method",
}
CLAIMED_SETTLE = 3.0
# the reviewers' significance score 1..5 as a word (the scale of the round-1 summary)
TIERS = ("trivial", "incremental", "solid", "strong", "breakthrough")
KNOWN = "known"  # a rediscovery: the result may be strong, but it is not new


async def usual_step(
    session: AsyncSession, quantity_id: Any, bound: Bound, exclude_paper: Any, previous: str
) -> tuple[float, bool]:
    """(usual step, whether it comes from history): the median earlier improvement of the best
    value of this quantity and bound, else 1% of the previous value."""
    rows = (
        await session.execute(
            select(RecordEntry.value_float)
            .where(
                RecordEntry.quantity_id == quantity_id,
                RecordEntry.bound == bound,
                (RecordEntry.paper_id.is_(None)) | (RecordEntry.paper_id != exclude_paper),
            )
            .order_by(
                func.coalesce(RecordEntry.recorded_on, func.date(RecordEntry.created_at)),
                RecordEntry.created_at,
            )
        )
    ).scalars()
    best: float | None = None
    gains: list[float] = []
    for v in rows:
        if best is None:
            best = v
            continue
        if (v > best) if bound is Bound.LOWER else (v < best):
            gains.append(abs(v - best))
            best = v
    if len(gains) >= 2:
        return statistics.median(gains), True
    return max(abs(value_to_float(previous)) * 0.01, 1e-12), False


def _change(gain: float, previous: str, bound: Bound) -> str:
    """'11% higher', '44% lower', '12,678× the previous value' for readers."""
    base = abs(value_to_float(previous))
    if base == 0:
        return f"+{gain:g}"
    if bound is Bound.LOWER and gain >= base:  # at least doubled
        ratio = (base + gain) / base
        return f"{ratio:,.0f}× the previous value" if ratio >= 10 else f"{ratio:.1f}× the previous value"
    rel = gain / base * 100
    pct = f"{rel:.0f}%" if rel >= 10 else f"{rel:.1f}%" if rel >= 1 else f"{rel:.2f}%" if rel >= 0.01 else "<0.01%"
    return f"{pct} {'higher' if bound is Bound.LOWER else 'lower'}"


async def compute(session: AsyncSession, paper: Paper) -> tuple[float, list[str]]:
    if paper.hidden:
        return 0.0, []
    trust = TRUST.get(paper.level, 0.0)
    for label in paper.labels:
        trust *= LABEL_FACTOR.get(label, 1.0)
    novelty = CATEGORY.get(paper.novelty_category, 0.8) if paper.novelty_category else 0.8
    novelty *= STATUS.get(paper.novelty_status, 0.6)

    problems = list(
        (
            await session.execute(
                select(Problem)
                .join(PaperProblem, PaperProblem.problem_id == Problem.id)
                .where(PaperProblem.paper_id == paper.id, Problem.hidden.is_(False))
            )
        ).scalars()
    )
    top = max(problems, key=lambda p: p.importance, default=None)
    importance = top.importance if top else 0.0

    progress, notes = 1.0, []
    settled = [
        p for p in problems
        if p.status is ProblemStatus.SOLVED_ON_PLATFORM and await _settled_by(session, p) == paper.id
    ]
    if settled:
        progress = MAX_PROGRESS
        notes.append(f"Settles “{settled[0].title}”")
    else:
        claimed = await _claimed_settles(session, paper, problems)
        if claimed:
            progress = CLAIMED_SETTLE
            notes.append(f"Claims to settle “{claimed[0].title}”")

    claims = (
        await session.execute(
            select(Claim, Quantity)
            .join(Quantity, Quantity.id == Claim.quantity_id)
            .where(Claim.paper_id == paper.id, Claim.version == paper.current_version)
        )
    ).all()
    best_record: tuple[float, str] | None = None
    for claim, quantity in claims:
        status = claim.record_status or {}
        if status.get("status") != "new_record" or claim.value is None or claim.bound is None:
            continue
        previous = str(status.get("previous"))
        gain = abs(float(status.get("delta") or 0.0))
        step, from_history = await usual_step(session, quantity.id, claim.bound, paper.id, previous)
        magnitude = min(MAX_PROGRESS, math.log2(1 + gain / step))
        if best_record is None or magnitude > best_record[0]:
            how = _change(gain, previous, claim.bound)
            if from_history:
                how += f", {gain / step:.1f}× the usual step"
            best_record = (magnitude, f"New record for {quantity.name}: {previous} → {claim.value} ({how})")
    scores, source = await _review_signals(session, paper)
    if best_record:
        magnitude, note = best_record
        if source is not None:
            origin, how = source
            magnitude *= SOURCE_FACTOR.get(how, 1.0)
            note += f"; {SOURCE_NOTE[how]} ({origin} reviews)" if how in SOURCE_NOTE else ""
        notes.append(note)
        progress = max(progress, magnitude)

    if scores:
        origin, mean = scores
        progress = max(progress, mean - 1)
        notes.append(f"Significance {mean:.1f}/5 in {origin} reviews")

    if top is not None:
        notes += external_signals(top, shown_only=True)[1]

    signals = (
        await session.execute(
            select(func.count()).select_from(Signal).where(
                Signal.paper_id == paper.id, Signal.kind.in_([SignalKind.LIKE, SignalKind.FOLLOW])
            )
        )
    ).scalar_one()
    if signals >= 3:
        notes.append(f"{signals} likes and follows")
    score = trust * novelty * (1 + importance) * progress + 0.25 * math.log1p(signals)
    return round(score, 3), notes[:4]


async def _claimed_settles(
    session: AsyncSession, paper: Paper, problems: list[Problem]
) -> list[Problem]:
    """Open problems the authors say this version settles (paper.json "settles")."""
    manifest = (
        await session.execute(
            select(PaperVersion.manifest).where(
                PaperVersion.paper_id == paper.id, PaperVersion.version == paper.current_version
            )
        )
    ).scalar_one_or_none() or {}
    slugs = set(manifest.get("settles") or [])
    open_states = (ProblemStatus.OPEN, ProblemStatus.PROPOSED)
    return [p for p in problems if p.slug in slugs and p.status in open_states]


async def _review_signals(
    session: AsyncSession, paper: Paper
) -> tuple[tuple[str, float] | None, tuple[str, ImprovementSource] | None]:
    """(mean significance score, most common source of improvement) from the reviews of the
    current version: decisive reviews if they say anything, else exchange reviews."""
    reviews = (
        await session.execute(
            select(Review.origin, Review.body).where(
                Review.paper_id == paper.id,
                Review.version == paper.current_version,
                Review.removed.is_(False),
                Review.origin.in_([ReviewOrigin.TRUSTED, ReviewOrigin.EXCHANGE]),
            )
        )
    ).all()
    score: tuple[str, float] | None = None
    source: tuple[str, ImprovementSource] | None = None
    for origin, label in ((ReviewOrigin.TRUSTED, "platform"), (ReviewOrigin.EXCHANGE, "peer")):
        bodies = [body for o, body in reviews if o is origin and body]
        values = [int(b["significance_score"]) for b in bodies if b.get("significance_score")]
        if values and score is None:
            score = (label, sum(values) / len(values))
        said = [
            ImprovementSource(b["improvement_source"])
            for b in bodies
            if b.get("improvement_source") not in (None, ImprovementSource.NOT_APPLICABLE.value)
        ]
        if said and source is None:
            source = (label, Counter(said).most_common(1)[0][0])
    return score, source


async def tier(session: AsyncSession, paper: Paper) -> tuple[str | None, str | None]:
    """(tier, why): the mean significance score of the decisive reviews of the current version
    (else of the exchange reviews) rounded to a word, or "known" when the result was known."""
    if paper.novelty_category in (NoveltyCategory.REDISCOVERY, NoveltyCategory.FOUND_IN_LITERATURE):
        how = "rediscovered" if paper.novelty_category is NoveltyCategory.REDISCOVERY else "found in the literature"
        return KNOWN, f"the result was already known ({how})"
    bodies = (
        await session.execute(
            select(Review.origin, Review.body).where(
                Review.paper_id == paper.id,
                Review.version == paper.current_version,
                Review.removed.is_(False),
                Review.origin.in_([ReviewOrigin.TRUSTED, ReviewOrigin.EXCHANGE]),
            )
        )
    ).all()
    for origin, label in ((ReviewOrigin.TRUSTED, "platform"), (ReviewOrigin.EXCHANGE, "peer")):
        scores = [int(b["significance_score"]) for o, b in bodies if o is origin and b and b.get("significance_score")]
        if scores:
            mean = sum(scores) / len(scores)
            word = TIERS[min(len(TIERS), max(1, math.floor(mean + 0.5))) - 1]
            plural = "s" if len(scores) > 1 else ""
            return word, f"mean {mean:.1f}/5 of {len(scores)} {label} review{plural} ({', '.join(map(str, scores))})"
    return None, None


async def update(session: AsyncSession, paper: Paper) -> None:
    paper.significance, paper.significance_notes = await compute(session, paper)
    paper.tier, paper.tier_basis = await tier(session, paper)


async def refresh_all(session: AsyncSession) -> int:
    """Periodic refresh: problem importance and attention change without touching papers."""
    papers = list((await session.execute(select(Paper).where(Paper.hidden.is_(False)))).scalars())
    for paper in papers:
        await update(session, paper)
    return len(papers)


def trending(paper: Paper, at: datetime) -> float:
    """Significance with a 30-day half-life, for 'notable lately' lists."""
    since = paper.verified_at or paper.created_at
    age_days = max((at - since).total_seconds() / 86400, 0.0)
    return paper.significance * 0.5 ** (age_days / 30)
