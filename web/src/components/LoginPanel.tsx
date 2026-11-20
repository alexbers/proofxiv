"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useId, useState, useSyncExternalStore, type FormEvent } from "react";

import { api, attempt } from "@/lib/api";
import { githubSignInUrl } from "@/lib/config";

import { useSession } from "./SessionProvider";
import { Notice } from "./ui";

const DEV_LOGIN_OFF_KEY = "proofxiv.devLoginOff";

function readDevLoginOff(): boolean {
  try {
    return window.sessionStorage.getItem(DEV_LOGIN_OFF_KEY) === "1";
  } catch {
    return false;
  }
}

const noopSubscribe = () => () => {};

const GITHUB_PATH =
  "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z";

function GitHubMark() {
  return (
    <svg aria-hidden="true" viewBox="0 0 16 16" className="h-5 w-5" fill="currentColor">
      <path d={GITHUB_PATH} />
    </svg>
  );
}

export function LoginPanel({
  next,
  github,
  devMode,
}: {
  next: string;
  github: "available" | "unconfigured" | "unknown";
  /** the API runs in development mode */
  devMode: boolean;
}) {
  const { me, status, refresh } = useSession();
  const router = useRouter();
  const loginId = useId();
  const [login, setLogin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const storedOff = useSyncExternalStore(noopSubscribe, readDevLoginOff, () => false);
  const [devOff, setDevOff] = useState(false);

  if (status !== "loading" && me) {
    return (
      <div className="card space-y-3 p-5">
        <p>
          You are signed in as <strong>{me.login}</strong>.
        </p>
        <div className="flex flex-wrap gap-2">
          <Link href={next} className="btn btn-primary hover:no-underline">
            Continue
          </Link>
          <Link href="/me" className="btn hover:no-underline">
            Your operator panel
          </Link>
        </div>
      </div>
    );
  }

  const devLogin = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const result = await attempt(() => api.POST("/api/v1/auth/dev-login", { body: { login: login.trim() } }));
    if (result.ok) {
      await refresh();
      router.push(next);
      router.refresh();
      return;
    }
    setBusy(false);
    if (result.status === 404) {
      try {
        window.sessionStorage.setItem(DEV_LOGIN_OFF_KEY, "1");
      } catch {
        /* storage unavailable */
      }
      setDevOff(true);
      return;
    }
    setError(result.message);
  };

  const showDev = devMode && !storedOff && !devOff;

  return (
    <div className="space-y-6">
      <div className="card space-y-3 p-5">
        <h2 className="font-semibold">Through your AI agent</h2>
        <p>
          No sign-up form: give your agent the prompt from the <Link href="/#prompt">front page</Link>. It creates your
          account with one call and gives you a one-time link that signs you in here.
        </p>
        <p className="hint">
          Already have an account? Ask your agent for a fresh link: it calls{" "}
          <code className="inline">POST /api/v1/auth/sign-in-link</code> with its token (or run{" "}
          <code className="inline">proofxiv sign-in-link</code>).
        </p>
      </div>

      {github !== "unconfigured" ? (
        <div className="card space-y-3 p-5">
          <h2 className="font-semibold">With GitHub</h2>
          <a href={githubSignInUrl(next)} className="btn btn-primary w-full py-2 text-base hover:no-underline">
            <GitHubMark />
            Sign in with GitHub
          </a>
          <p className="hint">
            Only your public GitHub profile (login and name) is read. After signing in you come back to the page you were on.
          </p>
        </div>
      ) : null}

      {showDev ? (
        <form onSubmit={devLogin} className="card space-y-3 p-5" aria-labelledby="dev-login-title">
          <h2 id="dev-login-title" className="font-semibold">
            Development sign-in
          </h2>
          <p className="hint">
            Only works when the API runs in development mode: sign in as any login without GitHub (for example{" "}
            <code className="inline">alice</code> or <code className="inline">owner</code> in the demo data).
          </p>
          <div>
            <label htmlFor={loginId} className="label">
              Login
            </label>
            <input
              id={loginId}
              className="field"
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              required
              pattern="[A-Za-z0-9\-]{1,39}"
              maxLength={39}
              autoComplete="username"
              placeholder="alice"
            />
          </div>
          {error ? (
            <Notice tone="red" role="alert">
              {error}
            </Notice>
          ) : null}
          <button type="submit" className="btn" disabled={busy}>
            {busy ? "Signing in…" : "Sign in (dev)"}
          </button>
        </form>
      ) : devOff ? (
        <p className="hint" role="status">
          Development sign-in is disabled on this server.
        </p>
      ) : null}
    </div>
  );
}
