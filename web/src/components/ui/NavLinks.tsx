"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

// A phone header fits three links and the search. Method gives way: it is
// in the search, and the footer links to it.
const LINKS = [
  { href: "/", label: "Board", phone: true },
  { href: "/wire/", label: "Wire", phone: true },
  { href: "/founders/", label: "Founders", phone: true },
  { href: "/method/", label: "Method", phone: false },
];

export function NavLinks() {
  const path = usePathname();
  return (
    <nav aria-label="Primary" className="flex items-center gap-4 sm:gap-7">
      {LINKS.map((l) => {
        const active = l.href === "/" ? path === "/" || path.startsWith("/c/") : path.startsWith(l.href);
        return (
          // As tall as the header, so the whole bar is the target and not an 11px word.
          <Link
            key={l.href}
            href={l.href}
            aria-current={active ? "page" : undefined}
            className={`label relative h-14 items-center transition-colors duration-200 hover:!text-ink ${
              active ? "!text-ink" : ""
            } ${l.phone ? "flex" : "hidden sm:flex"}`}
          >
            {l.label}
            <span
              className={`absolute inset-x-0 bottom-3.5 h-px origin-left bg-ink transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                active ? "scale-x-100" : "scale-x-0"
              }`}
            />
          </Link>
        );
      })}
    </nav>
  );
}
