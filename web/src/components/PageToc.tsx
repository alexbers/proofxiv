"use client";

import { useEffect, useState } from "react";

import { cx } from "./ui";

// just below the sticky header, where a link to #section puts the section's top (scroll-mt-20)
const LINE = 100;

/** The section being read: the last one whose top has passed the line; at the bottom of the page,
 * the last section (a short one never reaches the line). */
function currentSection(ids: string[]): string | null {
  const sections = ids.map((id) => document.getElementById(id)).filter((el): el is HTMLElement => el !== null);
  if (!sections.length) return null;
  if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
    return sections[sections.length - 1].id;
  }
  let current = sections[0].id;
  for (const section of sections) {
    if (section.getBoundingClientRect().top <= LINE) current = section.id;
  }
  return current;
}

export function PageToc({ items, className }: { items: { id: string; label: string }[]; className?: string }) {
  const [active, setActive] = useState<string | null>(null);
  const ids = items.map((item) => item.id).join(" ");

  useEffect(() => {
    const list = ids.split(" ");
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setActive(currentSection(list)));
    };
    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, [ids]);

  return (
    <nav className={cx("card p-4", className)} aria-label="On this page">
      <h2 className="font-semibold">On this page</h2>
      <ul className="mt-2 space-y-0.5">
        {items.map((item) => (
          <li key={item.id}>
            <a
              href={`#${item.id}`}
              aria-current={item.id === active ? "location" : undefined}
              className={cx(
                "-mx-2 block rounded-md px-2 py-0.5 hover:no-underline",
                item.id === active ? "bg-accent-soft font-medium text-accent" : "hover:bg-surface-2",
              )}
            >
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
