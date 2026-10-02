import Link from "next/link";
import { FamilyGlyph } from "@/components/ui/FamilyGlyph";
import { Reveal } from "@/components/ui/Reveal";
import { FAMILY_LABEL, SECTOR_LABEL, ago, daysBetween } from "@/lib/format";
import type { BoardRow } from "@/lib/types";

/**
 * The front page: the three companies where independent signals have lined
 * up most recently. Chosen by rule, not by hand: corroborated, with a dated
 * signal in the last three weeks, highest edge first, one per sector.
 */
export function pickLeads(rows: BoardRow[], asOf: string): BoardRow[] {
  const dated = (r: BoardRow) => r.why.filter((w) => !w.state);
  const fresh = rows.filter(
    (r) =>
      r.convergence >= 2 &&
      dated(r).length >= 2 &&
      dated(r).some((w) => daysBetween(w.occurredAt, asOf) <= 21),
  );
  const out: BoardRow[] = [];
  const sectors = new Set<string>();
  for (const r of fresh) {
    if (sectors.has(r.sector)) continue;
    out.push(r);
    sectors.add(r.sector);
    if (out.length === 3) break;
  }
  // Too few distinct sectors: fill from the same list regardless of sector.
  for (const r of fresh) {
    if (out.length === 3) break;
    if (!out.includes(r)) out.push(r);
  }
  return out;
}

export function Leads({ rows, asOf }: { rows: BoardRow[]; asOf: string }) {
  const leads = pickLeads(rows, asOf);
  if (leads.length === 0) return null;
  return (
    <section className="mt-9" aria-label="First look">
      <div className="flex items-baseline justify-between border-b border-ink pb-2">
        <h2 className="label !text-ink">First look</h2>
        <p className="label hidden sm:block">Fresh signals from two or more sources</p>
      </div>
      {/* Ruled cells, like the tiles above: each card is a cell that takes a wash on hover. */}
      <div className="grid grid-cols-1 border-l border-rule md:grid-cols-3">
        {leads.map((r, i) => (
          <Reveal
            key={r.slug}
            delay={0.08 * i}
            className="group relative border-b border-r border-rule px-5 py-5 transition-colors duration-200 hover:bg-paper-2 md:py-6"
          >
            <p className="flex items-center gap-3">
              <span className="label">{SECTOR_LABEL[r.sector]}</span>
              <FamilyGlyph families={r.families} details={r.bySignalFamily} asOf={asOf} size={8} align="left" className="relative z-20" />
              <span className="num ml-auto text-[13px] text-ink-3 transition-colors duration-200 group-hover:text-ink">
                {String(r.rank).padStart(2, "0")} · {r.edge.toFixed(0)}
              </span>
            </p>
            <h3 className="display mt-3 text-[26px] leading-[1.05] sm:text-[28px]">
              <Link href={`/c/${r.slug}/`} className="after:absolute after:inset-0">
                {r.name}
                {/* The arrow says the card opens the dossier; it arrives on hover. */}
                <span
                  aria-hidden
                  className="ml-2 inline-block -translate-x-1.5 font-sans text-[18px] text-ink-3 opacity-0 transition-[opacity,transform] duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] group-hover:translate-x-0 group-hover:opacity-100"
                >
                  →
                </span>
              </Link>
            </h3>
            {r.oneLiner && (
              <p className="mt-2 line-clamp-1 text-[13.5px] leading-snug text-ink-3">
                {r.oneLiner}
              </p>
            )}
            <ol className="mt-4 flex flex-col gap-2.5">
              {r.why
                .filter((w) => !w.state)
                .slice(0, 2)
                .map((w) => (
                  <li
                    key={w.url + w.title}
                    className="border-l border-rule-2 pl-3"
                  >
                    <p className="text-[14.5px] leading-snug text-ink">
                      {/* The source, above the card-wide link to the dossier. */}
                      <a href={w.url} target="_blank" rel="noreferrer" className="group/source relative z-10">
                        <span className="underline decoration-transparent decoration-1 underline-offset-4 transition-colors duration-150 group-hover/source:decoration-ink">
                          {w.title}
                        </span>{" "}
                        <span className="text-[12px] text-ink-4 transition-colors duration-150 group-hover/source:text-ink">
                          ↗
                        </span>
                      </a>
                    </p>
                    <p className="label mt-1 !text-ink-4">
                      {FAMILY_LABEL[w.family]} · {ago(w.occurredAt, asOf)}
                    </p>
                  </li>
                ))}
            </ol>
            <span
              aria-hidden
              className="pointer-events-none absolute inset-x-0 -top-px h-[2px] origin-left scale-x-0 bg-ink transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] group-hover:scale-x-100"
            />
          </Reveal>
        ))}
      </div>
    </section>
  );
}
