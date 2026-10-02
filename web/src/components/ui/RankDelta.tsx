type Props = {
  rank: number;
  prev: number | null;
  /** The row carries the "new" flag: its first signal landed this week. */
  isNew?: boolean;
  className?: string;
};

/** Movement against last week's rank. */
export function RankDelta({ rank, prev, isNew = false, className }: Props) {
  if (prev == null) {
    // Unranked a week ago is not the same as new: a company can enter on a
    // signal that is months old. Green "New" is kept for a first signal.
    return isNew ? (
      <span className={`label !text-signal ${className ?? ""}`} title="First signal in the last week">
        New
      </span>
    ) : (
      <span className={`label !text-ink ${className ?? ""}`} title="Not ranked a week ago">
        In
      </span>
    );
  }
  const d = prev - rank;
  if (d === 0) {
    return (
      <span className={`num text-[11px] text-ink-4 ${className ?? ""}`} title="No change in a week">
        –
      </span>
    );
  }
  const up = d > 0;
  return (
    <span
      className={`num text-[11px] ${up ? "text-signal" : "text-ink-4"} ${className ?? ""}`}
      title={`${up ? "Up" : "Down"} ${Math.abs(d)} in a week (was ${prev})`}
    >
      {up ? "▲" : "▼"}
      {Math.abs(d)}
    </span>
  );
}
