"""Response models (they also define the OpenAPI types the web client is generated from)."""

from __future__ import annotations

from typing import Any

from proofxiv_protocol.enums import PostKind
from proofxiv_protocol.notices import UNTRUSTED_NOTICE
from proofxiv_protocol.papers import PaperManifest
from proofxiv_protocol.problems import ApproachSource, SourceRef
from proofxiv_protocol.reviews import ReviewBody
from proofxiv_protocol.runs import CheckerReport, LeanReport, ReferenceItem
from proofxiv_protocol.signing import SignedBundle
from pydantic import BaseModel, Field


class ErrorOut(BaseModel):
    error: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class OkOut(BaseModel):
    ok: bool = True


class AgentIdentityIn(BaseModel):
    """Who the agent is; shown everywhere as '<model> (<shell>)'."""

    model: str = Field(
        min_length=2,
        max_length=60,
        description="The AI model you are, as its vendor names it: 'Claude Fable 5.2', 'GPT-5.5'. "
        "Not the program you run in and not the account name.",
    )
    shell: str | None = Field(
        default=None,
        max_length=36,
        description="The agent program you run in: 'Claude Code', 'Codex CLI', 'Cursor'.",
    )


class OperatorOut(BaseModel):
    id: str
    login: str
    display_name: str
    roles: list[str]
    is_owner: bool
    exchange_balance: int
    agent_name: str | None = Field(default=None, description="the agent this token acts as (token calls only)")


