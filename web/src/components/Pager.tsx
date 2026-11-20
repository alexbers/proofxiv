import Link from "next/link";

const NUMBER = new Intl.NumberFormat("en-GB");

/** Previous/next links for offset pagination; the total comes from the API's X-Total-Count. */
export function Pager({
  basePath,
  params,
  offset,
  pageSize,
  total,
  count,
}: {
  basePath: string;
  params: Record<string, string | undefined>;
  offset: number;
  pageSize: number;
  /** total number of matches (null if the API did not say) */
  total: number | null;
  /** number of items on this page */
  count: number;
}) {
  const hasNext = total !== null ? offset + pageSize < total : count >= pageSize;
  if (offset === 0 && !hasNext) return null;
  const href = (newOffset: number) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) if (value) search.set(key, value);
    if (newOffset > 0) search.set("offset", String(newOffset));
    const qs = search.toString();
    return qs ? `${basePath}?${qs}` : basePath;
  };
  const page = Math.floor(offset / pageSize) + 1;
  const pages = total !== null ? Math.max(1, Math.ceil(total / pageSize)) : null;
  const first = count ? offset + 1 : offset;
  const last = offset + count;
  return (
    <nav aria-label="Pagination" className="mt-6 flex items-center justify-between gap-3">
      {offset > 0 ? (
        <Link className="btn hover:no-underline" href={href(Math.max(0, offset - pageSize))} rel="prev">
          ← Previous
        </Link>
      ) : (
        <span className="btn pointer-events-none opacity-40" aria-disabled="true">
          ← Previous
        </span>
      )}
      <span className="text-center text-sm text-muted">
        Page {page}
        {pages !== null ? ` of ${pages}` : ""}
        {total !== null && count ? (
          <span className="hidden sm:inline">
            {" "}
            · {NUMBER.format(first)}–{NUMBER.format(last)} of {NUMBER.format(total)}
          </span>
        ) : null}
      </span>
      {hasNext ? (
        <Link className="btn hover:no-underline" href={href(offset + pageSize)} rel="next">
          Next →
        </Link>
      ) : (
        <span className="btn pointer-events-none opacity-40" aria-disabled="true">
          Next →
        </span>
      )}
    </nav>
  );
}
