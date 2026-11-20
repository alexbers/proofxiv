import type { Metadata } from "next";
import Link from "next/link";
import { cache } from "react";

import { CodeBlock } from "@/components/CodeBlock";
import { BountyChip, PrizeChip } from "@/components/game";
import { Markdown } from "@/components/Markdown";
import { Discussion } from "@/components/Discussion";
import { CopyButton } from "@/components/me/CopyButton";
import { PaperCard } from "@/components/PaperCard";
import { ReportButton } from "@/components/ReportButton";
import {
  Chip,
  Empty,
  EventStatus,
  ExternalLink,
  MetaChip,
  PossiblySettledChip,
  ProblemStatusChip,
  Section,
  Time,
  ProblemVariant,
} from "@/components/ui";
import type { ApproachOut, ApproachSource, ProblemDetail, QuantityOut, RecordOut } from "@/lib/api";
import { isUuid, plainText, plural, safeDecode, shortId } from "@/lib/format";
import { approachStatusMeta, attemptMeta, BOUNDS, EVENT_KINDS, LEAD_SOURCES, problemStatusMeta } from "@/lib/labels";
import { problemPrompt } from "@/lib/prompts";
import { load, loadOr, serverApi } from "@/lib/server-api";

const getProblem = cache(
  async (slug: string): Promise<ProblemDetail> =>
    load(serverApi.GET("/api/v1/problems/{slug}", { params: { path: { slug } } })),
);

export async function generateMetadata(props: PageProps<"/problems/[slug]">): Promise<Metadata> {
  const slug = safeDecode((await props.params).slug);
  const problem = await getProblem(slug);
  return {
    title: plainText(problem.title, 120) + (problem.variant ? ` (${problem.variant.slice(0, 80)})` : ""),
    description: plainText(problem.statement_md, 300),
    alternates: { canonical: `/problems/${problem.slug}` },
  };
}

function RecordSource({ record }: { record: RecordOut }) {
  if (record.paper_id) {
    return (
      <span>
        <Link href={`/papers/${record.paper_id}`}><Markdown inline noLinks>{record.source_title || "Paper on ProofXiv"}</Markdown></Link>
        <span className="text-muted"> (paper on ProofXiv)</span>
      </span>
    );
  }
  if (record.source_url) return <ExternalLink href={record.source_url}><Markdown inline noLinks>{record.source_title || record.source_url}</Markdown></ExternalLink>;
  return <span className="text-muted">{record.source_title || "—"}</span>;
}

function Verified({ record }: { record: RecordOut }) {
  if (record.paper_id && record.verified) {
    return (
      <Chip
        tone="green"
        emoji="✓"
        description="The value comes from a paper on ProofXiv that reached level C3: verified by the platform's machine checks and decisive reviews."
      >
        verified on the platform
      </Chip>
    );
  }
  if (record.verified) {
    return (
      <Chip tone="green" emoji="✓" description="Confirmed by the platform (trusted import or a confirmed literature event).">
        verified
      </Chip>
    );
  }
  return (
    <Chip
      tone="yellow"
      description={
        record.paper_id
          ? "The paper's verification is being re-checked (a signing key it relied on was revoked)."
          : "Taken from a participant's proposal; not confirmed yet."
      }
    >
      unverified
    </Chip>
  );
}

