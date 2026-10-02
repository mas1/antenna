type Props = {
  children: React.ReactNode;
  /** Seconds to wait before rising, for a stagger. */
  delay?: number;
  className?: string;
  as?: "div" | "section" | "li";
};

/**
 * Rise and fade, once, on load. It is one CSS rule ("reveal" in globals.css)
 * and no JavaScript, so the server HTML is already visible: the page prints,
 * reads with scripts blocked, and is complete in a tab opened in the background.
 */
export function Reveal({ children, delay = 0, className, as: Tag = "div" }: Props) {
  return (
    <Tag
      className={`reveal ${className ?? ""}`}
      style={delay ? ({ "--d": `${delay}s` } as React.CSSProperties) : undefined}
    >
      {children}
    </Tag>
  );
}
