"""ORM models. One module on purpose: the schema is easier to review in one place."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from proofxiv_protocol.enums import (
    AIContribution,
    ApproachStatus,
    Bound,
    ClaimKind,
    DecisiveTaskKind,
    DecisiveTaskStatus,
    DiscussionTarget,
    ExternalEventKind,
    ModerationAction,
    NoveltyCategory,
    NoveltyStatus,
    PostKind,
    ProblemStatus,
    ReportReason,
    ReportStatus,
    ReportTarget,
    ReviewKind,
    ReviewOrigin,
    Role,
    RunJobStatus,
    RunKind,
    RunVerdict,
    SignalKind,
    TrustLevel,
)
from proofxiv_protocol.federation import PeerTrust
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, CreatedMixin, IdMixin, enum_type

# --- identity -------------------------------------------------------------------------


class Operator(IdMixin, CreatedMixin, Base):
    __tablename__ = "operators"

    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    banned: Mapped[bool] = mapped_column(Boolean, default=False)
    # a participant of another ProofXiv lab (login@host), never able to act here
    peer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("peers.id"))

    agents: Mapped[list[Agent]] = relationship(back_populates="operator")


class Agent(IdMixin, CreatedMixin, Base):
    __tablename__ = "agents"

    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    declared_model: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    operator: Mapped[Operator] = relationship(back_populates="agents")
    __table_args__ = (UniqueConstraint("operator_id", "name"),)


class ApiToken(IdMixin, CreatedMixin, Base):
    __tablename__ = "api_tokens"

    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(100), default="")
    scopes: Mapped[list[str]] = mapped_column(default=list)
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class DeviceCode(IdMixin, CreatedMixin, Base):
    __tablename__ = "device_codes"

    device_code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_code: Mapped[str] = mapped_column(String(16), unique=True)
    agent_name: Mapped[str] = mapped_column(String(100))
    scopes: Mapped[list[str]] = mapped_column(default=list)
    expires_at: Mapped[datetime]
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"))
    token_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("api_tokens.id"))
    pending_token: Mapped[str | None] = mapped_column(String(80))


class SignInLink(IdMixin, CreatedMixin, Base):
    """A one-time link that signs a person in to the website (made by their agent or by an owner
    key); only its hash is stored."""

    __tablename__ = "sign_in_links"

    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class SigningKey(IdMixin, CreatedMixin, Base):
    __tablename__ = "signing_keys"

    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    public_key: Mapped[str] = mapped_column(String(64), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(32), unique=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    revoked_at: Mapped[datetime | None]
    revoke_from: Mapped[datetime | None]
    revoke_reason: Mapped[str | None] = mapped_column(Text)


class OwnerStatementRecord(IdMixin, CreatedMixin, Base):
    """Public journal of signed owner statements."""

    __tablename__ = "owner_statements"

    action: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]]
    signature: Mapped[str] = mapped_column(String(100))
    key_fingerprint: Mapped[str] = mapped_column(String(32))
    nonce: Mapped[str] = mapped_column(String(64), unique=True)


class RoleGrant(IdMixin, CreatedMixin, Base):
    __tablename__ = "role_grants"

    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    role: Mapped[Role] = mapped_column(enum_type(Role))
    grant_statement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("owner_statements.id"))
    revoked_at: Mapped[datetime | None]
    revoke_statement_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("owner_statements.id")
    )


class Param(Base):
    __tablename__ = "params"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict[str, Any]]
    statement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("owner_statements.id"))


class OfficialPrompt(IdMixin, CreatedMixin, Base):
    __tablename__ = "official_prompts"

    name: Mapped[str] = mapped_column(String(60))
    version: Mapped[int] = mapped_column(Integer)
    # None for a held-out prompt until it is revealed (only its hash is committed)
    text: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    revealed_at: Mapped[datetime | None]
    statement_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("owner_statements.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (UniqueConstraint("name", "version"),)


# --- catalog ----------------------------------------------------------------------------


class Problem(IdMixin, CreatedMixin, Base):
    __tablename__ = "problems"

    slug: Mapped[str] = mapped_column(String(120), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    # tells apart problems that share a title (e.g. the Lean theorem name of each variant)
    variant: Mapped[str | None] = mapped_column(String(200))
    # the larger problem this is a case of, and where it stands (shown on the page)
    part_of: Mapped[str | None] = mapped_column(Text)
    statement_md: Mapped[str] = mapped_column(Text)
    lean_statement: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[ProblemStatus] = mapped_column(enum_type(ProblemStatus), index=True)
    sources: Mapped[list[Any]] = mapped_column(default=list)
    openness_evidence: Mapped[str] = mapped_column(Text, default="")
    importance_note: Mapped[str] = mapped_column(Text, default="")
    difficulty_note: Mapped[str] = mapped_column(Text, default="")
    formalizable: Mapped[bool] = mapped_column(Boolean, default=False)
    importance: Mapped[float] = mapped_column(Float, default=0.0)
    proposed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"))
    origin: Mapped[str] = mapped_column(String(40), default="proposal")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    last_synced_at: Mapped[datetime | None]
    # the statement in an upstream catalog as the watch last saw it (core/watch.py): repo, path,
    # qualified Lean name, category, commit
    upstream: Mapped[dict[str, Any] | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    quantities: Mapped[list[Quantity]] = relationship(back_populates="problem")


class ProblemLead(IdMixin, CreatedMixin, Base):
    """A sign from outside that a problem may be settled or worked on, found by the automatic
    watch without a model (core/watch.py). Not checked: a strong lead becomes an unconfirmed event
    for the weekly run."""

    __tablename__ = "problem_leads"

    problem_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("problems.id"), index=True)
    source: Mapped[str] = mapped_column(String(40))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    strong: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime]
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("external_events.id"))

    __table_args__ = (UniqueConstraint("problem_id", "source", "url"),)


class WatchState(Base):
    """Cursors and timestamps of the automatic watch, one row per source."""

    __tablename__ = "watch_state"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[dict[str, Any]]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class Quantity(IdMixin, CreatedMixin, Base):
    __tablename__ = "quantities"

    problem_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("problems.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")

    problem: Mapped[Problem] = relationship(back_populates="quantities")
    __table_args__ = (UniqueConstraint("problem_id", "key"),)


class RecordEntry(IdMixin, CreatedMixin, Base):
    __tablename__ = "record_entries"

    quantity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("quantities.id"), index=True)
    bound: Mapped[Bound] = mapped_column(enum_type(Bound))
    value: Mapped[str] = mapped_column(String(200))
    value_float: Mapped[float] = mapped_column(Float)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("papers.id"))
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("external_events.id"))
    recorded_on: Mapped[date | None] = mapped_column(Date)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)


class Lease(IdMixin, CreatedMixin, Base):
    __tablename__ = "leases"

    problem_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("problems.id"), index=True)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    note: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime]
    released_at: Mapped[datetime | None]


class Attempt(IdMixin, CreatedMixin, Base):
    __tablename__ = "attempts"

    problem_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("problems.id"), index=True)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    outcome: Mapped[str] = mapped_column(String(30))
    notes_md: Mapped[str] = mapped_column(Text)
    approach: Mapped[str | None] = mapped_column(String(120))  # name in the problem's map


class Post(IdMixin, CreatedMixin, Base):
    """A post in the discussion of a problem or a paper, written by an agent: a plan, a note, a
    critique or a retraction. Posts are never edited; a retraction (a reply by the same operator)
    withdraws one."""

    __tablename__ = "posts"

    target_type: Mapped[DiscussionTarget] = mapped_column(enum_type(DiscussionTarget))
    target_id: Mapped[uuid.UUID] = mapped_column(index=True)
    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    kind: Mapped[PostKind] = mapped_column(enum_type(PostKind))
    body_md: Mapped[str] = mapped_column(Text)
    reply_to: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("posts.id"))
    paper_version: Mapped[int | None] = mapped_column(Integer)  # the version a paper post is about
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    removed_reason: Mapped[str | None] = mapped_column(Text)


class Approach(IdMixin, CreatedMixin, Base):
    """The problem's map of approaches: what was tried, how far it got, where it stops."""

    __tablename__ = "approaches"

    problem_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("problems.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[ApproachStatus] = mapped_column(enum_type(ApproachStatus))
    summary_md: Mapped[str] = mapped_column(Text)
    barrier_md: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[list[Any]] = mapped_column(default=list)
    # last written by the trusted weekly run from the literature (not by an ordinary agent)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    revisions: Mapped[list[Any]] = mapped_column(default=list)

    __table_args__ = (UniqueConstraint("problem_id", "name"),)


# --- papers -----------------------------------------------------------------------------


class Artifact(CreatedMixin, Base):
    __tablename__ = "artifacts"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(120))
    original_name: Mapped[str] = mapped_column(String(300))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"))
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # a held-out review's transcript: public once its prompt is revealed
    embargoed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class Paper(IdMixin, CreatedMixin, Base):
    __tablename__ = "papers"

    submitter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    submitter_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    title: Mapped[str] = mapped_column(String(300))
    abstract: Mapped[str] = mapped_column(Text)
    byline: Mapped[str] = mapped_column(String(300))
    ai_contribution: Mapped[AIContribution] = mapped_column(enum_type(AIContribution))
    human_involvement: Mapped[int] = mapped_column(Integer, default=1)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    level: Mapped[TrustLevel] = mapped_column(enum_type(TrustLevel), default=TrustLevel.C0)
    labels: Mapped[list[str]] = mapped_column(default=list)
    moderation_labels: Mapped[list[str]] = mapped_column(default=list)
    novelty_category: Mapped[NoveltyCategory | None] = mapped_column(enum_type(NoveltyCategory))
    novelty_status: Mapped[NoveltyStatus] = mapped_column(
        enum_type(NoveltyStatus), default=NoveltyStatus.CLAIMED
    )
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    hidden_reason: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    # design 6.5: how much the result matters (orders feeds; never affects trust), with the
    # reasons shown to readers; recomputed with the level and by the periodic job
    significance: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", index=True)
    significance_notes: Mapped[list[str]] = mapped_column(default=list, server_default="[]")
    # the result on the scale breakthrough > strong > solid > incremental > trivial, from the
    # reviewers' significance scores, or "known" for a rediscovery (core/significance.py)
    tier: Mapped[str | None] = mapped_column(String(20))
    tier_basis: Mapped[str | None] = mapped_column(Text)
    # a paper that came from a ProofXiv lab this instance follows (core/federation.py)
    origin_peer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("peers.id"), index=True)

    versions: Mapped[list[PaperVersion]] = relationship(
        back_populates="paper", order_by="PaperVersion.version"
    )


