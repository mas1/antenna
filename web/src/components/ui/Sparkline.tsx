import { useId } from "react";

type Props = {
  values: number[];
  width?: number;
  height?: number;
  /** Shared y-max so rows are comparable. Defaults to this series' max. */
  max?: number;
  /** Delay before the line draws, in seconds. */
  delay?: number;
  /** Fill under the line. */
  area?: boolean;
  className?: string;
  label?: string;
};

const css = (vars: Record<string, string>) => vars as React.CSSProperties;

/**
 * A line that draws itself left to right, ending on a dot at "now". The line
 * is whole in the HTML and the drawing is CSS ("draw-line" in globals.css),
 * so it shows without JavaScript, in print and in a background tab.
 */
export function Sparkline({
  values,
  width = 96,
  height = 24,
  max,
  delay = 0,
  area = false,
  className,
  label,
}: Props) {
  const id = useId();
  if (values.length < 2) return <svg width={width} height={height} className={className} aria-hidden />;

  const pad = 2.5;
  const top = Math.max(max ?? Math.max(...values), 1e-6);
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2);
  const y = (v: number) => height - pad - (Math.min(v, top) / top) * (height - pad * 2);
  const pts = values.map((v, i) => [x(i), y(v)] as const);
  const line = pts.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(2)},${py.toFixed(2)}`).join(" ");
  const fill = `${line} L${x(values.length - 1).toFixed(2)},${height} L${x(0).toFixed(2)},${height} Z`;
  const [lx, ly] = pts[pts.length - 1];
  const rising = values[values.length - 1] > values[Math.max(0, values.length - 3)];

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="img"
      aria-label={label ?? "Edge score over the last twelve weeks"}
    >
      {area && (
        <>
          <defs>
            <linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor="var(--ink)" stopOpacity="0.14" />
              <stop offset="100%" stopColor="var(--ink)" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={fill} fill={`url(#${id})`} className="fade-in" style={css({ "--d": `${delay + 0.5}s` })} />
        </>
      )}
      <path
        d={line}
        pathLength={1}
        fill="none"
        stroke="var(--ink)"
        strokeWidth={1.25}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="draw-line"
        style={css({ "--d": `${delay}s` })}
      />
      <circle
        cx={lx}
        cy={ly}
        r={2}
        fill={rising ? "var(--signal)" : "var(--ink)"}
        className="pop-in"
        style={css({ "--d": `${delay + 0.75}s` })}
      />
    </svg>
  );
}
