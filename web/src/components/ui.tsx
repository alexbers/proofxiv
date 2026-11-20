/** Small presentational components shared by server and client components (no hooks). */
import type { ReactNode } from "react";

import { fmtDate, fmtDateTime, lowerFirst } from "@/lib/format";
import {
  labelMeta,
  levelMeta,
  moderationLabelMeta,
  noveltyMeta,
  noveltyStatusMeta,
  tierMeta,
  problemStatusMeta,
  sortLabels,
  TRUST_SHORT,
  type Meta,
  type Tone,
} from "@/lib/labels";

export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

/** A coloured chip with readable text; the explanation is a tooltip and is read by screen readers. */
export function Chip({
  tone = "gray",
  emoji,
  children,
  description,
  square,
  className,
}: {
  tone?: Tone;
  emoji?: string;
  children: ReactNode;
  description?: string;
  square?: boolean;
  className?: string;
}) {
  return (
    <span className={cx("chip", `tone-${tone}`, square && "chip-square", className)} title={description || undefined}>
      {emoji ? <span aria-hidden="true">{emoji}</span> : null}
      <span className="truncate">{children}</span>
      {description ? <span className="sr-only"> — {description}</span> : null}
    </span>
  );
}

export function MetaChip({ meta, prefix, className }: { meta: Meta; prefix?: string; className?: string }) {
  return (
    <Chip tone={meta.tone} emoji={meta.emoji} description={meta.description} className={className}>
      {prefix}
      {meta.text}
    </Chip>
  );
}

export function LevelBadge({ level, compact, short }: { level: string; compact?: boolean; short?: boolean }) {
  const meta = levelMeta(level);
  const code = level === "recheck" ? "↻" : level;
  const name = short ? (TRUST_SHORT[level] ?? meta.text) : meta.text;
  return (
    <span className={cx("level", `tone-${meta.tone}`)} title={`${code} — ${name}. ${meta.description}`}>
      <span className="level-code">{code}</span>
      {compact ? <span className="sr-only">{name}</span> : <span className="level-name">{name}</span>}
      <span className="sr-only"> — {meta.description}</span>
    </span>
  );
}

/** Claims of the current version that are machine-checked (C2 or above), out of all claims. */
export function checkedClaims(claimLevels: Record<string, number> | undefined): { checked: number; total: number } {
  const levels = claimLevels ?? {};
  const total = Object.values(levels).reduce((sum, n) => sum + n, 0);
  const checked = (levels.C2 ?? 0) + (levels.C3 ?? 0) + (levels.C4 ?? 0);
  return { checked, total };
}

/**
 * A paper's trust level, honest about partial checks: a C1 paper (some claims are informal) whose
 * other claims are machine-checked says "Partly machine-checked · 4 of 9 claims".
 */
export function TrustBadge({
  level,
  claimLevels,
  short,
  stacked,
}: {
  level: string;
  claimLevels?: Record<string, number>;
  short?: boolean;
  /** for narrow columns: the claim count on a second line */
  stacked?: boolean;
}) {
  const { checked, total } = checkedClaims(claimLevels);
  if (level !== "C1" || checked === 0) return <LevelBadge level={level} short={short} />;
  const meta = levelMeta(level);
  const count = `${checked} of ${total} claims`;
  const description =
    `${count} are machine-checked (Lean proofs or certificate checkers); ` +
    "the other claims are informal and are checked by the platform's reviews.";
  return (
    <span
      className={cx("level max-w-full", `tone-${meta.tone}`)}
      title={`C1 — Partly machine-checked: ${description}`}
    >
      <span className="level-code">C1</span>
      {stacked ? (
        <span className="level-name">
          <span className="block">Partly machine-checked</span>
          <span className="block font-medium">{count}</span>
        </span>
      ) : (
        <span className="level-name whitespace-normal">
          Partly machine-checked <span className="whitespace-nowrap">· {count}</span>
        </span>
      )}
      <span className="sr-only"> — {description}</span>
    </span>
  );
}

export function LabelChips({
  labels,
  moderationLabels = [],
  exchangeCount,
  nextCheck,
  hidden,
  hiddenReason,
}: {
  labels: string[];
  moderationLabels?: string[];
  exchangeCount?: number;
  nextCheck?: string | null;
  hidden?: boolean;
  hiddenReason?: string | null;
}) {
  // the server may not have recomputed labels right after a manual hide
  const all = hidden && !labels.includes("hidden") ? ["hidden", ...labels] : labels;
  if (!all.length && !moderationLabels.length) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Labels">
      {moderationLabels.map((label) => (
        <li key={`m-${label}`} className="max-w-full">
          <MetaChip meta={{ ...moderationLabelMeta(label), emoji: "⚠" }} />
        </li>
      ))}
      {sortLabels(all).map((label) => (
        <li key={label} className="max-w-full">
          <MetaChip meta={labelMeta(label, { exchangeCount, nextCheck, hiddenReason, formatDate: fmtDate })} />
        </li>
      ))}
    </ul>
  );
}

/** The paper came from another ProofXiv lab this platform follows (federation). */
export function OriginChip({ lab }: { lab: string }) {
  return (
    <Chip tone="sky" emoji="⇄" description="Submitted to another ProofXiv lab this platform follows; see the paper page for how much it is trusted.">
      from {lab}
    </Chip>
  );
}

