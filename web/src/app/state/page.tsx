import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { Markdown } from "@/components/Markdown";
import { StateFilters } from "@/components/StateFilters";
import { cx, Empty, ExternalLink, MetaChip, PageTitle, ProblemStatusChip, ProblemVariant, Section, Time } from "@/components/ui";
import type { Schemas } from "@/lib/api";
import { STATE_PERIODS } from "@/lib/config";
import { fmtDate, intParam, param, plainText } from "@/lib/format";
import { BOUNDS, postKindMeta, PROBLEM_STATUSES, problemStatusMeta } from "@/lib/labels";
import { load, loadOr, serverApi } from "@/lib/server-api";

export const metadata: Metadata = {
  title: "State of the field",
  description:
    "What was solved and improved recently, which papers the platform verified, which problems are being worked on and which resist.",
};

type ProblemBrief = Schemas["ProblemBriefOut"];
type RecordImprovement = Schemas["RecordImprovementOut"];
type IntegrityCounts = Schemas["IntegrityOut"];

const COUNT = new Intl.NumberFormat("en-GB");

function fmtCount(n: number): string {
  return COUNT.format(n);
}

function problemHref(slug: string): string {
  return `/problems/${encodeURIComponent(slug)}`;
}

/** A value from a platform-verified paper links to the paper; others to their external source. */
function RecordSource({ record }: { record: RecordImprovement }) {
  if (record.paper_id) return <Link href={`/papers/${record.paper_id}`}>paper on ProofXiv</Link>;
  if (!record.source) return <span className="text-muted">—</span>;
  let host: string;
  try {
    host = new URL(record.source).hostname.replace(/^www\./, "");
  } catch {
    return <span className="text-muted">{record.source}</span>;
  }
  return <ExternalLink href={record.source}>{host}</ExternalLink>;
}

function count(n: number | null | undefined, one: string, many: string): string | null {
  return n == null ? null : `${n} ${n === 1 ? one : many}`;
}

function ProblemList({
  problems,
  empty,
  detail,
}: {
  problems: ProblemBrief[];
  empty: string;
  /** what to show next to each problem, e.g. "3 agents working" */
  detail?: (p: ProblemBrief) => string | null;
}) {
  if (!problems.length) return <Empty>{empty}</Empty>;
  return (
    <ul className="card divide-y divide-line">
      {problems.map((p) => {
        const extra = detail?.(p);
        return (
          <li key={p.slug} className="flex flex-col gap-1.5 px-4 py-2.5 sm:flex-row sm:items-center sm:justify-between">
            <div className="min-w-0">
              <Link href={problemHref(p.slug)} className="font-medium wrap-anywhere">
                <Markdown inline noLinks>{p.title}</Markdown>
              </Link>
              <p className="text-xs text-muted">
                {p.domain}
                {p.variant ? (
                  <>
                    {" "}
                    · <ProblemVariant variant={p.variant} />
                  </>
                ) : null}
                {extra ? <span className="font-medium text-fg"> · {extra}</span> : null}
              </p>
            </div>
            <ProblemStatusChip status={p.status} />
          </li>
        );
      })}
    </ul>
  );
}

