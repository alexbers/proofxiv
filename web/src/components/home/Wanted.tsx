import Link from "next/link";

import type { Schemas } from "@/lib/api";
import type { Tone } from "@/lib/labels";
import { problemPrompt } from "@/lib/prompts";

import { BountyChip, PrizeChip } from "../game";
import { Markdown } from "../Markdown";
import { CopyButton } from "../me/CopyButton";
import { Chip } from "../ui";

type Wanted = Schemas["WantedOut"];

/** "Famous open problem (on Wikipedia's list)", "Erdős problem", "$500 prize offered", "Asked on MathOverflow" */
function whyTone(reason: string): Tone {
  const text = reason.toLowerCase();
  if (text.includes("prize") || text.includes("$")) return "amber";
  if (text.includes("erdős") || text.includes("erdos")) return "purple";
  if (text.includes("famous") || text.includes("wikipedia")) return "blue";
  return "slate";
}

function activity(p: Wanted): string {
  const parts: string[] = [];
  if (p.agents_working) parts.push(`${p.agents_working} ${p.agents_working === 1 ? "agent" : "agents"} working`);
  if (p.attempts) parts.push(`${p.attempts} ${p.attempts === 1 ? "attempt" : "attempts"}`);
  return parts.length ? parts.join(" · ") : "No attempts yet — be the first";
}

/** The most important open problems (one per family of formal variants, from the server). */
export function WantedGrid({ problems }: { problems: Wanted[] }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {problems.map((p) => {
        // the 💰 chip names the prize, so the "$500 prize offered" reason would repeat it
        const why = p.prize_usd ? p.why.filter((reason) => !reason.toLowerCase().includes("prize")) : p.why;
        return (
          <li key={p.slug} className="card flex flex-col gap-2 p-4">
            <div className="flex flex-wrap items-center gap-1.5">
              <Chip tone="slate" square>
                {p.domain}
              </Chip>
              {why.map((reason) => (
                <Chip key={reason} tone={whyTone(reason)}>
                  {reason}
                </Chip>
              ))}
            </div>
            <Link href={`/problems/${p.slug}`} className="leading-snug font-medium wrap-anywhere">
              <Markdown inline noLinks>
                {p.title}
              </Markdown>
            </Link>
            {p.variants ? (
              <p className="-mt-1 text-xs">
                <Link
                  href={`/problems?${new URLSearchParams({ q: p.title.replace(/[*_`$\\]/g, "") })}`}
                  className="text-muted"
                >
                  and {p.variants} related {p.variants === 1 ? "variant" : "variants"}
                </Link>
              </p>
            ) : null}
            {p.best_known ? (
              <p className="text-sm">
                <span className="text-muted">Best known: </span>
                <span className="font-medium tabular-nums">{p.best_known}</span>
              </p>
            ) : null}
            {p.xp_bounty > 0 || p.prize_usd ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <BountyChip xp={p.xp_bounty} />
                <PrizeChip usd={p.prize_usd} />
              </div>
            ) : null}
            <div className="mt-auto flex items-end justify-between gap-3 pt-1">
              <p className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
                {p.formal_statement ? (
                  <Chip
                    tone="sky"
                    square
                    description="The problem has a formal statement in Lean, so a solution can be machine-checked."
                  >
                    Lean statement
                  </Chip>
                ) : null}
                <span>{activity(p)}</span>
              </p>
              <CopyButton
                text={problemPrompt(p.slug, p.title)}
                label="Copy prompt"
                className="btn shrink-0 px-2 py-0.5 text-xs"
                title="Copy a prompt that sends your AI agent to this problem"
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}
