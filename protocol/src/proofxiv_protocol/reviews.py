"""Structured review format shared by author, exchange and trusted reviews."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field

from .enums import ImprovementSource, NoveltyCategory, Recommendation, Severity

MIN_SUMMARY_CHARS = 80
# Shown to reviewers in the JSON schema: one-line walls of text are hard to read on the site.
PROSE = (
    "Markdown; formulas in $...$. Short paragraphs separated by a blank line, and lists for steps, "
    "cases or items; never one long paragraph."
)
SIGNIFICANCE_SCALE = (
    "How much the result matters, judged against the history of the problem (not the writing); "
    "shown on the site as a word: 1 trivial: an elementary observation, or a known technique giving a "
    "small step; 2 incremental: a modest improvement; 3 solid: clear progress on a recognised problem; "
    "4 strong: a major advance, or settles a well-known open problem; 5 breakthrough: a landmark "
    "result. A record that beats the previous one by its usual step is 1-2."
)


class Reference(BaseModel):
    title: str
    url: str | None = None
    note: str | None = None


class Issue(BaseModel):
    location: str = Field(description="Where in the paper, e.g. 'Section 3, Lemma 2' or 'p. 4'")
    severity: Severity
    description: str = Field(description=PROSE)
    suggested_fix: str | None = None


class CodeCheck(BaseModel):
    """Code the reviewer ran to check a claim. For trusted reviews it ran in the sandbox."""

    description: str = Field(description=PROSE)
    language: str = "python"
    code: str
    output_summary: str


class IssueResponse(BaseModel):
    """A decisive reviewer's answer to a serious issue raised in another review."""

    review_id: UUID
    issue_index: int
    verdict: str = Field(pattern="^(accepted|rejected|converted_to_check)$")
    reasoning: str = Field(description=PROSE)


class NoveltyAssessment(BaseModel):
    summary: str = Field(description=PROSE)
    category: NoveltyCategory | None = None
    sources_checked: list[str] = Field(default_factory=list)
    prior_work: list[Reference] = Field(default_factory=list)


class ReviewBody(BaseModel):
    summary: str = Field(description=PROSE)
    correctness: str = Field(description=PROSE)
    statement_faithfulness: str = Field(description=PROSE)
    novelty: NoveltyAssessment
    significance: str = Field(description=PROSE)
    significance_score: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description=SIGNIFICANCE_SCALE,
    )
    improvement_source: ImprovementSource | None = Field(
        default=None,
        description=(
            "for a better numeric value: new_idea (a new construction or argument), more_compute "
            "(a known method with more search or computation), tuning (parameter tuning of a "
            "known method); not_applicable without a numeric record"
        ),
    )
    reproducibility: str = Field(default="", description=PROSE)
    issues: list[Issue] = Field(default_factory=list)
    checks: list[CodeCheck] = Field(default_factory=list)
    responses: list[IssueResponse] = Field(default_factory=list)
    recommendation: Recommendation
    confidence: int = Field(ge=1, le=5)
    model: str = Field(description="Model that produced the review, as declared")
    harness: str | None = Field(default=None, description="Agent shell, e.g. 'claude-code 2.x'")

    def exchange_problems(self) -> list[str]:
        """Reasons why this review cannot count in the review exchange (empty if fine)."""
        problems: list[str] = []
        if len(self.summary.strip()) < MIN_SUMMARY_CHARS:
            problems.append(f"summary shorter than {MIN_SUMMARY_CHARS} characters")
        for name in ("correctness", "statement_faithfulness", "significance"):
            if not getattr(self, name).strip():
                problems.append(f"field '{name}' is empty")
        if not self.novelty.summary.strip():
            problems.append("novelty summary is empty")
        if not self.issues and self.recommendation is not Recommendation.ACCEPT:
            problems.append("a non-accept recommendation must list issues")
        for i, issue in enumerate(self.issues):
            if not issue.location.strip():
                problems.append(f"issue {i} has no location in the paper")
        return problems

    def unresolved_serious_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.severity in (Severity.CRITICAL, Severity.MAJOR)]
