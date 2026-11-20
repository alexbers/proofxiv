"use client";

import { useRef, useState, type KeyboardEvent } from "react";

import { PROMPT_KINDS, PROMPTS, type PromptKind } from "@/lib/prompts";

import { CopyButton } from "../me/CopyButton";
import { cx } from "../ui";

/** A terminal-style box with the prompts to paste into an AI agent: one tab per kind of work. */
export function PromptBox() {
  const [kind, setKind] = useState<PromptKind>("attack");
  const tabs = useRef<Partial<Record<PromptKind, HTMLButtonElement | null>>>({});
  const prompt = PROMPTS[kind];

  // arrow keys move between tabs (the WAI-ARIA tabs pattern with automatic activation)
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const i = PROMPT_KINDS.indexOf(kind);
    const moves: Record<string, number> = {
      ArrowRight: (i + 1) % PROMPT_KINDS.length,
      ArrowLeft: (i - 1 + PROMPT_KINDS.length) % PROMPT_KINDS.length,
      Home: 0,
      End: PROMPT_KINDS.length - 1,
    };
    if (!(event.key in moves)) return;
    event.preventDefault();
    const next = PROMPT_KINDS[moves[event.key]];
    setKind(next);
    tabs.current[next]?.focus();
  };

  return (
    <div className="terminal">
      <div className="flex items-center gap-3 border-b border-white/10 px-2 sm:px-4">
        <span aria-hidden="true" className="hidden shrink-0 gap-1.5 sm:flex">
          <span className="h-2.5 w-2.5 rounded-full bg-[#ff5f57]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#febc2e]" />
          <span className="h-2.5 w-2.5 rounded-full bg-[#28c840]" />
        </span>
        <div
          role="tablist"
          aria-label="What should your agent do?"
          className="flex min-w-0 flex-1 gap-0.5 sm:gap-1"
          onKeyDown={onKeyDown}
        >
          {PROMPT_KINDS.map((k) => {
            const selected = k === kind;
            return (
              <button
                key={k}
                ref={(el) => {
                  tabs.current[k] = el;
                }}
                type="button"
                role="tab"
                id={`prompt-tab-${k}`}
                aria-selected={selected}
                aria-controls="prompt-panel"
                tabIndex={selected ? 0 : -1}
                onClick={() => setKind(k)}
                className={cx(
                  "-mb-px border-b-2 px-2 py-2.5 text-[13px] font-medium whitespace-nowrap sm:px-3 sm:text-sm",
                  selected
                    ? "border-[#a9b0ff] text-white"
                    : "border-transparent text-white/60 hover:border-white/25 hover:text-white/90",
                )}
              >
                <span className="sm:hidden">{PROMPTS[k].short}</span>
                <span className="hidden sm:inline">{PROMPTS[k].label}</span>
              </button>
            );
          })}
        </div>
      </div>
      <div role="tabpanel" id="prompt-panel" aria-labelledby={`prompt-tab-${kind}`} className="px-4 pt-4 pb-4 sm:px-5 sm:pt-5">
        <p className="font-mono text-[13px] leading-relaxed whitespace-pre-wrap wrap-anywhere sm:text-[14.5px]">
          <span aria-hidden="true" className="text-[#7ee2a8] select-none">
            ❯{" "}
          </span>
          {prompt.text}
        </p>
        <div className="mt-4 flex justify-end">
          <CopyButton
            text={prompt.text}
            label="Copy prompt"
            copiedLabel="Copied ✓ — now paste it into your agent"
            className="btn btn-terminal w-full sm:w-auto"
          />
        </div>
      </div>
    </div>
  );
}
