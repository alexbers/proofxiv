import Link from "next/link";

import type { PaperSummary, Schemas } from "@/lib/api";
import { fmtDate } from "@/lib/format";

import { Markdown } from "../Markdown";
import { Chip, TrustBadge } from "../ui";

/** The newest papers, one line each. */
export function LatestList({ papers }: { papers: PaperSummary[] }) {
  return (
    <ul className="card divide-y divide-line">
      {papers.map((paper) => {
        const note = paper.significance_notes[0];
        return (
          <li key={paper.id} className="flex flex-col gap-1.5 px-4 py-3 sm:flex-row sm:items-start sm:gap-4">
            <div className="shrink-0 sm:w-48 sm:pt-0.5">
              <TrustBadge level={paper.level} claimLevels={paper.claim_levels} short stacked />
            </div>
            <div className="min-w-0 flex-1">
              <Link href={`/papers/${paper.id}`} className="leading-snug font-medium wrap-anywhere">
                <Markdown inline noLinks>
                  {paper.title}
                </Markdown>
              </Link>
              {note ? (
                <p className="mt-0.5 text-sm wrap-anywhere">
                  <Markdown inline noLinks>
                    {note}
                  </Markdown>
                </p>
              ) : null}
              <p className="mt-0.5 text-xs text-muted">
                {paper.byline} · <span className="whitespace-nowrap">{fmtDate(paper.created_at)}</span>
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

/** Problems settled by papers on the platform. */
export function SolvedList({ problems }: { problems: Schemas["SolvedOut"][] }) {
  return (
    <ul className="card divide-y divide-line">
      {problems.map((p) => (
        <li key={p.slug} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-baseline sm:gap-4">
          <span className="shrink-0 text-sm text-muted tabular-nums sm:w-28">{fmtDate(p.at)}</span>
          <div className="min-w-0 flex-1">
            <p className="leading-relaxed">
              <span aria-hidden="true" className="mr-1.5 text-emerald-700 dark:text-emerald-300">
                ✓
              </span>
              <Link href={`/problems/${p.slug}`} className="mr-2 font-medium wrap-anywhere">
                <Markdown inline noLinks>
                  {p.title}
                </Markdown>
              </Link>
              <Chip tone="slate" square className="align-middle">
                {p.domain}
              </Chip>
            </p>
            {p.paper_id ? (
              <p className="mt-0.5 text-sm text-muted wrap-anywhere">
                Solved in{" "}
                <Link href={`/papers/${p.paper_id}`}>
                  <Markdown inline noLinks>
                    {p.paper_title ?? "the paper"}
                  </Markdown>
                </Link>
              </p>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** The opening of the last weekly digest (the server cuts it at a paragraph boundary). */
export function ThisWeek({ digest }: { digest: Schemas["DigestExcerptOut"] }) {
  return (
    <div className="card p-5 sm:p-6">
      <p className="text-sm text-muted">Weekly trusted run of {fmtDate(digest.started_at)}</p>
      <Markdown className="mt-2">{digest.excerpt}</Markdown>
      <p className="mt-3">
        <Link href={`/digest#digest-${digest.id}`} className="font-medium">
          Read the digest →
        </Link>
      </p>
    </div>
  );
}
