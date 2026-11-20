"""Problem proposals, record quantities and literature-sync events."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from .enums import ApproachStatus, Bound, ExternalEventKind


class SourceRef(BaseModel):
    url: str = Field(min_length=4)
    title: str | None = None
    quote: str | None = Field(default=None, description="Exact quote that states the problem")


class QuantitySpec(BaseModel):
    """A numeric quantity with a record table, e.g. the kissing number in dimension 19."""

    key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    name: str
    description: str = ""
    best_lower: str | None = None
    best_upper: str | None = None
    source: str | None = None


class ProblemProposal(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    variant: str | None = Field(
        default=None,
        max_length=200,
        description="Tells apart problems that share a title: a part, a special case, or the "
        "name of the formal statement (e.g. 'erdos_357.variants.monotone')",
    )
    part_of: str | None = Field(
        default=None,
        max_length=2000,
        description="If this is a case or a part of a larger problem: that problem and where it stands "
        "(e.g. 'The conjecture for every n; false in general: counterexamples were known for n = 9, 11, 15'). "
        "Shown on the page, so that settling this part is not read as settling the whole",
    )
    statement_md: str = Field(min_length=10)
    domain: str = Field(description="e.g. 'combinatorics', 'number theory'")
    sources: list[SourceRef] = Field(min_length=1)
    openness_evidence: str = Field(min_length=10, description="What was searched and what was found")
    importance_note: str = ""
    difficulty_note: str = ""
    formalizable: bool = False
    lean_statement: str | None = None
    related_slugs: list[str] = Field(default_factory=list)
    quantities: list[QuantitySpec] = Field(default_factory=list)


class ExternalEventSpec(BaseModel):
    """Something that happened in the literature and matters for a problem or a paper."""

    kind: ExternalEventKind
    problem_slug: str | None = None
    paper_id: UUID | None = None
    source_url: str = Field(min_length=4)
    source_title: str | None = None
    published_on: date | None = None
    summary: str = Field(min_length=10)
    quantity: str | None = None
    value: str | None = None
    bound: Bound | None = None

    @model_validator(mode="after")
    def _target(self) -> ExternalEventSpec:
        if self.problem_slug is None and self.paper_id is None:
            raise ValueError("an event must refer to a problem or a paper")
        if self.kind is ExternalEventKind.RECORD_IMPROVED and (
            self.quantity is None or self.value is None or self.bound is None
        ):
            raise ValueError("a record event needs quantity, value and bound")
        return self


class ApproachSource(BaseModel):
    kind: str = Field(description="paper (on the platform) | attempt | literature")
    ref: str = Field(description="paper or attempt id, or a URL")
    title: str | None = None


class ApproachSpec(BaseModel):
    """One line of the problem's map of approaches: what was tried and where it stops."""

    name: str = Field(min_length=3, max_length=120, description="e.g. 'Paley-type constructions'")
    status: ApproachStatus
    summary_md: str = Field(min_length=10, max_length=4000, description="the idea and its best result")
    barrier_md: str = Field(
        default="", max_length=4000, description="why it stops there (required unless succeeded)"
    )
    sources: list[ApproachSource] = Field(default_factory=list)

    @model_validator(mode="after")
    def _barrier(self) -> ApproachSpec:
        if self.status in (ApproachStatus.STALLED, ApproachStatus.REFUTED) and len(self.barrier_md) < 10:
            raise ValueError("a stalled or refuted approach needs barrier_md: why it stops")
        return self


class SyncReport(BaseModel):
    """Result of checking problems against the literature for a period."""

    problem_slugs: list[str] = Field(min_length=1)
    period_from: date | None = None
    sources_checked: list[str] = Field(default_factory=list)
    events: list[ExternalEventSpec] = Field(default_factory=list)
    approaches: dict[str, list[ApproachSpec]] = Field(
        default_factory=dict, description="problem slug -> approaches found in the literature"
    )
    notes: str = ""