/** How good the result is, from the reviewers' significance scores (or known: a rediscovery). */
export function TierChip({ tier, basis }: { tier: string; basis?: string | null }) {
  const meta = tierMeta(tier);
  return (
    <Chip tone={meta.tone} emoji={meta.emoji} description={basis ? `${meta.description} From the ${basis}.` : meta.description}>
      {meta.text}
    </Chip>
  );
}

export function NoveltyChip({ category, status }: { category: string | null; status?: string }) {
  if (category) {
    const meta = noveltyMeta(category);
    const statusText = status ? ` (${lowerFirst(noveltyStatusMeta(status).text)})` : "";
    return (
      <Chip tone={meta.tone} emoji="📚" description={`${meta.description}${statusText}`}>
        Novelty: {meta.text}
      </Chip>
    );
  }
  if (status) {
    const meta = noveltyStatusMeta(status);
    return (
      <Chip tone="gray" emoji="📚" description={meta.description}>
        Novelty: {lowerFirst(meta.text)}
      </Chip>
    );
  }
  return null;
}

export function ProblemStatusChip({ status }: { status: string }) {
  return <MetaChip meta={problemStatusMeta(status)} />;
}

/** An unchecked sign from outside that an open problem is already settled (the automatic watch). */
export function PossiblySettledChip() {
  return (
    <Chip
      tone="amber"
      emoji="⚠"
      description="The automatic watch found an unchecked sign that it is already settled (formal-conjectures, erdosproblems.com, Palomar…); the weekly run will check it."
    >
      possibly settled
    </Chip>
  );
}

/** Status of a literature event: confirmed / rejected by the weekly trusted run, or not checked yet. */
export function EventStatus({ confirmed }: { confirmed: boolean | null }) {
  if (confirmed === true) {
    return (
      <Chip tone="green" emoji="✓" description="Confirmed by the weekly trusted run.">
        Confirmed
      </Chip>
    );
  }
  if (confirmed === false) {
    return (
      <Chip tone="red" emoji="✗" description="Checked by the weekly trusted run and rejected.">
        Rejected
      </Chip>
    );
  }
  return (
    <Chip tone="yellow" emoji="?" description="Reported by a participant's agent; waiting for the weekly check.">
      Unconfirmed
    </Chip>
  );
}

export function Time({ iso, withTime }: { iso: string | null | undefined; withTime?: boolean }) {
  if (!iso) return null;
  return (
    <time dateTime={iso} title={fmtDateTime(iso)}>
      {withTime ? fmtDateTime(iso) : fmtDate(iso)}
    </time>
  );
}

export function ExternalLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  const safe = /^https?:\/\//i.test(href) ? href : undefined;
  if (!safe) return <span className={className}>{children}</span>;
  return (
    <a href={safe} className={className} rel="noopener noreferrer nofollow ugc" target="_blank">
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

export function Section({
  id,
  title,
  emoji,
  count,
  description,
  actions,
  children,
  className,
  level = 2,
}: {
  id?: string;
  title: string;
  /** decoration before the title (hidden from screen readers) */
  emoji?: string;
  count?: number;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  /** heading level (3 for sub-sections) */
  level?: 2 | 3;
}) {
  const headingId = id ? `${id}-title` : undefined;
  const Heading = level === 3 ? "h3" : "h2";
  return (
    <section id={id} aria-labelledby={headingId} className={cx("scroll-mt-20", className)}>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <Heading id={headingId} className="text-lg font-semibold tracking-tight">
            {emoji ? (
              <span aria-hidden="true" className="mr-2">
                {emoji}
              </span>
            ) : null}
            {title}
            {count !== undefined ? <span className="ml-2 text-sm font-normal text-muted">({count})</span> : null}
          </Heading>
          {description ? <div className="mt-0.5 text-sm text-muted">{description}</div> : null}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">{children}</p>;
}

export function Notice({ tone = "gray", children, role }: { tone?: Tone; children: ReactNode; role?: "status" | "alert" | "note" }) {
  return (
    <div className={cx("notice", `tone-${tone}`)} role={role}>
      {children}
    </div>
  );
}

export function Hash({ value, length = 12 }: { value: string; length?: number }) {
  return (
    <code className="inline" title={value}>
      {value.slice(0, length)}
      {value.length > length ? "…" : ""}
    </code>
  );
}

/** What tells apart catalog problems that share a title (for imported ones, the Lean theorem name). */
export function ProblemVariant({ variant, className }: { variant: string | null | undefined; className?: string }) {
  if (!variant) return null;
  return (
    <code
      className={cx("inline text-xs wrap-anywhere", className)}
      title="Variant: problems that share a title are told apart by it (for imported problems, the name of the Lean statement)."
    >
      {variant}
    </code>
  );
}

export function PageTitle({ children, subtitle }: { children: ReactNode; subtitle?: ReactNode }) {
  return (
    <div className="mb-6">
      <h1 className="text-2xl font-semibold tracking-tight wrap-anywhere sm:text-3xl">{children}</h1>
      {subtitle ? <div className="mt-1.5 max-w-3xl text-muted">{subtitle}</div> : null}
    </div>
  );
}
