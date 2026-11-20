"use client";

/** 👍 like, 🔔 follow and 🚩 report for a paper. Signals never change trust levels. */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { api, attempt } from "@/lib/api";

import { ReportButton } from "../ReportButton";
import { Notice } from "../ui";
import { usePaperViewer } from "./PaperViewer";

type Kind = "like" | "follow";

export function PaperActions({
  paperId,
  title,
  likes,
  follows,
}: {
  paperId: string;
  title: string;
  likes: number;
  follows: number;
}) {
  const viewer = usePaperViewer();
  const pathname = usePathname() ?? `/papers/${paperId}`;
  const [delta, setDelta] = useState<Record<Kind, number>>({ like: 0, follow: 0 });
  const [busy, setBusy] = useState<Kind | null>(null);
  const [error, setError] = useState<string | null>(null);

  const counts = { like: likes + delta.like, follow: follows + delta.follow };
  const active = (kind: Kind) => viewer.signals.includes(kind);

  if (!viewer.signedIn) {
    return (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted">
        <span>
          <span aria-hidden="true">👍</span> {likes} {likes === 1 ? "like" : "likes"}
        </span>
        <span>
          <span aria-hidden="true">🔔</span> {follows} {follows === 1 ? "follower" : "followers"}
        </span>
        <Link href={`/login?next=${encodeURIComponent(pathname)}`}>Sign in to like, follow or report</Link>
      </div>
    );
  }

  const toggle = async (kind: Kind) => {
    const on = !active(kind);
    setBusy(kind);
    setError(null);
    const result = await attempt(() =>
      api.POST("/api/v1/papers/{paper_id}/signals", {
        params: { path: { paper_id: paperId } },
        body: { kind, on },
      }),
    );
    if (result.ok) {
      viewer.setSignal(kind, on);
      setDelta((d) => ({ ...d, [kind]: d[kind] + (on ? 1 : -1) }));
    } else {
      setError(result.message);
    }
    setBusy(null);
  };

  return (
    <div className="space-y-2">
      <div role="group" aria-label="Paper actions" className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn"
          aria-pressed={active("like")}
          disabled={viewer.loading || busy !== null}
          onClick={() => toggle("like")}
          title="Likes raise the paper's priority in the weekly queue; they never change its trust level."
        >
          <span aria-hidden="true">👍</span> {active("like") ? "Liked" : "Like"}
          <span className="rounded bg-surface-2 px-1.5 text-xs tabular-nums">{counts.like}</span>
        </button>
        <button
          type="button"
          className="btn"
          aria-pressed={active("follow")}
          disabled={viewer.loading || busy !== null}
          onClick={() => toggle("follow")}
          title="Follow to be notified about new versions, reviews and literature events."
        >
          <span aria-hidden="true">🔔</span> {active("follow") ? "Following" : "Follow"}
          <span className="rounded bg-surface-2 px-1.5 text-xs tabular-nums">{counts.follow}</span>
        </button>
        <ReportButton targetType="paper" targetId={paperId} subject={title} />
      </div>
      {error ? (
        <Notice tone="red" role="alert">
          {error}
        </Notice>
      ) : null}
    </div>
  );
}
