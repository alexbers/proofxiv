"""What the model must return for each kind of task (JSON schemas passed to the harness)."""

from __future__ import annotations

from typing import Any

from proofxiv_protocol.enums import (
    ImprovementSource,
    ModerationAction,
    NoveltyCategory,
    Recommendation,
)
from proofxiv_protocol.problems import ProblemProposal, SyncReport
from proofxiv_protocol.reviews import (
    PROSE,
    SIGNIFICANCE_SCALE,
    CodeCheck,
    Issue,
    IssueResponse,
    NoveltyAssessment,
    ReviewBody,
)
from proofxiv_protocol.trusted import ConfirmDecision
from pydantic import BaseModel, Field


class ReviewDraft(BaseModel):
    summary: str = Field(description=PROSE)
    correctness: str = Field(description=PROSE)
    statement_faithfulness: str = Field(description=PROSE)
    novelty: NoveltyAssessment
    significance: str = Field(description=PROSE)
    significance_score: int | None = Field(default=None, ge=1, le=5, description=SIGNIFICANCE_SCALE)
    improvement_source: ImprovementSource | None = Field(
        default=None,
        description="for a better numeric value: new_idea | more_compute | tuning; not_applicable otherwise",
    )
    reproducibility: str = Field(default="", description=PROSE)
    issues: list[Issue] = Field(default_factory=list)
    checks: list[CodeCheck] = Field(default_factory=list)
    responses: list[IssueResponse] = Field(default_factory=list)
    recommendation: Recommendation
    confidence: int = Field(ge=1, le=5)

    def to_body(self, model: str, harness: str) -> ReviewBody:
        return ReviewBody(**self.model_dump(), model=model, harness=harness)


class ReviewOutput(BaseModel):
    review: ReviewDraft
    novelty_category: NoveltyCategory | None = Field(
        default=None, description="Required for novelty tasks"
    )
    resolves_problem_slugs: list[str] = Field(
        default_factory=list,
        description="Slugs of catalog problems (materials.paper.problems) this paper settles "
        "completely; empty if it only makes progress",
    )


class ModerationOutput(BaseModel):
    upheld: bool
    action: ModerationAction
    label: str | None = None
    reasoning: str = Field(min_length=10)


class ConfirmOutput(BaseModel):
    confirm: ConfirmDecision
    novelty_category: NoveltyCategory | None = None


class SyncOutput(BaseModel):
    sync: SyncReport


class DigestOutput(BaseModel):
    digest_md: str = Field(min_length=20)


class DiscoverOutput(BaseModel):
    problems: list[ProblemProposal]


OUTPUT_FOR_KIND: dict[str, type[BaseModel]] = {
    "novelty_consistency": ReviewOutput,
    "novelty_audit": ReviewOutput,
    "anchor_review": ReviewOutput,
    "statement_audit": ReviewOutput,
    "heldout_review": ReviewOutput,
    "moderation": ModerationOutput,
    "confirm_problem": ConfirmOutput,
    "confirm_event": ConfirmOutput,
    "sync": SyncOutput,
    "digest": DigestOutput,
}

WEB_KINDS = {"novelty_consistency", "novelty_audit", "anchor_review", "statement_audit",
             "heldout_review", "moderation", "confirm_problem", "confirm_event", "sync"}


def schema_for(model: type[BaseModel]) -> dict[str, Any]:
    return model.model_json_schema()
