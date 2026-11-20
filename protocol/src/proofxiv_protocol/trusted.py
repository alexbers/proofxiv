"""Decisive tasks of the weekly trusted run and the signed results they produce."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from .enums import (
    DecisiveTaskKind,
    ModerationAction,
    NoveltyCategory,
    ProblemStatus,
)
from .problems import SyncReport
from .reviews import ReviewBody

TRUSTED_RESULT_TYPE = "proofxiv.trusted_result.v1"

REVIEW_TASKS = frozenset(
    {
        DecisiveTaskKind.NOVELTY_CONSISTENCY,
        DecisiveTaskKind.NOVELTY_AUDIT,
        DecisiveTaskKind.ANCHOR_REVIEW,
        DecisiveTaskKind.STATEMENT_AUDIT,
        DecisiveTaskKind.HELDOUT_REVIEW,
    }
)


class TaskTarget(BaseModel):
    paper_id: UUID | None = None
    version: int | None = None
    problem_id: UUID | None = None
    event_id: UUID | None = None
    report_id: UUID | None = None


class DecisiveTaskSpec(BaseModel):
    """What the weekly run receives for one decisive task."""

    task_id: UUID
    kind: DecisiveTaskKind
    target: TaskTarget
    priority: float
    prompt_name: str
    prompt_version: int
    prompt_sha256: str
    adversarial: bool = False
    avoid_vendors: list[str] = Field(
        default_factory=list, description="Vendors already used for this paper's anchor reviews"
    )
    materials_sha256: str
    materials: dict[str, Any] = Field(description="Paper, claims, runs, reviews, report, ...")


class ModerationDecision(BaseModel):
    report_id: UUID
    upheld: bool
    action: ModerationAction
    label: str | None = None
    reasoning: str = Field(min_length=10)


class ConfirmDecision(BaseModel):
    confirmed: bool
    reasoning: str = Field(min_length=10)
    new_status: ProblemStatus | None = None
    records_verified: bool = Field(
        default=False,
        description="problems only: every best-known value in the proposal was checked against its source",
    )


class TrustedResult(BaseModel):
    """Payload of the signed bundle produced by a trusted client for one decisive task."""

    type: str = TRUSTED_RESULT_TYPE
    task_id: UUID
    kind: DecisiveTaskKind
    materials_sha256: str
    prompt_sha256: str
    vendor: str = Field(description="e.g. 'anthropic', 'openai', 'google'")
    model: str
    harness: str
    request_ids: list[str] = Field(default_factory=list)
    transcript_sha256: str | None = None
    client_version: str
    review: ReviewBody | None = None
    novelty_category: NoveltyCategory | None = None
    resolves_problem_slugs: list[str] = Field(
        default_factory=list,
        description="Catalog problems the paper settles completely (applied when it reaches C3)",
    )
    moderation: ModerationDecision | None = None
    confirm: ConfirmDecision | None = None
    sync: SyncReport | None = None
    digest_md: str | None = None
    issued_at: str
    nonce: str

    @model_validator(mode="after")
    def _shape(self) -> TrustedResult:
        kind = self.kind
        if kind in REVIEW_TASKS and self.review is None:
            raise ValueError(f"{kind} needs a review")
        if kind in (DecisiveTaskKind.NOVELTY_CONSISTENCY, DecisiveTaskKind.NOVELTY_AUDIT) and (
            self.novelty_category is None
        ):
            raise ValueError(f"{kind} needs a novelty category")
        if kind is DecisiveTaskKind.MODERATION and self.moderation is None:
            raise ValueError("moderation needs a decision")
        if kind in (DecisiveTaskKind.CONFIRM_PROBLEM, DecisiveTaskKind.CONFIRM_EVENT) and (
            self.confirm is None
        ):
            raise ValueError(f"{kind} needs a confirm decision")
        if kind is DecisiveTaskKind.SYNC and self.sync is None:
            raise ValueError("sync needs a sync report")
        if kind is DecisiveTaskKind.DIGEST and not self.digest_md:
            raise ValueError("digest needs text")
        return self
