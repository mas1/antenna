import { FAMILY_LABEL } from "@/lib/format";
import { FAMILIES, type Family } from "@/lib/types";

type Props = {
  byFamily: Partial<Record<Family, number>>;
  signals: number;
  entities: number;
  onThesis: number;
  board: number;
};

const W = 920;
const H = 290;
const ROW = 34;
const TOP = 36;
const X_FAM = 166; // where family rows end
const X_MAT = 484; // match node
const X_SCO = 684; // score node
const X_BRD = 884; // board node
const MID = TOP + (ROW * 7) / 2;

const fmt = (n: number) => n.toLocaleString("en-US");

/**
 * The pipeline as one picture: eight families of public signals converge on
 * a single entity, which is scored and ranked.
 */
export function PipelineDiagram({ byFamily, signals, entities, onThesis, board }: Props) {
  const max = Math.max(1, ...FAMILIES.map((f) => byFamily[f] ?? 0));

  return (
    <>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="hidden h-auto w-full lg:block"
        role="img"
        aria-label={`${fmt(signals)} signals from eight families, matched to ${fmt(entities)} companies, of which ${fmt(onThesis)} are on thesis and ${fmt(board)} are ranked.`}
      >
        {/* Stage heads */}
        {[
          { x: 0, anchor: "start", t: "COLLECT" },
          { x: X_MAT, anchor: "middle", t: "MATCH" },
          { x: X_SCO, anchor: "middle", t: "SCORE" },
          { x: X_BRD, anchor: "middle", t: "RANK" },
        ].map((h) => (
          <text key={h.t} x={h.x} y={12} textAnchor={h.anchor as "start" | "middle"} fontSize={10.5} letterSpacing="0.08em" fill="var(--ink-3)" fontFamily="var(--font-plex-mono)">
            {h.t}
          </text>
        ))}

        {FAMILIES.map((f, i) => {
          const y = TOP + i * ROW;
          const n = byFamily[f] ?? 0;
          const path = `M${X_FAM},${y} C${X_FAM + 130},${y} ${X_MAT - 150},${MID} ${X_MAT - 34},${MID}`;
          const weight = 0.6 + 1.6 * (n / max);
          return (
            <g key={f}>
              <text x={0} y={y + 4.5} fontSize={13} fill="var(--ink)" fontFamily="var(--font-plex-sans)">
                {FAMILY_LABEL[f]}
              </text>
              <text x={X_FAM - 12} y={y + 4} textAnchor="end" fontSize={11} fill="var(--ink-3)" fontFamily="var(--font-plex-mono)">
                {fmt(n)}
              </text>
              <path
                d={path}
                pathLength={1}
                fill="none"
                stroke="var(--rule-2)"
                strokeWidth={weight}
                className="draw-line"
                style={{ "--d": `${(0.1 + i * 0.06).toFixed(2)}s` } as React.CSSProperties}
              />
            </g>
          );
        })}

        {/* Match -> Score -> Rank */}
        {[
          { x1: X_MAT + 34, x2: X_SCO - 34, d: 0.9 },
          { x1: X_SCO + 34, x2: X_BRD - 34, d: 1.15 },
        ].map((l) => (
          <line
            key={l.x1}
            x1={l.x1}
            x2={l.x2}
            y1={MID}
            y2={MID}
            pathLength={1}
            stroke="var(--ink)"
            strokeWidth={1.25}
            className="draw-line"
            style={{ "--d": `${l.d}s`, "--t": "0.5s" } as React.CSSProperties}
          />
        ))}

        {[
          { x: X_MAT, n: entities, l: "companies", d: 0.75 },
          { x: X_SCO, n: onThesis, l: "on thesis", d: 1.05 },
          { x: X_BRD, n: board, l: "ranked", d: 1.3 },
        ].map((nd) => (
          <g key={nd.l} className="fade-in" style={{ "--d": `${nd.d}s` } as React.CSSProperties}>
            <circle cx={nd.x} cy={MID} r={34} fill="var(--paper)" stroke="var(--ink)" strokeWidth={1.25} />
            <text x={nd.x} y={MID + 5} textAnchor="middle" fontSize={15} fill="var(--ink)" fontFamily="var(--font-plex-mono)">
              {fmt(nd.n)}
            </text>
            <text x={nd.x} y={MID + 58} textAnchor="middle" fontSize={13} fill="var(--ink)" fontFamily="var(--font-plex-sans)">
              {nd.l}
            </text>
          </g>
        ))}
      </svg>

      {/* Small screens: the same facts as a list. */}
      <ol className="lg:hidden">
        {[
          { k: "Collect", v: `${fmt(signals)} signals from eight families` },
          { k: "Match", v: `${fmt(entities)} companies` },
          { k: "Score", v: `${fmt(onThesis)} on thesis` },
          { k: "Rank", v: `${fmt(board)} ranked` },
        ].map((s) => (
          <li key={s.k} className="flex items-baseline gap-4 border-b border-rule py-3">
            <span className="label w-20">{s.k}</span>
            <span className="text-[14.5px]">{s.v}</span>
          </li>
        ))}
      </ol>
    </>
  );
}
