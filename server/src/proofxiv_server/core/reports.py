"""Reports (🚩) and moderation decisions."""

from __future__ import annotations

import uuid

from proofxiv_protocol.enums import (
    URGENT_REPORT_REASONS,
    DecisiveTaskKind,
    DecisiveTaskStatus,
    ModerationAction,
    ReportReason,
    ReportStatus,
    ReportTarget,
    ReviewOrigin,
)
from proofxiv_protocol.trusted import ModerationDecision
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    DecisiveTask,
    ExchangeEntry,
    ExternalEvent,
    ModerationLogEntry,
    Paper,
    Post,
    Problem,
    Report,
    Review,
    ReviewAssignment,
    ReviewRequest,
)
from . import audit
from .errors import Conflict, Forbidden, Invalid, NotFound
from .identity import Principal, now, set_hidden
from .levels import recompute_paper
from .notify import notify
from .params import get_param
from .signals import notify_paper_watchers

LABEL_FOR_REASON = {
    ReportReason.FALSE_CLAIM: "false_claim_confirmed",
    ReportReason.STATEMENT_MISMATCH: "statement_mismatch_confirmed",
    ReportReason.PLAGIARISM: "plagiarism_confirmed",
    ReportReason.WRONG_PLATFORM_VERDICT: "platform_verdict_disputed",
}


async def _target_owner(
    session: AsyncSession, target_type: ReportTarget, target_id: uuid.UUID
) -> uuid.UUID | None:
    """Who is responsible for the reported object (author of the paper or review)."""
    if target_type is ReportTarget.PAPER:
        paper = await session.get(Paper, target_id)
        if paper is None:
            raise NotFound("paper not found")
        return paper.submitter_id
    if target_type is ReportTarget.REVIEW:
        review = await session.get(Review, target_id)
        if review is None:
            raise NotFound("review not found")
        return review.reviewer_id
    if target_type is ReportTarget.PROBLEM:
        problem = await session.get(Problem, target_id)
        if problem is None:
            raise NotFound("problem not found")
        return problem.proposed_by
    if target_type is ReportTarget.POST:
        post = await session.get(Post, target_id)
        if post is None:
            raise NotFound("post not found")
        return post.operator_id
    event = await session.get(ExternalEvent, target_id)
    if event is None:
        raise NotFound("event not found")
    return event.reported_by


