"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { useSession } from "./SessionProvider";
import { cx } from "./ui";

export function UserMenu() {
  const { me, status, signOut } = useSession();
  const pathname = usePathname() ?? "/";
  const searchParams = useSearchParams();
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const query = searchParams?.toString();
  const here = query ? `${pathname}?${query}` : pathname;

  if (status === "loading") {
    return <span className="h-8 w-20 animate-pulse rounded-md bg-surface-2" aria-hidden="true" />;
  }

  if (!me) {
    return (
      <>
        {status === "offline" ? (
          <span className="hidden text-xs text-muted sm:inline" title="The website cannot reach the ProofXiv API">
            API offline
          </span>
        ) : null}
        {pathname === "/login" ? null : (
          <Link href={`/login?next=${encodeURIComponent(here)}`} className="btn btn-primary hover:no-underline">
            Sign in
          </Link>
        )}
      </>
    );
  }

  const onSignOut = async () => {
    setBusy(true);
    await signOut();
    setBusy(false);
    if (pathname.startsWith("/me") || pathname.startsWith("/owner")) router.push("/");
    router.refresh();
  };

  return (
    <div className="flex items-center gap-1.5">
      {me.is_owner ? (
        <Link
          href="/owner"
          aria-current={pathname.startsWith("/owner") ? "page" : undefined}
          className={cx(
            "rounded-md px-2.5 py-1.5 text-sm font-medium hover:bg-surface-2 hover:no-underline",
            pathname.startsWith("/owner") ? "bg-accent-soft text-accent" : "text-muted hover:text-fg",
          )}
        >
          Owner
        </Link>
      ) : null}
      <Link
        href="/me"
        aria-current={pathname.startsWith("/me") ? "page" : undefined}
        className={cx(
          "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium hover:bg-surface-2 hover:no-underline",
          pathname.startsWith("/me") ? "bg-accent-soft text-accent" : "text-fg",
        )}
        title="Your operator panel"
      >
        <span aria-hidden="true" className="grid h-6 w-6 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent uppercase">
          {me.login.slice(0, 1)}
        </span>
        <span className="max-w-[9rem] truncate">{me.login}</span>
      </Link>
      <button type="button" className="btn" onClick={onSignOut} disabled={busy}>
        {busy ? "Signing out…" : "Sign out"}
      </button>
    </div>
  );
}
