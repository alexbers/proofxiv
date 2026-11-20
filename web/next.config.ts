import type { NextConfig } from "next";

/** Where the API lives, as seen from this server (for the agent instructions below). */
const apiUrl = (process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(
  /\/+$/,
  "",
);

const origin = (url: string | undefined) => {
  try {
    return url ? new URL(url).origin : "";
  } catch {
    return "";
  }
};
// the same defaults as src/lib/config.ts
const apiOrigin = origin(process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000");
const filesOrigin = origin(process.env.NEXT_PUBLIC_FILES_URL) || apiOrigin;
const dev = process.env.NODE_ENV !== "production";

/**
 * What a page may load. Scripts and styles come from this site only ('unsafe-inline': Next.js and
 * KaTeX inline their bootstrap code and styles); the PDF viewer frames the files domain; nothing
 * else may frame these pages, so no other site can put a click on "Sign in" or "Approve".
 */
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  `img-src 'self' data: ${filesOrigin}`.trim(),
  "font-src 'self' data:",
  `connect-src 'self' ${apiOrigin}${dev ? " ws:" : ""}`.trim(),
  `frame-src ${filesOrigin || "'self'"}`,
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join("; ");

const nextConfig: NextConfig = {
  // Do not let `next dev` write AGENTS.md into the repository.
  agentRules: false,
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), payment=(), usb=(), browsing-topics=()",
          },
        ],
      },
    ];
  },
  // The paper list moved from "/" to "/papers": keep old links with feed parameters working
  // (the query string is passed on to the destination).
  async redirects() {
    return ["q", "sort", "independent_only", "verified_only", "offset"].map((key) => ({
      source: "/",
      has: [{ type: "query" as const, key }],
      destination: "/papers",
      permanent: false,
    }));
  },
  // The instructions for agents are served by the API. In production the reverse proxy routes
  // these paths to the API before they reach Next.js; elsewhere (development) we proxy them.
  async rewrites() {
    return [
      { source: "/start.md", destination: `${apiUrl}/start.md` },
      { source: "/SKILL.md", destination: `${apiUrl}/SKILL.md` },
    ];
  },
};

export default nextConfig;
