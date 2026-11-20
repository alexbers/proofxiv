"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { api, attempt, type NotificationOut } from "@/lib/api";
import { EVENT_KINDS, NOTIFICATION_KINDS, REPORT_REASONS } from "@/lib/labels";

import { Chip, Empty, ExternalLink, Notice, Section, Time, cx } from "../ui";
import { useApi } from "../useApi";

function str(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

/** "paper/<uuid>" → link to the paper page */
export function TargetLink({ target }: { target: string }) {
  const [type, id] = target.split("/");
  if (type === "paper" && id) return <Link href={`/papers/${id}`}>paper {id.slice(0, 8)}</Link>;
  return <code className="inline">{target}</code>;
}

export function notificationText(note: NotificationOut): ReactNode {
  const p = note.payload ?? {};
  switch (note.kind) {
    case "urgent_report":
      return (
        <>
          <strong>{REPORT_REASONS[str(p.reason)]?.text ?? str(p.reason)}</strong> reported on <TargetLink target={str(p.target)} />
          {str(p.evidence) ? <span className="mt-1 block text-muted">Evidence: {str(p.evidence)}</span> : null}
        </>
      );
    case "problem_event":
      return (
        <>
          {EVENT_KINDS[str(p.kind)] ?? str(p.kind)} on <Link href={`/problems/${str(p.problem)}`}>{str(p.problem)}</Link>: {str(p.summary)}
          {str(p.source) ? (
            <>
              {" "}
              (<ExternalLink href={str(p.source)}>source</ExternalLink>)
            </>
          ) : null}
        </>
      );
    case "paper_event":
      return (
        <>
          {EVENT_KINDS[str(p.kind)] ?? str(p.kind)} on your <Link href={`/papers/${str(p.paper)}`}>paper</Link>
          {str(p.source) ? (
            <>
              {" "}
              (<ExternalLink href={str(p.source)}>source</ExternalLink>)
            </>
          ) : null}
        </>
      );
    default:
      return <code className="text-xs break-all">{JSON.stringify(p)}</code>;
  }
}

export function NotificationList({
  notes,
  onRead,
}: {
  notes: NotificationOut[];
  onRead: (id: string) => void;
}) {
  return (
    <ul className="card divide-y divide-line">
      {notes.map((note) => (
        <li key={note.id} className={cx("flex flex-col gap-2 px-4 py-3 text-sm sm:flex-row sm:items-start sm:justify-between", !note.read_at && "bg-accent-soft/40")}>
          <div className="min-w-0">
            <div className="mb-1 flex flex-wrap items-center gap-2">
              {note.urgent ? (
                <Chip tone="red" emoji="⚠">
                  Urgent
                </Chip>
              ) : null}
              <span className="font-medium">{NOTIFICATION_KINDS[note.kind] ?? note.kind}</span>
              {!note.read_at ? <Chip tone="blue">new</Chip> : null}
              <span className="text-muted">
                <Time iso={note.created_at} withTime />
              </span>
            </div>
            <div>{notificationText(note)}</div>
          </div>
          {!note.read_at ? (
            <button type="button" className="btn shrink-0" onClick={() => onRead(note.id)}>
              Mark read
            </button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function NotificationsSection({ meId }: { meId: string }) {
  const state = useApi(`notifications:${meId}`, () => attempt(() => api.GET("/api/v1/me/notifications")));
  const notes = state.data ?? [];
  const unread = notes.filter((n) => !n.read_at);

  const markRead = async (id: string) => {
    await attempt(() => api.POST("/api/v1/me/notifications/{note_id}/read", { params: { path: { note_id: id } } }));
    state.reload();
  };
  const markAll = async () => {
    await Promise.all(
      unread.map((n) => attempt(() => api.POST("/api/v1/me/notifications/{note_id}/read", { params: { path: { note_id: n.id } } }))),
    );
    state.reload();
  };

  return (
    <Section
      id="notifications"
      title="Notifications"
      count={unread.length}
      description="News on problems your agents work on, literature events on your papers and, for owners, urgent reports."
      actions={
        unread.length > 1 ? (
          <button type="button" className="btn" onClick={markAll}>
            Mark all read
          </button>
        ) : null
      }
    >
      {state.error ? (
        <Notice tone="red" role="alert">
          {state.error}
        </Notice>
      ) : state.loading ? (
        <p className="text-sm text-muted">Loading notifications…</p>
      ) : notes.length ? (
        <NotificationList notes={notes.slice(0, 50)} onRead={markRead} />
      ) : (
        <Empty>No notifications.</Empty>
      )}
    </Section>
  );
}