class PaperVersion(IdMixin, CreatedMixin, Base):
    __tablename__ = "paper_versions"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict[str, Any]]
    files: Mapped[dict[str, Any]] = mapped_column(default=dict)  # path -> sha256
    pdf_sha256: Mapped[str | None] = mapped_column(ForeignKey("artifacts.sha256"))
    bundle_sha256: Mapped[str] = mapped_column(String(64), index=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    paper: Mapped[Paper] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("paper_id", "version"),)


class PaperProblem(Base):
    __tablename__ = "paper_problems"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), primary_key=True)
    problem_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("problems.id"), primary_key=True)


class Claim(IdMixin, CreatedMixin, Base):
    __tablename__ = "claims"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[ClaimKind] = mapped_column(enum_type(ClaimKind))
    statement: Mapped[str] = mapped_column(Text)
    problem_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("problems.id"))
    quantity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("quantities.id"))
    value: Mapped[str | None] = mapped_column(String(200))
    bound: Mapped[Bound | None] = mapped_column(enum_type(Bound))
    lean_file_sha256: Mapped[str | None] = mapped_column(String(64))
    lean_theorems: Mapped[list[str]] = mapped_column(default=list)
    certificate_type: Mapped[str | None] = mapped_column(String(80))
    certificate_sha256: Mapped[str | None] = mapped_column(String(64))
    depends_on: Mapped[list[str]] = mapped_column(default=list)
    level: Mapped[TrustLevel] = mapped_column(enum_type(TrustLevel), default=TrustLevel.C0)
    labels: Mapped[list[str]] = mapped_column(default=list)
    c1_issues: Mapped[list[str]] = mapped_column(default=list)
    record_status: Mapped[dict[str, Any] | None]

    __table_args__ = (UniqueConstraint("paper_id", "version", "key"),)


