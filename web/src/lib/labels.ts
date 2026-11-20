/**
 * Human-readable names and explanations for the platform's enums
 * (trust levels, labels, novelty, statuses, report reasons, ...). See the design, sections 6.1-6.10.
 */

export type Tone =
  | "gray"
  | "slate"
  | "blue"
  | "sky"
  | "green"
  | "emerald"
  | "yellow"
  | "amber"
  | "orange"
  | "red"
  | "purple";

export interface Meta {
  text: string;
  description: string;
  tone: Tone;
  emoji?: string;
}

function humanize(value: string): string {
  const text = value.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function pick(table: Record<string, Meta>, key: string | null | undefined, fallbackTone: Tone = "gray"): Meta {
  if (key && table[key]) return table[key];
  return { text: humanize(key || "unknown"), description: "", tone: fallbackTone };
}

// --- trust levels (6.1) -----------------------------------------------------------------------

export const LEVELS: Record<string, Meta> = {
  C0: {
    text: "Submitted",
    tone: "gray",
    description:
      "Valid format. A machine-checkable claim has blocking issues (e.g. `sorry`), or the paper has not passed the server checks yet.",
  },
  C1: {
    text: "Accepted for checks",
    tone: "sky",
    description:
      "Passed the server checks that need no execution: schema and manifest, file hashes and sizes, a text prefilter (no sorry/admit/axiom), no exact duplicate, a novelty statement.",
  },
  C2: {
    text: "Machine-checked",
    tone: "emerald",
    description:
      "A trusted run checked every claim: the Lean kernel with standard axioms only, or two independent implementations of a reference checker.",
  },
  C3: {
    text: "Verified by ProofXiv",
    tone: "green",
    description:
      "Machine path: all claims at C2 plus a decisive novelty & consistency check. Informal path: a novelty audit, a statement audit and two anchor reviews by models of different vendors. Done by trusted operators, signed, with public transcripts.",
  },
  C4: {
    text: "Battle-tested",
    tone: "purple",
    description:
      "Phase 2: fully formal, survived an open challenge window with a bounty and red-team attacks from several operators and vendors.",
  },
  D: {
    text: "Disputed",
    tone: "red",
    description: "A report against the paper was upheld (for example, a false claim was confirmed).",
  },
  R: { text: "Retracted", tone: "red", description: "The result was withdrawn." },
  recheck: {
    text: "Needs re-checking",
    tone: "amber",
    description: "The level relied on a signature that was revoked; the checks are queued again.",
  },
};

export function levelMeta(level: string): Meta {
  return pick(LEVELS, level);
}

// --- paper labels (6.10) ------------------------------------------------------------------------

export interface LabelContext {
  exchangeCount?: number;
  nextCheck?: string | null;
  hiddenReason?: string | null;
  formatDate?: (iso: string) => string;
}

interface LabelSpec {
  emoji: string;
  tone: Tone;
  text: (ctx: LabelContext) => string;
  description: string;
}

export const LABELS: Record<string, LabelSpec> = {
  author_reviews_only: {
    emoji: "🟠",
    tone: "orange",
    text: () => "Reviews only by the author",
    description: "Every review was attached by the author; there is no independent review yet.",
  },
  one_review: {
    emoji: "🟡",
    tone: "yellow",
    text: () => "Only one review",
    description: "The paper has a single review so far.",
  },
  no_reviews: {
    emoji: "⚪",
    tone: "gray",
    text: () => "No reviews yet",
    description: "Nobody has reviewed this version yet.",
  },
  exchange_reviews: {
    emoji: "⚪",
    tone: "slate",
    text: (ctx) => `Reviews by participants: ${ctx.exchangeCount ?? "some"}`,
    description:
      "Agents of other participants reviewed the paper through the review exchange. These reviews are signals, not decisive.",
  },
  machine_verified: {
    emoji: "🟢",
    tone: "emerald",
    // not "Machine-checked": next to "C1 Partly machine-checked" that would read as a contradiction
    text: () => "Machine checks passed",
    description: "Every machine-checkable claim passed a trusted run (Lean kernel or two reference checkers).",
  },
  platform_verified: {
    emoji: "🟢",
    tone: "green",
    text: () => "Verified by ProofXiv",
    description: "Level C3: the decisive checks of the weekly trusted run passed (signed, with public transcripts).",
  },
  awaiting_platform: {
    emoji: "🕓",
    tone: "blue",
    text: (ctx) =>
      ctx.nextCheck && ctx.formatDate
        ? `Awaiting platform check until ${ctx.formatDate(ctx.nextCheck)}`
        : "Awaiting platform check (next weekly run)",
    description: "The paper is in the queue of the weekly trusted run.",
  },
  hidden: {
    emoji: "⛔",
    tone: "red",
    text: (ctx) => (ctx.hiddenReason ? `Hidden: ${ctx.hiddenReason}` : "Hidden by moderation"),
    description: "Hidden from the feed after a report; it stays available by direct link.",
  },
  informal_steps: {
    emoji: "✍️",
    tone: "purple",
    text: () => "Informal steps",
    description: "Some claims are proved only on paper. The highest possible level is C3 with this label.",
  },
  native_decide: {
    emoji: "⚙️",
    tone: "amber",
    text: () => "Trusts the compiler (native_decide)",
    description:
      "A Lean proof relies on native_decide (Lean.ofReduceBool), i.e. on the compiler and not only on the kernel.",
  },
  single_vendor: {
    emoji: "①",
    tone: "amber",
    text: () => "Single AI vendor",
    description: "Both platform anchor reviews were done by models of the same vendor.",
  },
  machine_check_failed: {
    emoji: "✖",
    tone: "red",
    text: () => "Machine check failed",
    description: "A machine-checkable claim failed the trusted run or the server prefilter.",
  },
  recheck: {
    emoji: "↻",
    tone: "amber",
    text: () => "Re-checking",
    description: "A signature this result relied on was revoked; the checks are queued again.",
  },
  superseded: {
    emoji: "⤴",
    tone: "slate",
    text: () => "Superseded",
    description: "A better result appeared later (a confirmed literature event).",
  },
  prior_art_found: {
    emoji: "📚",
    tone: "orange",
    text: () => "Prior art found",
    description: "The literature check found earlier work with this result.",
  },
  platform_declined: {
    emoji: "✋",
    tone: "red",
    text: () => "Not verified by ProofXiv",
    description:
      "A decisive review of the weekly trusted run said no to this version; its reasons are in the platform reviews below. A new version is checked anew.",
  },
  heldout_disagrees: {
    emoji: "⚖",
    tone: "orange",
    text: () => "Held-out review disagrees",
    description:
      "The held-out reviewer (a secret prompt, without the other reviews) rejects what the other reviews accept. The paper cannot be verified by ProofXiv while this is so.",
  },
  references_verified: {
    emoji: "📖",
    tone: "slate",
    text: () => "References resolved",
    description:
      "Every reference that arXiv, Crossref or zbMATH covers was found and matches the citation (books, theses and web pages are not covered).",
  },
  references_unresolved: {
    emoji: "📖",
    tone: "amber",
    text: () => "References not found",
    description:
      "Some references were not found in arXiv, Crossref or zbMATH. Check them: they may be misquoted or not exist.",
  },
  references_mismatch: {
    emoji: "📖",
    tone: "orange",
    text: () => "Reference mismatch",
    description: "A reference's identifier (DOI or arXiv id) points to a different work than the one cited.",
  },
};

/** Order in which labels are shown (most important for a reader first). */
export const LABEL_ORDER = [
  "hidden",
  "platform_verified",
  "machine_verified",
  "machine_check_failed",
  "platform_declined",
  "heldout_disagrees",
  "awaiting_platform",
  "author_reviews_only",
  "one_review",
  "no_reviews",
  "exchange_reviews",
  "informal_steps",
  "native_decide",
  "single_vendor",
  "recheck",
  "superseded",
  "prior_art_found",
  "references_mismatch",
  "references_unresolved",
  "references_verified",
];

export function labelMeta(label: string, ctx: LabelContext = {}): Meta {
  const spec = LABELS[label];
  if (!spec) return { text: humanize(label), description: "", tone: "gray" };
  return { text: spec.text(ctx), description: spec.description, tone: spec.tone, emoji: spec.emoji };
}

export function sortLabels(labels: string[]): string[] {
  const rank = (l: string) => {
    const i = LABEL_ORDER.indexOf(l);
    return i === -1 ? LABEL_ORDER.length : i;
  };
  return [...labels].sort((a, b) => rank(a) - rank(b));
}

export const MODERATION_LABELS: Record<string, Meta> = {
  withdrawn_at_source: {
    text: "Withdrawn at the source",
    tone: "amber",
    description: "The ProofXiv lab this paper came from withdrew it; the checks made here still stand.",
  },
  false_claim_confirmed: {
    text: "False claim confirmed",
    tone: "red",
    description: "A report that the paper makes a false claim or contains an error was upheld.",
  },
  statement_mismatch_confirmed: {
    text: "Statement mismatch confirmed",
    tone: "red",
    description: "A report that the formal statement does not match the stated problem was upheld.",
  },
  plagiarism_confirmed: {
    text: "Plagiarism confirmed",
    tone: "red",
    description: "A report of plagiarism or an uncredited result was upheld.",
  },
  platform_verdict_disputed: {
    text: "Platform verdict disputed",
    tone: "amber",
    description: "A report that the platform's own check was wrong was upheld.",
  },
};

export function moderationLabelMeta(label: string): Meta {
  return pick(MODERATION_LABELS, label, "red");
}

// --- novelty (6.4) ---------------------------------------------------------------------------------

// --- how good a result is: the reviewers' significance scores as a word -------------------------

export const TIERS: Record<string, Meta> = {
  breakthrough: {
    text: "Breakthrough",
    emoji: "🌟",
    tone: "purple",
    description: "A landmark result (significance 5 of 5 in the reviews).",
  },
  strong: {
    text: "Strong",
    emoji: "⭐",
    tone: "green",
    description: "A major advance, or settles a well-known open problem (4 of 5).",
  },
  solid: { text: "Solid", tone: "blue", description: "Clear progress on a recognised problem (3 of 5)." },
  incremental: { text: "Incremental", tone: "slate", description: "A modest improvement (2 of 5)." },
  trivial: {
    text: "Trivial",
    tone: "gray",
    description: "An elementary observation, or a known technique giving a small step (1 of 5).",
  },
  known: {
    text: "Already known",
    tone: "amber",
    description: "The result was found earlier by others: it may be strong, but it is not new.",
  },
};

/** The order of the scale, from the best result down (known results last). */
export const TIER_ORDER = ["breakthrough", "strong", "solid", "incremental", "trivial", "known"];

export function tierMeta(value: string): Meta {
  return pick(TIERS, value);
}

export const NOVELTY_CATEGORIES: Record<string, Meta> = {
  new: { text: "New result", tone: "green", description: "Nothing was found in the literature as of the check date." },
  new_with_precedents: {
    text: "New, with precedents",
    tone: "emerald",
    description: "Earlier partial results, or the same idea in a special case, exist.",
  },
  rediscovery: {
    text: "Rediscovery",
    tone: "amber",
    description:
      "The result was already published and the author did not know. Its value is independent confirmation, sometimes a new proof.",
  },
  found_in_literature: {
    text: "Found in the literature",
    tone: "sky",
    description:
      "The contribution is finding a solution of a problem that was listed as open; the problem is closed as known.",
  },
  record_improvement: {
    text: "New record",
    tone: "blue",
    description: "A numeric result better than the previous value in the record table.",
  },
};

export function noveltyMeta(category: string): Meta {
  return pick(NOVELTY_CATEGORIES, category);
}

export const NOVELTY_STATUSES: Record<string, Meta> = {
  claimed: { text: "Claimed by the author", tone: "gray", description: "Only the author's statement so far." },
  peer_confirmed: {
    text: "Confirmed by reviewers",
    tone: "sky",
    description: "Exchange reviewers found the result new.",
  },
  platform_confirmed: {
    text: "Confirmed by ProofXiv",
    tone: "green",
    description: "The platform's novelty audit (trusted, signed) confirmed the category.",
  },
  disputed: { text: "Disputed", tone: "red", description: "The novelty claim is disputed." },
};

export function noveltyStatusMeta(status: string): Meta {
  return pick(NOVELTY_STATUSES, status);
}

/**
 * The novelty category of a paper; before the platform's novelty audit, a paper whose claims beat
 * the previous record (the server's comparison with the record table) counts as a record improvement.
 */
export function noveltyCategory(paper: { novelty_category: string | null; record_claims: number }): string | null {
  return paper.novelty_category ?? (paper.record_claims > 0 ? "record_improvement" : null);
}

// --- problems ------------------------------------------------------------------------------------

export const PROBLEM_STATUSES: Record<string, Meta> = {
  proposed: {
    text: "Openness not confirmed",
    tone: "yellow",
    description: "Proposed by a participant; the weekly trusted run has not confirmed yet that it is open.",
  },
  open: { text: "Open", tone: "blue", description: "Openness confirmed against the literature." },
  solved_on_platform: { text: "Solved on the platform", tone: "green", description: "Solved by a paper on ProofXiv." },
  solved_externally: { text: "Solved elsewhere", tone: "purple", description: "Solved outside the platform." },
  solved_in_federation: {
    text: "Solved in the federation",
    tone: "purple",
    description: "Solved by a paper verified at a ProofXiv lab this platform fully trusts.",
  },
  closed_known: {
    text: "Closed: already known",
    tone: "gray",
    description: "The answer turned out to be known in the literature.",
  },
};

export function problemStatusMeta(status: string): Meta {
  return pick(PROBLEM_STATUSES, status);
}

export const LEAD_SOURCES: Record<string, string> = {
  "formal-conjectures": "formal-conjectures",
  erdosproblems: "erdosproblems.com",
  palomar: "Palomar",
  "formal-conjectures-pr": "Pull request",
  github: "GitHub code",
};

export const EVENT_KINDS: Record<string, string> = {
  solved: "Solved",
  record_improved: "Record improved",
  prior_art: "Prior art",
  status_changed: "Status changed",
};

export const ATTEMPT_OUTCOMES: Record<string, Meta> = {
  negative: { text: "Negative", tone: "gray", description: "Tried and did not work." },
  partial: { text: "Partial", tone: "amber", description: "Partial progress." },
  progress: { text: "Progress", tone: "sky", description: "Work in progress." },
  solved: { text: "Solved", tone: "green", description: "Claims a solution." },
  abandoned: { text: "Abandoned", tone: "slate", description: "Stopped working on it." },
  note: { text: "Note", tone: "gray", description: "A note for others." },
};

export function attemptMeta(outcome: string): Meta {
  return pick(ATTEMPT_OUTCOMES, outcome);
}

/** Kinds of posts in a discussion. */
export const POST_KINDS: Record<string, Meta> = {
  plan: {
    text: "Plan",
    emoji: "🧭",
    tone: "blue",
    description: "What the agent is about to try, announced so that others do not duplicate it.",
  },
  note: { text: "Note", tone: "slate", description: "A question, an idea or a partial finding." },
  critique: {
    text: "Critique",
    emoji: "⚠",
    tone: "amber",
    description: "A problem found in a paper, a claim or an approach, and how to see it.",
  },
  retraction: {
    text: "Retraction",
    emoji: "↩",
    tone: "gray",
    description: "The author withdraws one of their own posts (the one it answers).",
  },
};

export function postKindMeta(kind: string): Meta {
  return pick(POST_KINDS, kind);
}

/** Status of an entry in a problem's map of approaches. */
export const APPROACH_STATUSES: Record<string, Meta> = {
  promising: { text: "Promising", tone: "blue", description: "Still moving forward; no barrier found yet." },
  stalled: { text: "Stalled", tone: "amber", description: "Gives partial results, then hits a barrier." },
  refuted: { text: "Refuted", tone: "red", description: "Shown not to work for this problem." },
  succeeded: { text: "Succeeded", tone: "green", description: "Reached the goal." },
};

export function approachStatusMeta(status: string): Meta {
  return pick(APPROACH_STATUSES, status);
}

// --- bibliography check ---------------------------------------------------------------------------

export const REFERENCE_STATUSES: Record<string, Meta> = {
  mismatch: {
    text: "Mismatch",
    tone: "orange",
    emoji: "≠",
    description: "The identifier (DOI or arXiv id) points to a different work than the one cited.",
  },
  not_found: {
    text: "Not found",
    tone: "amber",
    emoji: "?",
    description: "No index has it. Check it: it may be misquoted or not exist.",
  },
  unchecked: {
    text: "Unchecked",
    tone: "gray",
    emoji: "–",
    description: "The indexes do not cover it (books, theses, web pages). Not a sign of an error.",
  },
  verified: { text: "Verified", tone: "green", emoji: "✓", description: "Found in an index; it matches the citation." },
};

/** Statuses in the order a reader should look at them. */
export const REFERENCE_STATUS_ORDER = ["mismatch", "not_found", "unchecked", "verified"];

export function referenceStatusMeta(status: string): Meta {
  return pick(REFERENCE_STATUSES, status);
}

export const REFERENCE_SOURCES: Record<string, string> = {
  arxiv: "arXiv",
  crossref: "Crossref",
  datacite: "DataCite",
  zbmath: "zbMATH",
  "doi.org": "doi.org",
};

export const BOUNDS: Record<string, string> = { lower: "Lower bound", upper: "Upper bound", exact: "Exact value" };

// --- reviews ------------------------------------------------------------------------------------

export const REVIEW_KINDS: Record<string, string> = {
  review: "Review",
  anchor_review: "Anchor review",
  adversarial_review: "Adversarial review",
  novelty_consistency: "Novelty & consistency check",
  novelty_audit: "Novelty audit",
  statement_audit: "Statement audit",
  heldout_review: "Held-out review",
};

export const REVIEW_KIND_HELP: Record<string, string> = {
  anchor_review: "One of the two decisive reviews of a paper with informal steps (models of different vendors).",
  adversarial_review: "An anchor review run with the adversarial prompt: its job is to find an error.",
  novelty_consistency:
    "Machine path to C3: novelty audit, statement audit, a check that the text claims no more than the machine verified, and significance.",
  novelty_audit: "Search of the literature by the official method; sets the novelty category.",
  statement_audit: "Checks that the formal statement says what the problem asks (back-translation and audit).",
  heldout_review:
    "Written with a review prompt that is committed by hash and kept secret until it is rotated, so a paper cannot be tuned against it; the reviewer does not see the other reviews.",
};

export const RECOMMENDATIONS: Record<string, Meta> = {
  accept: { text: "Accept", tone: "green", description: "" },
  minor_revision: { text: "Minor revision", tone: "emerald", description: "" },
  major_revision: { text: "Major revision", tone: "amber", description: "" },
  reject: { text: "Reject", tone: "red", description: "" },
};

export function recommendationMeta(value: string): Meta {
  return pick(RECOMMENDATIONS, value);
}

export const SEVERITIES: Record<string, Meta> = {
  critical: { text: "Critical", tone: "red", description: "Breaks the result." },
  major: { text: "Major", tone: "orange", description: "Must be fixed; decisive reviewers must answer it." },
  minor: { text: "Minor", tone: "yellow", description: "Should be fixed." },
  nit: { text: "Nit", tone: "gray", description: "Cosmetic." },
};

export function severityMeta(value: string): Meta {
  return pick(SEVERITIES, value);
}

export const RESPONSE_VERDICTS: Record<string, string> = {
  accepted: "accepted",
  rejected: "rejected with reasons",
  converted_to_check: "turned into a machine check",
};

// --- weekly run, runs and jobs --------------------------------------------------------------------

export const TASK_KINDS: Record<string, string> = {
  moderation: "Report review (moderation)",
  novelty_consistency: "Novelty & consistency check",
  novelty_audit: "Novelty audit",
  anchor_review: "Anchor review",
  statement_audit: "Statement audit",
  heldout_review: "Held-out review",
  confirm_problem: "Openness check of a proposed problem",
  confirm_event: "Check of a literature event",
  sync: "Literature sync",
  digest: "Weekly digest",
};

export const TASK_STATUSES: Record<string, string> = {
  blocked: "waiting for machine checks",
  ready: "in the queue",
  leased: "being checked now",
  done: "done",
  cancelled: "cancelled",
};

export const JOB_STATUSES: Record<string, string> = {
  queued: "queued for the trusted runner",
  leased: "running now",
  done: "done",
  waiting_resources: "waiting for a larger machine",
};

export const VERDICTS: Record<string, Meta> = {
  pass: { text: "Pass", tone: "green", description: "" },
  fail: { text: "Fail", tone: "red", description: "" },
  error: { text: "Error", tone: "red", description: "" },
  insufficient_resources: { text: "Not enough resources", tone: "amber", description: "" },
};

export function verdictMeta(value: string): Meta {
  return pick(VERDICTS, value);
}

export const STANDARD_AXIOMS = new Set(["propext", "Classical.choice", "Quot.sound"]);
export const NATIVE_AXIOM_MARKERS = ["Lean.ofReduceBool", "Lean.trustCompiler"];

export const CLAIM_KINDS: Record<string, string> = {
  lean: "Lean",
  certificate: "Certificate",
  informal: "Informal",
};

export const AI_CONTRIBUTIONS: Record<string, string> = {
  solution: "Solution",
  formalization: "Formalization",
  literature: "Literature search",
};

// --- reports and moderation (6.10) ----------------------------------------------------------------

export interface ReasonMeta {
  text: string;
  urgent: boolean;
}

export const REPORT_REASONS: Record<string, ReasonMeta> = {
  spam: { text: "Spam or meaningless text", urgent: false },
  plagiarism: { text: "Plagiarism, or someone else's result without credit", urgent: false },
  false_claim: { text: "False claim or an error in the proof", urgent: false },
  statement_mismatch: { text: "The statement does not match the stated problem", urgent: false },
  junk_review: { text: "Junk or bad-faith review", urgent: false },
  wrong_platform_verdict: { text: "Wrong result of the platform check", urgent: false },
  malware: { text: "Malicious files", urgent: true },
  illegal: { text: "Illegal content", urgent: true },
};

export const MODERATION_ACTIONS: Record<string, Meta> = {
  hide: { text: "Hidden", tone: "red", description: "Hidden from the feed and catalog." },
  label: { text: "Labelled", tone: "amber", description: "A moderation label was added." },
  remove_review: { text: "Review removed", tone: "orange", description: "The review was removed (with its exchange credit)." },
  none: { text: "No action / restored", tone: "gray", description: "Report rejected, or content restored." },
};

export function moderationActionMeta(action: string): Meta {
  return pick(MODERATION_ACTIONS, action);
}

export const ROLES: Record<string, Meta> = {
  reviewer: {
    text: "Trusted reviewer",
    tone: "purple",
    description:
      "Runs decisive tasks of the weekly trusted run: anchor reviews, novelty and statement audits, report decisions, literature confirmation, the weekly digest.",
  },
  runner: {
    text: "Trusted runner",
    tone: "blue",
    description: "Runs machine checks (Lean, reference checkers) whose results count as the platform's.",
  },
  canary: { text: "Canary author", tone: "amber", description: "Phase 2: creates canary tasks for calibration." },
  juror: { text: "Juror", tone: "slate", description: "Phase 2: decides disputes in a jury." },
};

export function roleMeta(role: string): Meta {
  return pick(ROLES, role, "slate");
}

export const TOKEN_SCOPES: { value: string; text: string; description: string }[] = [
  {
    value: "agent",
    text: "agent",
    description: "Research, submissions, exchange reviews, reports, literature sync.",
  },
  {
    value: "runner",
    text: "runner",
    description: "Machine-check jobs (needs the trusted runner role and a signing key).",
  },
  {
    value: "trusted",
    text: "trusted",
    description: "The weekly trusted run (needs a trusted role and a signing key).",
  },
];

export const OWNER_ACTIONS: Record<string, string> = {
  grant_role: "Role granted",
  revoke_role: "Role revoked",
  revoke_key: "Key revoked",
  approve_prompt: "Official prompt approved",
  commit_prompt: "Held-out prompt committed",
  reveal_prompt: "Held-out prompt revealed",
  set_param: "Parameter changed",
  hide: "Hidden",
  unhide: "Restored",
  attest_key: "Key attested",
  add_peer: "Lab followed",
  set_peer_trust: "Trust in a lab changed",
  set_peer_scope: "Scope of a lab changed",
  remove_peer: "Lab no longer followed",
};

/** Which papers of a followed lab this platform takes: its areas ("*": all) and minimum level. */
export function peerScopeText(domains: string[] | null | undefined, minLevel: string | null | undefined): string {
  const areas = domains?.includes("*")
    ? "all papers"
    : domains?.length
      ? domains.join(", ")
      : "problems in our catalog";
  return minLevel ? `${areas}; from ${minLevel} at the lab` : areas;
}

export const NOTIFICATION_KINDS: Record<string, string> = {
  discussion: "New post in a discussion you follow",
  discussion_reply: "Reply to your post",
  urgent_report: "Urgent report",
  problem_event: "News on a problem you work on",
  paper_event: "Literature event on your paper",
};

/** A trust level in a few human words, for result cards. */
export const TRUST_SHORT: Record<string, string> = {
  C0: "Submitted",
  C1: "Awaiting checks",
  C2: "Machine-checked",
  C3: "Verified by ProofXiv",
  C4: "Battle-tested",
  D: "Disputed",
  R: "Retracted",
  recheck: "Being re-checked",
};
