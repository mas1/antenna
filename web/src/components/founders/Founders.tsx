"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useMemo, useState } from "react";
import type { PersonRow, RoleGroup } from "@/lib/data";
import { SECTOR_LABEL, compact } from "@/lib/format";
import { setQuery, useQuery } from "@/lib/urlState";

const PAGE = 80;
const EASE = [0.16, 1, 0.3, 1] as const;
type View = "founders" | "researchers" | "everyone";

/** `group` is the role class a view keeps; null keeps every class. */
const VIEWS: { key: View; label: string; hint: string; group: RoleGroup | null }[] = [
  {
    key: "founders",
    label: "Founders and officers",
    hint: "Founders, executives and directors, and the people a research brief names",
    group: "founder",
  },
  {
    key: "researchers",
    label: "Researchers",
    hint: "Authors of papers under the company's name and principal investigators on its awards",
    group: "researcher",
  },
  {
    key: "everyone",
    label: "Everyone",
    hint: "Every person named on a ranked company, including filing contacts and code contributors",
    group: null,
  },
];

/**
 * A web search for the person at the company, for when no source gave a profile.
 * The name is quoted, so a nickname in brackets or a trailing ", PhD" would have
 * to appear on the page word for word: both are left out of the query.
 */
function searchUrl(r: PersonRow): string {
  const name =
    r.name
      .replace(/\s*\([^)]*\)/g, "")
      .replace(/,.*$/, "")
      .replace(/"/g, "")
      .trim() || r.name;
  return `https://www.google.com/search?q=${encodeURIComponent(`"${name}" ${r.company}`)}`;
}