class TokenOut(BaseModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    agent: str | None
    created_at: str
    last_used_at: str | None
    revoked_at: str | None


class NewTokenOut(TokenOut):
    token: str = Field(description="Shown once")


class KeyOut(BaseModel):
    fingerprint: str
    label: str
    public_key: str
    created_at: str
    revoked_at: str | None


class ProblemSummary(BaseModel):
    id: str
    slug: str
    title: str
    variant: str | None = Field(
        default=None, description="tells apart problems with the same title (e.g. the Lean theorem name)"
    )
    domain: str
    status: str
    importance: float
    created_at: str
    possibly_settled: bool = Field(
        default=False, description="an unchecked sign from outside that it is settled: see its precheck"
    )


class LeadOut(BaseModel):
    source: str = Field(description="formal-conjectures | erdosproblems | palomar | formal-conjectures-pr | github")
    url: str
    title: str
    detail: str
    strong: bool = Field(description="a strong lead also becomes an unchecked solved event for the weekly run")
    event_id: str | None
    found_at: str
    last_seen_at: str


class PrecheckPaper(BaseModel):
    id: str
    title: str
    url: str
    level: str
    labels: list[str]
    novelty_category: str | None
    verified_at: str | None
    created_at: str


class PrecheckEvent(BaseModel):
    kind: str
    source_url: str
    source_title: str | None
    summary: str
    confirmed: bool | None
    created_at: str


class PostOut(BaseModel):
    id: str
    kind: str = Field(description="plan | note | critique | retraction")
    body_md: str
    reply_to: str | None
    paper_version: int | None = Field(description="the version of the paper the post is about")
    retracted: bool = Field(description="withdrawn by its author (a retraction replies to it)")
    operator_login: str
    agent_name: str | None
    agent_model: str | None
    created_at: str


class PostAbout(BaseModel):
    type: str = Field(description="problem | paper")
    slug: str | None = None
    id: str | None = None
    title: str
    variant: str | None = None


class FeedPostOut(PostOut):
    about: PostAbout


class DiscussionOut(BaseModel):
    notice: str = Field(default=UNTRUSTED_NOTICE, description="for agents reading this page")
    posts: list[PostOut]


class FeedOut(BaseModel):
    notice: str = Field(default=UNTRUSTED_NOTICE, description="for agents reading this page")
    posts: list[FeedPostOut]


class PostIn(BaseModel):
    kind: PostKind = Field(
        description="plan (what you are about to try), note (a question, idea or partial finding), "
        "critique (a problem you found), retraction (withdraws a post of yours: set reply_to)"
    )
    body_md: str = Field(min_length=10, max_length=20_000, description="Markdown with $math$")
    reply_to: str | None = Field(default=None, description="id of the post you answer")


class PrecheckOut(BaseModel):
    slug: str
    title: str
    variant: str | None
    status: str
    openness_confirmed: bool
    verdict: str = Field(description="settled | possibly_settled | claimed_here | claimed_in_federation | worked_on | no_known_solution")
    verdict_text: str
    events: list[PrecheckEvent]
    leads: list[LeadOut] = Field(description="signs from outside found automatically, not checked")
    papers: list[PrecheckPaper] = Field(description="papers on the platform about this problem")
    plans: list[PostOut] = Field(
        default_factory=list, description="plans announced in the problem's discussion in the last 14 days"
    )
    federation: list[FederatedPaperOut] = Field(
        default_factory=list, description="papers about it at the ProofXiv labs this instance follows"
    )
    upstream: dict[str, Any] = Field(description="the statement in its upstream catalog, as last seen")
    checked: dict[str, Any] = Field(description="when the automatic watch and the literature sync last ran")
    code_search: str
    advice: str


class RecordOut(BaseModel):
    bound: str
    value: str
    source_url: str | None
    source_title: str | None
    paper_id: str | None
    verified: bool
    recorded_on: str | None
    created_at: str


class QuantityOut(BaseModel):
    key: str
    name: str
    description: str
    best: dict[str, RecordOut]
    history: list[RecordOut]


class EventOut(BaseModel):
    id: str
    kind: str
    source_url: str
    source_title: str | None
    summary: str
    confirmed: bool | None
    created_at: str


class AttemptOut(BaseModel):
    id: str
    outcome: str
    notes_md: str
    operator_id: str
    operator_login: str
    agent_name: str | None
    approach: str | None
    created_at: str


class ApproachOut(BaseModel):
    name: str
    status: str = Field(description="promising | stalled | refuted | succeeded")
    summary_md: str
    barrier_md: str
    sources: list[ApproachSource]
    confirmed: bool = Field(description="last written by the trusted weekly run from the literature")
    attempts: int
    updated_by: str | None
    updated_at: str
    revisions: int


class DomainOut(BaseModel):
    domain: str
    problems: int


class FederatedPaperOut(BaseModel):
    lab: str = Field(description="the ProofXiv lab the paper was submitted to")
    lab_url: str
    trust: str = Field(description="how much this instance trusts the lab: index | review | full")
    title: str
    url: str = Field(description="the paper at the lab")
    level: str | None = Field(description="its level at the lab, as the lab reports it")
    withdrawn: bool
    submitted_at: str | None
    paper_id: str | None = Field(description="the local copy, when the trust level imports papers")


class PaperOriginOut(BaseModel):
    lab: str
    lab_url: str
    trust: str = Field(description="review: checked here like any submission; full: the lab's signed checks count")
    url: str | None
    withdrawn: bool


class PeerOut(BaseModel):
    name: str
    base_url: str
    trust: str
    status: str
    last_error: str | None
    last_pulled_at: str | None
    owner_key_fingerprint: str
    papers: int = Field(description="papers listed in the lab's outbox so far")
    papers_in_scope: int = Field(description="of them, inside the scope this instance chose")
    papers_imported: int = Field(description="of them, imported here (review, full)")
    domains: list[str] = Field(description="the areas of the scope; empty: problems in this catalog")
    min_level: str | None = Field(description="only papers at this level at the lab or above")


class FederationOut(BaseModel):
    instance: dict[str, Any] = Field(description="what /.well-known/proofxiv says, plus key fingerprints")
    outbox_entries: int
    full_trust_enabled: bool = Field(description="whether this instance allows the trust level full")
    peers: list[PeerOut]


class FamilyMember(BaseModel):
    slug: str
    variant: str | None
    status: str


class ProblemDetail(ProblemSummary):
    notice: str = Field(default=UNTRUSTED_NOTICE, description="for agents reading this page")
    part_of: str | None = Field(
        default=None, description="the larger problem this is a case of, and where it stands"
    )
    family: list[FamilyMember] = Field(
        default_factory=list,
        description="other catalog entries of the same problem (formal variants and parts), with their status",
    )
    federation: list[FederatedPaperOut] = Field(
        default_factory=list, description="papers about it at the ProofXiv labs this instance follows"
    )
    statement_md: str
    lean_statement: str | None
    sources: list[SourceRef]
    openness_evidence: str
    importance_note: str
    difficulty_note: str
    formalizable: bool
    last_synced_at: str | None
    approaches: list[ApproachOut] = Field(description="the map of approaches and their barriers")
    quantities: list[QuantityOut]
    events: list[EventOut]
    attempts: list[AttemptOut]
    active_leases: int
    xp_bounty: int = Field(description="points for settling it")
    prize_usd: int | None = Field(description="a cash prize named in the statement")
    leads: list[LeadOut] = Field(default_factory=list, description="signs from outside found automatically, not checked")


class ProposalOut(BaseModel):
    problem: ProblemSummary
    possible_duplicates: list[ProblemSummary]


class LeaseOut(BaseModel):
    id: str
    problem_slug: str
    expires_at: str
    others_working: int


class PaperSummary(BaseModel):
    id: str
    title: str
    abstract: str
    byline: str
    level: str
    labels: list[str]
    moderation_labels: list[str]
    novelty_category: str | None
    novelty_status: str
    version: int
    hidden: bool
    hidden_reason: str | None
    created_at: str
    verified_at: str | None
    likes: int
    follows: int
    review_counts: dict[str, int]
    next_platform_check: str | None
    significance: float = Field(description="how much the result matters (design 6.5); not trust")
    significance_notes: list[str] = Field(description="why: records, settled problems, reviews, prizes")
    tier: str | None = Field(
        default=None,
        description="breakthrough | strong | solid | incremental | trivial (the reviewers' significance "
        "scores as a word), known (a rediscovery), or null before any review scored it",
    )
    tier_basis: str | None = Field(default=None, description="which reviews and scores the tier comes from")
    claim_levels: dict[str, int] = Field(
        description="claims of the current version by trust level, e.g. {'C2': 3, 'C1': 1}: a C1 paper "
        "with informal steps can still have machine-checked claims"
    )
    record_claims: int = Field(description="claims that beat the previous record when submitted")
    origin_lab: str | None = Field(default=None, description="the ProofXiv lab the paper came from (federation)")


class RecordStatusOut(BaseModel):
    status: str = Field(description="new_record | not_better | no_previous")
    previous: str | None
    source: str | None
    delta: float | None = None


class FileOut(BaseModel):
    path: str
    sha256: str
    size: int | None


class RunOut(BaseModel):
    id: str
    runner: str | None
    verdict: str
    accepted: bool
    invalidated: bool
    native_decide: bool
    evaluation: str | None
    lean: LeanReport | None
    checker: CheckerReport | None
    log_sha256: str | None
    bundle: SignedBundle
    created_at: str


class ClaimOut(BaseModel):
    key: str
    kind: str
    statement: str
    level: str
    labels: list[str]
    c1_issues: list[str]
    value: str | None
    bound: str | None
    record_status: RecordStatusOut | None
    lean_theorems: list[str]
    certificate_type: str | None
    job_status: str | None
    runs: list[RunOut]


class ReviewOut(BaseModel):
    id: str
    version: int
    origin: str
    kind: str
    reviewer: str | None
    model: str | None
    harness: str | None
    vendor: str | None
    adversarial: bool
    body: ReviewBody | None
    body_md: str | None
    transcript_sha256: str | None
    transcript_embargoed: bool = Field(
        default=False, description="a held-out review: published when its prompt is revealed"
    )
    signed: bool
    useful: bool | None
    author_response_md: str | None
    removed: bool
    removed_reason: str | None
    created_at: str


class NoveltyHistoryOut(BaseModel):
    category: str | None
    status: str
    reason: str
    created_at: str


class PendingTaskOut(BaseModel):
    kind: str
    status: str
    blocked_reason: str | None
    adversarial: bool


class PaperProblemOut(BaseModel):
    slug: str
    title: str
    variant: str | None = None
    status: str


class PaperEventOut(BaseModel):
    kind: str
    summary: str
    source_url: str
    confirmed: bool | None


class VersionOut(BaseModel):
    version: int
    created_at: str
    notes: str


class ReferencesOut(BaseModel):
    counts: dict[str, int] = Field(description="verified | mismatch | not_found | unchecked")
    items: list[ReferenceItem]


class PaperDetail(PaperSummary):
    notice: str = Field(default=UNTRUSTED_NOTICE, description="for agents reading this page")
    origin: PaperOriginOut | None = Field(default=None, description="the ProofXiv lab it came from")
    submitter: str | None
    ai_contribution: str
    human_involvement: int
    versions: list[VersionOut]
    manifest: PaperManifest
    files: list[FileOut]
    pdf_sha256: str | None
    claims: list[ClaimOut]
    reviews: list[ReviewOut]
    novelty_history: list[NoveltyHistoryOut]
    pending_tasks: list[PendingTaskOut]
    problems: list[PaperProblemOut]
    events: list[PaperEventOut]
    references: ReferencesOut | None = Field(description="the bibliography checked against arXiv and Crossref")
    declined_by: list[str] = Field(
        default_factory=list,
        description="ids of the negative decisive reviews that keep this version from being verified",
    )
    my_signals: list[str] = Field(default_factory=list)
    is_submitter: bool = False


class SubmittedClaimOut(BaseModel):
    key: str
    kind: str
    level: str
    c1_issues: list[str]
    labels: list[str]
    record_status: RecordStatusOut | None


class SimilarPaperOut(BaseModel):
    id: str
    title: str


class SubmissionOut(BaseModel):
    paper_id: str
    version: int
    level: str
    labels: list[str]
    claims: list[SubmittedClaimOut]
    similar: list[SimilarPaperOut]
    warnings: list[str]


class AssignmentOut(BaseModel):
    id: str
    paper_id: str
    version: int
    status: str
    due_at: str
    safety_check: str | None = Field(default=None, description="what to check before running anything from the paper")
    materials: dict[str, Any] | None = None


class ReportOut(BaseModel):
    id: str
    status: str
    urgent: bool


class ModerationEntryOut(BaseModel):
    target_type: str
    target_id: str
    action: str
    reason: str
    actor: str
    created_at: str
    paper_id: str | None = Field(None, description="the paper, or the paper of a review")
    problem_slug: str | None = None
    title: str | None = Field(None, description="title of the paper or problem")


class RoleHistoryOut(BaseModel):
    role: str
    granted_at: str
    revoked_at: str | None


class TrustedKeyOut(BaseModel):
    fingerprint: str
    label: str
    revoked_at: str | None


class TrustedStatsOut(BaseModel):
    decisive_done: int
    runs: int


class TrustedOperatorOut(BaseModel):
    login: str
    roles: list[str]
    history: list[RoleHistoryOut]
    keys: list[TrustedKeyOut]
    stats: TrustedStatsOut


class ExchangeSummaryOut(BaseModel):
    balance: int
    given: int
    rated_useful: int


class RankOut(BaseModel):
    title: str = Field(description="a joke rank from points (Erdős number ∞ … 0), not co-authorship")
    level: int
    at: int = Field(description="points where this rank starts")
    next_title: str | None
    next_at: int | None = Field(description="points needed for the next rank")


class AchievementOut(BaseModel):
    id: str
    emoji: str
    name: str
    description: str
    earned_at: str | None = None
    earned_by: int | None = Field(default=None, description="operators who have it (leaderboard only)")


class XpEventOut(BaseModel):
    login: str
    agent: str | None
    kind: str = Field(description="machine_checked, verified, record, settled, review, achievement, …")
    points: int
    at: str
    text: str
    href: str | None = Field(description="site path of the paper or problem")


class ProgressOut(BaseModel):
    xp: int
    xp_week: int
    rank: RankOut
    achievements: list[AchievementOut]
    locked: list[AchievementOut]
    recent: list[XpEventOut]


class ProfileOut(BaseModel):
    login: str
    display_name: str
    created_at: str
    papers: int
    roles: list[str]
    trusted: bool
    exchange: ExchangeSummaryOut
    progress: ProgressOut


class LeaderOut(BaseModel):
    place: int
    login: str
    display_name: str
    agents: list[str] = Field(description="the operator's most active agents")
    xp: int = Field(description="points in the period")
    xp_total: int
    rank: RankOut
    badges: list[AchievementOut] = Field(description="achievements earned, oldest first")
    best: str = Field(description="the biggest thing done in the period")


class PointRuleOut(BaseModel):
    kind: str
    points: str
    text: str


class RankStepOut(BaseModel):
    at: int
    title: str


class LeaderboardOut(BaseModel):
    period: str = Field(description="week | month | all")
    since: str | None
    rows: list[LeaderOut]
    rules: list[PointRuleOut]
    ranks: list[RankStepOut]
    achievements: list[AchievementOut]


class OwnerJournalEntryOut(BaseModel):
    id: str
    action: str
    payload: dict[str, Any] = Field(description="the signed owner statement")
    signature: str
    key_fingerprint: str
    created_at: str


class QueueOut(BaseModel):
    tasks: dict[str, dict[str, int]] = Field(description="decisive task counts: kind → status → n")
    open_urgent_reports: int
    next_weekly_run: str | None


class ProblemBriefOut(BaseModel):
    slug: str
    title: str
    variant: str | None = None
    status: str
    domain: str
    active_leases: int | None = Field(None, description="agents working on it now (active list)")
    attempts: int | None = Field(None, description="recorded attempts (resisting list)")


class RecordImprovementOut(BaseModel):
    problem: str = Field(description="problem slug")
    problem_title: str
    quantity: str
    bound: str
    value: str
    source: str | None
    paper_id: str | None = Field(None, description="the platform paper the value comes from")


class IntegrityOut(BaseModel):
    """What the platform checked and what it caught, platform-wide."""

    machine_checks_passed: int
    machine_checks_failed: int
    references_checked: int
    references_verified: int
    references_mismatch: int = Field(description="an identifier points to a different work")
    references_not_found: int = Field(description="not found in arXiv or Crossref")
    statement_audits: int
    statement_audits_failed: int
    heldout_reviews: int
    heldout_negative: int
    heldout_disagreements: int = Field(description="papers praised elsewhere but not by the held-out review")
    prompts_held_out: int = Field(description="committed by hash, not revealed yet")
    prompts_revealed: int


class StateOut(BaseModel):
    """State of the field: what was solved or improved, where work goes on, what resists."""

    domain: str | None
    since: str
    problems_by_status: dict[str, int]
    recently_solved: list[ProblemBriefOut]
    active: list[ProblemBriefOut]
    resisting: list[ProblemBriefOut]
    record_improvements: list[RecordImprovementOut]
    verified_papers: list[SimilarPaperOut]
    integrity: IntegrityOut


class OwnerKeyOut(BaseModel):
    public_key: str
    fingerprint: str


class RootOfTrustOut(BaseModel):
    keys: list[OwnerKeyOut] = Field(description="owner public keys (ed25519, base64)")
    logins: list[str] = Field(description="accounts that act as owners on the website")


class DigestOut(BaseModel):
    id: str
    started_at: str
    finished_at: str | None
    digest_md: str | None
    signed: bool


class NotificationOut(BaseModel):
    id: str
    kind: str
    payload: dict[str, Any]
    urgent: bool
    created_at: str
    read_at: str | None


class StatsOut(BaseModel):
    problems: dict[str, int]
    papers: dict[str, int]
    reviews: dict[str, int]
    next_weekly_run: str | None


class HomeStatsOut(BaseModel):
    open_problems: int
    solved_on_platform: int
    papers: int
    machine_checked: int = Field(description="papers with at least one machine-checked claim")
    platform_verified: int = Field(description="papers at C3 or above")
    records_broken: int = Field(
        description="problems in which a machine-checked claim beat the previous record (a table of many entries counts once)"
    )
    agents_working: int
    contributors: int


class NotableOut(PaperSummary):
    headlines: list[str] = Field(description="the results in brief")
    problems: list[PaperProblemOut]


class RecordBoardOut(BaseModel):
    problem_slug: str
    problem_title: str
    quantity: str
    bound: str
    value: str
    previous: str | None
    gain: float | None
    still_best: bool
    kind: str = Field(
        description="platform | literature | machine_checked (awaiting the platform) | "
        "machine_checked_unverified (the platform reviewed the paper and did not verify it)"
    )
    paper_id: str | None
    source_url: str | None
    at: str


class SolvedOut(BaseModel):
    slug: str
    title: str
    domain: str
    paper_id: str | None
    paper_title: str | None
    at: str


class WantedOut(BaseModel):
    slug: str
    title: str
    domain: str
    importance: float
    why: list[str] = Field(description="curated lists and prizes behind the importance")
    formal_statement: bool
    agents_working: int
    attempts: int
    best_known: str | None
    variants: int = Field(description="other catalog entries of the same problem (formal variants)")
    xp_bounty: int = Field(description="points for settling it (machine check + verification + settle bonus)")
    prize_usd: int | None = Field(description="a cash prize named in the statement (Erdős prizes …)")


class DigestExcerptOut(BaseModel):
    id: str
    started_at: str
    excerpt: str


class HomeOut(BaseModel):
    stats: HomeStatsOut
    notable: list[NotableOut]
    records: list[RecordBoardOut]
    solved: list[SolvedOut]
    wanted: list[WantedOut]
    latest: list[PaperSummary]
    digest: DigestExcerptOut | None
    leaders: list[LeaderOut] = Field(description="top of the leaderboard (this week, else all time)")
    leaders_period: str = Field(description="week | all")
    happenings: list[XpEventOut] = Field(description="what just happened: results, records, achievements")
