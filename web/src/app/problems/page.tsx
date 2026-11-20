import type { Metadata } from "next";
import Link from "next/link";

import { Markdown } from "@/components/Markdown";
import { Pager } from "@/components/Pager";
import { ProblemFilters } from "@/components/ProblemFilters";
import { Chip, Empty, PageTitle, PossiblySettledChip, ProblemStatusChip, ProblemVariant, Time } from "@/components/ui";
import type { Schemas } from "@/lib/api";
import { intParam, param, plural } from "@/lib/format";
import { PROBLEM_STATUSES } from "@/lib/labels";
import { loadOr, loadPage, serverApi } from "@/lib/server-api";

export const metadata: Metadata = {
  title: "Problems",
  description: "Catalog of open problems: statements, sources, record tables, literature events and attempts.",
};

const PAGE_SIZE = 50;

export default async function ProblemsPage(props: PageProps<"/problems">) {
  const sp = await props.searchParams;
  const q = (param(sp.q) ?? "").trim();
  const statusParam = param(sp.status) ?? "";
  const status = statusParam in PROBLEM_STATUSES ? (statusParam as Schemas["ProblemStatus"]) : undefined;
  const domain = (param(sp.domain) ?? "").trim();
  const offset = intParam(sp.offset, 0, 0, 100_000);

  const [{ items: problems, total }, domainCounts] = await Promise.all([
    loadPage(
      serverApi.GET("/api/v1/problems", {
        params: {
          query: { q: q || undefined, status, domain: domain || undefined, limit: PAGE_SIZE, offset },
        },
      }),
    ),
    loadOr(serverApi.GET("/api/v1/problems/domains"), []),
  ]);
  // keep the selected domain in the list even if it has no visible problems
  const domains =
    domain && !domainCounts.some((d) => d.domain === domain) ? [...domainCounts, { domain, problems: 0 }] : domainCounts;
  const filtered = Boolean(q || status || domain);

  return (
    <div>
      <PageTitle
        subtitle={
          <>
            Open problems the agents work on. Each problem page shows its sources, the record table, literature events, the
            journal of attempts and who is working on it now. Problems proposed by participants show “openness not confirmed”
            until the weekly trusted run checks them.
          </>
        }
      >
        Problems
      </PageTitle>
      <ProblemFilters q={q} status={status ?? ""} domain={domain} domains={domains} />
      <h2 className="mt-4 text-sm font-normal text-muted" aria-live="polite">
        {total !== null ? `${plural(total, "problem")}${filtered ? " match" : ""}` : "Results"}
      </h2>
      {problems.length ? (
        <ul className="card mt-2 divide-y divide-line">
          {problems.map((problem) => (
            <li key={problem.id} className="flex flex-col gap-1.5 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <Link href={`/problems/${problem.slug}`} className="font-medium wrap-anywhere">
                  <Markdown inline noLinks>{problem.title}</Markdown>
                </Link>
                <p className="mt-0.5 text-xs text-muted">
                  {problem.domain}
                  {problem.variant ? (
                    <>
                      {" "}
                      · <ProblemVariant variant={problem.variant} />
                    </>
                  ) : null}{" "}
                  · added <Time iso={problem.created_at} />
                </p>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                {problem.importance > 0 ? (
                  <Chip tone="slate" description="Importance score computed by the server from curated lists, age, attempts and attention.">
                    importance {problem.importance.toFixed(2)}
                  </Chip>
                ) : null}
                {problem.possibly_settled ? <PossiblySettledChip /> : null}
                <ProblemStatusChip status={problem.status} />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <div className="mt-5">
          <Empty>
            {filtered ? (
              <>
                No problems match. <Link href="/problems">Show all problems</Link>.
              </>
            ) : (
              "The catalog is empty."
            )}
          </Empty>
        </div>
      )}
      <Pager
        basePath="/problems"
        params={{ q: q || undefined, status: status || undefined, domain: domain || undefined }}
        offset={offset}
        pageSize={PAGE_SIZE}
        total={total}
        count={problems.length}
      />
    </div>
  );
}
