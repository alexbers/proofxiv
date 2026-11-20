import { highlightToHtml } from "@/lib/highlight";

/** A highlighted code block (Server Component). Shiki escapes the code, so the HTML is safe. */
export async function CodeBlock({
  code,
  lang,
  label,
}: {
  code: string;
  lang?: string | null;
  /** accessible name, e.g. the file name */
  label?: string;
}) {
  const html = await highlightToHtml(code, lang);
  return (
    <div
      className="code-block my-2 max-w-full"
      role={label ? "region" : undefined}
      aria-label={label}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
