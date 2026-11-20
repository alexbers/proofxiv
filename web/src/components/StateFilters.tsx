"use client";

import { useRef } from "react";

import type { DomainOut } from "@/lib/api";
import { STATE_PERIODS } from "@/lib/config";

/** Domain and period of the "state of the field" page (a GET form; applies on change with JavaScript). */
export function StateFilters({ domain, days, domains }: { domain: string; days: number; domains: DomainOut[] }) {
  const form = useRef<HTMLFormElement>(null);
  const submit = () => form.current?.requestSubmit();
  return (
    <form ref={form} method="get" action="/state" aria-label="Choose domain and period" className="card p-3 sm:p-4">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_12rem_auto] sm:items-end">
        <div>
          <label htmlFor="state-domain" className="label">
            Domain
          </label>
          <select id="state-domain" name="domain" className="field" defaultValue={domain} onChange={submit}>
            <option value="">All domains</option>
            {domains.map((d) => (
              <option key={d.domain} value={d.domain}>
                {d.domain} ({d.problems})
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="state-days" className="label">
            Period
          </label>
          <select id="state-days" name="days" className="field" defaultValue={String(days)} onChange={submit}>
            {STATE_PERIODS.map((n) => (
              <option key={n} value={n}>
                {n === 365 ? "Last year" : `Last ${n} days`}
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className="btn btn-primary">
          Show
        </button>
      </div>
    </form>
  );
}
