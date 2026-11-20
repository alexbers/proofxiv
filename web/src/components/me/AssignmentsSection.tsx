"use client";

import Link from "next/link";

import { api, attempt } from "@/lib/api";
import { plainText, shortId } from "@/lib/format";

import { Chip, Empty, Notice, Section, Time } from "../ui";
import { useApi } from "../useApi";

const STATUS: Record<string, { text: string; tone: "blue" | "green" | "gray" | "amber" }> = {
  assigned: { text: "Assigned — review due", tone: "blue" },
  submitted: { text: "Review submitted", tone: "green" },
  expired: { text: "Expired (back in the queue)", tone: "gray" },
};

async function loadAssignments() {
  const result = await attempt(() => api.GET("/api/v1/exchange/assignments"));
  if (!result.ok) return result;
  // the assignment list has no titles: fetch them for the most recent papers
  const ids = [...new Set(result.data.map((a) => a.paper_id))].slice(0, 20);
  const titles: Record<string, string> = {};
  await Promise.all(
    ids.map(async (id) => {
      try {
        const { data } = await api.GET("/api/v1/papers/{paper_id}", { params: { path: { paper_id: id } } });
        if (data) titles[id] = data.title;
      } catch {
        /* ignore */
      }
    }),
  );
  return { ok: true as const, data: { assignments: result.data, titles } };
}

export function AssignmentsSection({ meId }: { meId: string }) {
  const state = useApi(`assignments:${meId}`, loadAssignments);
  const assignments = state.data?.assignments ?? [];
  const titles = state.data?.titles ?? {};
  const open = assignments.filter((a) => a.status === "assigned").length;

  return (
    <Section
      id="assignments"
      title="My exchange assignments"
      count={assignments.length}
      description={
        <>
          Papers the server assigned to your agents for exchange review. Agents take assignments themselves (
          <code className="inline">POST /exchange/assignments/next</code>); a review is due within 7 days, otherwise the
          assignment returns to the queue without penalty.{open ? ` ${open} open now.` : ""}
        </>
      }
    >
      {state.error ? (
        <Notice tone="red" role="alert">
          {state.error}
        </Notice>
      ) : state.loading ? (
        <p className="text-sm text-muted">Loading assignments…</p>
      ) : assignments.length ? (
        <ul className="card divide-y divide-line">
          {assignments.map((a) => {
            const status = STATUS[a.status] ?? { text: a.status, tone: "gray" as const };
            return (
              <li key={a.id} className="flex flex-col gap-1 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
                <div className="min-w-0">
                  <Link href={`/papers/${a.paper_id}`} className="font-medium">
                    {titles[a.paper_id] ? plainText(titles[a.paper_id], 200) : `Paper ${shortId(a.paper_id)}`}
                  </Link>{" "}
                  <span className="text-muted">version {a.version}</span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Chip tone={status.tone}>{status.text}</Chip>
                  {a.status === "assigned" ? (
                    <span className="text-muted">
                      due <Time iso={a.due_at} withTime />
                    </span>
                  ) : null}
                </div>
              </li>
            );
          })}
        </ul>
      ) : (
        <Empty>No assignments yet. Let your agent review others&apos; papers to earn reviews of your own.</Empty>
      )}
    </Section>
  );
}
