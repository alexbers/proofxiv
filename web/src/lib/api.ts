/**
 * Typed ProofXiv API client for the browser (signed-in actions use the session cookie).
 * Types come from openapi.json via `npm run gen:api`.
 */
import createClient from "openapi-fetch";

import type { components, paths } from "./api-types";
import { API_URL } from "./config";

export type Schemas = components["schemas"];
export type PaperSummary = Schemas["PaperSummary"];
export type PaperDetail = Schemas["PaperDetail"];
export type ClaimOut = Schemas["ClaimOut"];
export type RunOut = Schemas["RunOut"];
export type ReviewOut = Schemas["ReviewOut"];
export type ReviewBody = Schemas["ReviewBody"];
export type ProblemSummary = Schemas["ProblemSummary"];
export type ProblemDetail = Schemas["ProblemDetail"];
export type QuantityOut = Schemas["QuantityOut"];
export type RecordOut = Schemas["RecordOut"];
export type EventOut = Schemas["EventOut"];
export type OperatorOut = Schemas["OperatorOut"];
export type ProfileOut = Schemas["ProfileOut"];
export type TokenOut = Schemas["TokenOut"];
export type NewTokenOut = Schemas["NewTokenOut"];
export type KeyOut = Schemas["KeyOut"];
export type AssignmentOut = Schemas["AssignmentOut"];
export type NotificationOut = Schemas["NotificationOut"];
export type DigestOut = Schemas["DigestOut"];
export type ModerationEntryOut = Schemas["ModerationEntryOut"];
export type TrustedOperatorOut = Schemas["TrustedOperatorOut"];
export type StatsOut = Schemas["StatsOut"];
export type ReportReason = Schemas["ReportReason"];
export type ReportTarget = Schemas["ReportTarget"];
export type QueueOut = Schemas["QueueOut"];
export type OwnerJournalEntryOut = Schemas["OwnerJournalEntryOut"];
export type DomainOut = Schemas["DomainOut"];
export type RootOfTrustOut = Schemas["RootOfTrustOut"];
export type DevicePreviewOut = Schemas["DevicePreviewOut"];
export type AttemptOut = Schemas["AttemptOut"];
export type PostOut = Schemas["PostOut"];
export type FeedPostOut = Schemas["FeedPostOut"];
export type PendingTaskOut = Schemas["PendingTaskOut"];
export type ReferencesOut = Schemas["ReferencesOut"];
export type ReferenceItem = Schemas["ReferenceItem"];
export type ApproachOut = Schemas["ApproachOut"];
export type ApproachSource = Schemas["ApproachSource"];
export type PromptOut = Schemas["PromptOut"];
export type IntegrityOut = Schemas["IntegrityOut"];

/** Total number of matches of a list endpoint (`X-Total-Count`), or null if absent. */
export function totalCount(response: Response): number | null {
  const raw = response.headers.get("X-Total-Count");
  if (raw === null) return null;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

/** Browser client: sends the session cookie to the API (CORS with credentials). */
export const api = createClient<paths>({ baseUrl: API_URL, credentials: "include" });

interface DomainError {
  error?: string;
  message?: string;
  details?: Record<string, unknown>;
  detail?: unknown;
}

/** A readable message for an API error body (domain errors and FastAPI validation errors). */
export function errorMessage(error: unknown, response?: Response): string {
  if (error && typeof error === "object") {
    const e = error as DomainError;
    if (typeof e.message === "string" && e.message) return e.message;
    if (Array.isArray(e.detail)) {
      const parts = e.detail
        .map((d) => {
          if (d && typeof d === "object" && "msg" in d) {
            const item = d as { msg: string; loc?: unknown[] };
            const field = Array.isArray(item.loc) ? item.loc.filter((x) => x !== "body").join(".") : "";
            return field ? `${field}: ${item.msg}` : item.msg;
          }
          return String(d);
        })
        .filter(Boolean);
      if (parts.length) return parts.join("; ");
    }
    if (typeof e.detail === "string") return e.detail;
  }
  if (typeof error === "string" && error) return error;
  if (response) {
    if (response.status === 401) return "Please sign in first.";
    if (response.status === 403) return "You are not allowed to do this.";
    if (response.status === 404) return "Not found.";
    return `Request failed (HTTP ${response.status}).`;
  }
  return "The request failed. Is the API reachable?";
}

/** Details of a domain error (e.g. `{balance: 0}` when the exchange balance is too low). */
export function errorDetails(error: unknown): Record<string, unknown> {
  if (error && typeof error === "object" && "details" in error) {
    const details = (error as DomainError).details;
    if (details && typeof details === "object") return details;
  }
  return {};
}

/** Runs an API call and turns network failures into a readable message. */
export async function attempt<T>(
  call: () => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<{ ok: true; data: T; response: Response } | { ok: false; message: string; error: unknown; status: number }> {
  try {
    const result = await call();
    if (result.error !== undefined || !result.response.ok) {
      return {
        ok: false,
        message: errorMessage(result.error, result.response),
        error: result.error,
        status: result.response.status,
      };
    }
    return { ok: true, data: result.data as T, response: result.response };
  } catch {
    return {
      ok: false,
      message: `Cannot reach the ProofXiv API at ${API_URL}.`,
      error: null,
      status: 0,
    };
  }
}
