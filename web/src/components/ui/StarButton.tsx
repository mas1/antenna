"use client";

type Props = {
  on: boolean;
  onToggle: () => void;
  name: string;
  size?: number;
  className?: string;
};

/** A star that keeps a company starred in this browser. */
export function StarButton({ on, onToggle, name, size = 15, className }: Props) {
  return (
    <button
      type="button"
      aria-pressed={on}
      aria-label={on ? `Remove ${name} from starred` : `Star ${name}`}
      title={on ? "Remove star" : "Star"}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        onToggle();
      }}
      className={`grid size-6 place-items-center text-ink transition-[opacity,transform] duration-150 active:scale-90 aria-[pressed=false]:hover:[&_path]:fill-paper-3 ${className ?? ""}`}
    >
      <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden>
        {/* Empty is transparent, not none: a colour is something the fill can ease from. */}
        <path
          d="M8 1.6l1.9 4.1 4.5.5-3.3 3 .9 4.4L8 11.4l-4 2.2.9-4.4-3.3-3 4.5-.5z"
          className="transition-[fill] duration-150"
          fill={on ? "var(--ink)" : "transparent"}
          stroke="var(--ink)"
          strokeWidth={1.1}
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}
