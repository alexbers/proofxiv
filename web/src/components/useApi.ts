"use client";

import { useCallback, useEffect, useEffectEvent, useState } from "react";

type Attempt<T> = { ok: true; data: T } | { ok: false; message: string; status: number };

interface ApiState<T> {
  data: T | undefined;
  error: string | null;
  status: number | null;
  loading: boolean;
  reload: () => void;
}

/**
 * Load data in the browser (for signed-in pages). `key` identifies the request; `null` skips it.
 * `fetcher` is usually `() => attempt(() => api.GET(...))`.
 */
export function useApi<T>(key: string | null, fetcher: () => Promise<Attempt<T>>): ApiState<T> {
  const [state, setState] = useState<{ key: string | null; data?: T; error: string | null; status: number | null }>({
    key: null,
    error: null,
    status: null,
  });
  const [tick, setTick] = useState(0);
  const run = useEffectEvent(() => fetcher());

  useEffect(() => {
    if (key === null) return;
    let cancelled = false;
    run().then((result) => {
      if (cancelled) return;
      if (result.ok) setState({ key, data: result.data, error: null, status: 200 });
      else setState((prev) => ({ key, data: prev.key === key ? prev.data : undefined, error: result.message, status: result.status }));
    });
    return () => {
      cancelled = true;
    };
  }, [key, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const current = state.key === key;
  return {
    data: current ? state.data : undefined,
    error: current ? state.error : null,
    status: current ? state.status : null,
    loading: key !== null && !current,
    reload,
  };
}
