"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { FAMILY_LABEL, shortDate } from "@/lib/format";
import { FAMILIES, type Family, type FeedItem } from "@/lib/types";
import { setQuery, useQuery } from "@/lib/urlState";

const PAGE = 120;
const TOP = 50;
const EASE = [0.16, 1, 0.3, 1] as const;
// Family, company, signal, rank and stage, strength, source. On a phone the family
// has a line of its own above the signal. The strength bar waits for a wide screen.
const ROW =
  "grid grid-cols-[minmax(0,1fr)_1.25rem] items-baseline gap-x-3 md:grid-cols-[5rem_11rem_minmax(0,1fr)_8.25rem_1.25rem] md:gap-x-4 xl:grid-cols-[5rem_12rem_minmax(0,1fr)_8.25rem_3.5rem_1.25rem] xl:gap-x-5";

type Stage = NonNullable<FeedItem["stage"]>;
/** "young" is where the wire opens: everything but growth-stage and established companies. */
type StageView = "young" | "formation" | "early" | "growth" | "any";

const STAGE_VIEWS: { key: StageView; label: string; hint: string }[] = [
  {
    key: "young",
    label: "Formation and early",
    hint: "Hides growth-stage and established companies; companies with no known stage stay in",
  },
  { key: "formation", label: "Formation", hint: "Pre-seed or seed" },
  { key: "early", label: "Early", hint: "Series A or B" },
  { key: "growth", label: "Growth", hint: "Growth-stage and established companies" },
  { key: "any", label: "Any", hint: "" },
];
const STAGE_LABEL: Record<Stage, string> = {
  formation: "Formation",
  early: "Early",
  growth: "Growth",
  incumbent: "Established",
};

const STRENGTHS: { key: string; min: number; label: string; hint: string }[] = [
  { key: "0", min: 0, label: "Any", hint: "" },
  { key: "50", min: 0.5, label: "50+", hint: "" },
  { key: "70", min: 0.7, label: "70+", hint: "" },
];

function inStage(it: FeedItem, view: StageView): boolean {
  const late = it.stage === "growth" || it.stage === "incumbent";
  if (view === "any") return true;
  if (view === "young") return !late;
  if (view === "growth") return late;
  return it.stage === view;
}

/** A toggle's count, for its hover title. */
const signalCount = (n: number) => `${n} signal${n === 1 ? "" : "s"}`;

