"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { api, attempt } from "@/lib/api";

import { useSession } from "./SessionProvider";
import { Notice } from "./ui";

/** The button that uses a one-time sign-in link (a click, so link previews cannot use it up). */
export function SignInWithLink({ code, login }: { code: string; login: string }) {
  const { refresh } = useSession();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signIn = async () => {
    setBusy(true);
    setError(null);
    const result = await attempt(() =>
      api.POST("/api/v1/auth/sign-in-link/{code}", { params: { path: { code } } }),
    );
    if (result.ok) {
      await refresh();
      router.push("/me");
      router.refresh();
      return;
    }
    setBusy(false);
    setError(result.message);
  };

  return (
    <div className="space-y-3">
      <button type="button" className="btn btn-primary w-full py-2 text-base" onClick={signIn} disabled={busy}>
        {busy ? "Signing in…" : `Sign in as ${login}`}
      </button>
      {error ? (
        <Notice tone="red" role="alert">
          {error}
        </Notice>
      ) : null}
    </div>
  );
}
