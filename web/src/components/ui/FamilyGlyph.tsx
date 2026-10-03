"use client";

import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FAMILY_BLURB, FAMILY_LABEL, shortDate } from "@/lib/format";
import { FAMILIES, type Family } from "@/lib/types";

export type FamilyDetail = {
  count: number;
  /** `state` marks a reading with no date of its own. */
  items: { title: string; occurredAt: string; state?: boolean }[];
};

type Props = {
  families: Record<Family, number>;
  /** What sits behind each cell. With it, hovering a cell lists that family's signals. */
  details?: Partial<Record<Family, FamilyDetail>>;
  /** Run date, so dates in the hover read "Sep 18" rather than "Sep 18, 2026". */
  asOf?: string;
  /** Cell edge in px. */
  size?: number;
  gap?: number;
  /** Which edge of the glyph the hover card lines up with. */
  align?: "left" | "right";
  className?: string;
};

const FIRING = 0.12;
/** Room the hover card needs: a head and two signals, wrapped. */
const CARD_ROOM = 200;
/** The card's width (19rem), and the least it keeps clear of the screen's edge. */
const CARD_WIDTH = 304;
const CARD_EDGE = 8;

/** Where the card sits on the screen: hung below the glyph, or stood on top of it. */
type Place = { left: number; top?: number; bottom?: number };

/**
 * Eight cells, one per signal family, always in the same order. A cell fills
 * with ink in proportion to how hard that family is firing, so convergence
 * across independent sources reads at a glance without any colour. Hover a
 * cell to see which signals put it there.
 */
export function FamilyGlyph({
  families,
  details,
  asOf,
  size = 9,
  gap = 2,
  align = "right",
  className,
}: Props) {
  const [active, setActive] = useState<Family | null>(null);
  // The card is drawn on the page body at a fixed position, measured on
  // entering a cell. Inside the row it was clipped by whatever sat above the
  // row in the stacking order: the sticky column header cut it in half.
  const [place, setPlace] = useState<Place>({ left: 0, top: 0 });
  const rootRef = useRef<HTMLSpanElement>(null);
  const reduce = useReducedMotion();

  // A fixed card does not travel with the page, so scrolling puts it away.
  useEffect(() => {
    if (!active) return;
    const close = () => setActive(null);
    window.addEventListener("scroll", close, { passive: true, capture: true });
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, { capture: true });
      window.removeEventListener("resize", close);
    };
  }, [active]);
  const firing = FAMILIES.filter((f) => families[f] >= FIRING);
  const label = firing.length
    ? `Signals: ${firing.map((f) => FAMILY_LABEL[f]).join(", ")}`
    : "No signals";
  const detail = active ? details?.[active] : undefined;
  // Each cell sits in a taller hit area so a 9px box is not a 9px target.
  const pad = Math.max(4, Math.round((22 - size) / 2));

  return (
    <span
      ref={rootRef}
      className={`relative inline-block align-middle ${className ?? ""}`}
      onMouseLeave={() => setActive(null)}
    >
      <span
        role="img"
        aria-label={label}
        title={details ? undefined : label}
        style={{
          display: "inline-grid",
          gridTemplateColumns: `repeat(8, ${size + gap}px)`,
        }}
      >
        {FAMILIES.map((f) => {
          const v = families[f] ?? 0;
          const on = v >= FIRING;
          return (
            <span
              key={f}
              onMouseEnter={
                details
                  ? () => {
                      const glyph = rootRef.current?.getBoundingClientRect();
                      if (!glyph) return;
                      const screen = document.documentElement.clientWidth;
                      const width = Math.min(
                        CARD_WIDTH,
                        screen - 2 * CARD_EDGE,
                      );
                      // Hang from the glyph's own edge, kept on the screen.
                      const wanted =
                        align === "left" ? glyph.left : glyph.right - width;
                      const left = Math.round(
                        Math.max(
                          CARD_EDGE,
                          Math.min(wanted, screen - width - CARD_EDGE),
                        ),
                      );
                      // Below by choice; above only when below is short and above is roomier.
                      const below = window.innerHeight - glyph.bottom;
                      const above = below < CARD_ROOM && glyph.top > below;
                      setPlace(
                        above
                          ? {
                              left,
                              bottom: Math.round(
                                window.innerHeight - glyph.top + 4,
                              ),
                            }
                          : { left, top: Math.round(glyph.bottom + 4) },
                      );
                      setActive(f);
                    }
                  : undefined
              }
              style={{
                padding: `${pad}px ${gap / 2}px`,
                cursor: details ? "help" : undefined,
              }}
            >
              <span
                className="block transition-[outline-color] duration-100"
                style={{
                  width: size,
                  height: size,
                  // Strength is in the fill, not in opacity, so the hover
                  // ring below stays full ink on a weak family.
                  background: on
                    ? `color-mix(in srgb, var(--ink) ${Math.round((0.28 + 0.72 * Math.min(1, v)) * 100)}%, transparent)`
                    : "transparent",
                  boxShadow: on ? "none" : "inset 0 0 0 1px var(--rule)",
                  outline:
                    active === f
                      ? "1px solid var(--ink)"
                      : "1px solid transparent",
                  outlineOffset: 1,
                }}
              />
            </span>
          );
        })}
      </span>

      {active &&
        details &&
        createPortal(
          <AnimatePresence>
            <motion.span
              key={active}
              role="tooltip"
              initial={
                reduce
                  ? false
                  : { opacity: 0, y: place.bottom != null ? 4 : -4 }
              }
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
              className="pointer-events-none fixed z-[60] block w-[19rem] max-w-[calc(100vw-1rem)] border border-ink bg-paper px-3.5 py-3 text-left"
              style={place}
            >
              <span className="flex items-baseline justify-between gap-3">
                <span className="text-[13.5px] font-medium normal-case tracking-normal text-ink">
                  {FAMILY_LABEL[active]}
                </span>
                <span className="label">
                  {detail
                    ? `${detail.count} signal${detail.count === 1 ? "" : "s"}`
                    : "No signals"}
                </span>
              </span>
              {detail ? (
                <span className="mt-2 flex flex-col gap-2">
                  {detail.items.map((it) => (
                    <span key={it.title + it.occurredAt} className="block">
                      <span className="block text-[13px] leading-snug normal-case tracking-normal text-ink-2">
                        {it.title}
                      </span>
                      <span className="num mt-0.5 block text-[11px] text-ink-4">
                        {it.state ? "Undated" : shortDate(it.occurredAt, asOf)}
                      </span>
                    </span>
                  ))}
                  {detail.count > detail.items.length && (
                    <span className="label !text-ink-4">
                      and {detail.count - detail.items.length} more
                    </span>
                  )}
                </span>
              ) : (
                <span className="mt-1.5 block text-[12.5px] leading-snug normal-case tracking-normal text-ink-3">
                  {FAMILY_BLURB[active]}
                </span>
              )}
            </motion.span>
          </AnimatePresence>,
          document.body,
        )}
    </span>
  );
}
