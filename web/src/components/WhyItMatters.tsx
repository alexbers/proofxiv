import Link from "next/link";

import { Markdown } from "./Markdown";
import { cx } from "./ui";

/**
 * "Why it matters": the server's significance notes (records, settled problems, reviews, prizes).
 * Significance only ranks results; it never changes their trust level.
 */
export function WhyItMatters({
  notes,
  limit = 3,
  explain = false,
  className,
}: {
  notes: string[];
  limit?: number;
  /** add a link to the explanation of significance */
  explain?: boolean;
  className?: string;
}) {
  const shown = notes.slice(0, limit);
  if (!shown.length) return null;
  return (
    <div className={className}>
      <p className="text-xs font-semibold tracking-wide text-muted uppercase">
        Why it matters
        {explain ? (
          <>
            {" "}
            <Link href="/about#significance" className="font-normal tracking-normal normal-case">
              (how results are ranked)
            </Link>
          </>
        ) : null}
      </p>
      <ul className={cx("mt-1 list-disc space-y-0.5 pl-5 text-sm marker:text-accent")}>
        {shown.map((note) => (
          <li key={note} className="wrap-anywhere">
            <Markdown inline noLinks>
              {note}
            </Markdown>
          </li>
        ))}
      </ul>
    </div>
  );
}
