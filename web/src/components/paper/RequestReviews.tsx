"use client";

/** Submitter only: ask the review exchange for 1-3 reviews of the current version. */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, type FormEvent } from "react";

import { api, attempt, errorDetails } from "@/lib/api";

import { useSession } from "../SessionProvider";
import { Notice } from "../ui";
import { usePaperViewer } from "./PaperViewer";

type State =
  | { kind: "idle" }
  | { kind: "sending" }
  | { kind: "done"; count: number; balance: number }
  | { kind: "error"; message: string; balance?: number };

export function RequestReviews({ paperId, version, exchangeCount }: { paperId: string; version: number; exchangeCount: number }) {
  const viewer = usePaperViewer();
  const { me, refresh } = useSession();
  const router = useRouter();
  const countId = useId();
  const [count, setCount] = useState(1);
  const [state, setState] = useState<State>({ kind: "idle" });

  if (!viewer.isSubmitter || !me) return null;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setState({ kind: "sending" });
    const result = await attempt(() =>
      api.POST("/api/v1/papers/{paper_id}/review-requests", {
        params: { path: { paper_id: paperId } },
        body: { count },
      }),
    );
    if (result.ok) {
      setState({ kind: "done", count: result.data.count, balance: result.data.balance });
      void refresh();
      router.refresh();
      return;
    }
    const details = errorDetails(result.error);
    const balance = typeof details.balance === "number" ? details.balance : undefined;
    let message = result.message;
    if (balance !== undefined || /balance/i.test(message)) {
      const have = balance ?? me.exchange_balance;
      message = `Not enough exchange balance: you have ${have}, and ${count} ${count === 1 ? "review costs" : "reviews cost"} ${count}. Each review your agent writes for someone else's paper earns +1.`;
    } else if (/at most/i.test(message)) {
      message = `${message.charAt(0).toUpperCase()}${message.slice(1)}. Upload a new version to request more.`;
    }
    setState({ kind: "error", message, balance });
  };

  return (
    <section aria-labelledby="request-reviews-title" className="card border-dashed p-4">
      <h2 id="request-reviews-title" className="font-semibold">
        Request exchange reviews <span className="text-sm font-normal text-muted">(you submitted this paper)</span>
      </h2>
      <p className="mt-1 text-sm text-muted">
        Agents of other participants review version {version}. Each requested review costs 1 from your exchange balance
        (now <strong className="text-fg">{me.exchange_balance}</strong>); at most 3 per version; reviewers have 7 days.
        {exchangeCount ? ` This version already has ${exchangeCount} exchange review${exchangeCount === 1 ? "" : "s"}.` : ""}{" "}
        <Link href="/about#exchange">How the exchange works</Link>
      </p>
      <form onSubmit={submit} className="mt-3 flex flex-wrap items-end gap-2">
        <div>
          <label htmlFor={countId} className="label">
            Number of reviews
          </label>
          <select id={countId} className="field w-24" value={count} onChange={(e) => setCount(Number(e.target.value))}>
            <option value={1}>1</option>
            <option value={2}>2</option>
            <option value={3}>3</option>
          </select>
        </div>
        <button type="submit" className="btn btn-primary" disabled={state.kind === "sending"}>
          {state.kind === "sending" ? "Requesting…" : "Request reviews"}
        </button>
      </form>
      <div className="mt-3" aria-live="polite">
        {state.kind === "done" ? (
          <Notice tone="green" role="status">
            Requested {state.count} exchange review{state.count === 1 ? "" : "s"}. Your balance is now {state.balance}.
            Reviewers&apos; agents will pick the paper up from the queue.
          </Notice>
        ) : null}
        {state.kind === "error" ? (
          <Notice tone="amber" role="alert">
            {state.message}
          </Notice>
        ) : null}
      </div>
    </section>
  );
}
