/**
 * API access for Server Components (public pages are rendered on the server for indexing).
 * Requests are anonymous and never cached, so pages always show the current state.
 */
import createClient from "openapi-fetch";
import { notFound, unstable_rethrow } from "next/navigation";

import type { paths } from "./api-types";
import { errorMessage, totalCount } from "./api";
import { serverApiUrl } from "./config";

export const serverApi = createClient<paths>({
  baseUrl: serverApiUrl(),
  // Look `fetch` up on every call (Next.js patches the global one) and never cache:
  // pages always show live data and are rendered at request time.
  fetch: (request: Request) => fetch(request, { cache: "no-store" }),
});

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface Result<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

function unreachable(cause: unknown): ApiError {
  // Next.js signals dynamic rendering (and notFound/redirect) by throwing: never swallow those
  unstable_rethrow(cause);
  return new ApiError(
    `Cannot reach the ProofXiv API at ${serverApiUrl()} (${cause instanceof Error ? cause.message : "network error"}).`,
    503,
  );
}

async function settle<T>(promise: Promise<Result<T>>, opts: { notFoundOn422?: boolean }): Promise<Result<T>> {
  let result: Result<T>;
  try {
    result = await promise;
  } catch (cause) {
    throw unreachable(cause);
  }
  const status = result.response.status;
  if (status === 404 || (opts.notFoundOn422 && status === 422)) notFound();
  if (result.error !== undefined || !result.response.ok) {
    throw new ApiError(errorMessage(result.error, result.response), status);
  }
  return result;
}

/**
 * Await an API call made with `serverApi` and return its data.
 * 404 (and 422 for malformed path parameters) render the not-found page; other errors throw.
 */
export async function load<T>(promise: Promise<Result<T>>, opts: { notFoundOn422?: boolean } = {}): Promise<T> {
  return (await settle(promise, opts)).data as T;
}

/** A page of a list endpoint together with the total number of matches (`X-Total-Count`). */
export async function loadPage<T>(promise: Promise<Result<T[]>>): Promise<{ items: T[]; total: number | null }> {
  const result = await settle(promise, {});
  return { items: (result.data ?? []) as T[], total: totalCount(result.response) };
}

/** Like `load`, but returns `fallback` instead of failing (for optional parts of a page). */
export async function loadOr<T>(promise: Promise<Result<T>>, fallback: T): Promise<T> {
  try {
    const result = await promise;
    if (result.error !== undefined || !result.response.ok) return fallback;
    return result.data as T;
  } catch (cause) {
    unstable_rethrow(cause);
    return fallback;
  }
}

/** Read a small text artifact (e.g. a Lean file) for display; null if missing, blocked or too large. */
export async function loadArtifactText(sha256: string, maxBytes = 200_000): Promise<string | null> {
  try {
    const response = await fetch(`${serverApiUrl()}/api/v1/artifacts/${encodeURIComponent(sha256)}`, {
      cache: "no-store",
    });
    if (!response.ok) return null;
    const length = Number(response.headers.get("content-length") || "0");
    if (length > maxBytes) return null;
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength > maxBytes) return null;
    return new TextDecoder("utf-8", { fatal: false }).decode(buffer);
  } catch (cause) {
    unstable_rethrow(cause);
    return null;
  }
}
