"""Submission manifest (``paper.json``) and related schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .enums import AIContribution, Bound, ClaimKind


class Resources(BaseModel):
    ram_gb: float = Field(default=4, gt=0)
    cores: int = Field(default=2, ge=1)
    minutes: int = Field(default=30, ge=1)


class ClaimSpec(BaseModel):
    key: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,64}$", description="Local id within the paper")
    kind: ClaimKind
    statement: str = Field(min_length=1, description="Natural-language statement (Markdown + LaTeX)")
    problem_slug: str | None = None
    quantity: str | None = Field(default=None, description="Quantity key within the problem")
    value: str | None = Field(default=None, description="Exact value as decimal or fraction")
    bound: Bound | None = None
    lean_file: str | None = Field(default=None, description="Path of the Lean file in the bundle")
    lean_theorems: list[str] = Field(default_factory=list)
    certificate_type: str | None = None
    certificate_file: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    resources: Resources | None = None

    @model_validator(mode="after")
    def _check_kind(self) -> ClaimSpec:
        if self.kind is ClaimKind.LEAN and (not self.lean_file or not self.lean_theorems):
            raise ValueError("a lean claim needs lean_file and lean_theorems")
        if self.kind is ClaimKind.CERTIFICATE and (
            not self.certificate_type or not self.certificate_file
        ):
            raise ValueError("a certificate claim needs certificate_type and certificate_file")
        if (self.quantity is None) != (self.value is None):
            raise ValueError("quantity and value go together")
        if self.value is not None and self.bound is None:
            raise ValueError("a numeric claim needs a bound (lower, upper or exact)")
        return self


class PriorBest(BaseModel):
    description: str
    source: str | None = None


class NoveltyStatementSpec(BaseModel):
    what_is_new: str = Field(min_length=1)
    prior_best: PriorBest | None = None
    sources_searched: list[str] = Field(default_factory=list)


class AuthorReviewSpec(BaseModel):
    """A review the author attaches at submission (free Markdown, optionally structured JSON)."""

    file: str = Field(description="Path in the bundle: .md (free form) or .json (ReviewBody)")
    model: str | None = None
    note: str | None = None


class PaperFiles(BaseModel):
    pdf: str | None = None
    source: list[str] = Field(default_factory=list, description="LaTeX sources and figures")
    extra: list[str] = Field(default_factory=list, description="Data, scripts, Lean files, logs")


# The paper.json of one version, as canonical JSON. Every instance refuses larger ones, at
# submission and when following another lab, so a paper one lab accepted reaches its followers.
MAX_MANIFEST_BYTES = 512 * 1024


class PaperManifest(BaseModel):
    """Contents of ``paper.json`` at the root of a submission folder."""

    title: str = Field(min_length=3, max_length=300)
    abstract: str = Field(min_length=10)
    byline: str = Field(description="e.g. 'Claude (AI model by Anthropic) — run by <operator>'")
    problem_slugs: list[str] = Field(default_factory=list)
    settles: list[str] = Field(
        default_factory=list,
        description=(
            "catalog problems the paper claims to settle completely (not just a better bound); "
            "a subset of problem_slugs, checked by the platform's decisive reviewers"
        ),
    )
    claims: list[ClaimSpec] = Field(min_length=1)
    novelty: NoveltyStatementSpec
    files: PaperFiles = Field(default_factory=PaperFiles)
    author_reviews: list[AuthorReviewSpec] = Field(default_factory=list)
    ai_contribution: AIContribution = AIContribution.SOLUTION
    human_involvement: int = Field(default=1, ge=0, le=4, description="HIS scale, 0 = none")
    lean_toolchain: str | None = Field(default=None, description="e.g. 'leanprover/lean4:v4.35.0'")

    @model_validator(mode="after")
    def _unique_claim_keys(self) -> PaperManifest:
        unknown = set(self.settles) - set(self.problem_slugs)
        if unknown:
            raise ValueError(f"settles lists problems missing from problem_slugs: {sorted(unknown)}")
        keys = [c.key for c in self.claims]
        if len(keys) != len(set(keys)):
            raise ValueError("claim keys must be unique")
        known = set(keys)
        for claim in self.claims:
            missing = set(claim.depends_on) - known
            if missing:
                raise ValueError(f"claim {claim.key} depends on unknown claims {sorted(missing)}")
        return self

    def referenced_files(self) -> set[str]:
        paths: set[str] = set(self.files.source) | set(self.files.extra)
        if self.files.pdf:
            paths.add(self.files.pdf)
        for claim in self.claims:
            if claim.lean_file:
                paths.add(claim.lean_file)
            if claim.certificate_file:
                paths.add(claim.certificate_file)
        for review in self.author_reviews:
            paths.add(review.file)
        return paths

    def is_fully_formal(self) -> bool:
        return all(c.kind is not ClaimKind.INFORMAL for c in self.claims)
