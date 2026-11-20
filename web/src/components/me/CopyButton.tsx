"use client";

import { useState } from "react";

/** Copies through a hidden text area: for browsers without the async clipboard API (non-secure pages). */
function legacyCopy(text: string): boolean {
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    area.remove();
  }
}

export function CopyButton({
  text,
  label = "Copy",
  copiedLabel = "Copied ✓",
  className = "btn",
  title,
}: {
  text: string;
  label?: string;
  copiedLabel?: string;
  /** button classes (default: the standard `btn`) */
  className?: string;
  title?: string;
}) {
  const [copied, setCopied] = useState<"idle" | "ok" | "fail">("idle");
  const copy = async () => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      ok = legacyCopy(text);
    }
    setCopied(ok ? "ok" : "fail");
    setTimeout(() => setCopied("idle"), 2500);
  };
  return (
    <button type="button" className={className} onClick={copy} title={title} aria-live="polite">
      {copied === "ok" ? copiedLabel : copied === "fail" ? "Copy failed — select the text" : label}
    </button>
  );
}
