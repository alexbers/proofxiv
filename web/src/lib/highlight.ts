/**
 * Syntax highlighting with Shiki on the server (Lean 4 and a few other languages).
 * Uses the JavaScript regex engine, so no WebAssembly is needed.
 */
import { createHighlighterCore, type HighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

const ALIASES: Record<string, string> = {
  lean: "lean4",
  lean4: "lean4",
  py: "python",
  python: "python",
  json: "json",
  sh: "shellscript",
  bash: "shellscript",
  shell: "shellscript",
  shellscript: "shellscript",
  tex: "latex",
  latex: "latex",
  cpp: "cpp",
  "c++": "cpp",
  c: "cpp",
  rust: "rust",
  rs: "rust",
};

const MAX_HIGHLIGHT_CHARS = 150_000;

let highlighter: Promise<HighlighterCore> | null = null;

function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighter) {
    highlighter = createHighlighterCore({
      themes: [import("shiki/themes/github-light.mjs"), import("shiki/themes/github-dark.mjs")],
      langs: [
        import("shiki/langs/lean4.mjs"),
        import("shiki/langs/python.mjs"),
        import("shiki/langs/json.mjs"),
        import("shiki/langs/shellscript.mjs"),
        import("shiki/langs/latex.mjs"),
        import("shiki/langs/cpp.mjs"),
        import("shiki/langs/rust.mjs"),
      ],
      engine: createJavaScriptRegexEngine({ forgiving: true }),
    });
  }
  return highlighter;
}

export function normalizeLang(lang: string | null | undefined): string {
  if (!lang) return "text";
  return ALIASES[lang.toLowerCase()] ?? "text";
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/** HTML of a highlighted code block (light and dark themes via CSS variables). */
export async function highlightToHtml(code: string, lang?: string | null): Promise<string> {
  const language = normalizeLang(lang);
  if (code.length > MAX_HIGHLIGHT_CHARS || language === "text") {
    return `<pre class="shiki plain" tabindex="0"><code>${escapeHtml(code)}</code></pre>`;
  }
  try {
    const hl = await getHighlighter();
    return hl.codeToHtml(code, {
      lang: language,
      themes: { light: "github-light", dark: "github-dark" },
    });
  } catch {
    return `<pre class="shiki plain" tabindex="0"><code>${escapeHtml(code)}</code></pre>`;
  }
}