export function Founders({ rows }: { rows: PersonRow[] }) {
  const params = useQuery();
  const reduce = useReducedMotion();

  // The view and the search live in the URL, so they survive a trip to a dossier and back.
  const view = VIEWS.find((v) => v.key === params.get("view")) ?? VIEWS[0];
  const query = params.get("q") ?? "";
  const viewKey = params.toString();

  const matched = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.company.toLowerCase().includes(q) ||
        (r.role ?? "").toLowerCase().includes(q) ||
        (r.bio ?? "").toLowerCase().includes(q) ||
        r.pedigree.some((p) => p.toLowerCase().includes(q)) ||
        r.affiliations.some((a) => a.toLowerCase().includes(q)),
    );
  }, [rows, query]);

  // Counts follow the search, so a query shows which view holds its matches.
  const counts = useMemo(() => {
    const c: Record<View, number> = { founders: 0, researchers: 0, everyone: matched.length };
    for (const r of matched) {
      if (r.group === "founder") c.founders += 1;
      else if (r.group === "researcher") c.researchers += 1;
    }
    return c;
  }, [matched]);

  const filtered = useMemo(
    () => (view.group ? matched.filter((r) => r.group === view.group) : matched),
    [matched, view],
  );

  // Paging resets whenever the view or the search changes.
  const [page, setPage] = useState({ key: "", shown: PAGE });
  const shown = page.key === viewKey ? page.shown : PAGE;

  return (
    <div>
      <div className="flex flex-col gap-4 border-b border-ink pb-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex flex-wrap gap-x-5 gap-y-2" role="group" aria-label="View">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              type="button"
              title={v.hint}
              aria-pressed={view.key === v.key}
              onClick={() => setQuery({ view: v === VIEWS[0] ? null : v.key })}
              className={`relative pb-1 text-[14px] transition-colors duration-150 hover:text-ink ${
                view.key === v.key ? "text-ink" : "text-ink-3"
              }`}
            >
              {v.label}
              <span className="num ml-1.5 text-[11px] text-ink-4">{counts[v.key]}</span>
              <span
                className={`absolute inset-x-0 bottom-0 h-px origin-left bg-ink transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                  view.key === v.key ? "scale-x-100" : "scale-x-0"
                }`}
              />
            </button>
          ))}
        </div>
        <label className="block w-full lg:w-80">
          <span className="sr-only">Search people</span>
          <input
            type="search"
            autoComplete="off"
            enterKeyHint="search"
            value={query}
            onChange={(e) => setQuery({ q: e.target.value })}
            placeholder="Search a name, company, lab or background"
            className="w-full border-b border-rule-2 bg-transparent py-2 text-[14px] outline-none transition-colors duration-200 placeholder:text-ink-4 focus:border-ink"
          />
        </label>
      </div>

      {/* One fade when the view changes. Rows are in the server HTML as they are, and typing does not blink them. */}
      <AnimatePresence mode="wait" initial={false}>
        <motion.ol
          key={view.key}
          initial={reduce ? false : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={reduce ? undefined : { opacity: 0, transition: { duration: 0.1 } }}
          transition={{ duration: 0.35, ease: EASE }}
        >
          {filtered.slice(0, shown).map((r) => (
            <li
              key={`${r.slug}-${r.name}`}
              className="grid grid-cols-12 items-baseline gap-x-5 gap-y-1 border-b border-rule py-4"
            >
              <div className="col-span-12 sm:col-span-4 lg:col-span-3">
                {r.link ? (
                  <a
                    href={r.link}
                    target="_blank"
                    rel="noreferrer"
                    className="font-serif text-[19px] leading-tight underline decoration-transparent decoration-1 underline-offset-4 transition-colors hover:decoration-ink"
                  >
                    {r.name}
                  </a>
                ) : (
                  <p className="flex flex-wrap items-baseline gap-x-3">
                    <span className="font-serif text-[19px] leading-tight">{r.name}</span>
                    <a
                      href={searchUrl(r)}
                      target="_blank"
                      rel="noreferrer"
                      title={`Search the web for ${r.name} at ${r.company}`}
                      aria-label={`Search the web for ${r.name} at ${r.company}`}
                      className="label -my-2 py-2 underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:!text-ink hover:decoration-ink"
                    >
                      Search ↗
                    </a>
                  </p>
                )}
                {r.role && <p className="label mt-1">{r.role}</p>}
                {r.origin && <p className="mt-1 text-[12.5px] leading-snug text-ink-3">{r.origin}</p>}
              </div>
              <div className="col-span-12 sm:col-span-4 lg:col-span-3">
                <Link
                  href={`/c/${r.slug}/`}
                  className="text-[14.5px] underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink"
                >
                  {r.company}
                </Link>
                <p className="label mt-1">
                  {SECTOR_LABEL[r.sector]} · Rank {String(r.rank).padStart(2, "0")}
                </p>
              </div>
              <div className="col-span-12 space-y-1 sm:col-span-4 lg:col-span-6">
                {r.pedigree.length > 0 && (
                  <p className="flex flex-wrap gap-x-3 gap-y-1">
                    {r.pedigree.map((o) => (
                      <span key={o} className="label !text-ink">
                        {o}
                      </span>
                    ))}
                  </p>
                )}
                {(r.affiliations.length > 0 || r.hIndex != null) && (
                  <p className="text-[13px] leading-snug text-ink-3">
                    {r.affiliations.slice(0, 3).join(" · ")}
                    {r.affiliations.length > 0 && r.hIndex != null && " · "}
                    {r.hIndex != null && (
                      <span title={r.citations ? `${compact(r.citations)} citations` : undefined}>
                        h-index <span className="num text-ink">{r.hIndex}</span>
                      </span>
                    )}
                  </p>
                )}
                {r.bio && <p className="line-clamp-2 text-[13.5px] leading-snug text-ink-2">{r.bio}</p>}
              </div>
            </li>
          ))}
        </motion.ol>
      </AnimatePresence>

      {filtered.length === 0 && (
        <p className="border-b border-rule py-16 text-center text-[14px] text-ink-3">
          No one matches
          {counts.everyone > 0 ? (
            <>
              {" "}
              in this view.{" "}
              <button
                type="button"
                onClick={() => setQuery({ view: "everyone" })}
                className="text-ink underline decoration-1 underline-offset-4"
              >
                Look in everyone
              </button>
              .
            </>
          ) : (
            "."
          )}
        </p>
      )}

      <div className="mt-6 flex items-center justify-between">
        <p className="label">
          Showing {Math.min(shown, filtered.length)} of {filtered.length}
        </p>
        {filtered.length > shown && (
          <button
            type="button"
            onClick={() => setPage({ key: viewKey, shown: shown + PAGE })}
            className="label border border-ink px-4 py-2 !text-ink transition-colors duration-150 hover:bg-ink hover:!text-paper"
          >
            Show {Math.min(PAGE, filtered.length - shown)} more
          </button>
        )}
      </div>
    </div>
  );
}
