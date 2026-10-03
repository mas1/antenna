"use client";

import { AnimatePresence, motion, useIsPresent, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { memo, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { CountUp } from "@/components/ui/CountUp";
import { FamilyGlyph } from "@/components/ui/FamilyGlyph";
import { RankDelta } from "@/components/ui/RankDelta";
import { Sparkline } from "@/components/ui/Sparkline";
import { StarButton } from "@/components/ui/StarButton";
import { FAMILY_BLURB, FAMILY_LABEL, SECTOR_LABEL, ago, compact, shortDate } from "@/lib/format";
import { useLastRun, useStarred } from "@/lib/local";
import { FAMILIES, SECTORS, type BoardRow, type Family, type Sector, type Why } from "@/lib/types";
import { listParam, setQuery, toggleInList, useQuery } from "@/lib/urlState";

/** What the board reads from a row. Every field is inlined into the page, so nothing else is sent. */
export type ClientRow = Pick<
  BoardRow,
  | "slug"
  | "name"
  | "oneLiner"
  | "domain"
  | "location"
  | "sector"
  | "rank"
  | "rankPrev"
  | "edge"
  | "momentum"
  | "fit"
  | "earliness"
  | "families"
  | "history"
  | "why"
  | "latest"
  | "founded"
  | "raisedUsd"
  | "bySignalFamily"
  | "lastSignalAt"
  | "flags"
  | "hasBrief"
  | "stage"
>;

type SortKey = "edge" | "momentum" | "mover" | "recent";
type Stage = NonNullable<BoardRow["stage"]>;
/** Row flags from the pipeline, plus three that only this browser knows. */
type Mark = BoardRow["flags"][number] | "brief" | "starred" | "changed";

const SORTS: { key: SortKey; label: string; hint?: string }[] = [
  { key: "edge", label: "Edge" },
  { key: "momentum", label: "Momentum", hint: "Strength of recent signals, before thesis fit, earliness and team" },
  { key: "mover", label: "Biggest movers" },
  { key: "recent", label: "Latest signal" },
];

const STAGES: { key: Stage; label: string; hint: string }[] = [
  { key: "formation", label: "Formation", hint: "Pre-seed or seed" },
  { key: "early", label: "Early", hint: "Series A or B" },
  { key: "growth", label: "Growth", hint: "Later stage, already well known" },
];
const STAGE_LABEL: Record<Stage, string> = {
  formation: "Formation",
  early: "Early",
  growth: "Growth",
  incumbent: "Established",
};

const MARK_LABEL: Record<Mark, string> = {
  new: "New",
  convergent: "2+ sources",
  "pre-consensus": "Pre-consensus",
  pedigree: "Pedigree",
  brief: "Brief",
  starred: "Starred",
  changed: "Since last visit",
};
// The show-only toggles: the marks no tile filters by. Pre-consensus is left
// out: it selects almost exactly the formation stage. A saved link that
// carries it still filters.
const MARKS: { key: Mark; hint: string }[] = [{ key: "pedigree", hint: "Team from a top lab or company" }];
const ALL_MARKS: Mark[] = ["new", "convergent", "pre-consensus", "pedigree", "brief", "starred", "changed"];

const PAGE = 60;
const FIRING = 0.12;
const EASE = [0.16, 1, 0.3, 1] as const;
// Three columns on a phone (rank, company, edge), five from sm (with the
// week's change and the families), all seven only from lg: any narrower and
// the fixed columns crush the company.
const COLS =
  "grid grid-cols-[2.25rem_minmax(0,1fr)_3.5rem] gap-x-3 max-sm:items-start sm:grid-cols-[2.5rem_2.75rem_minmax(0,1fr)_5.5rem_4rem] sm:items-center sm:gap-x-5 lg:grid-cols-[2.5rem_2.75rem_minmax(0,1fr)_9rem_5.5rem_6.5rem_4rem]";

/** Places gained in a week. A company that was not ranked outranks any gain, in rank order. */
function moved(r: ClientRow): number {
  return r.rankPrev == null ? Number.MAX_SAFE_INTEGER - r.rank : r.rankPrev - r.rank;
}

/** The signal printed under the name: the strongest, or the newest dated one when sorting by date. */
function lineOf(r: ClientRow, sort: SortKey): Why | undefined {
  return (sort === "recent" && r.latest) || r.why[0];
}

function csvCell(v: string | number | null | undefined): string {
  const s = v == null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

// The list the reader is working, left in sessionStorage for the dossier: it
// steps through these slugs and links back to this search. One per tab.
const VIEW_KEY = "antenna:view";

function saveView(view: { search: string; slugs: string[]; shown: number }) {
  try {
    window.sessionStorage.setItem(VIEW_KEY, JSON.stringify(view));
  } catch {
    // storage disabled: the dossier falls back to rank order
  }
}

/** How many rows to start with: what the stored view had rendered when it is this URL's view. */
function restoredShown(): number {
  try {
    const view = JSON.parse(window.sessionStorage.getItem(VIEW_KEY) ?? "null");
    if (view?.search === window.location.search && Number.isFinite(view.shown)) {
      return Math.max(PAGE, Math.ceil(view.shown / PAGE) * PAGE);
    }
  } catch {
    // unreadable or disabled: start from the first page
  }
  return PAGE;
}

// Only the board writes the stored view, so there is nothing to subscribe to.
const unchanging = () => () => {};

type Page = { key: string | null; shown: number; cursor: number };

export function Board({
  rows,
  asOf,
  children,
}: {
  rows: ClientRow[];
  asOf: string;
  /** Rendered between the quick filters and the table: the lead stories. */
  children?: React.ReactNode;
}) {
  const params = useQuery();
  const [starred, toggleStar] = useStarred();
  const lastRun = useLastRun(asOf);
  const router = useRouter();
  const reduce = useReducedMotion();
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // --- the view, read from the URL -------------------------------------
  const sectorParam = params.get("sector");
  const sector: Sector | "all" = (SECTORS as readonly string[]).includes(sectorParam ?? "")
    ? (sectorParam as Sector)
    : "all";
  const stageParam = params.get("stage");
  const stage: Stage | "any" = STAGES.some((s) => s.key === stageParam) ? (stageParam as Stage) : "any";
  const sortParam = params.get("sort");
  const sort: SortKey = SORTS.some((s) => s.key === sortParam) ? (sortParam as SortKey) : "edge";
  const query = params.get("q") ?? "";
  const viewKey = params.toString();
  const marks = useMemo(() => listParam(params, "show") as Set<Mark>, [params]);
  const fams = useMemo(() => listParam(params, "fam") as Set<Family>, [params]);
  const filtering = sector !== "all" || stage !== "any" || marks.size > 0 || fams.size > 0 || query !== "";

  const starSet = useMemo(() => new Set(starred), [starred]);
  const has = useMemo(
    () => (r: ClientRow, m: Mark) => {
      if (m === "brief") return Boolean(r.hasBrief);
      if (m === "starred") return starSet.has(r.slug);
      if (m === "changed") return lastRun != null && r.lastSignalAt > lastRun;
      return r.flags.includes(m);
    },
    [starSet, lastRun],
  );

  // --- counts for the tiles and chips -----------------------------------
  const counts = useMemo(() => {
    const bySector: Partial<Record<Sector, number>> = {};
    const byStage: Partial<Record<Stage, number>> = {};
    const byFamily: Partial<Record<Family, number>> = {};
    const byMark: Partial<Record<Mark, number>> = {};
    for (const r of rows) {
      bySector[r.sector] = (bySector[r.sector] ?? 0) + 1;
      if (r.stage) byStage[r.stage] = (byStage[r.stage] ?? 0) + 1;
      for (const f of FAMILIES) if (r.families[f] >= FIRING) byFamily[f] = (byFamily[f] ?? 0) + 1;
      for (const m of ALL_MARKS) if (has(r, m)) byMark[m] = (byMark[m] ?? 0) + 1;
    }
    return { bySector, byStage, byFamily, byMark };
  }, [rows, has]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const out = rows.filter((r) => {
      if (sector !== "all" && r.sector !== sector) return false;
      if (stage !== "any" && r.stage !== stage) return false;
      for (const m of marks) if (!has(r, m)) return false;
      for (const f of fams) if (!(r.families[f] >= FIRING)) return false;
      if (!q) return true;
      return (
        r.name.toLowerCase().includes(q) ||
        (r.oneLiner ?? "").toLowerCase().includes(q) ||
        (r.domain ?? "").includes(q) ||
        (r.location ?? "").toLowerCase().includes(q) ||
        r.why.some((w) => w.title.toLowerCase().includes(q))
      );
    });
    if (sort === "momentum") out.sort((a, b) => b.momentum - a.momentum);
    else if (sort === "mover") out.sort((a, b) => moved(b) - moved(a) || b.edge - a.edge);
    else if (sort === "recent")
      out.sort((a, b) => b.lastSignalAt.localeCompare(a.lastSignalAt) || b.edge - a.edge);
    else out.sort((a, b) => a.rank - b.rank);
    return out;
  }, [rows, sector, stage, marks, fams, sort, query, has]);

  // Paging and the keyboard cursor reset whenever the view changes. A view
  // the reader left for a dossier comes back with as many rows as it had.
  // The key is null on the server and while hydrating, before the URL and
  // the stored view can be read.
  const restored = useSyncExternalStore(unchanging, restoredShown, () => null);
  const pageKey = restored == null ? null : viewKey;
  const [page, setPage] = useState<Page>({ key: null, shown: PAGE, cursor: -1 });
  const cur = page.key === pageKey ? page : { key: pageKey, shown: restored ?? PAGE, cursor: -1 };
  if (cur !== page) setPage(cur);
  const { shown, cursor } = cur;
  const visible = useMemo(() => filtered.slice(0, shown), [filtered, shown]);

  useEffect(() => {
    if (pageKey == null) return;
    // A link to the board renders it before the address changes. That render
    // is of the address being left: storing it would overwrite the view this
    // one is about to restore.
    if (new URLSearchParams(window.location.search).toString() !== pageKey) return;
    saveView({
      search: window.location.search,
      slugs: filtered.map((r) => r.slug),
      shown: Math.min(shown, filtered.length),
    });
  }, [pageKey, filtered, shown]);

  // The cursor is real focus: it follows whichever row holds focus, so Enter,
  // Cmd+Enter and Tab all start from the row that looks selected.
  const setCursor = useCallback(
    (to: number) => setPage((p) => (p.cursor === to ? p : { ...p, cursor: to })),
    [],
  );
  const focusRow = useCallback(
    (to: number) => {
      const head = listRef.current?.firstElementChild;
      const row = listRef.current?.querySelector<HTMLElement>(`[data-row="${to}"]`);
      if (!head || !row || row.contains(document.activeElement)) return;
      row.querySelector<HTMLElement>("a")?.focus({ preventScroll: true });
      // Scroll by hand, and only as far as it takes: the row comes to rest
      // just under the column heads or just above the bottom edge. The heads
      // stick below the site header, so their offset plus their height is
      // what the row has to clear.
      const clear = (parseFloat(getComputedStyle(head).top) || 0) + head.getBoundingClientRect().height;
      const box = row.getBoundingClientRect();
      const under = box.top - clear;
      const over = box.bottom + 16 - window.innerHeight;
      const by = under < 0 ? Math.floor(under) : Math.max(0, Math.min(Math.ceil(over), Math.floor(under)));
      if (by) window.scrollBy({ top: by, behavior: reduce ? "auto" : "smooth" });
    },
    [reduce],
  );

  // A cursor moved past the rendered rows takes focus once its row exists.
  useEffect(() => {
    if (cursor >= 0) focusRow(cursor);
  }, [cursor, focusRow]);

  // --- keyboard: "/" search, j and k move, o opens the source, s stars ----
  useEffect(() => {
    // With no cursor yet the keys start at the first row clear of the sticky
    // header, so a reader who scrolled, or came back from a dossier, carries
    // on from where they are looking and is not thrown back to the top.
    const firstInView = () => {
      const list = listRef.current;
      const head = list?.firstElementChild?.getBoundingClientRect().bottom ?? 0;
      const all = list?.querySelectorAll<HTMLElement>("[data-row]") ?? [];
      for (const el of all) if (el.getBoundingClientRect().top >= head - 1) return Number(el.dataset.row);
      return all.length - 1;
    };
    const step = (by: number) => {
      const to = cursor < 0 ? firstInView() : Math.min(Math.max(cursor + by, 0), filtered.length - 1);
      if (to < 0) return;
      if (to < visible.length) {
        // The row's focus event sets the cursor too, but a row that already
        // holds focus fires none.
        setCursor(to);
        focusRow(to);
      }
      // Past the last rendered row: bring in the next batch and carry on.
      else setPage((p) => ({ ...p, shown: p.shown + PAGE, cursor: to }));
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const el = e.target instanceof HTMLElement ? e.target : null;
      // The search box and the Cmd-K palette handle their own keys.
      if (el?.closest("input, textarea, select, [role=dialog]")) return;
      if (e.key === "/") {
        e.preventDefault();
        searchRef.current?.focus();
      } else if (e.key === "j" || (e.key === "ArrowDown" && cursor >= 0)) {
        // The arrows scroll the page until a cursor exists.
        e.preventDefault();
        step(1);
      } else if (e.key === "k" || (e.key === "ArrowUp" && cursor >= 0)) {
        e.preventDefault();
        step(-1);
      } else if (e.key === "Enter" || e.key === "s" || e.key === "o") {
        const row = visible[cursor];
        // A focused button or link keeps its own keys. A row's own links are
        // the cursor, so s and o work there; Enter is already theirs, which
        // is what lets Cmd+Enter open a tab.
        const control = el?.closest("a, button");
        const onRow = control?.tagName === "A" && control.closest("[data-row]") != null;
        if (!row || (control && (!onRow || e.key === "Enter"))) return;
        if (e.key === "Enter") router.push(`/c/${row.slug}/`);
        else if (e.key === "s") toggleStar(row.slug);
        else {
          const source = lineOf(row, sort);
          if (source) window.open(source.url, "_blank", "noopener,noreferrer");
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cursor, visible, filtered.length, sort, router, toggleStar, focusRow, setCursor]);

  const onSearchKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !e.nativeEvent.isComposing && visible[0]) {
      router.push(`/c/${visible[0].slug}/`);
    } else if (e.key === "ArrowDown" && visible[0]) {
      e.preventDefault();
      setCursor(0);
      focusRow(0);
    } else if (e.key === "Escape") {
      // Clear first; only an empty box lets go of focus.
      e.preventDefault();
      if (query) setQuery({ q: null });
      else e.currentTarget.blur();
    }
  };

  const clearAll = () => setQuery({ sector: null, stage: null, show: null, fam: null, q: null });
  const toggleMark = (m: Mark) => toggleInList(params, "show", m);

  const exportCsv = () => {
    const head = [
      "rank", "company", "sector", "stage", "founded", "raised_usd", "location", "domain",
      "edge", "momentum", "thesis_fit", "earliness", "families", "marks",
      "top_signal", "top_signal_date", "last_signal", "dossier",
    ];
    const lines = filtered.map((r) =>
      [
        r.rank, r.name, r.sector, r.stage ?? "", r.founded, r.raisedUsd, r.location ?? "", r.domain ?? "",
        r.edge, r.momentum, r.fit, r.earliness,
        FAMILIES.filter((f) => r.families[f] >= FIRING).map((f) => FAMILY_LABEL[f]).join("; "),
        r.flags.filter((f) => f !== "pre-consensus").map((f) => MARK_LABEL[f]).join("; "),
        r.why[0]?.title ?? "", r.why[0]?.occurredAt ?? "", r.lastSignalAt,
        new URL(`c/${r.slug}/`, document.baseURI).href,
      ]
        .map(csvCell)
        .join(","),
    );
    // The byte-order mark is what makes Excel read the file as UTF-8.
    const blob = new Blob(["\uFEFF", [head.join(","), ...lines].join("\n")], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `antenna-${asOf}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  // --- the tiles: numbers you can click ---------------------------------
  type Tile = { key: string; label: string; value: number; hint: string; active: boolean; onClick: () => void };
  const tiles: Tile[] = [
    { key: "all", label: "Ranked", value: rows.length, hint: "Every ranked company. Clears all filters.", active: !filtering, onClick: clearAll },
    { key: "new", label: "New this week", value: counts.byMark.new ?? 0, hint: "First signal in the last seven days", active: marks.has("new"), onClick: () => toggleMark("new") },
    { key: "convergent", label: "2+ sources", value: counts.byMark.convergent ?? 0, hint: "Signals in two or more families, such as capital and hiring", active: marks.has("convergent"), onClick: () => toggleMark("convergent") },
    { key: "formation", label: "Formation stage", value: counts.byStage.formation ?? 0, hint: "Pre-seed or seed", active: stage === "formation", onClick: () => setQuery({ stage: stage === "formation" ? null : "formation" }) },
    { key: "brief", label: "With a brief", value: counts.byMark.brief ?? 0, hint: "Research brief: summary, risks and open questions", active: marks.has("brief"), onClick: () => toggleMark("brief") },
  ];
  // Two tiles depend on this browser: Starred once a company is starred, Since once there is an earlier visit.
  // Starred also stays while its filter is on, so the filter can be switched off.
  if (counts.byMark.starred || marks.has("starred")) {
    tiles.push({
      key: "starred",
      label: "Starred",
      value: counts.byMark.starred ?? 0,
      hint: "Companies you starred in this browser",
      active: marks.has("starred"),
      onClick: () => toggleMark("starred"),
    });
  }
  if (lastRun) {
    tiles.push({
      key: "changed",
      label: `Since ${shortDate(lastRun, asOf)}`,
      value: counts.byMark.changed ?? 0,
      hint: "Companies with a new signal since your last visit",
      active: marks.has("changed"),
      onClick: () => toggleMark("changed"),
    });
  }
  // The last tile takes what is left of its row, so the two- and three-column grids end on a full row.
  const lastTile = [
    tiles.length % 2 === 1 ? "max-sm:col-span-2" : "",
    tiles.length % 3 === 1 ? "sm:max-lg:col-span-3" : tiles.length % 3 === 2 ? "sm:max-lg:col-span-2" : "",
  ].join(" ");

  const activeLabels: string[] = [
    ...(sector !== "all" ? [SECTOR_LABEL[sector]] : []),
    ...(stage !== "any" ? [STAGE_LABEL[stage]] : []),
    ...[...marks].map((m) => MARK_LABEL[m] ?? m),
    ...[...fams].map((f) => FAMILY_LABEL[f] ?? f),
    ...(query ? [`"${query}"`] : []),
  ];

  return (
    <div className="mx-auto max-w-[1320px] px-5 sm:px-8">
      {/* Tiles */}
      <div
        className="mt-6 grid grid-cols-2 border-l border-t border-rule sm:grid-cols-3 lg:[grid-template-columns:repeat(var(--n),minmax(0,1fr))]"
        style={{ "--n": tiles.length } as React.CSSProperties}
        role="group"
        aria-label="Quick filters"
      >
        {tiles.map((t, i) => (
          <button
            key={t.key}
            type="button"
            title={t.hint}
            aria-pressed={t.active}
            onClick={t.onClick}
            className={`group relative border-b border-r border-rule px-4 py-3.5 text-left transition-colors duration-150 hover:bg-paper-2 active:bg-paper-3 ${
              t.active ? "bg-paper-2" : ""
            } ${i === tiles.length - 1 ? lastTile : ""}`}
          >
            <span
              className={`label block whitespace-nowrap transition-colors ${t.active ? "!text-ink" : "group-hover:!text-ink"}`}
            >
              {t.label}
            </span>
            <span className="mt-1.5 block text-[26px] leading-none">
              <CountUp value={t.value} />
            </span>
            {/* The bar across the top: ink when the filter is on, a lighter rule on hover. */}
            <span
              aria-hidden
              className={`absolute inset-x-0 -top-px h-[2px] origin-left transition-[transform,background-color] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
                t.active ? "scale-x-100 bg-ink" : "scale-x-0 bg-rule-2 group-hover:scale-x-100"
              }`}
            />
          </button>
        ))}
      </div>

      {children}

      {/* Controls */}
      <section className="mt-10" aria-label="Ranked companies">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div className="flex flex-wrap gap-x-5 gap-y-2" role="group" aria-label="Sector">
            <Chip active={sector === "all"} onClick={() => setQuery({ sector: null })}>
              All
            </Chip>
            {SECTORS.filter((s) => counts.bySector[s]).map((s) => (
              <Chip
                key={s}
                active={sector === s}
                onClick={() => setQuery({ sector: sector === s ? null : s })}
                count={counts.bySector[s]}
              >
                {SECTOR_LABEL[s]}
              </Chip>
            ))}
          </div>
          <label className="relative block w-full lg:w-72">
            <span className="sr-only">Search companies</span>
            <input
              ref={searchRef}
              type="search"
              autoComplete="off"
              enterKeyHint="search"
              value={query}
              onChange={(e) => setQuery({ q: e.target.value })}
              onKeyDown={onSearchKey}
              placeholder="Search name, place, signal"
              title="Keys: / search, j and k move, enter open, o source, s star"
              className="w-full border-b border-rule-2 bg-transparent py-2 pr-8 text-[14px] !outline-none transition-colors duration-200 placeholder:text-ink-5 focus:border-ink"
            />
            <kbd className="num pointer-events-none absolute right-0 top-1/2 -translate-y-1/2 border border-rule px-1.5 text-[11px] text-ink-5">
              /
            </kbd>
          </label>
        </div>

        {/* Ruled off only from lg: below that the search box sits right above, with an underline of its own. */}
        <div className="mt-4 flex flex-wrap items-center justify-between gap-x-8 gap-y-3 lg:border-t lg:border-rule lg:pt-4">
          <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-3" role="group" aria-label="Stage">
              <span className="label !text-ink-4">Stage</span>
              <TextToggle active={stage === "any"} onClick={() => setQuery({ stage: null })}>
                Any
              </TextToggle>
              {STAGES.map((st) => (
                <TextToggle
                  key={st.key}
                  title={st.hint}
                  active={stage === st.key}
                  onClick={() => setQuery({ stage: stage === st.key ? null : st.key })}
                  count={counts.byStage[st.key] ?? 0}
                >
                  {st.label}
                </TextToggle>
              ))}
            </div>
            <div className="flex flex-wrap gap-x-5 gap-y-3" role="group" aria-label="Show only">
              {MARKS.map((f) => (
                <button
                  key={f.key}
                  type="button"
                  title={f.hint}
                  aria-pressed={marks.has(f.key)}
                  onClick={() => toggleMark(f.key)}
                  className={`label -my-2 flex items-center gap-2 py-2 transition-colors duration-150 hover:!text-ink ${
                    marks.has(f.key) ? "!text-ink" : ""
                  }`}
                >
                  <span
                    className={`inline-block size-[9px] border transition-colors duration-150 ${
                      marks.has(f.key) ? "border-ink bg-ink" : "border-rule-2"
                    }`}
                  />
                  {MARK_LABEL[f.key]}
                </button>
              ))}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-3" role="group" aria-label="Sort">
            <span className="label !text-ink-4">Sort</span>
            {SORTS.map((s) => (
              <TextToggle
                key={s.key}
                title={s.hint}
                active={sort === s.key}
                onClick={() => setQuery({ sort: s.key === "edge" ? null : s.key })}
              >
                {s.label}
              </TextToggle>
            ))}
          </div>
        </div>

        {/* Families: a filter, and the key to the eight boxes on each row. */}
        <div
          className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-3 border-t border-rule pt-4"
          role="group"
          aria-label="Signal family"
        >
          <span className="label !text-ink-4">Families</span>
          {FAMILIES.map((f) => (
            <TextToggle
              key={f}
              title={FAMILY_BLURB[f]}
              active={fams.has(f)}
              onClick={() => toggleInList(params, "fam", f)}
              count={counts.byFamily[f] ?? 0}
            >
              {FAMILY_LABEL[f]}
            </TextToggle>
          ))}
        </div>

        {/* Table */}
        <div role="table" aria-rowcount={filtered.length} className="mt-6" ref={listRef}>
          <div role="row" className={`${COLS} sticky top-14 z-20 border-b border-ink bg-paper pb-2 pt-3`}>
            <span role="columnheader" className="label">
              #
            </span>
            <span role="columnheader" className="label hidden sm:block" title="Rank change over seven days">
              7d
            </span>
            <span role="columnheader" className="flex min-w-0 items-baseline gap-3">
              {/* Below xl the summary of the active filters needs the room. */}
              <span className={`label shrink-0 ${filtering ? "max-xl:hidden" : ""}`}>Company and why now</span>
              {filtering && (
                <>
                  {/* The names give way first; the count always shows. */}
                  <span className="flex min-w-0 items-baseline text-[12.5px] text-ink">
                    <span className="truncate">{activeLabels.join(" · ")}</span>
                    <span className="shrink-0 whitespace-nowrap">
                      : {filtered.length} of {rows.length}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={clearAll}
                    className="label -my-2 shrink-0 py-2 !text-ink underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink"
                  >
                    Clear
                  </button>
                </>
              )}
            </span>
            <span role="columnheader" className="label hidden lg:block" title="Stage, and total raised where known">
              Sector, stage
            </span>
            <span
              role="columnheader"
              className="label hidden sm:block"
              title="One box per family, in the order of the Families filter above. Darker is stronger."
            >
              Families
            </span>
            <span
              role="columnheader"
              className="label hidden lg:block"
              title="Edge score over the last twelve weeks, each row drawn to its own scale"
            >
              Edge, 12w
            </span>
            <span
              role="columnheader"
              className="label text-right"
              title="Score from 0 to 100: strength of recent signals, scaled by thesis fit, how early the company is, and team. The dossier shows the working."
            >
              Edge
            </span>
          </div>

          {/* No entrance on first paint: those rows are the server's HTML. Rows
              that arrive later fade in at once, with no stagger. A row that
              stays put is not re-rendered when another leaves; one that moves
              has a new index, and is measured then. */}
          <AnimatePresence initial={false} mode="popLayout" presenceAffectsLayout={false}>
            {visible.map((r, i) => (
              <Row
                key={r.slug}
                r={r}
                i={i}
                line={lineOf(r, sort)}
                asOf={asOf}
                isCursor={cursor === i}
                isStarred={starSet.has(r.slug)}
                changed={has(r, "changed")}
                reduce={reduce}
                onStar={toggleStar}
                onCursor={setCursor}
              />
            ))}
          </AnimatePresence>

          {filtered.length === 0 && (
            <p className="border-b border-rule py-16 text-center text-[14px] text-ink-3">
              Nothing matches.{" "}
              <button
                type="button"
                onClick={clearAll}
                className="text-ink underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink"
              >
                Clear filters
              </button>
            </p>
          )}
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-end gap-3">
          {filtered.length > shown && (
            <span className="label">
              {visible.length} of {filtered.length}
            </span>
          )}
          <button
            type="button"
            onClick={exportCsv}
            className="label border border-rule px-4 py-2 transition-colors duration-150 hover:border-ink hover:!text-ink active:bg-paper-3"
            title="All matching rows, not only those shown"
          >
            Export CSV
          </button>
          {filtered.length > shown && (
            <button
              type="button"
              onClick={() => setPage((p) => ({ ...p, shown: p.shown + PAGE }))}
              className="label border border-ink px-4 py-2 !text-ink transition-colors duration-150 hover:bg-ink hover:!text-paper"
            >
              Show {Math.min(PAGE, filtered.length - shown)} more
            </button>
          )}
        </div>
      </section>
    </div>
  );
}

type RowProps = {
  r: ClientRow;
  i: number;
  line: Why | undefined;
  asOf: string;
  isCursor: boolean;
  isStarred: boolean;
  changed: boolean;
  reduce: boolean | null;
  onStar: (slug: string) => void;
  onCursor: (i: number) => void;
  /** Set by AnimatePresence, which measures a leaving row to lift it out of the list. */
  ref?: React.Ref<HTMLDivElement>;
};

// Memoised, so moving the cursor re-renders the row it left and the row it
// reached, not the whole list. Every prop is a value or a stable reference.
const Row = memo(function Row({
  r,
  i,
  line,
  asOf,
  isCursor,
  isStarred,
  changed,
  reduce,
  onStar,
  onCursor,
  ref,
}: RowProps) {
  const isNew = r.flags.includes("new");
  // A row on its way out keeps its old index for a moment; the cursor must not find it.
  const isPresent = useIsPresent();
  return (
    <motion.div
      ref={ref}
      role="row"
      data-row={isPresent ? i : undefined}
      onFocus={() => onCursor(i)}
      layout={reduce ? false : "position"}
      // A row moves only when its place in the list does, so only then is it measured.
      layoutDependency={i}
      initial={reduce ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={reduce ? undefined : { opacity: 0, transition: { duration: 0.12 } }}
      transition={{ layout: { duration: 0.45, ease: EASE }, opacity: { duration: 0.15 } }}
      // The row is a plain container: the name is the link, stretched over it,
      // and the source, the families and the star sit above that link.
      className={`${COLS} group relative isolate border-b border-rule py-2.5 transition-colors duration-150 hover:z-10 hover:bg-paper-2 ${
        isCursor ? "bg-paper-2" : ""
      }`}
    >
      <span role="cell" className="num text-[13px] text-ink-3 max-sm:pt-[3px]">
        {String(r.rank).padStart(2, "0")}
      </span>
      <span role="cell" className="hidden sm:block">
        <RankDelta rank={r.rank} prev={r.rankPrev} isNew={isNew} className="relative z-10" />
      </span>
      <span role="cell" className="min-w-0">
        <span className="flex min-w-0 items-baseline gap-x-3">
          {/* The scroll margins are for Tab: the browser brings this link
              into view, and it has to clear both sticky bars. */}
          <Link
            href={`/c/${r.slug}/`}
            className="flex min-w-0 scroll-mb-8 items-baseline gap-2 font-serif text-[17px] font-medium leading-tight tracking-[-0.015em] after:absolute after:inset-0"
          >
            {changed && (
              <span
                role="img"
                aria-label="New signal since your last visit"
                title="New signal since your last visit"
                className="relative z-10 inline-block size-[6px] shrink-0 -translate-y-[3px] bg-signal"
              />
            )}
            <span className="sm:truncate">{r.name}</span>
            {r.hasBrief && (
              <span className="num shrink-0 text-[11px] font-normal uppercase tracking-[0.08em] text-ink-4 max-sm:hidden">
                Brief
              </span>
            )}
          </Link>
          {r.oneLiner && (
            <span className="hidden min-w-0 flex-1 basis-0 truncate text-[13px] text-ink-3 md:block">{r.oneLiner}</span>
          )}
        </span>
        {/* Below lg, where the sector column is gone. On a phone it runs on
            under the edge column, as the signal line does. */}
        <span className="mt-1 flex items-baseline gap-x-2 max-sm:-mr-[4.25rem] lg:hidden">
          {/* Only a change is printed here: with no column round it, the dash for none reads as a stray mark. */}
          {r.rankPrev !== r.rank && (
            <RankDelta rank={r.rank} prev={r.rankPrev} isNew={isNew} className="sm:hidden" />
          )}
          <span className="label whitespace-nowrap">
            {SECTOR_LABEL[r.sector]}
            {r.stage && ` · ${STAGE_LABEL[r.stage]}`}
          </span>
        </span>
        {line && (
          // On a phone the line runs on under the edge column (its width plus
          // the gap), which is empty below the number.
          <span className="mt-0.5 flex min-w-0 items-baseline gap-x-2 text-[13px] max-sm:-mr-[4.25rem]">
            <a
              href={line.url}
              target="_blank"
              rel="noreferrer"
              title={FAMILY_LABEL[line.family]}
              aria-label={`Open the source for: ${line.title}`}
              className="group/source relative z-10 flex min-w-0 items-baseline gap-x-2 text-ink-2 transition-colors duration-150 hover:text-ink"
            >
              <span className="truncate underline decoration-transparent decoration-1 underline-offset-2 transition-colors duration-150 group-hover/source:decoration-ink">
                {line.title}
              </span>
              <span className="shrink-0 text-[12px] text-ink-4 transition-colors duration-150 group-hover/source:text-ink">
                ↗
              </span>
            </a>
            {/* An undated signal has no age: its date is only when it was read. */}
            <span className="num shrink-0 text-[11px] text-ink-4">
              {line.state ? "undated" : ago(line.occurredAt, asOf)}
            </span>
          </span>
        )}
      </span>
      <span role="cell" className="hidden min-w-0 lg:block">
        <span className="label block truncate">{SECTOR_LABEL[r.sector]}</span>
        {r.stage && (
          <span className="label mt-1 block truncate !text-ink-4">
            {STAGE_LABEL[r.stage]}
            {r.raisedUsd != null && (
              <>
                {" · "}
                <span className="num">${compact(r.raisedUsd)}</span>
              </>
            )}
          </span>
        )}
      </span>
      <span role="cell" className="hidden sm:block">
        <FamilyGlyph families={r.families} details={r.bySignalFamily} asOf={asOf} className="relative z-20" />
      </span>
      <span role="cell" className="hidden lg:block">
        {/* Drawn to the row's own range. The floor keeps a quiet row from reading as a spike. */}
        <Sparkline values={r.history} max={Math.max(10, ...r.history)} />
      </span>
      <span role="cell" className="num text-right text-[20px] leading-none">
        {r.edge.toFixed(0)}
      </span>
      {/* Left of the row from sm; under the rank on a phone, where nothing hovers. */}
      <StarButton
        on={isStarred}
        onToggle={() => onStar(r.slug)}
        name={r.name}
        className={`absolute z-10 max-sm:-left-2.5 max-sm:top-8 max-sm:size-9 sm:-left-7 sm:top-1/2 sm:-translate-y-1/2 ${
          isStarred
            ? "opacity-100"
            : "opacity-0 focus-visible:opacity-100 group-hover:opacity-100 max-sm:opacity-50 [@media(hover:none)]:opacity-50"
        }`}
      />
      {/* The keyboard cursor. Hover is the wash alone. */}
      <span
        aria-hidden
        className={`pointer-events-none absolute inset-y-0 left-0 w-px origin-top bg-ink transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
          isCursor ? "scale-y-100" : "scale-y-0"
        }`}
      />
    </motion.div>
  );
});

function Chip({
  active,
  onClick,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  count?: number;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`relative pb-1 text-[14px] transition-colors duration-150 hover:text-ink ${
        active ? "text-ink" : "text-ink-3"
      }`}
    >
      {children}
      {count != null && <span className="num ml-1.5 text-[11px] text-ink-4">{count}</span>}
      <span
        className={`absolute inset-x-0 bottom-0 h-px origin-left bg-ink transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
          active ? "scale-x-100" : "scale-x-0"
        }`}
      />
    </button>
  );
}

function TextToggle({
  active,
  onClick,
  title,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title?: string;
  count?: number;
  children: React.ReactNode;
}) {
  // The count is in the hover: on the row itself it repeats what the tiles say.
  const companies = count == null ? null : `${count} ${count === 1 ? "company" : "companies"}`;
  return (
    <button
      type="button"
      title={[title, companies].filter(Boolean).join(" · ") || undefined}
      aria-pressed={active}
      onClick={onClick}
      className={`label -my-2 whitespace-nowrap py-2 transition-colors duration-150 hover:!text-ink ${
        active ? "!text-ink underline decoration-1 underline-offset-4" : ""
      }`}
    >
      {children}
    </button>
  );
}
