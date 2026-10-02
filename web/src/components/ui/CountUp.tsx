"use client";

import { animate, useInView, useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";

type Props = { value: number; decimals?: number; duration?: number; className?: string };

/** A number that counts up once, the first time it scrolls into view. */
export function CountUp({ value, decimals = 0, duration = 0.9, className }: Props) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true, margin: "-10% 0px" });
  const reduce = useReducedMotion();
  const fmt = (n: number) =>
    n.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

  useEffect(() => {
    const el = ref.current;
    if (!el || !inView) return;
    // No counting when motion is reduced, or in a background tab, where
    // animation frames are paused and the number would sit at a part-way value.
    if (reduce || document.visibilityState === "hidden") {
      el.textContent = fmt(value);
      return;
    }
    const controls = animate(0, value, {
      duration,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => {
        el.textContent = fmt(v);
      },
    });
    return () => controls.stop();
    // fmt depends only on decimals
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, value, duration, reduce, decimals]);

  // Server render carries the final value so the page is correct without JS.
  return (
    <span ref={ref} className={`num ${className ?? ""}`} suppressHydrationWarning>
      {fmt(value)}
    </span>
  );
}