export function Wire({ items, asOf }: { items: FeedItem[]; asOf: string }) {
  const params = useQuery();
  const reduce = useReducedMotion();

  // The view lives in the URL, so it survives a trip to a dossier and back.
  const famParam = params.get("fam");
  const family: Family | "all" = (FAMILIES as readonly string[]).includes(famParam ?? "")
    ? (famParam as Family)
    : "all";
  const stageParam = params.get("stage");
  const stage: StageView = STAGE_VIEWS.some((s) => s.key === stageParam) ? (stageParam as StageView) : "young";
  const strength = STRENGTHS.find((s) => s.key === params.get("min")) ?? STRENGTHS[0];
  const top = params.get("top") === "1";
  const viewKey = params.toString();
  const narrowed = family !== "all" || stage !== "young" || strength.min > 0 || top;

  // Each count answers "how many would I see if I picked this", so it is taken
  // with every other control as it stands now.
  const { filtered, counts } = useMemo(() => {
    const filtered: FeedItem[] = [];
    const byFamily: Partial<Record<Family | "all", number>> = {};
    const byStage: Partial<Record<StageView, number>> = {};
    const byStrength: Record<string, number> = {};
    let inTop = 0;
    for (const it of items) {
      const f = family === "all" || it.family === family;
      const s = inStage(it, stage);
      const m = it.strength >= strength.min;
      const t = !top || it.rank <= TOP;
      if (s && m && t) {
        byFamily.all = (byFamily.all ?? 0) + 1;
        byFamily[it.family] = (byFamily[it.family] ?? 0) + 1;
      }
      if (f && m && t) {
        for (const v of STAGE_VIEWS) if (inStage(it, v.key)) byStage[v.key] = (byStage[v.key] ?? 0) + 1;
      }
      if (f && s && t) {
        for (const o of STRENGTHS) if (it.strength >= o.min) byStrength[o.key] = (byStrength[o.key] ?? 0) + 1;
      }
      if (f && s && m && it.rank <= TOP) inTop += 1;
      if (f && s && m && t) filtered.push(it);
    }
    return { filtered, counts: { byFamily, byStage, byStrength, inTop } };
  }, [items, family, stage, strength, top]);

  // A family keeps its button while a filter empties it, so the row does not jump.
  const present = useMemo(() => new Set(items.map((it) => it.family)), [items]);

  // Paging resets whenever the view changes.
  const [page, setPage] = useState({ key: "", shown: PAGE });
  const shown = page.key === viewKey ? page.shown : PAGE;

  const groups = useMemo(() => {
    const out: { day: string; items: FeedItem[] }[] = [];
    for (const it of filtered.slice(0, shown)) {
      const last = out[out.length - 1];
      if (last && last.day === it.occurredAt) last.items.push(it);
      else out.push({ day: it.occurredAt, items: [it] });
    }
    return out;
  }, [filtered, shown]);

  const reset = () => setQuery({ fam: null, stage: null, min: null, top: null });

  return (
    <div>
      <div className="border-b border-ink pb-3">
        <div className="flex flex-wrap gap-x-5 gap-y-2" role="group" aria-label="Signal family">
          <FilterButton active={family === "all"} onClick={() => setQuery({ fam: null })} count={counts.byFamily.all ?? 0}>
            All
          </FilterButton>
          {FAMILIES.filter((f) => present.has(f)).map((f) => (
            <FilterButton
              key={f}
              active={family === f}
              onClick={() => setQuery({ fam: family === f ? null : f })}
              count={counts.byFamily[f] ?? 0}
            >
              {FAMILY_LABEL[f]}
            </FilterButton>
          ))}
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-8 gap-y-3 border-t border-rule pt-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3" role="group" aria-label="Company stage">
            <span className="label !text-ink-4">Stage</span>
            {STAGE_VIEWS.map((v) => (
              <TextToggle
                key={v.key}
                title={v.hint}
                active={stage === v.key}
                onClick={() => setQuery({ stage: v.key === "young" ? null : v.key })}
                count={counts.byStage[v.key] ?? 0}
              >
                {v.label}
              </TextToggle>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3 sm:gap-x-8">
            {/* Always in the row, and first, so the controls after it do not move when it shows. */}
            <button
              type="button"
              title="Back to the view the wire opens on"
              onClick={reset}
              className={`label -my-2 py-2 !text-ink underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink ${
                narrowed ? "" : "invisible"
              }`}
            >
              Clear
            </button>
            <button
              type="button"
              title={`Only companies ranked in the top ${TOP} · ${signalCount(counts.inTop)}`}
              aria-pressed={top}
              onClick={() => setQuery({ top: top ? null : "1" })}
              className={`label -my-2 flex items-center gap-2 py-2 transition-colors duration-150 hover:!text-ink ${
                top ? "!text-ink" : ""
              }`}
            >
              <span
                className={`inline-block size-[9px] border transition-colors duration-150 ${
                  top ? "border-ink bg-ink" : "border-rule-2"
                }`}
              />
              Top {TOP}
            </button>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-3" role="group" aria-label="Minimum strength">
              <span className="label !text-ink-4" title="Signal strength, 0 to 100">
                Strength
              </span>
              {STRENGTHS.map((o) => (
                <TextToggle
                  key={o.key}
                  title={o.hint}
                  active={strength.key === o.key}
                  onClick={() => setQuery({ min: o.min > 0 ? o.key : null })}
                  count={counts.byStrength[o.key] ?? 0}
                >
                  {o.label}
                </TextToggle>
              ))}
            </div>
          </div>
        </div>
      </div>

      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={viewKey}
          initial={reduce ? false : { opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          exit={reduce ? undefined : { opacity: 0, transition: { duration: 0.1 } }}
          transition={{ duration: 0.35, ease: EASE }}
        >
          {groups.map((g) => (
            <section key={g.day} className="grid grid-cols-12 gap-x-6 border-b border-rule">
              <h2 className="col-span-12 pt-5 lg:col-span-2 lg:py-5">
                <span className="label !text-ink">{shortDate(g.day, asOf)}</span>
              </h2>
              <ol className="col-span-12 py-2.5 lg:col-span-10">
                {g.items.map((it, i) => (
                  <li key={it.id + i} className={`${ROW} py-2.5`}>
                    <span className="label col-span-2 md:col-span-1">{FAMILY_LABEL[it.family]}</span>
                    <Link
                      href={`/c/${it.slug}/`}
                      title={it.name}
                      className="hidden truncate font-serif text-[17px] underline decoration-transparent decoration-1 underline-offset-4 transition-colors hover:decoration-ink md:block"
                    >
                      {it.name}
                    </Link>
                    <span className="min-w-0 text-[14px] leading-snug text-ink-2">
                      <span className="num text-[11px] text-ink-3 md:hidden">#{it.rank} </span>
                      <Link href={`/c/${it.slug}/`} className="font-serif text-[15px] text-ink md:hidden">
                        {it.name}.{" "}
                      </Link>
                      <a
                        href={it.url}
                        target="_blank"
                        rel="noreferrer"
                        className="underline decoration-transparent decoration-1 underline-offset-2 transition-colors hover:decoration-ink"
                      >
                        {it.title}
                      </a>
                    </span>
                    <span className="label hidden whitespace-nowrap md:block" title={it.stage ? undefined : "Stage unknown"}>
                      <span className="num text-ink">#{it.rank}</span>
                      {it.stage && ` · ${STAGE_LABEL[it.stage]}`}
                    </span>
                    <span className="hidden xl:block" title={`Strength ${Math.round(it.strength * 100)}`}>
                      <span className="relative block h-[3px] w-full bg-paper-3">
                        <span className="absolute inset-y-0 left-0 bg-ink" style={{ width: `${it.strength * 100}%` }} />
                      </span>
                    </span>
                    {/* The same link as the signal text, for a pointer only: one tab stop per source. */}
                    <a
                      href={it.url}
                      target="_blank"
                      rel="noreferrer"
                      tabIndex={-1}
                      aria-hidden="true"
                      className="-m-2 p-2 text-[12px] text-ink-4 transition-colors hover:text-ink"
                    >
                      ↗
                    </a>
                  </li>
                ))}
              </ol>
            </section>
          ))}
          {filtered.length === 0 && (
            <p className="border-b border-rule py-16 text-center text-[14px] text-ink-3">
              No signals match.{" "}
              <button
                type="button"
                onClick={reset}
                className="text-ink underline decoration-rule-2 decoration-1 underline-offset-4 transition-colors hover:decoration-ink"
              >
                Clear filters
              </button>
            </p>
          )}
        </motion.div>
      </AnimatePresence>

      {filtered.length > shown && (
        <div className="mt-6 flex justify-end">
          <button
            type="button"
            onClick={() => setPage({ key: viewKey, shown: shown + PAGE })}
            className="label border border-ink px-4 py-2 !text-ink transition-colors duration-150 hover:bg-ink hover:!text-paper"
          >
            Show {Math.min(PAGE, filtered.length - shown)} more
          </button>
        </div>
      )}
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`relative pb-1 text-[14px] transition-colors duration-150 hover:text-ink ${active ? "text-ink" : "text-ink-3"}`}
    >
      {children}
      <span className="num ml-1.5 text-[11px] text-ink-4">{count}</span>
      <span
        className={`absolute inset-x-0 bottom-0 h-px origin-left bg-ink transition-transform duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
          active ? "scale-x-100" : "scale-x-0"
        }`}
      />
    </button>
  );
}

/** A mono text option. Its count is in the hover title. The padding is click area, not layout. */
function TextToggle({
  active,
  onClick,
  title,
  count,
  children,
}: {
  active: boolean;
  onClick: () => void;
  title: string;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      title={[title, signalCount(count)].filter(Boolean).join(" · ")}
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