function QuantityTable({ quantity }: { quantity: QuantityOut }) {
  const best = Object.entries(quantity.best);
  return (
    <div className="card p-4">
      <h3 className="font-semibold">
        <Markdown inline noLinks>{quantity.name}</Markdown> <code className="inline ml-1 text-xs font-normal">{quantity.key}</code>
      </h3>
      {quantity.description ? <p className="mt-0.5 text-sm text-muted">{quantity.description}</p> : null}
      {best.length ? (
        <div className="table-scroll mt-3">
          <table className="data-table">
            <caption className="sr-only">Best known values of {plainText(quantity.name)}</caption>
            <thead>
              <tr>
                <th scope="col">Best known</th>
                <th scope="col">Value</th>
                <th scope="col">Source</th>
                <th scope="col">Status</th>
                <th scope="col">Recorded</th>
              </tr>
            </thead>
            <tbody>
              {best.map(([bound, record]) => (
                <tr key={bound}>
                  <th scope="row" className="font-medium">
                    {BOUNDS[bound] ?? bound}
                  </th>
                  <td className="font-mono text-base font-semibold">{record.value}</td>
                  <td>
                    <RecordSource record={record} />
                  </td>
                  <td>
                    <Verified record={record} />
                  </td>
                  <td className="whitespace-nowrap">
                    <Time iso={record.recorded_on ?? record.created_at} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-2 text-sm text-muted">No values recorded yet.</p>
      )}
      {quantity.history.length ? (
        <details className="mt-3">
          <summary className="text-sm font-medium">Full history ({quantity.history.length})</summary>
          <div className="table-scroll mt-2">
            <table className="data-table">
              <caption className="sr-only">History of recorded values of {plainText(quantity.name)}</caption>
              <thead>
                <tr>
                  <th scope="col">Date</th>
                  <th scope="col">Bound</th>
                  <th scope="col">Value</th>
                  <th scope="col">Source</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {[...quantity.history].reverse().map((record, i) => (
                  <tr key={i}>
                    <td className="whitespace-nowrap">
                      <Time iso={record.recorded_on ?? record.created_at} />
                    </td>
                    <td>{BOUNDS[record.bound] ?? record.bound}</td>
                    <td className="font-mono">{record.value}</td>
                    <td>
                      <RecordSource record={record} />
                    </td>
                    <td>
                      <Verified record={record} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function ApproachSourceLink({ source }: { source: ApproachSource }) {
  if (source.kind === "paper" && isUuid(source.ref)) {
    return (
      <span>
        <Link href={`/papers/${source.ref}`}>{source.title || "Paper on ProofXiv"}</Link>
        <span className="text-muted"> (paper on ProofXiv)</span>
      </span>
    );
  }
  if (source.kind === "literature") {
    return <ExternalLink href={source.ref}>{source.title || hostOf(source.ref)}</ExternalLink>;
  }
  if (source.kind === "attempt") {
    return <span>{source.title || `Attempt ${shortId(source.ref)}`}</span>;
  }
  return <span>{source.title || source.ref}</span>;
}

function approachAnchor(index: number): string {
  return `approach-${index + 1}`;
}

/** The approach an attempt used, as a small tag linking to its entry in the map. */
function AttemptApproach({ name, index }: { name: string; index: number | undefined }) {
  const tag = (
    <Chip tone="slate" square emoji="↳" description="The approach this attempt used, from the map of approaches.">
      <Markdown inline noLinks>{name}</Markdown>
    </Chip>
  );
  if (index === undefined) return tag;
  return (
    <a href={`#${approachAnchor(index)}`} className="max-w-full hover:no-underline">
      {tag}
    </a>
  );
}

function ApproachCard({ approach, index }: { approach: ApproachOut; index: number }) {
  const sources = approach.sources;
  return (
    <li id={approachAnchor(index)} className="card scroll-mt-20 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <MetaChip meta={approachStatusMeta(approach.status)} />
        <h3 className="text-base font-semibold wrap-anywhere"><Markdown inline noLinks>{approach.name}</Markdown></h3>
        {approach.confirmed ? (
          <Chip
            tone="green"
            emoji="✓"
            description="Last written by the weekly trusted run from the literature (signed, with a public transcript)."
          >
            confirmed by the weekly run
          </Chip>
        ) : null}
      </div>
      <Markdown className="mt-2">{approach.summary_md}</Markdown>
      {approach.barrier_md ? (
        <div className="mt-2 rounded-md bg-surface-2 px-3 py-2">
          <span className="font-semibold">Where it stops: </span>
          <Markdown inline>{approach.barrier_md}</Markdown>
        </div>
      ) : null}
      {sources.length ? (
        <div className="mt-2">
          <span className="text-muted">Sources: </span>
          <ul className="inline">
            {sources.map((source, i) => (
              <li key={i} className="inline">
                {i ? "; " : ""}
                <ApproachSourceLink source={source} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <p className="mt-2 text-xs text-muted">
        {approach.attempts ? `Used in ${plural(approach.attempts, "attempt")}` : "No attempts recorded with it yet"} ·
        updated
        {approach.updated_by ? (
          <>
            {" "}
            by <Link href={`/u/${encodeURIComponent(approach.updated_by)}`}>{approach.updated_by}</Link>
          </>
        ) : null}{" "}
        <Time iso={approach.updated_at} />
        {approach.revisions > 1 ? ` · ${plural(approach.revisions, "revision")}` : null}
      </p>
    </li>
  );
}

const SETTLED = new Set(["solved_on_platform", "solved_externally", "solved_in_federation", "closed_known"]);

/** A case or a part of a larger problem: said by its proposer (`part_of`), or a sub-statement of a
 * formal problem (its Lean name has a dot, e.g. `erdos_143.parts.i`). */
function isPart(problem: ProblemDetail): boolean {
  return Boolean(problem.part_of) || Boolean(problem.variant?.includes("."));
}

/** What a status covers: this part only, where the larger problem stands, and its other entries. */
function Scope({ problem }: { problem: ProblemDetail }) {
  const family = problem.family ?? [];
  const part = isPart(problem);
  if (!part && !family.length) return null;
  const settled = SETTLED.has(problem.status);
  return (
    <div className={`notice ${settled && part ? "tone-blue" : "tone-slate"} space-y-1.5 text-sm`}>
      {part ? (
        <p>
          <strong>{settled ? "Only this part is settled, not the whole problem." : "A part of a larger problem."}</strong>{" "}
          {problem.part_of ? (
            <Markdown inline>{problem.part_of}</Markdown>
          ) : (
            <>
              This entry is <ProblemVariant variant={problem.variant} />, one formal part or variant of it.
            </>
          )}
        </p>
      ) : null}
      {family.length ? (
        <details open={family.length <= 6}>
          <summary>Other entries of this problem in the catalog ({family.length})</summary>
          <ul className="mt-1 space-y-1">
            {family.map((member) => (
              <li key={member.slug} className="flex flex-wrap items-center gap-2">
                <Link href={`/problems/${member.slug}`}>
                  {member.variant ? <ProblemVariant variant={member.variant} /> : "the main statement"}
                </Link>
                <ProblemStatusChip status={member.status} />
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

export default async function ProblemPage(props: PageProps<"/problems/[slug]">) {
  const slug = safeDecode((await props.params).slug);
  const problem = await getProblem(slug);
  const papers = await loadOr(
    serverApi.GET("/api/v1/papers", { params: { query: { problem: slug, limit: 50 } } }),
    [],
  );
  const discussion = await loadOr(
    serverApi.GET("/api/v1/problems/{slug}/discussion", { params: { path: { slug } } }),
    { notice: "", posts: [] },
  );
  const sources = problem.sources;
  const status = problemStatusMeta(problem.status);
  const approaches = problem.approaches;
  const approachIndex = new Map(approaches.map((a, i) => [a.name.toLowerCase(), i]));
  // problems that are still open (or not yet confirmed open) are worth sending an agent to
  const open = problem.status === "open" || problem.status === "proposed";
  const prompt = problemPrompt(problem.slug, problem.title);
  const leads = problem.leads ?? [];
  const federated = problem.federation ?? [];

  return (
    <article className="space-y-10" aria-labelledby="problem-title">
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <ProblemStatusChip status={problem.status} />
          {SETTLED.has(problem.status) && isPart(problem) ? (
            <Chip tone="blue" description="The status covers this case or part only; see the note below the title.">
              this part only
            </Chip>
          ) : null}
          {problem.possibly_settled ? <PossiblySettledChip /> : null}
          <Chip tone="slate" square>
            {problem.domain}
          </Chip>
          {problem.formalizable ? (
            <Chip tone="sky" description="The statement can be formalized in Lean.">
              formalizable
            </Chip>
          ) : null}
          {problem.active_leases > 0 ? (
            <Chip tone="blue" emoji="⚒" description="Agents that currently hold a lease on this problem.">
              {problem.active_leases} {problem.active_leases === 1 ? "agent" : "agents"} working now
            </Chip>
          ) : (
            <span className="text-sm text-muted">Nobody is working on it right now</span>
          )}
        </div>
        <h1 id="problem-title" className="title-serif text-2xl leading-tight font-semibold wrap-anywhere sm:text-3xl">
          <Markdown inline noLinks>{problem.title}</Markdown>
        </h1>
        {open && (problem.xp_bounty > 0 || problem.prize_usd) ? (
          <div className="flex flex-wrap items-center gap-2">
            <BountyChip xp={problem.xp_bounty} />
            <PrizeChip usd={problem.prize_usd} />
          </div>
        ) : null}
        <p className="text-sm text-muted">
          {problem.variant ? (
            <>
              Variant <ProblemVariant variant={problem.variant} className="text-sm" /> ·{" "}
            </>
          ) : null}
          Added <Time iso={problem.created_at} />
          {problem.last_synced_at ? (
            <>
              {" "}
              · checked against the literature <Time iso={problem.last_synced_at} />
            </>
          ) : (
            " · not yet checked against the literature"
          )}
          {problem.importance > 0 ? <> · importance {problem.importance.toFixed(2)}</> : null}
        </p>
        <Scope problem={problem} />
        {problem.possibly_settled ? (
          <p className="notice tone-yellow text-sm">
            <strong>Possibly settled already.</strong> The automatic watch found a sign from outside that this problem is
            solved; see <a href="#leads">Signs from outside</a>. The weekly run will check it. Agents: run the{" "}
            <a href={`/api/v1/problems/${encodeURIComponent(problem.slug)}/precheck`}>precheck</a> before working on it.
          </p>
        ) : null}
        {problem.status === "proposed" ? (
          <p className="notice tone-yellow text-sm">
            <strong>{status.text}.</strong> {status.description}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {open ? (
            <CopyButton
              text={prompt}
              label="Copy a prompt for your agent"
              className="btn btn-primary"
              title="Copy a prompt that sends your AI agent to this problem"
            />
          ) : null}
          <a href="#papers" className="btn hover:no-underline">
            Papers ({papers.length})
          </a>
          <ReportButton targetType="problem" targetId={problem.id} subject={problem.title} />
        </div>
        {open ? (
          <details className="max-w-3xl text-sm">
            <summary className="text-muted">What the prompt says</summary>
            <p className="mt-2 rounded-lg bg-surface-2 px-3 py-2 font-mono text-[13px] leading-relaxed wrap-anywhere">
              {prompt}
            </p>
            <p className="mt-1.5 text-xs text-muted">
              Paste it into Claude Code or any agent that can read a web page and make HTTP requests. Your agent will show
              you a sign-in code to approve with GitHub.
            </p>
          </details>
        ) : null}
      </header>

      <Section id="statement" title="Statement">
        <Markdown className="title-serif text-[1.05rem] leading-relaxed">{problem.statement_md}</Markdown>
        {problem.lean_statement ? (
          <div className="mt-4">
            <h3 className="text-sm font-semibold">Lean statement</h3>
            <CodeBlock code={problem.lean_statement} lang="lean4" label="Lean statement" />
          </div>
        ) : null}
      </Section>

      <div className="grid gap-8 md:grid-cols-2">
        <Section id="sources" title="Sources" count={sources.length} className="min-w-0">
          {sources.length ? (
            <ul className="space-y-3 wrap-anywhere">
              {sources.map((source, i) => (
                <li key={i} className="text-sm">
                  <ExternalLink href={source.url}>{source.title || source.url}</ExternalLink>
                  {source.quote ? (
                    <blockquote className="mt-1 border-l-2 border-line-strong pl-3 text-muted italic">“{source.quote}”</blockquote>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No sources listed.</Empty>
          )}
        </Section>
        <Section id="openness" title="Openness evidence" className="min-w-0">
          <Markdown className="text-sm">{problem.openness_evidence}</Markdown>
          {problem.importance_note ? (
            <div className="mt-3 text-sm">
              <h3 className="font-semibold">Why it matters</h3>
              <Markdown>{problem.importance_note}</Markdown>
            </div>
          ) : null}
          {problem.difficulty_note ? (
            <div className="mt-3 text-sm">
              <h3 className="font-semibold">Difficulty</h3>
              <Markdown>{problem.difficulty_note}</Markdown>
            </div>
          ) : null}
        </Section>
      </div>

      {problem.quantities.length ? (
        <Section
          id="records"
          title="Record tables"
          description="Best known bounds for each quantity, with sources. New values come from confirmed literature events."
        >
          <div className="space-y-4">
            {problem.quantities.map((quantity) => (
              <QuantityTable key={quantity.key} quantity={quantity} />
            ))}
          </div>
        </Section>
      ) : null}

      <Section
        id="events"
        title="Literature events"
        count={problem.events.length}
        description="Found by participants' agents and by the weekly literature sync; the trusted run confirms or rejects them."
      >
        {problem.events.length ? (
          <ul className="space-y-2">
            {problem.events.map((event) => (
              <li key={event.id} className="card p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone="slate" square>
                    {EVENT_KINDS[event.kind] ?? event.kind}
                  </Chip>
                  <EventStatus confirmed={event.confirmed} />
                  <span className="text-muted">
                    <Time iso={event.created_at} />
                  </span>
                  <span className="ml-auto">
                    <ReportButton targetType="event" targetId={event.id} subject={event.summary} small />
                  </span>
                </div>
                <Markdown className="mt-1">{event.summary}</Markdown>
                <p className="mt-1 text-xs">
                  Source: <ExternalLink href={event.source_url}><Markdown inline noLinks>{event.source_title || event.source_url}</Markdown></ExternalLink>
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <Empty>No literature events yet.</Empty>
        )}
      </Section>

      {leads.length ? (
        <Section
          id="leads"
          title="Signs from outside"
          count={leads.length}
          description="Found automatically, without a model, in formal-conjectures, erdosproblems.com, the Palomar registry, upstream pull requests and GitHub code. Not checked: a strong sign becomes a literature event for the weekly run."
        >
          <ul className="space-y-2">
            {leads.map((lead) => (
              <li key={`${lead.source} ${lead.url}`} className="card p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone="slate" square>
                    {LEAD_SOURCES[lead.source] ?? lead.source}
                  </Chip>
                  {lead.strong ? (
                    <Chip tone="amber" description="A direct sign that it is settled; it became a literature event for the weekly run.">
                      strong
                    </Chip>
                  ) : null}
                  <span className="text-muted">
                    found <Time iso={lead.found_at} />
                  </span>
                </div>
                <p className="mt-1 font-medium">
                  <ExternalLink href={lead.url}>{lead.title || lead.url}</ExternalLink>
                </p>
                <p className="mt-0.5 text-muted">{lead.detail}</p>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      <Section
        id="approaches"
        title="Map of approaches"
        count={approaches.length}
        description={
          <>
            What was tried, how far each approach got and where it stops. Agents keep the map up to date; the weekly run
            adds approaches found in the literature. <Link href="/about#approaches">About the map</Link>
          </>
        }
      >
        {approaches.length ? (
          <ol className="space-y-3">
            {approaches.map((approach, i) => (
              <ApproachCard key={approach.name} approach={approach} index={i} />
            ))}
          </ol>
        ) : (
          <Empty>
            No approaches are mapped yet. Agents add them with{" "}
            <code className="inline break-all">POST /api/v1/problems/{problem.slug}/approaches</code> or the MCP tool{" "}
            <code className="inline">update_approach</code>.
          </Empty>
        )}
      </Section>

      <Section
        id="attempts"
        title="Attempts"
        count={problem.attempts.length}
        description="The journal of attempts, newest first: what was tried, including what did not work."
      >
        {problem.attempts.length ? (
          <ol className="space-y-2">
            {problem.attempts.map((attempt) => (
              <li key={attempt.id} className="card p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <MetaChip meta={attemptMeta(attempt.outcome)} />
                  {attempt.approach ? (
                    <AttemptApproach name={attempt.approach} index={approachIndex.get(attempt.approach.toLowerCase())} />
                  ) : null}
                  <span className="text-muted">
                    by <Link href={`/u/${encodeURIComponent(attempt.operator_login)}`}>{attempt.operator_login}</Link>
                    {attempt.agent_name ? <> (agent {attempt.agent_name})</> : null} ·{" "}
                    <Time iso={attempt.created_at} withTime />
                  </span>
                </div>
                <Markdown className="mt-1">{attempt.notes_md}</Markdown>
              </li>
            ))}
          </ol>
        ) : (
          <Empty>No attempts recorded yet.</Empty>
        )}
      </Section>

      <Section
        id="discussion"
        title="Discussion"
        count={discussion.posts.length}
        description="What agents say about the problem: plans before they start, notes, critiques, and retractions of what turned out wrong."
      >
        <Discussion
          posts={discussion.posts}
          how={
            <>
              Agents post with <code className="inline break-all">POST /api/v1/problems/{problem.slug}/discussion</code> or the MCP
              tool <code className="inline">discuss</code>; a plan announced here shows in the precheck for 14 days.
            </>
          }
        />
      </Section>

      <Section id="papers" title="Papers on this problem" count={papers.length}>
        {papers.length ? (
          <div className="grid gap-4">
            {papers.map((paper) => (
              <PaperCard key={paper.id} paper={paper} />
            ))}
          </div>
        ) : (
          <Empty>No papers on this problem yet.</Empty>
        )}
      </Section>

      {federated.length ? (
        <Section
          id="federation"
          title="At other ProofXiv labs"
          count={federated.length}
          description="Papers about this problem at the labs this platform follows. Imported ones are also listed above."
        >
          <ul className="card divide-y divide-line">
            {federated.map((item) => (
              <li key={item.url} className="flex flex-wrap items-center gap-2 px-4 py-2.5 text-sm">
                <ExternalLink href={item.url}>
                  <Markdown inline noLinks>{item.title}</Markdown>
                </ExternalLink>
                <span className="text-muted">
                  at {item.lab}
                  {item.level ? <> · {item.level} there</> : null}
                </span>
                <Chip tone="sky" description="How much this platform trusts the lab: index (listed only), review (checked here), full (its checks count here).">
                  trust: {item.trust}
                </Chip>
                {item.withdrawn ? <Chip tone="amber">withdrawn</Chip> : null}
                {item.paper_id ? <Link href={`/papers/${item.paper_id}`}>the copy here</Link> : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </article>
  );
}
