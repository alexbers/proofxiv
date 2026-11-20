"use client";

import { useState } from "react";

import { artifactUrl } from "@/lib/config";
import { plainText } from "@/lib/format";

/** Download link plus an inline viewer (the API serves the PDF with `?inline=true`). */
export function PdfViewer({ sha256, title, size }: { sha256: string; title: string; size?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <a className="btn btn-primary hover:no-underline" href={artifactUrl(sha256)} download>
          <span aria-hidden="true">⤓</span> Download PDF{size ? ` (${size})` : ""}
        </a>
        <button type="button" className="btn" aria-expanded={open} aria-controls="pdf-frame" onClick={() => setOpen((v) => !v)}>
          {open ? "Hide inline viewer" : "Show inline"}
        </button>
        <a className="btn hover:no-underline" href={artifactUrl(sha256, true)} target="_blank" rel="noopener noreferrer">
          Open in a new tab<span className="sr-only"> (opens in a new tab)</span>
        </a>
      </div>
      {open ? (
        <div id="pdf-frame" className="mt-3 overflow-hidden rounded-lg border border-line">
          <iframe src={artifactUrl(sha256, true)} title={`PDF of “${plainText(title, 200)}”`} className="block h-[80vh] w-full bg-white" />
          <p className="border-t border-line bg-surface-2 px-3 py-1.5 text-xs text-muted">
            If the PDF does not appear (some browsers block PDFs served in a sandbox), use “Open in a new tab” or download it.
          </p>
        </div>
      ) : null}
    </div>
  );
}
