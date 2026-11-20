"use client";

/**
 * Signed-in view of a paper: the page itself is rendered on the server anonymously, and this
 * provider loads what depends on the viewer (own likes/follows, whether they submitted it).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api } from "@/lib/api";

import { useSession } from "../SessionProvider";

interface ViewerState {
  forUser: string;
  isSubmitter: boolean;
  signals: string[];
}

interface PaperViewerValue {
  paperId: string;
  signedIn: boolean;
  loading: boolean;
  isSubmitter: boolean;
  signals: string[];
  setSignal: (kind: string, on: boolean) => void;
}

const PaperViewerContext = createContext<PaperViewerValue>({
  paperId: "",
  signedIn: false,
  loading: false,
  isSubmitter: false,
  signals: [],
  setSignal: () => {},
});

export function PaperViewerProvider({ paperId, children }: { paperId: string; children: ReactNode }) {
  const { me, status } = useSession();
  const [state, setState] = useState<ViewerState | null>(null);

  useEffect(() => {
    if (!me) return;
    let cancelled = false;
    api
      .GET("/api/v1/papers/{paper_id}", { params: { path: { paper_id: paperId } } })
      .then(({ data }) => {
        if (!cancelled && data) {
          setState({ forUser: me.id, isSubmitter: Boolean(data.is_submitter), signals: data.my_signals ?? [] });
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [me, paperId]);

  const current = me && state && state.forUser === me.id ? state : null;

  const setSignal = useCallback(
    (kind: string, on: boolean) => {
      setState((prev) => {
        if (!prev || !me || prev.forUser !== me.id) return prev;
        const signals = new Set(prev.signals);
        if (on) signals.add(kind);
        else signals.delete(kind);
        return { ...prev, signals: [...signals] };
      });
    },
    [me],
  );

  const value = useMemo<PaperViewerValue>(
    () => ({
      paperId,
      signedIn: Boolean(me),
      loading: status === "loading" || (Boolean(me) && !current),
      isSubmitter: current?.isSubmitter ?? false,
      signals: current?.signals ?? [],
      setSignal,
    }),
    [paperId, me, status, current, setSignal],
  );

  return <PaperViewerContext.Provider value={value}>{children}</PaperViewerContext.Provider>;
}

export function usePaperViewer(): PaperViewerValue {
  return useContext(PaperViewerContext);
}

/** Renders its children only for the paper's submitter. */
export function SubmitterOnly({ children }: { children: ReactNode }) {
  const viewer = usePaperViewer();
  return viewer.isSubmitter ? <>{children}</> : null;
}