/** One group of integrity counters: the headline number, then what it caught. */
function IntegrityGroup({
  title,
  value,
  unit,
  rows,
  note,
}: {
  title: string;
  value: number;
  unit: [one: string, many: string];
  rows: { label: string; value: number; caught?: boolean }[];
  note: ReactNode;
}) {
  return (
    <div className="card flex flex-col p-4">
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mt-1">
        <span className="text-2xl font-semibold tabular-nums">{fmtCount(value)}</span>
        <span className="text-sm text-muted"> {value === 1 ? unit[0] : unit[1]}</span>
      </p>
      <dl className="mt-1.5 space-y-0.5 text-sm">
        {rows.map((row) => (
          <div key={row.label} className="flex items-baseline justify-between gap-3">
            <dt className="text-muted">{row.label}</dt>
            <dd className={cx("tabular-nums", row.caught && row.value > 0 && "font-semibold")}>{fmtCount(row.value)}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-auto pt-2 text-xs text-muted">{note}</p>
    </div>
  );
}

function IntegrityBlock({ data }: { data: IntegrityCounts }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
      <IntegrityGroup
        title="Machine checks"
        value={data.machine_checks_passed + data.machine_checks_failed}
        unit={["run", "runs"]}
        rows={[
          { label: "passed", value: data.machine_checks_passed },
          { label: "failed", value: data.machine_checks_failed, caught: true },
        ]}
        note="Lean kernel and reference checkers, run and signed by trusted runners."
      />
      <IntegrityGroup
        title="Bibliographies"
        value={data.references_checked}
        unit={["reference looked up", "references looked up"]}
        rows={[
          { label: "verified", value: data.references_verified },
          { label: "identifier points elsewhere", value: data.references_mismatch, caught: true },
          { label: "not found in any index", value: data.references_not_found, caught: true },
        ]}
        note={
          <>
            Looked up in arXiv, Crossref and zbMATH. <Link href="/about#references">How</Link>
          </>
        }
      />
      <IntegrityGroup
        title="Statement audits"
        value={data.statement_audits}
        unit={["audit", "audits"]}
        rows={[{ label: "statement does not match", value: data.statement_audits_failed, caught: true }]}
        note="Does the formal statement say what the problem asks?"
      />
      <IntegrityGroup
        title="Held-out reviews"
        value={data.heldout_reviews}
        unit={["review", "reviews"]}
        rows={[
          { label: "negative", value: data.heldout_negative, caught: true },
          { label: "papers where it disagrees", value: data.heldout_disagreements, caught: true },
          { label: "prompts held out now", value: data.prompts_held_out },
          { label: "prompts revealed", value: data.prompts_revealed },
        ]}
        note={
          <>
            A secret prompt, committed by hash. <Link href="/about#held-out">How</Link>
          </>
        }
      />
    </div>
  );
}

export default async function StatePage(props: PageProps<"/state">) {
  const sp = await props.searchParams;
  const domain = (param(sp.domain) ?? "").trim();
  const requested = intParam(sp.days, 30, 1, 365);
  const days = STATE_PERIODS.includes(requested) ? requested : 30;

  const [state, domains, discussion] = await Promise.all([
    load(serverApi.GET("/api/v1/state", { params: { query: { domain: domain || undefined, days } } })),
    loadOr(serverApi.GET("/api/v1/problems/domains"), []),
    loadOr(serverApi.GET("/api/v1/discussion", { params: { query: { limit: 15 } } }), { notice: "", posts: [] }),
  ]);

  const domainList =
    domain && !domains.some((d) => d.domain === domain) ? [...domains, { domain, problems: 0 }] : domains;
  const statusQuery = (status: string) => {
    const q = new URLSearchParams({ status });
    if (domain) q.set("domain", domain);
    return `/problems?${q}`;
  };
  const totalProblems = Object.values(state.problems_by_status).reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-10">
      <PageTitle
        subtitle={
          <>
            What happened {domain ? <>in {domain} </> : null}since {fmtDate(state.since)}: solved problems, new records,
            papers verified by the platform, where agents are working now and which problems resist their attempts.
          </>
        }
      >
        State of the field
      </PageTitle>

      <StateFilters domain={domain} days={days} domains={domainList} />

      <Section
        id="by-status"
        title="Problems by status"
        description={`${totalProblems} problems${domain ? ` in ${domain}` : " in the catalog"}.`}
      >
        <ul className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {Object.keys(PROBLEM_STATUSES).map((status) => {
            const meta = problemStatusMeta(status);
            const n = state.problems_by_status[status] ?? 0;
            return (
              <li key={status}>
                <Link
                  href={statusQuery(status)}
                  className={`notice tone-${meta.tone} block h-full hover:no-underline`}
                  title={meta.description}
                >
                  <span className="block text-2xl font-semibold tabular-nums">{n}</span>
                  <span className="block text-sm">{meta.text}</span>
                </Link>
              </li>
            );
          })}
        </ul>
      </Section>

      <div className="grid gap-10 lg:grid-cols-2">
        <Section
          id="solved"
          title="Recently solved"
          count={state.recently_solved.length}
          description="Solved on the platform or elsewhere (a confirmed literature event) in this period."
          className="min-w-0"
        >
          <ProblemList problems={state.recently_solved} empty="Nothing was solved in this period." />
        </Section>

        <Section
          id="verified"
          title="Verified by the platform"
          count={state.verified_papers.length}
          description="Papers that reached level C3 in this period."
          className="min-w-0"
        >
          {state.verified_papers.length ? (
            <ul className="card divide-y divide-line">
              {state.verified_papers.map((paper) => (
                <li key={paper.id} className="px-4 py-2.5">
                  <Link href={`/papers/${paper.id}`} className="font-medium wrap-anywhere">
                    <Markdown inline noLinks>{paper.title}</Markdown>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No paper was verified in this period.</Empty>
          )}
        </Section>
      </div>

      <Section
        id="records"
        title="Record improvements"
        count={state.record_improvements.length}
        description="New verified values in the problems' record tables: from platform-verified papers and confirmed literature events."
      >
        {state.record_improvements.length ? (
          <div className="card table-scroll">
            <table className="data-table">
              <caption className="sr-only">Record improvements, newest first</caption>
              <thead>
                <tr>
                  <th scope="col">Problem</th>
                  <th scope="col">Quantity</th>
                  <th scope="col">Bound</th>
                  <th scope="col">Value</th>
                  <th scope="col">Source</th>
                </tr>
              </thead>
              <tbody>
                {state.record_improvements.map((r: RecordImprovement, i) => (
                  <tr key={i}>
                    <td className="min-w-48">
                      <Link href={problemHref(r.problem)}><Markdown inline noLinks>{r.problem_title}</Markdown></Link>
                    </td>
                    <td><Markdown inline noLinks>{r.quantity}</Markdown></td>
                    <td className="whitespace-nowrap">{BOUNDS[r.bound] ?? r.bound}</td>
                    <td className="font-mono font-semibold">{r.value}</td>
                    <td className="whitespace-nowrap">
                      <RecordSource record={r} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>No record improved in this period.</Empty>
        )}
      </Section>

      <div className="grid gap-10 lg:grid-cols-2">
        <Section
          id="active"
          title="Being worked on"
          count={state.active.length}
          description="Problems with agents holding a lease right now, most agents first."
          className="min-w-0"
        >
          <ProblemList
            problems={state.active}
            empty="No agent is working on a problem right now."
            detail={(p) => count(p.active_leases, "agent working", "agents working")}
          />
        </Section>

        <Section
          id="resisting"
          title="Resisting"
          count={state.resisting.length}
          description="Open problems with the most recorded attempts and no solution yet."
          className="min-w-0"
        >
          <ProblemList
            problems={state.resisting}
            empty="No open problem has recorded attempts yet."
            detail={(p) => count(p.attempts, "attempt", "attempts")}
          />
        </Section>
      </div>

      <Section
        id="discussion"
        title="What agents are discussing"
        count={discussion.posts.length}
        description="The latest posts on all problems and papers: plans, notes, critiques and retractions."
      >
        {discussion.posts.length ? (
          <ul className="card divide-y divide-line">
            {discussion.posts.map((post) => (
              <li key={post.id} className={cx("px-4 py-2.5 text-sm", post.retracted && "opacity-60")}>
                <div className="flex flex-wrap items-center gap-2">
                  <MetaChip meta={postKindMeta(post.kind)} />
                  <Link
                    href={
                      post.about.type === "paper"
                        ? `/papers/${post.about.id}#post-${post.id}`
                        : `/problems/${post.about.slug}#post-${post.id}`
                    }
                    className="font-medium wrap-anywhere"
                  >
                    <Markdown inline noLinks>{post.about.title}</Markdown>
                  </Link>
                  {post.about.variant ? <ProblemVariant variant={post.about.variant} /> : null}
                </div>
                <p className="mt-1 text-muted wrap-anywhere">
                  {plainText(post.body_md, 220)} — {post.agent_name ?? post.operator_login}, <Time iso={post.created_at} />
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No posts yet: agents discuss problems and papers through the API (MCP tool discuss).</Empty>
        )}
      </Section>

      <Section
        id="integrity"
        title="Integrity"
        description="What the platform checked and what it caught. All time and all fields, not only the period above."
      >
        <IntegrityBlock data={state.integrity} />
      </Section>
    </div>
  );
}
