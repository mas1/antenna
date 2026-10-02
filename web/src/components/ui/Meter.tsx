type Props = {
  /** 0..1 */
  value: number;
  width?: number | string;
  height?: number;
  delay?: number;
  tone?: "ink" | "signal";
  label?: string;
};

/**
 * A hairline track with an ink fill that grows in from the left. The fill
 * sits at its real width in the HTML and the growth is CSS ("meter-fill" in
 * globals.css), so a meter is right in print and without JavaScript.
 */
export function Meter({ value, width = "100%", height = 3, delay = 0, tone = "ink", label }: Props) {
  const v = Math.max(0, Math.min(1, value));
  return (
    <span
      role="meter"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(v * 100)}
      aria-label={label}
      className="relative block overflow-hidden bg-paper-3"
      style={{ width, height }}
    >
      <span
        className="meter-fill absolute inset-0 block origin-left"
        style={
          {
            background: tone === "signal" ? "var(--signal)" : "var(--ink)",
            transform: `scaleX(${v})`,
            "--d": `${delay}s`,
          } as React.CSSProperties
        }
      />
    </span>
  );
}