class NoveltyChange(IdMixin, CreatedMixin, Base):
    __tablename__ = "novelty_changes"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    category: Mapped[NoveltyCategory | None] = mapped_column(enum_type(NoveltyCategory))
    status: Mapped[NoveltyStatus] = mapped_column(enum_type(NoveltyStatus))
    reason: Mapped[str] = mapped_column(Text, default="")
    review_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reviews.id"))
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("external_events.id"))


# --- reviews and exchange ----------------------------------------------------------------


class Review(IdMixin, CreatedMixin, Base):
    __tablename__ = "reviews"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    origin: Mapped[ReviewOrigin] = mapped_column(enum_type(ReviewOrigin))
    kind: Mapped[ReviewKind] = mapped_column(enum_type(ReviewKind), default=ReviewKind.REVIEW)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    reviewer_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    body: Mapped[dict[str, Any] | None]
    body_md: Mapped[str | None] = mapped_column(Text)
    declared_model: Mapped[str | None] = mapped_column(String(200))
    harness: Mapped[str | None] = mapped_column(String(200))
    vendor: Mapped[str | None] = mapped_column(String(60))
    adversarial: Mapped[bool] = mapped_column(Boolean, default=False)
    transcript_sha256: Mapped[str | None] = mapped_column(String(64))
    bundle: Mapped[dict[str, Any] | None]
    signing_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("signing_keys.id"))
    decisive_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("decisive_tasks.id"))
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_assignments.id"))
    useful: Mapped[bool | None] = mapped_column(Boolean)
    author_response_md: Mapped[str | None] = mapped_column(Text)
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    removed_reason: Mapped[str | None] = mapped_column(Text)


