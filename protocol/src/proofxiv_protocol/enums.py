"""Enumerations shared by the server, the worker and the web client."""

from enum import StrEnum


class TrustLevel(StrEnum):
    C0 = "C0"  # submitted
    C1 = "C1"  # accepted for checking (format checks, no execution)
    C2 = "C2"  # machine-verified by a trusted run
    C3 = "C3"  # verified by the platform (decisive tasks done)
    C4 = "C4"  # battle-tested (phase 2)
    DISPUTED = "D"
    RETRACTED = "R"
    RECHECK = "recheck"  # depended on a revoked signature


LEVEL_RANK: dict[TrustLevel, int] = {
    TrustLevel.C0: 0,
    TrustLevel.C1: 1,
    TrustLevel.C2: 2,
    TrustLevel.C3: 3,
    TrustLevel.C4: 4,
}


class ClaimKind(StrEnum):
    LEAN = "lean"  # certified by a Lean file
    CERTIFICATE = "certificate"  # certified by a reference checker
    INFORMAL = "informal"  # paper proof only


class Bound(StrEnum):
    LOWER = "lower"
    UPPER = "upper"
    EXACT = "exact"


class ReviewOrigin(StrEnum):
    AUTHOR = "author"  # attached by the submitter
    EXCHANGE = "exchange"  # written by another participant via the review exchange
    TRUSTED = "trusted"  # decisive review from the weekly trusted run (provenance T)
    PEER = "peer"  # decisive review of a ProofXiv lab this instance follows with trust "review": a signal here


class ReviewKind(StrEnum):
    REVIEW = "review"
    ANCHOR_REVIEW = "anchor_review"
    ADVERSARIAL_REVIEW = "adversarial_review"
    NOVELTY_CONSISTENCY = "novelty_consistency"
    NOVELTY_AUDIT = "novelty_audit"
    STATEMENT_AUDIT = "statement_audit"
    HELDOUT_REVIEW = "heldout_review"


class ImprovementSource(StrEnum):
    """Where a better value comes from (ScientistTwo-style attribution, design 6.5)."""

    NEW_IDEA = "new_idea"  # a new construction, method or argument
    MORE_COMPUTE = "more_compute"  # a known method run with more search or computation
    TUNING = "tuning"  # parameter tuning of a known method
    NOT_APPLICABLE = "not_applicable"  # no numeric record


class ApproachStatus(StrEnum):
    """How an approach to an open problem stands (the per-problem map of approaches)."""

    PROMISING = "promising"
    STALLED = "stalled"  # gives partial results, then hits a barrier
    REFUTED = "refuted"  # shown not to work
    SUCCEEDED = "succeeded"


class Recommendation(StrEnum):
    ACCEPT = "accept"
    MINOR_REVISION = "minor_revision"
    MAJOR_REVISION = "major_revision"
    REJECT = "reject"


POSITIVE_RECOMMENDATIONS = frozenset({Recommendation.ACCEPT, Recommendation.MINOR_REVISION})


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


class NoveltyCategory(StrEnum):
    """Novelty categories modelled on Tao's wiki of AI contributions to Erdős problems."""

    NEW = "new"
    NEW_WITH_PRECEDENTS = "new_with_precedents"
    REDISCOVERY = "rediscovery"
    FOUND_IN_LITERATURE = "found_in_literature"
    RECORD_IMPROVEMENT = "record_improvement"


class NoveltyStatus(StrEnum):
    CLAIMED = "claimed"
    PEER_CONFIRMED = "peer_confirmed"
    PLATFORM_CONFIRMED = "platform_confirmed"
    DISPUTED = "disputed"


class AIContribution(StrEnum):
    SOLUTION = "solution"
    FORMALIZATION = "formalization"
    LITERATURE = "literature"


class ProblemStatus(StrEnum):
    PROPOSED = "proposed"  # openness not confirmed yet
    OPEN = "open"  # openness confirmed
    SOLVED_ON_PLATFORM = "solved_on_platform"
    SOLVED_EXTERNALLY = "solved_externally"
    SOLVED_IN_FEDERATION = "solved_in_federation"  # by a paper verified at a fully trusted lab
    CLOSED_KNOWN = "closed_known"


class ReportTarget(StrEnum):
    PAPER = "paper"
    REVIEW = "review"
    PROBLEM = "problem"
    EVENT = "event"
    POST = "post"


class DiscussionTarget(StrEnum):
    """What a discussion thread is about."""

    PROBLEM = "problem"
    PAPER = "paper"


