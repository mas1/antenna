"use client";

type Props = {
  on: boolean;
  onToggle: () => void;
  name: string;
  size?: number;
  className?: string;
};

/** A star that keeps a company on this browser's watchlist. */
export function StarButton({ on, onToggle, name, size = 15, className }: Props) {
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={on ? `Remove ${name} from starred` : `Star ${name}`}
      title={on ? "Starred. Click to remove." : "Star to keep on your watchlist"}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onToggle();
      }}
      className={`grid size-6 place-items-center text-ink transition-[opacity,transform] duration-150 active:scale-90 ${className ?? ""}`}
    >
      <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
        <path
          d="M8 1.6l1.9 4.1 4.5.5-3.3 3 .9 4.4L8 11.4l-4 2.2.9-4.4-3.3-3 4.5-.5z"
          fill={on ? "var(--ink)" : "none"}
          stroke="var(--ink)"
          strokeWidth={1.1}
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
