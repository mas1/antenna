"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useRouter } from "next/navigation";
import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { SECTOR_LABEL } from "@/lib/format";
import type { Sector } from "@/lib/types";

export type PaletteItem = { slug: string; name: string; sector: Sector; rank: number; oneLiner: string | null };

const PAGES = [
  { href: "/", label: "Board" },
  { href: "/wire/", label: "Wire" },
  { href: "/founders/", label: "Founders" },
  { href: "/method/", label: "Method" },
];
const EASE = [0.16, 1, 0.3, 1] as const;

const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

/** Jump to any company or page. Opens with Cmd or Ctrl and K. */
export function CommandPalette() {
  const [items, setItems] = useState<PaletteItem[]>([]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  // What had focus when the palette opened, to hand it back on close.
  const openerRef = useRef<HTMLElement | null>(null);
  const router = useRouter();
  const reduce = useReducedMotion();

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    const pages = PAGES.filter((p) => !q || p.label.toLowerCase().includes(q)).map((p) => ({
      key: p.href,
      href: p.href,
      title: p.label,
      meta: "Page",
      sub: null as string | null,
    }));
    const companies = items
      .filter((it) => !q || it.name.toLowerCase().includes(q) || (it.oneLiner ?? "").toLowerCase().includes(q))
      .slice(0, q ? 12 : 6)
      .map((it) => ({
        key: it.slug,
        href: `/c/${it.slug}/`,
        title: it.name,
        meta: `${SECTOR_LABEL[it.sector]} · #${it.rank}`,
        sub: it.oneLiner,
      }));
    return q ? [...companies, ...pages] : [...pages, ...companies];
  }, [items, query]);

  const show = () => {
    openerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setOpen(true);
  };
  const close = () => {
    setOpen(false);
    setQuery("");
    setCursor(0);
    // It had focus before, so it is where the reader left it: do not scroll to it.
    openerRef.current?.focus({ preventScroll: true });
    openerRef.current = null;
  };
  const go = (href: string) => {
    close();
    router.push(href);
  };

  // Every key is handled here, on the window and in the capture phase, and
  // goes no further while the palette is open. The page behind has shortcuts
  // of its own (j, k, Enter, /) and none of them may act through the overlay;
  // and Escape has to work wherever the focus happens to be.
  const onKey = useEffectEvent((e: KeyboardEvent) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      e.stopPropagation();
      if (open) close();
      else show();
      return;
    }
    if (!open) return;
    e.stopPropagation();
    // A click on the dialog's edge takes focus off the input. Any key brings
    // it back, so typing carries on and Tab has nowhere else to go.
    if (document.activeElement !== inputRef.current) inputRef.current?.focus();
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "Tab") {
      e.preventDefault();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor((c) => Math.min(c + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor((c) => Math.max(c - 1, 0));
    } else if (e.key === "Enter" && !e.isComposing && results[cursor]) {
      e.preventDefault();
      go(results[cursor].href);
    }
  });

  useEffect(() => {
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // The company list is fetched the first time the palette opens.
  useEffect(() => {
    if (!open || items.length) return;
    let live = true;
    fetch(`${BASE}/palette.json`)
      .then((r) => (r.ok ? r.json() : []))
      .then((data: PaletteItem[]) => {
        if (live) setItems(data);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [open, items.length]);

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-i="${cursor}"]`)?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  return (
    <>
      <button
        type="button"
        onClick={show}
        aria-label="Search companies and pages"
        className="label flex h-14 items-center gap-2 border-rule transition-colors duration-150 hover:border-ink hover:!text-ink sm:h-auto sm:border sm:px-2.5 sm:py-1"
      >
        Search
        <kbd className="num hidden text-[10.5px] text-ink-5 md:inline">⌘K</kbd>
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            className="fixed inset-0 z-50 flex items-start justify-center bg-ink/20 px-4 pt-[14vh] backdrop-blur-[2px]"
            initial={reduce ? false : { opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, transition: { duration: 0.12 } }}
            transition={{ duration: 0.18 }}
            onMouseDown={(e) => {
              if (e.target !== e.currentTarget) return;
              // Or the click would take focus straight back off the opener.
              e.preventDefault();
              close();
            }}
          >
            <motion.div
              role="dialog"
              aria-modal="true"
              aria-label="Search"
              className="w-full max-w-[620px] border border-ink bg-paper"
              initial={reduce ? false : { opacity: 0, y: -8, scale: 0.985 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -4, transition: { duration: 0.1 } }}
              transition={{ duration: 0.28, ease: EASE }}
            >
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setCursor(0);
                }}
                placeholder="Search companies and pages"
                // The dialog's own border is the focus mark: the input is the
                // only stop inside it, and the global ring would stick out
                // past the frame.
                className="w-full border-b border-rule bg-transparent px-5 py-4 font-serif text-[20px] !outline-none placeholder:text-ink-5"
                role="combobox"
                aria-expanded="true"
                aria-controls="palette-list"
                aria-activedescendant={results[cursor] ? `palette-${cursor}` : undefined}
              />
              <ul id="palette-list" ref={listRef} role="listbox" className="max-h-[46vh] overflow-y-auto py-1">
                {results.map((r, i) => (
                  <li
                    key={r.key}
                    id={`palette-${i}`}
                    data-i={i}
                    role="option"
                    aria-selected={i === cursor}
                    // Move, not enter: a list scrolled by the arrow keys slides
                    // rows under a pointer that has not moved.
                    onMouseMove={() => setCursor(i)}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      go(r.href);
                    }}
                    className={`flex cursor-pointer items-baseline justify-between gap-4 px-5 py-2.5 ${
                      i === cursor ? "bg-paper-2" : ""
                    }`}
                  >
                    {/* The name is kept whole and the one-liner takes what is left. */}
                    <span className="flex min-w-0 items-baseline gap-3">
                      <span className="font-serif text-[17px] sm:max-w-full sm:shrink-0 sm:truncate">{r.title}</span>
                      {r.sub && <span className="hidden min-w-0 truncate text-[13px] text-ink-3 sm:block">{r.sub}</span>}
                    </span>
                    <span className="label shrink-0">{r.meta}</span>
                  </li>
                ))}
                {results.length === 0 && <li className="px-5 py-6 text-[14px] text-ink-3">Nothing matches.</li>}
              </ul>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