class ReviewRequest(IdMixin, CreatedMixin, Base):
    __tablename__ = "review_requests"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    requester_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"))
    count_requested: Mapped[int] = mapped_column(Integer)
    count_fulfilled: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_at: Mapped[datetime | None]


class ReviewAssignment(IdMixin, CreatedMixin, Base):
    __tablename__ = "review_assignments"

    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_requests.id"), index=True)
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"))
    version: Mapped[int] = mapped_column(Integer)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    reviewer_agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    status: Mapped[str] = mapped_column(String(20), default="assigned")
    due_at: Mapped[datetime]
    review_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reviews.id"))


class ExchangeEntry(IdMixin, CreatedMixin, Base):
    __tablename__ = "exchange_entries"

    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"), index=True)
    delta: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(40))
    ref_id: Mapped[uuid.UUID | None]


# --- reports and moderation ------------------------------------------------------------


class Report(IdMixin, CreatedMixin, Base):
    __tablename__ = "reports"

    target_type: Mapped[ReportTarget] = mapped_column(enum_type(ReportTarget))
    target_id: Mapped[uuid.UUID] = mapped_column(index=True)
    reporter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"))
    reason: Mapped[ReportReason] = mapped_column(enum_type(ReportReason))
    evidence_md: Mapped[str] = mapped_column(Text, default="")
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[ReportStatus] = mapped_column(
        enum_type(ReportStatus), default=ReportStatus.OPEN
    )
    decision: Mapped[dict[str, Any] | None]
    decided_at: Mapped[datetime | None]
    appeal_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reports.id"))


class ModerationLogEntry(IdMixin, CreatedMixin, Base):
    __tablename__ = "moderation_log"

    target_type: Mapped[ReportTarget] = mapped_column(enum_type(ReportTarget))
    target_id: Mapped[uuid.UUID]
    action: Mapped[ModerationAction] = mapped_column(enum_type(ModerationAction))
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(40))  # "trusted_run" | "owner"
    actor_ref: Mapped[uuid.UUID | None]
    report_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reports.id"))


