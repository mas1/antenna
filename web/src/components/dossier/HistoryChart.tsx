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
const H = 240;
const PAD = { l: 36, r: 16, t: 16, b: 44 };

/**
 * Edge over the last twelve weeks, with every dated signal marked on the
 * time axis so a rise can be read against what caused it. Markers are dated
 * events only: a standing reading has no place on a time axis.
 */
export function HistoryChart({ dates, values, markers }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  if (values.length < 2) return null;

  const t0 = Date.parse(dates[0]);
  const t1 = Date.parse(dates[dates.length - 1]);
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

  return (
    <figure>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Edge score from ${shortDate(dates[0])} to ${shortDate(dates[dates.length - 1])}, ending at ${values[values.length - 1].toFixed(0)}`}
      >
        <defs>
          <linearGradient id="hist-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="var(--ink)" stopOpacity="0.12" />
            <stop offset="100%" stopColor="var(--ink)" stopOpacity="0" />
          </linearGradient>
        </defs>

        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="var(--rule)" strokeWidth={1} strokeDasharray={t ? "2 4" : undefined} />
            <text x={PAD.l - 8} y={y(t) + 3.5} textAnchor="end" fontSize={10.5} fill="var(--ink-4)" fontFamily="var(--font-plex-mono)">
              {t}
            </text>
          </g>
        ))}

        {dates.map((d, i) =>
          i % 3 === 0 || i === dates.length - 1 ? (
            <text key={d} x={x(d)} y={H - 8} textAnchor={i === dates.length - 1 ? "end" : i === 0 ? "start" : "middle"} fontSize={10.5} fill="var(--ink-4)" fontFamily="var(--font-plex-mono)">
              {i === dates.length - 1 ? "NOW" : shortDate(d, dates[dates.length - 1]).toUpperCase()}
            </text>
          ) : null,
        )}

        {/* Whole in the HTML; the entrances are CSS, so the chart shows without JavaScript and in print. */}
        <path d={area} fill="url(#hist-fill)" className="fade-in" style={{ "--d": "0.6s" } as React.CSSProperties} />
        <path
          d={line}
          pathLength={1}
          fill="none"
          stroke="var(--ink)"
          strokeWidth={1.6}
          strokeLinejoin="round"
          strokeLinecap="round"
          className="draw-line"
          style={{ "--t": "1.1s" } as React.CSSProperties}
        />

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
                stroke={on ? "var(--signal)" : "var(--ink)"}
                strokeWidth={on ? 2 : 1.25}
                className="fade-in"
                style={{ "--d": `${(0.5 + ((mx - PAD.l) / W) * 0.8).toFixed(2)}s` } as React.CSSProperties}
              />
              {on && <line x1={mx} x2={mx} y1={PAD.t} y2={y(0)} stroke="var(--signal)" strokeWidth={1} strokeDasharray="2 3" />}
              <rect x={mx - 6} y={PAD.t} width={12} height={H - PAD.t - 20} fill="transparent" />
            </g>
          );
        })}

        <circle cx={lx} cy={ly} r={3.5} fill="var(--signal)" className="pop-in" style={{ "--d": "0.95s" } as React.CSSProperties} />
      </svg>
      <figcaption className="mt-2 flex min-h-5 items-baseline gap-3 text-[13px]">
        {hover != null && inRange[hover] ? (
          <>
            <span className="num text-[11px] text-ink-4">{shortDate(inRange[hover].t)}</span>
            <span className="label">{FAMILY_LABEL[inRange[hover].family]}</span>
            <span className="truncate text-ink-2">{inRange[hover].title}</span>
          </>
        ) : inRange.length > 0 ? (
          <span className="label !text-ink-4">
            {inRange.length} dated signal{inRange.length === 1 ? "" : "s"} on the axis. Hover a tick to read it.
          </span>
        ) : (
          <span className="label">
            No dated signals in these twelve weeks
            {latest && `. The latest was ${shortDate(latest, dates[dates.length - 1])}.`}
          </span>
        )}
      </figcaption>
    </figure>
  );
}
