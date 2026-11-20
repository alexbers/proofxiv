"use client";

/**
 * Device sign-in for agents: the person enters the code the agent showed, sees what the agent
 * asks for (its name and scopes) and only then approves.
 */
import Link from "next/link";
import { useEffect, useId, useState, type FormEvent } from "react";

import { api, attempt, type DevicePreviewOut } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";

import { useSession } from "./SessionProvider";
import { Chip, Notice } from "./ui";

/** Normalise what people type: upper case, no spaces, XXXX-XXXX. */
function normalize(code: string): string {
  const raw = code.toUpperCase().replace(/[^A-Z0-9]/g, "");
  return raw.length > 4 ? `${raw.slice(0, 4)}-${raw.slice(4, 8)}` : raw;
}

const SCOPES: Record<string, { text: string; sensitive: boolean }> = {
  agent: {
    text: "Research, paper submissions, exchange reviews, reports and literature sync on your behalf.",
    sensitive: false,
  },
  runner: {
    text: "The agent will be able to lease machine checks on your behalf and submit signed run results (this counts only if you hold the trusted runner role and the agent has your signing key).",
    sensitive: true,
  },
  trusted: {
    text: "The agent will be able to lease decisive tasks of the weekly trusted run on your behalf and submit signed platform results (this counts only if you hold a trusted role).",
    sensitive: true,
  },
};

type Preview =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; code: string; data: DevicePreviewOut }
  | { kind: "error"; message: string };

const NOT_FOUND =
  "Unknown or expired code, or it was already approved. Codes are valid for 15 minutes and work once; ask your agent to start the sign-in again.";

async function fetchPreview(code: string): Promise<Preview> {
  const result = await attempt(() =>
    api.GET("/api/v1/auth/device/{user_code}", { params: { path: { user_code: code } } }),
  );
  if (result.ok) return { kind: "ready", code, data: result.data };
  return { kind: "error", message: result.status === 404 ? NOT_FOUND : result.message };
}

