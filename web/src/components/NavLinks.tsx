"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cx } from "./ui";

function isActive(pathname: string, href: string): boolean {
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function NavLinks({ items }: { items: { href: string; label: string; short?: string }[] }) {
  const pathname = usePathname() ?? "/";
  return (
    <ul className="flex items-center gap-0.5 whitespace-nowrap">
      {items.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <li key={item.href}>
            <Link
              href={item.href}
              aria-current={active ? "page" : undefined}
              className={cx(
                "block rounded-md px-2 py-1.5 text-sm font-medium hover:bg-surface-2 hover:no-underline sm:px-2.5",
                active ? "bg-accent-soft text-accent" : "text-muted hover:text-fg",
              )}
            >
              {item.short ? (
                <>
                  <span className="lg:hidden">{item.short}</span>
                  <span className="hidden lg:inline">{item.label}</span>
                </>
              ) : (
                item.label
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
