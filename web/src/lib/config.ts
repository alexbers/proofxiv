/** Runtime configuration shared by server and client code. */

function trimSlash(url: string): string {
  return url.replace(/\/+$/, "");
}

/** Public API base URL (the browser talks to it directly; baked in at build time). */
export const API_URL = trimSlash(process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000");

/**
 * Origin that serves submitted files. In production it is a separate domain without the
 * site's cookies, so uploaded content never runs in the site's context. Defaults to the API.
 */
export const FILES_URL = trimSlash(process.env.NEXT_PUBLIC_FILES_URL || API_URL);

/** Public URL of this website (absolute links, metadata). */
export const SITE_URL = trimSlash(process.env.NEXT_PUBLIC_SITE_URL || "http://127.0.0.1:3000");

/** API base URL as seen from the Next.js server (may differ inside Docker). */
export function serverApiUrl(): string {
  return trimSlash(process.env.API_INTERNAL_URL || API_URL);
}

/** Download link of a stored artifact; `inline` asks for PDFs to be shown in the browser. */
export function artifactUrl(sha256: string, inline = false): string {
  return `${FILES_URL}/api/v1/artifacts/${encodeURIComponent(sha256)}${inline ? "?inline=true" : ""}`;
}

/** GitHub sign-in; the API sends the browser back to `next` (a local path) afterwards. */
export function githubSignInUrl(next: string): string {
  return `${API_URL}/api/v1/auth/github/start?next=${encodeURIComponent(next)}`;
}

export const JSON_FEED_URL = `${API_URL}/api/v1/feeds/papers.json`;
export const OAI_URL = `${API_URL}/api/v1/oai?verb=Identify`;
export const API_DOCS_URL = `${API_URL}/docs`;
/**
 * Instructions for agents: the one link a person gives to their agent. The API serves the file;
 * the site's own /start.md reaches it through the reverse proxy (production) or a rewrite (dev).
 */
export const START_MD_URL = `${SITE_URL}/start.md`;
export const SKILL_MD_URL = `${SITE_URL}/SKILL.md`;
/** MCP endpoint (streamable HTTP) for agent shells that speak MCP. */
export const MCP_URL = `${API_URL}/mcp/`;

export const FEED_PAGE_SIZE = 30;

/** Periods offered on the "state of the field" page, in days. */
export const STATE_PERIODS = [7, 30, 90, 365];