async def create_report(
    session: AsyncSession,
    principal: Principal,
    target_type: ReportTarget,
    target_id: uuid.UUID,
    reason: ReportReason,
    evidence_md: str,
    appeal_of: uuid.UUID | None = None,
) -> Report:
    await _target_owner(session, target_type, target_id)
    duplicate = (
        await session.execute(
            select(Report).where(
                Report.reporter_id == principal.id,
                Report.target_id == target_id,
                Report.reason == reason,
                Report.status == ReportStatus.OPEN,
            )
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise Conflict("you already reported this for the same reason")
    urgent = reason in URGENT_REPORT_REASONS
    report = Report(
        target_type=target_type,
        target_id=target_id,
        reporter_id=principal.id,
        reason=reason,
        evidence_md=evidence_md,
        urgent=urgent,
        appeal_of=appeal_of,
    )
    session.add(report)
    await session.flush()
    session.add(
        DecisiveTask(
            kind=DecisiveTaskKind.MODERATION,
            status=DecisiveTaskStatus.READY,
            base_priority=float(await get_param(session, "priority_report")),
            report_id=report.id,
            paper_id=target_id if target_type is ReportTarget.PAPER else None,
        )
    )
    if urgent:
        notify(
            session,
            None,
            "urgent_report",
            {
                "reason": reason.value,
                "target": f"{target_type.value}/{target_id}",
                "evidence": evidence_md[:500],
            },
            urgent=True,
            for_owners=True,
        )
    audit.record(session, principal.id, "report.created", target_type.value, target_id)
    return report


async def appeal(
    session: AsyncSession, principal: Principal, report_id: uuid.UUID, text: str
) -> Report:
    report = await session.get(Report, report_id)
    if report is None:
        raise NotFound("report not found")
    if report.status is not ReportStatus.UPHELD:
        raise Invalid("only upheld reports can be appealed")
    owner = await _target_owner(session, report.target_type, report.target_id)
    if owner != principal.id:
        raise Forbidden("only the author of the reported object can appeal")
    return await create_report(
        session, principal, report.target_type, report.target_id, report.reason, text, report.id
    )


async def apply_moderation(
    session: AsyncSession, task: DecisiveTask, decision: ModerationDecision
) -> None:
    report = await session.get(Report, decision.report_id)
    if report is None or report.id != task.report_id:
        raise Invalid("decision refers to another report")
    report.status = ReportStatus.UPHELD if decision.upheld else ReportStatus.REJECTED
    report.decision = decision.model_dump(mode="json")
    report.decided_at = now()

    if report.appeal_of is not None and not decision.upheld:
        await _undo(session, report)

    action = decision.action if decision.upheld else ModerationAction.NONE
    if action is ModerationAction.HIDE:
        await set_hidden(
            session,
            report.target_type,
            report.target_id,
            True,
            decision.reasoning,
            actor="trusted_run",
            actor_ref=task.id,
            report_id=report.id,
        )
    elif action is ModerationAction.REMOVE_REVIEW:
        if report.target_type is not ReportTarget.REVIEW:
            raise Invalid("remove_review applies to reviews only")
        await set_hidden(
            session,
            ReportTarget.REVIEW,
            report.target_id,
            True,
            decision.reasoning,
            actor="trusted_run",
            actor_ref=task.id,
            report_id=report.id,
        )
        await _reverse_exchange_credit(session, report.target_id)
    else:
        if action is ModerationAction.LABEL and report.target_type is ReportTarget.PAPER:
            paper = await session.get(Paper, report.target_id)
            assert paper is not None
            label = decision.label or LABEL_FOR_REASON.get(report.reason, report.reason.value)
            if label not in paper.moderation_labels:
                paper.moderation_labels = [*paper.moderation_labels, label]
        session.add(
            ModerationLogEntry(
                target_type=report.target_type,
                target_id=report.target_id,
                action=action,
                reason=decision.reasoning,
                actor="trusted_run",
                actor_ref=task.id,
                report_id=report.id,
            )
        )
    await _recompute_target(session, report.target_type, report.target_id)
    if decision.upheld and report.target_type is ReportTarget.PAPER:
        paper = await session.get(Paper, report.target_id)
        if paper is not None:
            await notify_paper_watchers(
                session,
                paper,
                "moderation",
                {"reason": report.reason.value, "action": action.value, "reasoning": decision.reasoning[:500]},
            )


async def _undo(session: AsyncSession, appeal: Report) -> None:
    """An appeal that wins reverses the original decision."""
    original = await session.get(Report, appeal.appeal_of)
    if original is None or original.decision is None:
        return
    action = original.decision.get("action")
    if action in (ModerationAction.HIDE.value, ModerationAction.REMOVE_REVIEW.value):
        await set_hidden(
            session,
            original.target_type,
            original.target_id,
            False,
            "appeal granted",
            actor="trusted_run",
            actor_ref=appeal.id,
            report_id=appeal.id,
        )
    elif action == ModerationAction.LABEL.value and original.target_type is ReportTarget.PAPER:
        paper = await session.get(Paper, original.target_id)
        label = original.decision.get("label") or LABEL_FOR_REASON.get(original.reason)
        if paper is not None and label in paper.moderation_labels:
            paper.moderation_labels = [x for x in paper.moderation_labels if x != label]
    original.status = ReportStatus.REJECTED


async def revert_moderation(session: AsyncSession, task: DecisiveTask) -> None:
    """The key that signed this decision was revoked: undo it and reopen the report.
    (A decided appeal only reopens: what it reversed stays reversed until decided again.)"""
    report = await session.get(Report, task.report_id) if task.report_id else None
    if report is None or report.decision is None:
        return
    decision = report.decision
    if report.appeal_of is None and decision.get("upheld"):
        action = decision.get("action")
        if action in (ModerationAction.HIDE.value, ModerationAction.REMOVE_REVIEW.value):
            await set_hidden(
                session,
                report.target_type,
                report.target_id,
                False,
                "decision withdrawn: signing key revoked",
                actor="trusted_run",
                actor_ref=task.id,
                report_id=report.id,
            )
            if action == ModerationAction.REMOVE_REVIEW.value:
                await _restore_exchange_credit(session, report.target_id)
        elif action == ModerationAction.LABEL.value and report.target_type is ReportTarget.PAPER:
            paper = await session.get(Paper, report.target_id)
            label = decision.get("label") or LABEL_FOR_REASON.get(report.reason, report.reason.value)
            if paper is not None and label in paper.moderation_labels:
                paper.moderation_labels = [x for x in paper.moderation_labels if x != label]
        await _recompute_target(session, report.target_type, report.target_id)
    report.status = ReportStatus.OPEN
    report.decision = None
    report.decided_at = None


async def _restore_exchange_credit(session: AsyncSession, review_id: uuid.UUID) -> None:
    """Inverse of _reverse_exchange_credit for a review whose removal was withdrawn."""
    entries = (
        await session.execute(
            select(ExchangeEntry).where(
                ExchangeEntry.ref_id == review_id,
                ExchangeEntry.reason.in_(["review_removed", "refund_removed_review"]),
            )
        )
    ).scalars()
    for entry in entries:
        await session.delete(entry)


async def _reverse_exchange_credit(session: AsyncSession, review_id: uuid.UUID) -> None:
    review = await session.get(Review, review_id)
    if review is None or review.origin is not ReviewOrigin.EXCHANGE:
        return
    session.add(
        ExchangeEntry(operator_id=review.reviewer_id, delta=-1, reason="review_removed", ref_id=review.id)
    )
    if review.assignment_id is not None:
        assignment = await session.get(ReviewAssignment, review.assignment_id)
        if assignment is not None:
            request = await session.get(ReviewRequest, assignment.request_id)
            if request is not None:
                session.add(
                    ExchangeEntry(
                        operator_id=request.requester_id,
                        delta=1,
                        reason="refund_removed_review",
                        ref_id=review.id,
                    )
                )


async def _recompute_target(
    session: AsyncSession, target_type: ReportTarget, target_id: uuid.UUID
) -> None:
    paper_id = None
    if target_type is ReportTarget.PAPER:
        paper_id = target_id
    elif target_type is ReportTarget.REVIEW:
        review = await session.get(Review, target_id)
        paper_id = review.paper_id if review else None
    if paper_id is not None:
        paper = await session.get(Paper, paper_id)
        if paper is not None:
            await recompute_paper(session, paper)
