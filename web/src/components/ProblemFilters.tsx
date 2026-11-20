"use client";

import { useRef } from "react";

import type { DomainOut } from "@/lib/api";
import { PROBLEM_STATUSES } from "@/lib/labels";

/** Filters of the problem catalog (a GET form; selects apply immediately with JavaScript). */
export function ProblemFilters({
  q,
  status,
  domain,
  domains,
}: {
  q: string;
  status: string;
  domain: string;
  /** domains with their number of problems, largest first */
  domains: DomainOut[];
}) {
  const form = useRef<HTMLFormElement>(null);
  const submit = () => form.current?.requestSubmit();
  return (
    <form ref={form} method="get" action="/problems" role="search" aria-label="Filter problems" className="card p-3 sm:p-4">
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_13rem_15rem_auto] md:items-end">
        <div>
          <label htmlFor="problems-q" className="label">
            Search
          </label>
          <input
            id="problems-q"
            name="q"
            type="search"
            className="field"
            defaultValue={q}
            placeholder="e.g. kissing number, Erdős 108, Hadamard"
          />
        </div>
        <div>
          <label htmlFor="problems-status" className="label">
            Status
          </label>
          <select id="problems-status" name="status" className="field" defaultValue={status} onChange={submit}>
            <option value="">Any status</option>
            {Object.entries(PROBLEM_STATUSES).map(([value, meta]) => (
              <option key={value} value={value}>
                {meta.text}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="problems-domain" className="label">
            Domain
          </label>
          <select id="problems-domain" name="domain" className="field" defaultValue={domain} onChange={submit}>
            <option value="">All domains</option>
            {domains.map((d) => (
              <option key={d.domain} value={d.domain}>
                {d.domain} ({d.problems})
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="btn btn-primary">
          Search
        </button>
      </div>
    </form>
  );
}
