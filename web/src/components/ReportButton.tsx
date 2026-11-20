"use client";

/** 🚩 Report a paper, review, problem, literature event or post (signed-in users). */
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useId, useRef, useState, type FormEvent } from "react";

import { api, attempt, type ReportReason, type ReportTarget } from "@/lib/api";
import { plainText } from "@/lib/format";
import { REPORT_REASONS } from "@/lib/labels";

import { useSession } from "./SessionProvider";
import { cx, Notice } from "./ui";

const REASON_ORDER: Record<ReportTarget, ReportReason[]> = {
  paper: ["false_claim", "statement_mismatch", "plagiarism", "wrong_platform_verdict", "spam"],
  review: ["junk_review", "wrong_platform_verdict", "spam", "plagiarism"],
  problem: ["statement_mismatch", "spam", "plagiarism"],
  event: ["false_claim", "spam"],
  post: ["spam", "false_claim", "plagiarism"],
};
const URGENT: ReportReason[] = ["malware", "illegal"];
const ALL_REASONS = Object.keys(REPORT_REASONS) as ReportReason[];

const TARGET_NAMES: Record<ReportTarget, string> = {
  paper: "paper",
  review: "review",
  problem: "problem",
  event: "literature event",
  post: "post",
};

type SendState =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "done"; urgent: boolean }
  | { kind: "error"; message: string };

export function ReportButton({
  targetType,
  targetId,
  subject,
  className,
  small,
}: {
  targetType: ReportTarget;
  targetId: string;
  /** what is reported, for the dialog title (e.g. the paper title) */
  subject?: string;
  className?: string;
  small?: boolean;
}) {
  const { me, status } = useSession();
  const pathname = usePathname() ?? "/";
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const reasonId = useId();
  const evidenceId = useId();
  const [reason, setReason] = useState<ReportReason | "">("");
  const [evidence, setEvidence] = useState("");
  const [state, setState] = useState<SendState>({ kind: "idle" });

  if (status === "loading") return null;
  if (!me) {
    if (small) return null;
    return (
      <Link href={`/login?next=${encodeURIComponent(pathname)}`} className={cx("btn", className)}>
        <span aria-hidden="true">🚩</span> Sign in to report
      </Link>
    );
  }

  const primary = REASON_ORDER[targetType];
  const other = ALL_REASONS.filter((r) => !primary.includes(r) && !URGENT.includes(r));
  const urgent = reason !== "" && URGENT.includes(reason);
  const targetName = TARGET_NAMES[targetType];

  const open = () => {
    setState({ kind: "idle" });
    dialog.current?.showModal();
  };
  const close = () => dialog.current?.close();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!reason) return;
    setState({ kind: "sending" });
    const result = await attempt(() =>
      api.POST("/api/v1/reports", {
        body: { target_type: targetType, target_id: targetId, reason, evidence_md: evidence.trim() },
      }),
    );
    if (result.ok) {
      setState({ kind: "done", urgent: result.data.urgent });
      setReason("");
      setEvidence("");
    } else {
      setState({
        kind: "error",
        message:
          result.status === 409 ? "You have already reported this for the same reason; it is waiting for a decision." : result.message,
      });
    }
  };

  return (
    <>
      <button
        type="button"
        className={cx("btn", small && "px-2 py-0.5 text-xs", className)}
        onClick={open}
        aria-haspopup="dialog"
      >
        <span aria-hidden="true">🚩</span> {small ? "Report" : `Report ${targetName}`}
      </button>
      <dialog ref={dialog} aria-labelledby={titleId} onClose={() => setState((s) => (s.kind === "sending" ? s : { kind: "idle" }))}>
        <form onSubmit={submit} className="space-y-4 p-5">
          <div>
            <h2 id={titleId} className="text-lg font-semibold">
              <span aria-hidden="true">🚩</span> Report this {targetName}
            </h2>
            {subject ? <p className="mt-0.5 line-clamp-2 text-sm text-muted">{plainText(subject, 300)}</p> : null}
          </div>

          {state.kind === "done" ? (
            <>
              <Notice tone="green" role="status">
                Thank you, the report was sent.{" "}
                {state.urgent
                  ? "It is urgent: the platform owner has been notified right away and can hide the material before the weekly run."
                  : "It goes first in the queue of the next weekly trusted run."}{" "}
                Reports stay private until a decision; the decision appears in the{" "}
                <Link href="/moderation">public moderation log</Link>.
              </Notice>
              <div className="flex justify-end">
                <button type="button" className="btn" onClick={close} autoFocus>
                  Close
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="text-sm text-muted">
                Reports are not published until they are decided, so they cannot be used to smear someone&apos;s work. The
                weekly trusted run checks the {targetName} against your reason and publishes the outcome.
              </p>
              <div>
                <label htmlFor={reasonId} className="label">
                  Reason
                </label>
                <select
                  id={reasonId}
                  required
                  className="field"
                  value={reason}
                  onChange={(e) => setReason(e.target.value as ReportReason)}
                >
                  <option value="" disabled>
                    Choose a reason…
                  </option>
                  <optgroup label={`Most common for a ${targetName}`}>
                    {primary.map((r) => (
                      <option key={r} value={r}>
                        {REPORT_REASONS[r].text}
                      </option>
                    ))}
                  </optgroup>
                  {other.length ? (
                    <optgroup label="Other">
                      {other.map((r) => (
                        <option key={r} value={r}>
                          {REPORT_REASONS[r].text}
                        </option>
                      ))}
                    </optgroup>
                  ) : null}
                  <optgroup label="⚠ Urgent — the owner is notified immediately">
                    {URGENT.map((r) => (
                      <option key={r} value={r}>
                        ⚠ URGENT: {REPORT_REASONS[r].text}
                      </option>
                    ))}
                  </optgroup>
                </select>
              </div>
              {urgent ? (
                <Notice tone="red" role="alert">
                  <strong>Urgent report.</strong> Use this only for malicious files or illegal content: the platform owner
                  is alerted at once and may hide the material immediately, before the weekly review.
                </Notice>
              ) : null}
              <div>
                <label htmlFor={evidenceId} className="label">
                  Evidence <span className="font-normal text-muted">(Markdown; links, locations, counterexamples)</span>
                </label>
                <textarea
                  id={evidenceId}
                  className="field min-h-32 font-mono text-sm"
                  maxLength={20000}
                  value={evidence}
                  onChange={(e) => setEvidence(e.target.value)}
                  placeholder="e.g. Lemma 4 fails for the family {∅, {1}, {2}, {1,2}} because …"
                />
              </div>
              {state.kind === "error" ? (
                <Notice tone="red" role="alert">
                  {state.message}
                </Notice>
              ) : null}
              <div className="flex flex-wrap justify-end gap-2">
                <button type="button" className="btn" onClick={close}>
                  Cancel
                </button>
                <button
                  type="submit"
                  className={cx("btn", urgent ? "btn-danger" : "btn-primary")}
                  disabled={!reason || state.kind === "sending"}
                >
                  {state.kind === "sending" ? "Sending…" : urgent ? "Send urgent report" : "Send report"}
                </button>
              </div>
            </>
          )}
        </form>
      </dialog>
    </>
  );
}
