"use client";

import { useRef } from "react";

import { FEED_SORTS } from "@/lib/feed";

/**
 * Filters of the paper list. A plain GET form (works without JavaScript);
 * with JavaScript, checkboxes and the sort order apply immediately.
 */
export function FeedFilters({
  q,
  independentOnly,
  verifiedOnly,
  sort,
  action = "/papers",
  hidden = {},
}: {
  q: string;
  independentOnly: boolean;
  verifiedOnly: boolean;
  sort: string;
  action?: string;
  hidden?: Record<string, string>;
}) {
  const form = useRef<HTMLFormElement>(null);
  const submit = () => form.current?.requestSubmit();

  return (
    <form ref={form} method="get" action={action} role="search" aria-label="Filter papers" className="card p-3 sm:p-4">
      {Object.entries(hidden).map(([name, value]) => (
        <input key={name} type="hidden" name={name} value={value} />
      ))}
      <div className="flex flex-col gap-3 md:flex-row md:items-end">
        <div className="flex-1">
          <label htmlFor="feed-q" className="label">
            Search papers
          </label>
          <input
            id="feed-q"
            name="q"
            type="search"
            defaultValue={q}
            placeholder="Title or abstract, e.g. Ramsey colouring"
            className="field"
          />
        </div>
        <div className="md:w-56">
          <label htmlFor="feed-sort" className="label">
            Sort by
          </label>
          <select id="feed-sort" name="sort" defaultValue={sort} className="field" onChange={submit}>
            {FEED_SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="btn btn-primary md:mb-px">
          Apply
        </button>
      </div>
      <fieldset className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-sm">
        <legend className="sr-only">Only show</legend>
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" name="independent_only" value="1" defaultChecked={independentOnly} onChange={submit} />
          Only with independent reviews
        </label>
        <label className="inline-flex items-center gap-2">
          <input type="checkbox" name="verified_only" value="1" defaultChecked={verifiedOnly} onChange={submit} />
          Only verified by the platform
        </label>
      </fieldset>
    </form>
  );
}
