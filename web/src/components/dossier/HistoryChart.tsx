"use client";

import { useState } from "react";
import { FAMILY_LABEL, shortDate } from "@/lib/format";
import type { Family } from "@/lib/types";

type Marker = { t: string; family: Family; title: string };

type Props = {
  dates: string[];
  values: number[];
  markers: Marker[];
};

const W = 880;
const H = 220;
const PAD = { l: 2, r: 6, t: 16, b: 20 };

/**
 * Edge over the last twelve weeks, with every dated signal marked on the
 * time axis so a rise can be read against what caused it. Markers are dated
 * events only: an undated signal has no place on a time axis.
 */
export function HistoryChart({ dates, values, markers }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  if (values.length < 2) return null;

  const last = dates.length - 1;
  const t0 = Date.parse(dates[0]);
  const t1 = Date.parse(dates[last]);
  const top = Math.max(10, Math.ceil(Math.max(...values) / 10) * 10);
  const x = (iso: string) => PAD.l + ((Date.parse(iso) - t0) / (t1 - t0)) * (W - PAD.l - PAD.r);
  const y = (v: number) => H - PAD.b - (v / top) * (H - PAD.t - PAD.b);

  const pts = values.map((v, i) => [x(dates[i]), y(v)] as const);
  const line = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const area = `${line} L${pts[pts.length - 1][0].toFixed(1)},${y(0)} L${pts[0][0].toFixed(1)},${y(0)} Z`;
  const inRange = markers.filter((m) => {
    const t = Date.parse(m.t);
    return t >= t0 && t <= t1;
  });
  // With nothing in the window, the caption says when the last event was.
  const latest = markers.reduce((max, m) => (m.t > max ? m.t : max), "");
  const ticks = [0, top / 2, top];
  const [lx, ly] = pts[pts.length - 1];
  // Green is for a rise, by the same rule as the board's sparkline.
  const rising = values[values.length - 1] > values[Math.max(0, values.length - 3)];

  return (
    <figure>
      {/* The axis labels are HTML beside the drawing, so they keep their size when the chart is scaled down to a phone. */}
      <div className="pl-7">
        <div className="relative">
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="h-auto w-full"
            role="img"
            aria-label={`Edge score from ${shortDate(dates[0])} to ${shortDate(dates[last])}, ending at ${values[values.length - 1].toFixed(0)}`}
          >
            <defs>
              <linearGradient id="hist-fill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor="var(--ink)" stopOpacity="0.12" />
                <stop offset="100%" stopColor="var(--ink)" stopOpacity="0" />
              </linearGradient>
            </defs>

            {ticks.map((t) => (
              <line key={t} x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="var(--rule)" strokeWidth={1} strokeDasharray={t ? "2 4" : undefined} vectorEffect="non-scaling-stroke" />
            ))}

            {/* Whole in the HTML; the entrances are CSS, so the chart shows without JavaScript and in print. */}
            <g>
              <path d={area} fill="url(#hist-fill)" className="fade-in" style={{ "--d": "0.6s" } as React.CSSProperties} />
              <path
                d={line}
                pathLength={1}
                fill="none"
                stroke="var(--ink)"
                strokeWidth={1.6}
                strokeLinejoin="round"
                strokeLinecap="round"
                // Thicker on a phone, where the drawing is scaled to a third.
                className="draw-line max-sm:[stroke-width:3.5]"
                style={{ "--t": "1.1s" } as React.CSSProperties}
              />
            </g>

            {/* Signal markers on the axis */}
            {inRange.map((m, i) => {
              const mx = x(m.t);
              const on = hover === i;
              return (
                <g key={`${m.t}-${i}`} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                  <line
                    x1={mx}
                    x2={mx}
                    y1={y(0)}
                    y2={y(0) + (on ? 14 : 9)}
                    stroke="var(--ink)"
                    strokeWidth={on ? 2 : 1.25}
                    vectorEffect="non-scaling-stroke"
                    className="fade-in"
                    style={{ "--d": `${(0.5 + ((mx - PAD.l) / W) * 0.8).toFixed(2)}s` } as React.CSSProperties}
                  />
                  {on && <line x1={mx} x2={mx} y1={PAD.t} y2={y(0)} stroke="var(--ink)" strokeWidth={1} strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />}
                  <rect x={mx - 6} y={PAD.t} width={12} height={H - PAD.t} fill="transparent" />
                </g>
              );
            })}

            <circle cx={lx} cy={ly} r={3.5} fill={rising ? "var(--signal)" : "var(--ink)"} className="pop-in" style={{ "--d": "0.95s" } as React.CSSProperties} />
          </svg>
          {ticks.map((t) => (
            <span
              key={t}
              aria-hidden
              className="num absolute right-full mr-2 -translate-y-1/2 text-[11px] leading-none text-ink-4"
              style={{ top: `${(y(t) / H) * 100}%` }}
            >
              {t}
            </span>
          ))}
        </div>
        {/* Every third week and today. A phone keeps the first, the middle and today. */}
        <div aria-hidden className="relative mt-1.5 h-4">
          {dates.map((d, i) =>
            i % 3 === 0 || i === last ? (
              <span
                key={d}
                className={`label absolute top-0 whitespace-nowrap !text-ink-4 ${
                  i === last ? "-translate-x-full" : i === 0 ? "" : "-translate-x-1/2"
                } ${i % 6 === 0 || i === last ? "" : "max-sm:hidden"}`}
                style={{ left: `${(x(d) / W) * 100}%` }}
              >
                {i === last ? "Now" : shortDate(d, dates[last])}
              </span>
            ) : null,
          )}
        </div>
      </div>
      {/* Empty until a tick is hovered: the room for it is taken from the gap below, so the section ends level with the others. */}
      <figcaption className={`mt-2 flex min-h-5 items-baseline gap-3 text-[13px] ${inRange.length ? "-mb-7" : ""}`}>
        {hover != null && inRange[hover] ? (
          <>
            <span className="num text-[11px] text-ink-4">{shortDate(inRange[hover].t, dates[last])}</span>
            <span className="label">{FAMILY_LABEL[inRange[hover].family]}</span>
            <span className="truncate text-ink-2">{inRange[hover].title}</span>
          </>
        ) : (
          inRange.length === 0 && latest && <span className="label">Last signal {shortDate(latest, dates[last])}</span>
        )}
      </figcaption>
    </figure>
  );
}
