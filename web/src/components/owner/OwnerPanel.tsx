"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { api, attempt, type ReportTarget } from "@/lib/api";
import { fmtDate, isUuid } from "@/lib/format";
import { TASK_KINDS, TASK_STATUSES } from "@/lib/labels";

import { NotificationList } from "../me/NotificationsSection";
import { useSession } from "../SessionProvider";
import { Empty, Notice, PageTitle, Section } from "../ui";
import { useApi } from "../useApi";

const STATUS_COLUMNS = ["blocked", "ready", "leased", "done", "cancelled"];

const loadQueue = () => attempt(() => api.GET("/api/v1/owner/queue"));

const CLI_COMMANDS: [string, string][] = [
  ["proofxiv owner grant-role <login> reviewer", "appoint a trusted reviewer (or runner)"],
  ["proofxiv owner revoke-role <login> runner --reason …", "take a role away"],
  ["proofxiv owner revoke-key <fingerprint> --since <ISO time> --reason …", "distrust a key; its results are re-checked"],
  ["proofxiv owner approve-prompts", "sign new versions of the official prompts"],
  ["proofxiv owner set-param weekly_period_days 7", "change a platform parameter"],
  ["proofxiv owner unhide paper <id> <reason>", "restore hidden content"],
];

export function OwnerPanel() {
  const { me, status } = useSession();
  const isOwner = Boolean(me?.is_owner);
  const queue = useApi(isOwner && me ? `queue:${me.id}` : null, loadQueue);
  const notes = useApi(isOwner && me ? `owner-notes:${me.id}` : null, () => attempt(() => api.GET("/api/v1/me/notifications")));
  const formId = useId();
  const [targetType, setTargetType] = useState<ReportTarget>("paper");
  const [targetId, setTargetId] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "green" | "red"; text: string } | null>(null);

  if (status === "loading") return <p className="text-muted">Loading…</p>;
  if (!me) {
    return (
      <div className="mx-auto max-w-xl">
        <PageTitle>Owner panel</PageTitle>
        <div className="card space-y-3 p-5">
          <p>Sign in with an owner account.</p>
          <Link href="/login?next=%2Fowner" className="btn btn-primary hover:no-underline">
            Sign in
          </Link>
        </div>
      </div>
    );
  }
  if (!isOwner) {
    return (
      <div className="mx-auto max-w-xl">
        <PageTitle>Owner panel</PageTitle>
        <Notice tone="amber">
          This panel is only for the platform owners. You are signed in as <strong>{me.login}</strong>.
        </Notice>
      </div>
    );
  }

  const urgent = (notes.data ?? []).filter((n) => n.urgent);
  const markRead = async (id: string) => {
    await attempt(() => api.POST("/api/v1/me/notifications/{note_id}/read", { params: { path: { note_id: id } } }));
    notes.reload();
  };

  const prefill = (target: string) => {
    const [type, id] = target.split("/");
    if (type && id) {
      setTargetType(type as ReportTarget);
      setTargetId(id);
      document.getElementById(`${formId}-reason`)?.focus();
    }
  };

  const hide = async (event: FormEvent) => {
    event.preventDefault();
    if (!isUuid(targetId.trim())) {
      setMessage({ tone: "red", text: "The target id must be a UUID (copy it from the page address or the report)." });
      return;
    }
    setBusy(true);
    setMessage(null);
    const result = await attempt(() =>
      api.POST("/api/v1/owner/hide", {
        body: { target_type: targetType, target_id: targetId.trim(), reason: reason.trim() },
      }),
    );
    setBusy(false);
    if (result.ok) {
      setMessage({ tone: "green", text: `Hidden. The action is in the public moderation log.` });
      setTargetId("");
      setReason("");
      queue.reload();
    } else {
      setMessage({ tone: "red", text: result.message });
    }
  };

  const tasks = queue.data?.tasks ?? {};
  const kinds = Object.keys(tasks).sort();

  return (
    <div className="space-y-10">
      <PageTitle
        subtitle={
          <>
            The weekly trusted run&apos;s queue, urgent reports and the emergency hide. Everything else that changes the chain
            of trust is done with signed commands, not here.
          </>
        }
      >
        Owner panel
      </PageTitle>

      <Section id="queue" title="Weekly run queue" description="Decisive tasks by kind and status.">
        {queue.error ? (
          <Notice tone="red" role="alert">
            {queue.error}
          </Notice>
        ) : queue.loading || !queue.data ? (
          <p className="text-sm text-muted">Loading the queue…</p>
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className={queue.data.open_urgent_reports ? "notice tone-red p-4" : "card p-4"}>
                <p className="text-xs">Open urgent reports</p>
                <p className="text-3xl font-semibold tabular-nums">{queue.data.open_urgent_reports}</p>
              </div>
              <div className="card p-4">
                <p className="text-xs text-muted">Next weekly run</p>
                <p className="text-xl font-semibold">
                  {queue.data.next_weekly_run ? fmtDate(queue.data.next_weekly_run) : "not scheduled (no run yet)"}
                </p>
              </div>
            </div>
            {kinds.length ? (
              <div className="card table-scroll">
                <table className="data-table">
                  <caption className="sr-only">Decisive tasks by kind and status</caption>
                  <thead>
                    <tr>
                      <th scope="col">Task</th>
                      {STATUS_COLUMNS.map((s) => (
                        <th key={s} scope="col" className="text-right" title={TASK_STATUSES[s]}>
                          {s}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {kinds.map((kind) => (
                      <tr key={kind}>
                        <th scope="row" className="font-medium">
                          {TASK_KINDS[kind] ?? kind}
                        </th>
                        {STATUS_COLUMNS.map((s) => (
                          <td key={s} className="text-right tabular-nums">
                            {tasks[kind]?.[s] ?? <span className="text-muted">·</span>}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty>The queue is empty.</Empty>
            )}
          </div>
        )}
      </Section>

      <Section
        id="urgent"
        title="Urgent notifications"
        count={urgent.filter((n) => !n.read_at).length}
        description="Reports of malware or illegal content reach the owners at once; hide the material below if needed, the weekly run reviews it afterwards."
      >
        {notes.error ? (
          <Notice tone="red" role="alert">
            {notes.error}
          </Notice>
        ) : notes.loading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : urgent.length ? (
          <div className="space-y-2">
            <NotificationList notes={urgent} onRead={markRead} />
            <div className="flex flex-wrap gap-2 text-sm">
              {urgent
                .filter((n) => !n.read_at && typeof n.payload.target === "string")
                .map((n) => (
                  <button key={n.id} type="button" className="btn" onClick={() => prefill(String(n.payload.target))}>
                    Prepare hide: {String(n.payload.target).slice(0, 16)}…
                  </button>
                ))}
            </div>
          </div>
        ) : (
          <Empty>No urgent notifications.</Empty>
        )}
      </Section>

      <Section id="hide" title="Hide now" description="Emergency hide for malware or illegal content. It is logged publicly in the moderation log.">
        <form onSubmit={hide} className="card space-y-3 p-4">
          <div className="grid gap-3 sm:grid-cols-[10rem_minmax(0,1fr)]">
            <div>
              <label htmlFor={`${formId}-type`} className="label">
                Target type
              </label>
              <select
                id={`${formId}-type`}
                className="field"
                value={targetType}
                onChange={(e) => setTargetType(e.target.value as ReportTarget)}
              >
                <option value="paper">Paper</option>
                <option value="review">Review</option>
                <option value="problem">Problem</option>
              </select>
            </div>
            <div>
              <label htmlFor={`${formId}-id`} className="label">
                Target id (UUID)
              </label>
              <input
                id={`${formId}-id`}
                className="field font-mono text-sm"
                required
                value={targetId}
                onChange={(e) => setTargetId(e.target.value)}
                placeholder="8b424bc3-6856-430e-91b1-4c0b5400457e"
                spellCheck={false}
              />
            </div>
          </div>
          <div>
            <label htmlFor={`${formId}-reason`} className="label">
              Reason (public)
            </label>
            <input
              id={`${formId}-reason`}
              className="field"
              required
              minLength={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Urgent report: executable file in the bundle"
            />
          </div>
          {message ? (
            <Notice tone={message.tone} role={message.tone === "red" ? "alert" : "status"}>
              {message.text}
            </Notice>
          ) : null}
          <button type="submit" className="btn btn-danger" disabled={busy}>
            {busy ? "Hiding…" : "Hide now"}
          </button>
        </form>
      </Section>

      <Section
        id="cli"
        title="Other owner actions"
        description="Roles, key revocation, official prompts and parameters change the chain of trust, so they are statements signed with the owner key on your machine — the server cannot forge them. They appear in the public owner journal."
      >
        <div className="card p-4">
          <ul className="space-y-2 text-sm">
            {CLI_COMMANDS.map(([cmd, what]) => (
              <li key={cmd}>
                <code className="inline">{cmd}</code> <span className="text-muted">— {what}</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-sm text-muted">
            The owner key is set with <code className="inline">proofxiv config set owner_key_file &lt;path&gt;</code>; its
            public key must be listed in the server&apos;s <code className="inline">PROOFXIV_OWNER_PUBLIC_KEYS</code>. See the{" "}
            <Link href="/trusted#journal">owner journal</Link>.
          </p>
        </div>
      </Section>
    </div>
  );
}