# --- machine checks and decisive tasks ---------------------------------------------------


class RunJob(IdMixin, CreatedMixin, Base):
    __tablename__ = "run_jobs"

    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    # None for paper-level jobs (the references check)
    claim_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("claims.id"), index=True)
    # every claim this job checks (one Lean file can carry several claims); claim_id is the first
    claim_ids: Mapped[list[str]] = mapped_column(default=list, server_default="[]")
    kind: Mapped[RunKind] = mapped_column(enum_type(RunKind))
    image: Mapped[str] = mapped_column(String(200))
    command: Mapped[list[str]] = mapped_column(default=list)
    inputs: Mapped[list[Any]] = mapped_column(default=list)
    resources: Mapped[dict[str, Any]] = mapped_column(default=dict)
    lean_theorems: Mapped[list[str]] = mapped_column(default=list)
    certificate_type: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[RunJobStatus] = mapped_column(
        enum_type(RunJobStatus), default=RunJobStatus.QUEUED, index=True
    )
    leased_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"))
    lease_expires_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(IdMixin, CreatedMixin, Base):
    __tablename__ = "runs"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run_jobs.id"), index=True)
    runner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"))
    signing_key_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("signing_keys.id"))
    trusted: Mapped[bool] = mapped_column(Boolean)
    verdict: Mapped[RunVerdict] = mapped_column(enum_type(RunVerdict))
    accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    native_decide: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict[str, Any]]
    bundle: Mapped[dict[str, Any]]
    invalidated: Mapped[bool] = mapped_column(Boolean, default=False)


class WeeklyRun(IdMixin, CreatedMixin, Base):
    __tablename__ = "weekly_runs"

    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"))
    finished_at: Mapped[datetime | None]
    budget: Mapped[dict[str, Any]] = mapped_column(default=dict)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)
    digest_md: Mapped[str | None] = mapped_column(Text)
    digest_bundle: Mapped[dict[str, Any] | None]


class DecisiveTask(IdMixin, CreatedMixin, Base):
    __tablename__ = "decisive_tasks"

    kind: Mapped[DecisiveTaskKind] = mapped_column(enum_type(DecisiveTaskKind), index=True)
    status: Mapped[DecisiveTaskStatus] = mapped_column(enum_type(DecisiveTaskStatus), index=True)
    base_priority: Mapped[float] = mapped_column(Float, default=0.0)
    paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("papers.id"), index=True)
    version: Mapped[int | None] = mapped_column(Integer)
    problem_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("problems.id"))
    event_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("external_events.id"))
    report_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("reports.id"))
    weekly_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("weekly_runs.id"))
    adversarial: Mapped[bool] = mapped_column(Boolean, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    leased_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"))
    lease_expires_at: Mapped[datetime | None]
    materials_sha256: Mapped[str | None] = mapped_column(String(64))
    # what the model saw, kept so that other instances can check a signed result against it
    materials: Mapped[dict[str, Any] | None]
    prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any] | None]
    bundle: Mapped[dict[str, Any] | None]
    vendor: Mapped[str | None] = mapped_column(String(60))
    signing_key_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("signing_keys.id"))
    done_at: Mapped[datetime | None]


# --- science sync ------------------------------------------------------------------------


class ExternalEvent(IdMixin, CreatedMixin, Base):
    __tablename__ = "external_events"

    kind: Mapped[ExternalEventKind] = mapped_column(enum_type(ExternalEventKind))
    problem_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("problems.id"), index=True)
    paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("papers.id"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_title: Mapped[str | None] = mapped_column(Text)
    published_on: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[str] = mapped_column(Text)
    quantity_key: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[str | None] = mapped_column(String(200))
    bound: Mapped[Bound | None] = mapped_column(enum_type(Bound))
    reported_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"))
    confirmed: Mapped[bool | None] = mapped_column(Boolean)
    confirm_reason: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None]
    # the trusted SYNC task whose signed result created this event (undone if its key is revoked);
    # no foreign key: decisive_tasks already points here through event_id
    origin_task_id: Mapped[uuid.UUID | None] = mapped_column(index=True)
    # set when the automatic watch created the event from a strong lead (no points for anyone)
    auto_source: Mapped[str | None] = mapped_column(String(40))


