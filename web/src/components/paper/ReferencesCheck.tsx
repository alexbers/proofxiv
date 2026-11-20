import Link from "next/link";

import type { ReferenceItem, ReferencesOut } from "@/lib/api";
import { REFERENCE_SOURCES, REFERENCE_STATUS_ORDER, referenceStatusMeta } from "@/lib/labels";

import { Chip, ExternalLink, MetaChip, Section } from "../ui";

const COUNT_TEXT: Record<string, string> = {
  verified: "verified",
  mismatch: "mismatch",
  not_found: "not found",
  unchecked: "unchecked",
};

function rank(status: string): number {
  const i = REFERENCE_STATUS_ORDER.indexOf(status);
  return i === -1 ? REFERENCE_STATUS_ORDER.length : i;
}

function Item({ item }: { item: ReferenceItem }) {
  const source = item.source ? (REFERENCE_SOURCES[item.source] ?? item.source) : null;
  return (
    <li className="py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <MetaChip meta={referenceStatusMeta(item.status)} />
        <code className="inline">{item.key}</code>
        {item.url ? (
          <ExternalLink href={item.url} className="text-xs">
            {source ? `${source} record` : "Found record"}
          </ExternalLink>
        ) : source ? (
          <span className="text-xs text-muted">via {source}</span>
        ) : null}
      </div>
      <p className="mt-1 wrap-anywhere">{item.text}</p>
      {item.status === "mismatch" && item.found_title ? (
        <p className="mt-0.5 wrap-anywhere">
          <span className="text-muted">The identifier points to: </span>“{item.found_title}”
        </p>
      ) : null}
      {item.note ? <p className="mt-0.5 text-xs text-muted">{item.note}</p> : null}
    </li>
  );
}

/** The bibliography resolved against arXiv, Crossref and zbMATH by a trusted runner. */
export function ReferencesCheck({ references }: { references: ReferencesOut }) {
  const items = [...references.items].sort((a, b) => rank(a.status) - rank(b.status));
  const count = (status: string) => references.counts[status] ?? items.filter((i) => i.status === status).length;
  const problems = count("mismatch") + count("not_found");
  return (
    <Section
      id="references"
      title="References check"
      count={items.length}
      description={
        <>
          Every reference is looked up automatically in arXiv, Crossref and zbMATH. “Unchecked” means these indexes do not
          cover it (books, theses, web pages), not that it is wrong; “not found” means no index has it, which a reader should
          check. <Link href="/about#references">How it works</Link>
        </>
      }
    >
      <div className="card px-4 py-3">
        {items.length ? (
          <>
            <ul className="flex flex-wrap gap-1.5" aria-label="References by status">
              {REFERENCE_STATUS_ORDER.filter((status) => count(status) > 0).map((status) => {
                const meta = referenceStatusMeta(status);
                return (
                  <li key={status}>
                    <Chip tone={meta.tone} emoji={meta.emoji} description={meta.description}>
                      <span className="tabular-nums">{count(status)}</span> {COUNT_TEXT[status]}
                    </Chip>
                  </li>
                );
              })}
            </ul>
            <details className="mt-2" open={problems > 0}>
              <summary className="text-sm font-medium">
                {problems ? "References, problems first" : "All references"} ({items.length})
              </summary>
              <ol className="divide-y divide-line">
                {items.map((item, i) => (
                  <Item key={`${item.key}-${i}`} item={item} />
                ))}
              </ol>
            </details>
          </>
        ) : (
          <p className="text-sm text-muted">No references were found in the bibliography files.</p>
        )}
      </div>
    </Section>
  );
}
