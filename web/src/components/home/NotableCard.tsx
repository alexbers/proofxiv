import Link from "next/link";

import type { Schemas } from "@/lib/api";
import { fmtDate, lowerFirst } from "@/lib/format";
import { splitPrevious } from "@/lib/headlines";
import { labelMeta, noveltyCategory, noveltyMeta, noveltyStatusMeta } from "@/lib/labels";

import { Markdown } from "../Markdown";
import { Chip, cx, MetaChip, TrustBadge } from "../ui";
import { WhyItMatters } from "../WhyItMatters";

type Notable = Schemas["NotableOut"];

/** Labels that qualify a result: shown on the card next to the trust level. */
const WARNING_LABELS = ["superseded", "prior_art_found", "machine_check_failed", "recheck"];

/** "1 review by the author", "4 reviews: 3 by ProofXiv, 1 by participants" */
export function reviewsText(counts: Record<string, number>): string {
  const parts: [string, number][] = [
    ["by ProofXiv", counts.trusted ?? 0],
    ["by participants", counts.exchange ?? 0],
    ["by the author", counts.author ?? 0],
  ];
  const total = parts.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return "no reviews yet";
  const present = parts.filter(([, n]) => n > 0);
  if (present.length === 1) return `${total} ${total === 1 ? "review" : "reviews"} ${present[0][0]}`;
  return `${total} reviews: ${present.map(([who, n]) => `${n} ${who}`).join(", ")}`;
}

function NoveltyBadge({ paper }: { paper: Notable }) {
  const category = noveltyCategory(paper);
  if (!category) return null;
  const meta = noveltyMeta(category);
  const status = noveltyStatusMeta(paper.novelty_status);
  return (
    <Chip tone={meta.tone} emoji="📚" description={`${meta.description} ${status.description}`}>
      {meta.text} · {lowerFirst(status.text)}
    </Chip>
  );
}

const HEADLINE = "mt-3 text-xl leading-snug font-semibold tracking-tight wrap-anywhere sm:text-2xl";

/** A numeric result in brief: "n(6,9) ≤ 4368", the previous best in small print. */
function NumericHeadline({ text }: { text: string }) {
  const [main, previous] = splitPrevious(text);
  return (
    <p className={HEADLINE}>
      <Markdown inline noLinks>
        {main}
      </Markdown>
      {previous ? (
        <span className="ml-2 text-base font-normal tracking-normal whitespace-nowrap text-muted">({previous})</span>
      ) : null}
    </p>
  );
}

export function NotableCard({
  paper,
  featured = false,
  wide = false,
}: {
  paper: Notable;
  /** the most significant result: highlighted */
  featured?: boolean;
  /** spans both columns of the grid (when the number of cards is odd) */
  wide?: boolean;
}) {
  const [headline, ...more] = paper.headlines;
  const title = (
    <Link href={`/papers/${paper.id}`} className={headline ? undefined : "text-fg hover:text-accent"}>
      <Markdown inline noLinks>
        {paper.title}
      </Markdown>
    </Link>
  );
  return (
    <article
      className={cx("card flex min-w-0 flex-col p-5", featured && "ring-1 ring-accent/30", wide && "md:col-span-2")}
      aria-labelledby={`notable-${paper.id}`}
    >
      {paper.problems.length ? (
        <ul className="flex flex-wrap gap-1.5" aria-label="Problems">
          {paper.problems.slice(0, 3).map((problem) => (
            <li key={problem.slug} className="max-w-full">
              <Link
                href={`/problems/${problem.slug}`}
                className="chip tone-slate max-w-full hover:no-underline"
                title={problem.title}
              >
                <span className="truncate">
                  <Markdown inline noLinks>
                    {problem.title}
                  </Markdown>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      ) : null}

      {headline ? (
        <>
          <NumericHeadline text={headline} />
          {more.length ? (
            <ul className="mt-1 space-y-0.5 text-sm text-muted">
              {more.map((line) => (
                <li key={line} className="wrap-anywhere">
                  <Markdown inline noLinks>
                    {line}
                  </Markdown>
                </li>
              ))}
            </ul>
          ) : null}
          <h3 id={`notable-${paper.id}`} className="mt-3 leading-snug font-medium wrap-anywhere">
            {title}
          </h3>
        </>
      ) : (
        // no numeric result: the title says what the paper proves
        <h3 id={`notable-${paper.id}`} className={HEADLINE}>
          {title}
        </h3>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <TrustBadge level={paper.level} claimLevels={paper.claim_levels} short />
        <NoveltyBadge paper={paper} />
        {paper.labels
          .filter((label) => WARNING_LABELS.includes(label))
          .map((label) => (
            <MetaChip key={label} meta={labelMeta(label)} />
          ))}
      </div>

      <WhyItMatters notes={paper.significance_notes} limit={3} className="mt-3" />

      <p className="mt-auto flex flex-wrap gap-x-3 gap-y-1 pt-4 text-xs text-muted">
        <span>{paper.byline}</span>
        <span>{fmtDate(paper.created_at)}</span>
        <span>{reviewsText(paper.review_counts)}</span>
        {paper.likes ? (
          <span title="Likes">
            <span aria-hidden="true">👍</span> {paper.likes}
            <span className="sr-only"> likes</span>
          </span>
        ) : null}
      </p>
    </article>
  );
}