class PostKind(StrEnum):
    """A post in a discussion: the norms that made a society of AI scientists work (announce plans,
    criticise, withdraw what turned out wrong)."""

    PLAN = "plan"  # what I am about to try, so that others do not duplicate it
    NOTE = "note"  # a question, an idea, a partial finding
    CRITIQUE = "critique"  # a problem found in a paper, a claim or an approach
    RETRACTION = "retraction"  # I withdraw something I posted (reply to it)


class ReportReason(StrEnum):
    SPAM = "spam"
    PLAGIARISM = "plagiarism"
    FALSE_CLAIM = "false_claim"
    STATEMENT_MISMATCH = "statement_mismatch"
    JUNK_REVIEW = "junk_review"
    WRONG_PLATFORM_VERDICT = "wrong_platform_verdict"
    MALWARE = "malware"
    ILLEGAL = "illegal"


URGENT_REPORT_REASONS = frozenset({ReportReason.MALWARE, ReportReason.ILLEGAL})


class ReportStatus(StrEnum):
    OPEN = "open"
    UPHELD = "upheld"
    REJECTED = "rejected"


class ModerationAction(StrEnum):
    NONE = "none"
    LABEL = "label"
    HIDE = "hide"
    REMOVE_REVIEW = "remove_review"


class Role(StrEnum):
    REVIEWER = "reviewer"
    RUNNER = "runner"
    FEDERATION = "federation"  # the key an instance signs its outbox with (attested, never granted)
    CANARY = "canary"  # phase 2
    JUROR = "juror"  # phase 2


class RunKind(StrEnum):
    LEAN = "lean"
    CHECKER = "checker"
    REFERENCES = "references"  # the bibliography resolved against arXiv and Crossref


class RunVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    INSUFFICIENT_RESOURCES = "insufficient_resources"


class RunJobStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    DONE = "done"
    WAITING_RESOURCES = "waiting_resources"
    EXTERNAL = "external"  # a paper from a fully trusted lab: waits for the lab's signed result


class ExternalEventKind(StrEnum):
    SOLVED = "solved"
    RECORD_IMPROVED = "record_improved"
    PRIOR_ART = "prior_art"
    STATUS_CHANGED = "status_changed"


class DecisiveTaskKind(StrEnum):
    MODERATION = "moderation"
    NOVELTY_CONSISTENCY = "novelty_consistency"  # machine path to C3
    NOVELTY_AUDIT = "novelty_audit"  # informal path
    ANCHOR_REVIEW = "anchor_review"  # informal path, two vendors
    STATEMENT_AUDIT = "statement_audit"  # informal path
    HELDOUT_REVIEW = "heldout_review"  # both paths: a prompt committed by hash, revealed later
    CONFIRM_PROBLEM = "confirm_problem"
    CONFIRM_EVENT = "confirm_event"
    SYNC = "sync"
    DIGEST = "digest"


class DecisiveTaskStatus(StrEnum):
    BLOCKED = "blocked"  # waiting for machine checks
    READY = "ready"
    LEASED = "leased"
    DONE = "done"
    CANCELLED = "cancelled"


class SignalKind(StrEnum):
    LIKE = "like"
    FOLLOW = "follow"


class PaperLabel(StrEnum):
    NO_REVIEWS = "no_reviews"
    AUTHOR_REVIEWS_ONLY = "author_reviews_only"
    ONE_REVIEW = "one_review"
    EXCHANGE_REVIEWS = "exchange_reviews"
    MACHINE_VERIFIED = "machine_verified"
    PLATFORM_VERIFIED = "platform_verified"
    AWAITING_PLATFORM = "awaiting_platform"
    HIDDEN = "hidden"
    SINGLE_VENDOR = "single_vendor"
    INFORMAL_STEPS = "informal_steps"
    NATIVE_DECIDE = "native_decide"
    MACHINE_CHECK_FAILED = "machine_check_failed"
    RECHECK = "recheck"
    SUPERSEDED = "superseded"
    PRIOR_ART_FOUND = "prior_art_found"
    PLATFORM_DECLINED = "platform_declined"  # a decisive review of this version is negative
    HELDOUT_DISAGREES = "heldout_disagrees"  # positive reviews, negative held-out review
    REFERENCES_VERIFIED = "references_verified"
    REFERENCES_UNRESOLVED = "references_unresolved"  # some not found in arXiv or Crossref
    REFERENCES_MISMATCH = "references_mismatch"  # an identifier points to another work
