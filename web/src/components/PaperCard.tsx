import Link from "next/link";

import type { PaperSummary } from "@/lib/api";
import { excerpt } from "@/lib/format";
import { noveltyCategory } from "@/lib/labels";

import { Markdown } from "./Markdown";
import { LabelChips, NoveltyChip, OriginChip, TierChip, Time, TrustBadge } from "./ui";

function reviewSummary(counts: Record<string, number>): string {
  const parts: string[] = [];
  if (counts.trusted) parts.push(`${counts.trusted} by the platform`);
  if (counts.exchange) parts.push(`${counts.exchange} by participants`);
  if (counts.author) parts.push(`${counts.author} by the author`);
  return parts.length ? `Reviews: ${parts.join(", ")}` : "No reviews";
}

export function PaperCard({ paper }: { paper: PaperSummary }) {
  return (
    <article className="card p-4 sm:p-5" aria-labelledby={`paper-${paper.id}`}>
      <div className="flex flex-wrap items-center gap-2">
        <TrustBadge level={paper.level} claimLevels={paper.claim_levels} />
        <NoveltyChip category={noveltyCategory(paper)} status={paper.novelty_status} />
        {paper.tier ? <TierChip tier={paper.tier} basis={paper.tier_basis} /> : null}
        {paper.origin_lab ? <OriginChip lab={paper.origin_lab} /> : null}
        {paper.version > 1 ? <span className="text-xs text-muted">version {paper.version}</span> : null}
      </div>
      <h2 id={`paper-${paper.id}`} className="title-serif mt-2 text-lg leading-snug font-semibold wrap-anywhere sm:text-xl">
        <Link href={`/papers/${paper.id}`} className="text-fg">
          <Markdown inline noLinks>
            {paper.title}
          </Markdown>
        </Link>
      </h2>
      <p className="mt-0.5 text-sm text-muted">{paper.byline}</p>
      {paper.significance_notes[0] ? (
        <p className="mt-2 text-sm wrap-anywhere">
          <span className="mr-1.5 text-xs font-semibold tracking-wide text-muted uppercase">Why it matters</span>
          <Markdown inline noLinks>
            {paper.significance_notes[0]}
          </Markdown>
        </p>
      ) : null}
      <Markdown className="mt-2 text-[0.94rem] text-fg/90">{excerpt(paper.abstract, 340)}</Markdown>
      <div className="mt-3">
        <LabelChips
          labels={paper.labels}
          moderationLabels={paper.moderation_labels}
          exchangeCount={paper.review_counts.exchange}
          nextCheck={paper.next_platform_check}
          hidden={paper.hidden}
          hiddenReason={paper.hidden_reason}
        />
      </div>
      <p className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span title="Likes">
          <span aria-hidden="true">👍</span> {paper.likes} <span className="sr-only">likes</span>
        </span>
        <span title="Followers">
          <span aria-hidden="true">🔔</span> {paper.follows} <span className="sr-only">followers</span>
        </span>
        <span>{reviewSummary(paper.review_counts)}</span>
        <span>
          Submitted <Time iso={paper.created_at} />
        </span>
        {paper.verified_at ? (
          <span>
            Verified <Time iso={paper.verified_at} />
          </span>
        ) : null}
      </p>
    </article>
  );
}
