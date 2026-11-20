/** Formatting helpers. Dates are shown in UTC so server and browser render the same text. */

const DATE = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  timeZone: "UTC",
});

const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function fmtDate(iso: string | null | undefined): string {
  const date = parse(iso);
  return date ? DATE.format(date) : (iso ?? "");
}

export function fmtDateTime(iso: string | null | undefined): string {
  const date = parse(iso);
  return date ? `${DATE_TIME.format(date)} UTC` : (iso ?? "");
}

/** "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", then the date. */
export function fmtAgo(iso: string | null | undefined, now: number = Date.now()): string {
  const date = parse(iso);
  if (!date) return iso ?? "";
  const minutes = Math.floor((now - date.getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const days = Math.floor(hours / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  return DATE.format(date);
}

export function fmtBytes(size: number | null | undefined): string {
  if (size == null) return "";
  if (size < 1024) return `${size} B`;
  const units = ["KB", "MB", "GB"];
  let value = size / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

export function shortHash(hash: string | null | undefined, length = 12): string {
  return hash ? hash.slice(0, length) : "";
}

export function shortId(id: string): string {
  return id.split("-")[0] ?? id;
}

/** "Confirmed by ProofXiv" → "confirmed by ProofXiv" (for use inside a sentence; names keep their case). */
export function lowerFirst(text: string): string {
  return text ? text[0].toLowerCase() + text.slice(1) : text;
}

const COUNT = new Intl.NumberFormat("en-GB");

/** "1 paper", "1,573 problems" */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${COUNT.format(n)} ${n === 1 ? one : many}`;
}

/** Positions of a Markdown marker outside inline math (`$...$`) and code spans. */
function markerPositions(text: string, marker: string): number[] {
  const out: number[] = [];
  let inMath = false;
  let inCode = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "\\") {
      i++;
      continue;
    }
    if (ch === "`" && !inMath) {
      if (marker === "`") out.push(i);
      inCode = !inCode;
      continue;
    }
    if (ch === "$" && !inCode) {
      if (marker === "$") out.push(i);
      inMath = !inMath;
      continue;
    }
    if (!inMath && !inCode && marker !== "`" && marker !== "$" && text.startsWith(marker, i)) {
      out.push(i);
      i += marker.length - 1;
    }
  }
  return out;
}

/**
 * The beginning of a Markdown text for previews: the first paragraph, cut at a word boundary
 * to at most `max` characters, never leaving an open math span, code span or emphasis.
 */
export function excerpt(markdown: string, max = 320): string {
  const first = markdown.trim().split(/\n\s*\n/)[0] ?? "";
  const text = first.replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  let cut = text.lastIndexOf(" ", max);
  if (cut < max * 0.6) cut = max;
  let out = text.slice(0, cut);
  for (const marker of ["$", "`", "**", "__", "*", "_"]) {
    const positions = markerPositions(out, marker);
    if (positions.length % 2 === 1) out = out.slice(0, positions[positions.length - 1]);
  }
  return `${out.trimEnd()} …`;
}

const TEX_SYMBOLS: Record<string, string> = {
  le: "≤", leq: "≤", ge: "≥", geq: "≥", ne: "≠", neq: "≠", times: "×", cdot: "·", to: "→", infty: "∞", in: "∈",
  pm: "±", approx: "≈", ldots: "…", cdots: "…", dots: "…", sqrt: "√",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ", eta: "η", theta: "θ",
  lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", pi: "π", rho: "ρ", sigma: "σ", tau: "τ", phi: "φ", varphi: "φ", chi: "χ",
  psi: "ψ", omega: "ω", Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Sigma: "Σ", Phi: "Φ", Omega: "Ω",
};
/** commands that only format their argument, and spacing */
const TEX_FORMATTING =
  /\\(?:operatorname\*?|text(?:bf|it|rm)?|emph|math(?:rm|bf|bb|cal|frak|sf|it)|left|right|[bB]igg?|displaystyle|[,;!: ])/g;

/**
 * Markdown/LaTeX as readable plain text, for contexts that cannot render math (page titles, meta
 * descriptions, tooltips): `$1/\det(M)$` becomes `1/det(M)`, `\tau_{19}` becomes `τ19`.
 */
export function plainText(markdown: string, max = 200): string {
  const text = markdown
    .replace(/\$\$?([^$]*)\$\$?/g, "$1")
    .replace(/\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}/g, "$1/$2")
    .replace(TEX_FORMATTING, "")
    .replace(/\\([a-zA-Z]+)/g, (_, name: string) => TEX_SYMBOLS[name] ?? name)
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1") // [text](url)
    .replace(/[{}_*`#>]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}…` : text;
}

/** decodeURIComponent that never throws (route params may or may not be encoded). */
export function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

export function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

/** First value of a search parameter. */
export function param(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) return value[0];
  return value;
}

export function truthy(value: string | string[] | undefined): boolean {
  const v = param(value);
  return v === "1" || v === "true" || v === "on";
}

export function intParam(value: string | string[] | undefined, fallback = 0, min = 0, max = 1_000_000): number {
  const n = Number.parseInt(param(value) ?? "", 10);
  if (Number.isNaN(n)) return fallback;
  return Math.min(Math.max(n, min), max);
}
