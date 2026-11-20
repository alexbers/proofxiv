"use client";

/** Submitter only: rate an exchange review and respond to any review. */
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";

import { api, attempt } from "@/lib/api";

import { Notice } from "../ui";
import { usePaperViewer } from "./PaperViewer";

export function ReviewAuthorTools({
  reviewId,
  origin,
  useful,
  hasResponse,
  currentResponse,
}: {
  reviewId: string;
  origin: string;
  useful: boolean | null;
  hasResponse: boolean;
  currentResponse?: string | null;
}) {
  const viewer = usePaperViewer();
  const router = useRouter();
  const textId = useId();
  const [rating, setRating] = useState<boolean | null>(useful);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(currentResponse ?? "");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "green" | "red"; text: string } | null>(null);

  if (!viewer.isSubmitter) return null;

  const rate = async (value: boolean) => {
    setBusy(true);
    setMessage(null);
    const result = await attempt(() =>
      api.POST("/api/v1/reviews/{review_id}/rate", {
        params: { path: { review_id: reviewId } },
        body: { useful: value },
      }),
    );
    setBusy(false);
    if (result.ok) {
      setRating(value);
      router.refresh();
    } else {
      setMessage({ tone: "red", text: result.message });
    }
  };

  const respond = async (event: FormEvent) => {
    event.preventDefault();
    if (text.trim().length < 5) {
      setMessage({ tone: "red", text: "Write at least a few words." });
      return;
    }
    setBusy(true);
    setMessage(null);
    const result = await attempt(() =>
      api.POST("/api/v1/reviews/{review_id}/response", {
        params: { path: { review_id: reviewId } },
        body: { text: text.trim() },
      }),
    );
    setBusy(false);
    if (result.ok) {
      setEditing(false);
      setMessage({ tone: "green", text: "Your response is published under the review." });
      router.refresh();
    } else {
      setMessage({ tone: "red", text: result.message });
    }
  };

  return (
    <div className="mt-3 rounded-lg border border-dashed border-line-strong p-3 text-sm">
      <p className="mb-2 text-xs font-semibold tracking-wide text-muted uppercase">Your tools as the submitter</p>
      <div className="flex flex-wrap items-center gap-2">
        {origin === "exchange" ? (
          <div role="group" aria-label="Rate this exchange review" className="flex flex-wrap items-center gap-2">
            <span className="text-muted">Was this review useful?</span>
            <button type="button" className="btn" aria-pressed={rating === true} disabled={busy} onClick={() => rate(true)}>
              <span aria-hidden="true">👍</span> Useful
            </button>
            <button type="button" className="btn" aria-pressed={rating === false} disabled={busy} onClick={() => rate(false)}>
              <span aria-hidden="true">👎</span> Not useful
            </button>
          </div>
        ) : null}
        {!editing ? (
          <button type="button" className="btn" onClick={() => setEditing(true)} aria-expanded={editing}>
            {hasResponse ? "Edit your response" : "Respond to this review"}
          </button>
        ) : null}
      </div>
      {editing ? (
        <form onSubmit={respond} className="mt-3 space-y-2">
          <label htmlFor={textId} className="label">
            Your response (Markdown, LaTeX with $…$)
          </label>
          <textarea
            id={textId}
            className="field min-h-28"
            value={text}
            maxLength={20000}
            onChange={(e) => setText(e.target.value)}
            placeholder="Thank the reviewer, answer the issues, say what changes in the next version…"
          />
          <div className="flex gap-2">
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "Publishing…" : "Publish response"}
            </button>
            <button type="button" className="btn" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : null}
      {message ? (
        <div className="mt-2">
          <Notice tone={message.tone} role={message.tone === "red" ? "alert" : "status"}>
            {message.text}
          </Notice>
        </div>
      ) : null}
    </div>
  );
}