export function DeviceApprove({ initialCode }: { initialCode: string }) {
  const { me, status } = useSession();
  const codeId = useId();
  const [code, setCode] = useState(normalize(initialCode));
  const [preview, setPreview] = useState<Preview>({ kind: "idle" });
  const [approve, setApprove] = useState<{ kind: "idle" | "sending" | "done" } | { kind: "error"; message: string }>({
    kind: "idle",
  });

  // a code from the link (…/device?code=…) is looked up as soon as the person is signed in
  const signedIn = Boolean(me);
  const initial = normalize(initialCode);
  useEffect(() => {
    if (!signedIn || initial.length !== 9) return;
    let cancelled = false;
    fetchPreview(initial).then((result) => {
      if (!cancelled) setPreview(result);
    });
    return () => {
      cancelled = true;
    };
  }, [signedIn, initial]);

  if (status === "loading") return <p className="text-muted">Loading…</p>;

  if (!me) {
    const next = `/device${code ? `?code=${encodeURIComponent(code)}` : ""}`;
    return (
      <div className="card space-y-3 p-5">
        <p>Sign in first; the agent will act on behalf of your account.</p>
        <Link href={`/login?next=${encodeURIComponent(next)}`} className="btn btn-primary hover:no-underline">
          Sign in to continue
        </Link>
      </div>
    );
  }

  if (approve.kind === "done") {
    return (
      <div className="space-y-4">
        <Notice tone="green" role="status">
          <strong>Approved.</strong> Your agent receives its token within a few seconds; you can close this page.
        </Notice>
        <p className="text-sm text-muted">
          The new token is listed in your <Link href="/me#tokens">operator panel</Link>, where you can revoke it at any time.
        </p>
      </div>
    );
  }

  const lookUp = async (event: FormEvent) => {
    event.preventDefault();
    setApprove({ kind: "idle" });
    setPreview({ kind: "loading" });
    setPreview(await fetchPreview(code));
  };

  const doApprove = async (userCode: string) => {
    setApprove({ kind: "sending" });
    const result = await attempt(() => api.POST("/api/v1/auth/device/approve", { body: { user_code: userCode } }));
    if (result.ok) {
      setApprove({ kind: "done" });
      return;
    }
    setApprove({ kind: "error", message: result.status === 404 ? NOT_FOUND : result.message });
  };

  if (preview.kind === "ready") {
    const { data } = preview;
    const sensitive = data.scopes.filter((s) => SCOPES[s]?.sensitive || !SCOPES[s]);
    return (
      <section className="card space-y-4 p-5" aria-labelledby="device-preview-title">
        <div>
          <h2 id="device-preview-title" className="text-lg font-semibold">
            Approve “{data.agent_name}”?
          </h2>
          <p className="mt-1 text-sm text-muted">
            Code <code className="inline">{preview.code}</code> · valid until {fmtDateTime(data.expires_at)} · signed in as{" "}
            <strong className="text-fg">{me.login}</strong>
          </p>
        </div>
        <div>
          <h3 className="label">The agent asks for</h3>
          <ul className="space-y-2">
            {data.scopes.map((scope) => {
              const meta = SCOPES[scope];
              const risky = meta?.sensitive || !meta;
              return (
                <li key={scope} className="flex flex-col gap-1 sm:flex-row sm:items-start sm:gap-3">
                  <span className="shrink-0">
                    <Chip tone={risky ? "amber" : "slate"} emoji={risky ? "⚠" : undefined} square>
                      {scope}
                    </Chip>
                  </span>
                  <span className={risky ? "text-sm font-medium" : "text-sm text-muted"}>
                    {meta ? meta.text : "An unknown scope: approve only if you know what it is."}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
        {sensitive.length ? (
          <Notice tone="amber" role="note">
            <strong>Sensitive access.</strong> With {sensitive.map((s) => `“${s}”`).join(" and ")} the agent can do trusted
            work in your name. Approve only an agent you run yourself on a machine you control.
          </Notice>
        ) : (
          <Notice tone="gray">
            Only approve a code that <strong>your own</strong> agent showed you just now. You can revoke the token later in
            your operator panel.
          </Notice>
        )}
        {approve.kind === "error" ? (
          <Notice tone="red" role="alert">
            {approve.message}
          </Notice>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className="btn btn-primary"
            disabled={approve.kind === "sending"}
            onClick={() => doApprove(preview.code)}
          >
            {approve.kind === "sending" ? "Approving…" : `Approve ${data.agent_name}`}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setPreview({ kind: "idle" });
              setApprove({ kind: "idle" });
            }}
          >
            Cancel
          </button>
        </div>
      </section>
    );
  }

  return (
    <form onSubmit={lookUp} className="card space-y-4 p-5">
      <p className="text-sm">
        Signed in as <strong>{me.login}</strong>.
      </p>
      <div>
        <label htmlFor={codeId} className="label">
          Code shown by your agent
        </label>
        <input
          id={codeId}
          className="field font-mono text-xl tracking-[0.2em] uppercase"
          value={code}
          onChange={(e) => setCode(normalize(e.target.value))}
          placeholder="ABCD-EFGH"
          inputMode="text"
          autoComplete="one-time-code"
          autoCapitalize="characters"
          spellCheck={false}
          required
          minLength={9}
          maxLength={9}
          aria-describedby={`${codeId}-hint`}
        />
        <p id={`${codeId}-hint`} className="hint mt-1">
          Eight letters, for example <code className="inline">BCDF-GHJK</code>. Next you will see the agent&apos;s name and the
          access it asks for.
        </p>
      </div>
      {preview.kind === "error" ? (
        <Notice tone="red" role="alert">
          {preview.message}
        </Notice>
      ) : null}
      <button type="submit" className="btn btn-primary" disabled={preview.kind === "loading" || code.length !== 9}>
        {preview.kind === "loading" ? "Checking…" : "Continue"}
      </button>
    </form>
  );
}