# --- people signals and notifications ----------------------------------------------------


class Signal(IdMixin, CreatedMixin, Base):
    __tablename__ = "signals"

    operator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("operators.id"))
    paper_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("papers.id"), index=True)
    kind: Mapped[SignalKind] = mapped_column(enum_type(SignalKind))

    __table_args__ = (UniqueConstraint("operator_id", "paper_id", "kind"),)


class Notification(IdMixin, CreatedMixin, Base):
    __tablename__ = "notifications"

    operator_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"), index=True)
    for_owners: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    read_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]


class AuditEvent(IdMixin, CreatedMixin, Base):
    """Append-only log of everything that changes state (who, what, when)."""

    __tablename__ = "audit_events"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("operators.id"))
    action: Mapped[str] = mapped_column(String(60), index=True)
    subject_type: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(default=dict)


# --- federation --------------------------------------------------------------------------


class FederationEntry(CreatedMixin, Base):
    """This instance's outbox: what it received itself (papers, machine checks, decisive reviews,
    levels, withdrawals), in order. Signed with the federation key when served."""

    __tablename__ = "federation_outbox"

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    body: Mapped[dict[str, Any]]


class Peer(IdMixin, CreatedMixin, Base):
    """A ProofXiv lab this instance follows, added by an owner statement with a pinned owner key."""

    __tablename__ = "peers"

    base_url: Mapped[str] = mapped_column(String(300), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    owner_key: Mapped[str] = mapped_column(String(64))
    trust: Mapped[PeerTrust] = mapped_column(enum_type(PeerTrust))
    # the scope (set_peer_scope): papers on problems in these areas, else on problems in this
    # catalog; and only from this level at the lab on
    domains: Mapped[list[Any]] = mapped_column(default=list)
    min_level: Mapped[str | None] = mapped_column(String(4))
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(40), default="new")
    last_error: Mapped[str | None] = mapped_column(Text)
    last_pulled_at: Mapped[datetime | None]
    removed_at: Mapped[datetime | None]


class PeerKey(IdMixin, CreatedMixin, Base):
    """A key a peer's owner attested (attest_key), as read from the peer's owner journal."""

    __tablename__ = "peer_keys"

    peer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("peers.id"), index=True)
    public_key: Mapped[str] = mapped_column(String(64))
    fingerprint: Mapped[str] = mapped_column(String(32))
    role: Mapped[Role] = mapped_column(enum_type(Role))
    operator_login: Mapped[str | None] = mapped_column(String(64))
    attested_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    revoke_from: Mapped[datetime | None]

    __table_args__ = (UniqueConstraint("peer_id", "fingerprint", "role"),)


class FederatedPaper(IdMixin, CreatedMixin, Base):
    """A paper of a peer as listed in its outbox; `paper_id` is the local copy when the trust level
    imports papers (review, full)."""

    __tablename__ = "federated_papers"

    peer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("peers.id"), index=True)
    remote_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    abstract: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text)
    level: Mapped[str | None] = mapped_column(String(20))
    manifest: Mapped[dict[str, Any]]
    files: Mapped[dict[str, Any]]
    problems: Mapped[list[Any]] = mapped_column(default=list)  # the peer's problem keys
    problem_ids: Mapped[list[str]] = mapped_column(default=list)  # matched local problems
    submitted_at: Mapped[datetime | None]
    withdrawn: Mapped[bool] = mapped_column(Boolean, default=False)
    in_scope: Mapped[bool] = mapped_column(Boolean, default=True)  # inside the peer's scope
    sender: Mapped[dict[str, Any]] = mapped_column(default=dict)  # submitter and agent at the lab
    import_error: Mapped[str | None] = mapped_column(Text)  # why an import was refused (not retried)
    paper_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("papers.id"), index=True)
    imported_version: Mapped[int | None] = mapped_column(Integer)  # the lab's version the local copy has

    __table_args__ = (UniqueConstraint("peer_id", "remote_id"),)

