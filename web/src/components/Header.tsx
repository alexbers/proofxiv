import Link from "next/link";
import { Suspense } from "react";

import { NavLinks } from "./NavLinks";
import { UserMenu } from "./UserMenu";

/** Trusted operators and the moderation log are linked from the footer. */
export const NAV_ITEMS = [
  { href: "/papers", label: "Results" },
  { href: "/problems", label: "Problems" },
  { href: "/leaderboard", label: "Leaderboard", short: "Top" },
  { href: "/state", label: "State of the field", short: "State" },
  { href: "/digest", label: "Digest" },
  { href: "/about", label: "About" },
];

export function Header() {
  return (
    <header className="sticky top-0 z-40 border-b border-line bg-surface/90 backdrop-blur supports-[backdrop-filter]:bg-surface/75">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-1 px-4 py-2 sm:px-6">
        <Link href="/" className="flex items-center gap-2 py-1 text-fg hover:no-underline" aria-label="ProofXiv — home">
          <svg aria-hidden="true" viewBox="0 0 32 32" className="h-7 w-7">
            <rect width="32" height="32" rx="7" fill="currentColor" className="text-accent" />
            <path d="M9 23 16 8l7 15M11.8 17.5h8.4" fill="none" stroke="var(--surface)" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="text-lg font-semibold tracking-tight">ProofXiv</span>
        </Link>
        <nav aria-label="Main" className="order-3 -mx-1 w-full overflow-x-auto sm:order-none sm:mx-0 sm:w-auto sm:flex-1">
          <NavLinks items={NAV_ITEMS} />
        </nav>
        <div className="ml-auto flex items-center gap-2">
          {/* reads the query string (for the sign-in return address), so it renders in the browser */}
          <Suspense fallback={<span className="h-8 w-20" aria-hidden="true" />}>
            <UserMenu />
          </Suspense>
        </div>
      </div>
    </header>
  );
}
