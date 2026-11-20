import type { Metadata } from "next";
import Link from "next/link";
import { connection } from "next/server";

import { Markdown } from "@/components/Markdown";
import { Empty, MetaChip, PageTitle, Time } from "@/components/ui";
import type { ModerationEntryOut } from "@/lib/api";
import { shortId } from "@/lib/format";
import { moderationActionMeta } from "@/lib/labels";
import { load, serverApi } from "@/lib/server-api";

export const metadata: Metadata = {
  title: "Moderation log",
  description: "Public log of moderation decisions on ProofXiv: hidden papers, labels, removed reviews and restorations.",
};

const ACTORS: Record<string, string> = {
  owner: "Platform owner (urgent action)",
  trusted_run: "Weekly trusted run",
};

/** What a decision was about, linked to its page when there is one. */
function Target({ entry }: { entry: ModerationEntryOut }) {
  const title = entry.title ?? null;
  switch (entry.target_type) {
    case "paper":
      return entry.paper_id ? (
        <Link href={`/papers/${entry.paper_id}`}><Markdown inline noLinks>{title ?? `Paper ${shortId(entry.paper_id)}`}</Markdown></Link>
      ) : (
        <span title={entry.target_id}>Paper {shortId(entry.target_id)}</span>
      );
    case "review":
      return entry.paper_id ? (
        <span>
          <Link href={`/papers/${entry.paper_id}#review-${entry.target_id}`}>A review</Link>
          <span className="text-muted"> of </span>
          <Link href={`/papers/${entry.paper_id}`}><Markdown inline noLinks>{title ?? `paper ${shortId(entry.paper_id)}`}</Markdown></Link>
        </span>
      ) : (
        <span title={entry.target_id}>Review {shortId(entry.target_id)}</span>
      );
    case "problem":
      return entry.problem_slug ? (
        <span>
          <span className="text-muted">Problem </span>
          <Link href={`/problems/${entry.problem_slug}`}><Markdown inline noLinks>{title ?? entry.problem_slug}</Markdown></Link>
        </span>
      ) : (
        <span title={entry.target_id}>Problem {shortId(entry.target_id)}</span>
      );
    default:
      return (
        <span title={entry.target_id}>
          {entry.target_type === "event" ? "Literature event" : entry.target_type} {shortId(entry.target_id)}
        </span>
      );
  }
}

export default async function ModerationPage() {
  await connection(); // always render with live data
  const entries = await load(serverApi.GET("/api/v1/moderation-log", { params: { query: { limit: 200 } } }));
  return (
    <div>
      <PageTitle
        subtitle={
          <>
            Reports 🚩 stay private until they are decided, so nobody can smear a paper with a report. Decisions are published
            here. Ordinary reports are decided first thing in the weekly trusted run; urgent ones (malware, illegal content) go
            to the owner at once. A hidden paper stays available by direct link with a banner; the author can appeal.
          </>
        }
      >
        Moderation log
      </PageTitle>
      {entries.length ? (
        <div className="card table-scroll">
          <table className="data-table">
            <caption className="sr-only">Moderation decisions, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Action</th>
                <th scope="col">Target</th>
                <th scope="col">Reason</th>
                <th scope="col">Decided by</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry, i) => (
                <tr key={i}>
                  <td className="whitespace-nowrap">
                    <Time iso={entry.created_at} withTime />
                  </td>
                  <td>
                    <MetaChip meta={moderationActionMeta(entry.action)} />
                  </td>
                  <td className="min-w-48">
                    <Target entry={entry} />
                  </td>
                  <td className="min-w-64">{entry.reason}</td>
                  <td className="whitespace-nowrap">{ACTORS[entry.actor] ?? entry.actor}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No moderation decisions yet.</Empty>
      )}
    </div>
  );
}
