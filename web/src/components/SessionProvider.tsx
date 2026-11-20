"use client";

/**
 * Who is signed in (cookie session on the API). Public pages render on the server anonymously;
 * this context loads `/me` in the browser and drives the signed-in parts of the UI.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, type OperatorOut } from "@/lib/api";

export type SessionStatus = "loading" | "ready" | "offline";

interface SessionValue {
  me: OperatorOut | null;
  status: SessionStatus;
  refresh: () => Promise<OperatorOut | null>;
  signOut: () => Promise<void>;
}

const SessionContext = createContext<SessionValue>({
  me: null,
  status: "loading",
  refresh: async () => null,
  signOut: async () => {},
});

async function loadMe(): Promise<{ me: OperatorOut | null; status: SessionStatus }> {
  try {
    const { data, response } = await api.GET("/api/v1/me");
    if (response.ok && data) return { me: data, status: "ready" };
    return { me: null, status: response.status === 401 ? "ready" : "offline" };
  } catch {
    return { me: null, status: "offline" };
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<OperatorOut | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");

  const refresh = useCallback(async () => {
    const result = await loadMe();
    setMe(result.me);
    setStatus(result.status);
    return result.me;
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.POST("/api/v1/auth/logout");
    } finally {
      setMe(null);
      setStatus("ready");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadMe().then((result) => {
      if (cancelled) return;
      setMe(result.me);
      setStatus(result.status);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo(() => ({ me, status, refresh, signOut }), [me, status, refresh, signOut]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  return useContext(SessionContext);
}
